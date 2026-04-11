"""Tests for WikiEngine with mocked LLM and Qdrant."""

import os
from unittest.mock import MagicMock

from wiki_engine import WikiEngine


def test_ingest_empty_source(tmp_data_dir):
    mock_llm = MagicMock()
    mock_qdrant = MagicMock()
    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)

    repo = MagicMock()
    repo.slug = "test"
    repo.id = 1

    gen = engine.ingest(repo, "alice", "missing.md")
    events = list(gen)

    assert any(e.get("phase") == "error" for e in events)
    err = next(e for e in events if e.get("phase") == "error")
    assert "missing.md" in err.get("message", "")


def test_ingest_full_flow(tmp_data_dir):
    base = os.path.join(tmp_data_dir, "alice", "test")
    os.makedirs(os.path.join(base, "raw"), exist_ok=True)
    os.makedirs(os.path.join(base, "wiki"), exist_ok=True)

    with open(os.path.join(base, "raw", "doc.md"), "w", encoding="utf-8") as f:
        f.write("# Source\n\nSome **markdown** content for ingest.\n")

    for name, body in [
        ("schema.md", "# Schema\n\nRules here.\n"),
        ("index.md", "---\ntitle: Home\ntype: index\nupdated: 2026-01-01\n---\n\n# Index\n"),
        ("log.md", "---\ntitle: Log\ntype: log\n---\n\n# Log\n"),
    ]:
        with open(os.path.join(base, "wiki", name), "w", encoding="utf-8") as f:
            f.write(body)

    analyze_json = {
        "summary": "test",
        "key_entities": ["E1"],
        "key_concepts": ["C1"],
        "main_findings": ["F1"],
    }
    plan_json = {
        "pages_to_create": [
            {
                "filename": "concept-e1.md",
                "title": "E1",
                "type": "concept",
                "reason": "new entity",
            }
        ],
        "pages_to_update": [],
    }
    page_md = """---
title: E1
type: concept
tags: [t1]
source: doc.md
updated: 2026-01-01
---

# E1

Body with [link](other.md).
"""
    index_md = """---
title: 首页
type: index
updated: 2026-01-01
---

# Wiki index

- [E1](concept-e1.md)
"""

    overview_md = "---\ntitle: 概览\ntype: overview\nupdated: 2026-01-01\n---\n\n# 概览\n\n概览内容。\n"

    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [analyze_json, plan_json]
    mock_llm.chat.side_effect = [page_md, index_md, overview_md]

    mock_qdrant = MagicMock()

    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)
    repo = MagicMock()
    repo.slug = "test"
    repo.id = 1

    events = list(engine.ingest(repo, "alice", "doc.md"))

    assert any(e.get("phase") == "done" for e in events)
    wiki_dir = os.path.join(base, "wiki")
    assert os.path.isfile(os.path.join(wiki_dir, "concept-e1.md"))
    mock_qdrant.upsert_page.assert_called()


def test_query_no_content(tmp_data_dir):
    mock_llm = MagicMock()
    mock_qdrant = MagicMock()
    mock_qdrant.search.return_value = []

    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)
    repo = MagicMock()
    repo.slug = "empty"
    repo.id = 1

    result = engine.query(repo, "alice", "anything?")

    assert "暂无" in result["answer"]
    assert result["referenced_pages"] == []


def test_query_with_pages(tmp_data_dir):
    wiki_dir = os.path.join(tmp_data_dir, "alice", "r1", "wiki")
    os.makedirs(wiki_dir, exist_ok=True)

    with open(os.path.join(wiki_dir, "index.md"), "w", encoding="utf-8") as f:
        f.write(
            "---\ntitle: 首页\ntype: index\nupdated: 2026-01-01\n---\n\n# Index\n\n- [C](concept.md)\n"
        )
    with open(os.path.join(wiki_dir, "concept.md"), "w", encoding="utf-8") as f:
        f.write(
            "---\ntitle: Concept\ntype: concept\nupdated: 2026-01-01\n---\n\n# Concept\n\nDetails.\n"
        )

    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [
        {"filenames": ["concept.md"]},
        {
            "answer": "Here is the **answer**.",
            "referenced_pages": ["concept.md"],
            "suggested_filename": None,
        },
    ]

    mock_qdrant = MagicMock()
    mock_qdrant.search.return_value = [{"filename": "concept.md", "score": 0.95}]

    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)
    repo = MagicMock()
    repo.slug = "r1"
    repo.id = 42

    result = engine.query(repo, "alice", "What is it?")

    assert "answer" in result
    assert result["answer"]
    assert "concept.md" in result["referenced_pages"]


def test_ingest_updates_overview(tmp_data_dir):
    """overview.md should be updated by LLM after ingest when pages are created."""
    base = os.path.join(tmp_data_dir, "alice", "test-ov")
    os.makedirs(os.path.join(base, "raw"), exist_ok=True)
    os.makedirs(os.path.join(base, "wiki"), exist_ok=True)

    with open(os.path.join(base, "raw", "doc.md"), "w", encoding="utf-8") as f:
        f.write("# Source\n\nSome content about AI.\n")

    overview_path = os.path.join(base, "wiki", "overview.md")
    for name, body in [
        ("schema.md", "# Schema\n\nRules here.\n"),
        ("index.md", "---\ntitle: Home\ntype: index\nupdated: 2026-01-01\n---\n\n# Index\n"),
        ("log.md", "---\ntitle: Log\ntype: log\n---\n\n# Log\n"),
        ("overview.md", "---\ntitle: 概览\ntype: overview\n---\n\n暂无概览内容。上传文档并摄入后，此页面将自动更新。\n"),
    ]:
        with open(os.path.join(base, "wiki", name), "w", encoding="utf-8") as f:
            f.write(body)

    analyze_json = {
        "summary": "AI overview",
        "key_entities": ["AI"],
        "key_concepts": ["ML"],
        "main_findings": ["AI is useful"],
    }
    plan_json = {
        "pages_to_create": [
            {"filename": "ai.md", "title": "AI", "type": "concept", "reason": "new topic"}
        ],
        "pages_to_update": [],
    }
    page_md = "---\ntitle: AI\ntype: concept\nupdated: 2026-01-01\n---\n\n# AI\n\nContent.\n"
    index_md = "---\ntitle: 首页\ntype: index\nupdated: 2026-01-01\n---\n\n# Wiki\n\n- [AI](ai.md)\n"
    overview_md = "---\ntitle: 概览\ntype: overview\nupdated: 2026-01-01\n---\n\n# 概览\n\n本知识库包含 AI 相关内容。\n"

    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [analyze_json, plan_json]
    mock_llm.chat.side_effect = [page_md, index_md, overview_md]

    mock_qdrant = MagicMock()

    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)
    repo = MagicMock()
    repo.slug = "test-ov"
    repo.id = 1

    events = list(engine.ingest(repo, "alice", "doc.md"))

    assert any(e.get("phase") == "done" for e in events)
    updated_content = open(overview_path, encoding="utf-8").read()
    assert "暂无概览内容" not in updated_content
    assert "AI" in updated_content
    assert mock_qdrant.upsert_page.call_count >= 2  # once for ai.md, once for overview.md


def test_lint_empty_wiki(tmp_data_dir):
    mock_llm = MagicMock()
    mock_qdrant = MagicMock()
    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)

    repo = MagicMock()
    repo.slug = "bare"
    repo.id = 1

    result = engine.lint(repo, "alice")

    assert any("empty" in s.lower() for s in result.get("suggestions", []))


def test_lint_with_pages(tmp_data_dir):
    wiki_dir = os.path.join(tmp_data_dir, "alice", "linted", "wiki")
    os.makedirs(wiki_dir, exist_ok=True)
    with open(os.path.join(wiki_dir, "a.md"), "w", encoding="utf-8") as f:
        f.write(
            "---\ntitle: A\ntype: concept\nupdated: 2026-01-01\n---\n\n# A\n\nText.\n"
        )

    issues_payload = {
        "issues": [
            {
                "type": "orphan",
                "page": "a.md",
                "description": "No inbound links",
            }
        ],
        "suggestions": ["Add cross-links"],
    }

    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = issues_payload
    mock_qdrant = MagicMock()

    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)
    repo = MagicMock()
    repo.slug = "linted"
    repo.id = 1

    result = engine.lint(repo, "alice")

    assert "issues" in result
    assert "suggestions" in result
    assert len(result["issues"]) == 1
    assert result["issues"][0].get("type") == "orphan"
    assert result["suggestions"] == ["Add cross-links"]


def test_apply_fixes_bad_frontmatter(tmp_path):
    """apply_fixes should fix bad_frontmatter issues via LLM."""
    import os as _os
    wiki_dir = tmp_path / "alice" / "test-fix" / "wiki"
    wiki_dir.mkdir(parents=True)
    (tmp_path / "alice" / "test-fix" / "raw").mkdir(parents=True, exist_ok=True)

    bad_page = wiki_dir / "bad-page.md"
    bad_page.write_text("# Missing Frontmatter\n\nSome content here.\n")
    (wiki_dir / "schema.md").write_text("---\ntitle: Schema\n---\n")
    (wiki_dir / "index.md").write_text("---\ntitle: Index\ntype: index\n---\n# Index\n")

    fixed_content = (
        "---\ntitle: Missing Frontmatter\ntype: concept\nupdated: 2026-01-01\n---\n\n"
        "# Missing Frontmatter\n\nSome content here.\n"
    )
    mock_llm = MagicMock()
    mock_llm.chat.return_value = fixed_content
    mock_qdrant = MagicMock()

    engine = WikiEngine(mock_llm, mock_qdrant, str(tmp_path))
    mock_repo = MagicMock()
    mock_repo.slug = "test-fix"
    mock_repo.id = 1

    issues = [{"type": "bad_frontmatter", "page": "bad-page.md", "description": "Missing frontmatter"}]
    result = engine.apply_fixes(mock_repo, "alice", issues)

    assert "bad-page.md" in result["fixed"]
    assert len(result["errors"]) == 0
    updated = bad_page.read_text()
    assert "title:" in updated


def test_apply_fixes_skips_contradictions(tmp_path):
    """apply_fixes should skip contradiction issues without calling LLM."""
    wiki_dir = tmp_path / "alice" / "test-skip" / "wiki"
    wiki_dir.mkdir(parents=True)
    (tmp_path / "alice" / "test-skip" / "raw").mkdir(parents=True, exist_ok=True)
    (wiki_dir / "schema.md").write_text("---\ntitle: Schema\n---\n")

    mock_llm = MagicMock()
    mock_qdrant = MagicMock()
    engine = WikiEngine(mock_llm, mock_qdrant, str(tmp_path))
    mock_repo = MagicMock()
    mock_repo.slug = "test-skip"
    mock_repo.id = 1

    issues = [{"type": "contradiction", "page": "page-a.md", "description": "Conflicting info"}]
    result = engine.apply_fixes(mock_repo, "alice", issues)

    assert "page-a.md" in result["skipped"]
    assert len(result["fixed"]) == 0
    mock_llm.chat.assert_not_called()


# ── confidence scoring ────────────────────────────────────────


def test_confidence_high(tmp_data_dir):
    mock_llm = MagicMock()
    engine = WikiEngine(mock_llm, MagicMock(), tmp_data_dir)
    result = engine._score_confidence(
        wiki_hit_count=3, chunk_hit_count=4,
        top_chunk_score=0.90, hit_overview=True,
        both_channels=True, answer_text="正常回答"
    )
    assert result["level"] == "high"
    assert result["score"] >= 0.75
    assert isinstance(result["reasons"], list)


def test_confidence_medium(tmp_data_dir):
    mock_llm = MagicMock()
    engine = WikiEngine(mock_llm, MagicMock(), tmp_data_dir)
    result = engine._score_confidence(
        wiki_hit_count=1, chunk_hit_count=2,
        top_chunk_score=0.60, hit_overview=False,
        both_channels=False, answer_text="正常回答"
    )
    assert result["level"] == "medium"
    assert 0.45 <= result["score"] < 0.75


def test_confidence_low_no_evidence(tmp_data_dir):
    mock_llm = MagicMock()
    engine = WikiEngine(mock_llm, MagicMock(), tmp_data_dir)
    result = engine._score_confidence(
        wiki_hit_count=0, chunk_hit_count=0,
        top_chunk_score=0.0, hit_overview=False,
        both_channels=False, answer_text=""
    )
    assert result["level"] == "low"
    assert result["score"] < 0.45


def test_confidence_uncertainty_penalty(tmp_data_dir):
    mock_llm = MagicMock()
    engine = WikiEngine(mock_llm, MagicMock(), tmp_data_dir)
    result = engine._score_confidence(
        wiki_hit_count=1, chunk_hit_count=2,
        top_chunk_score=0.78, hit_overview=False,
        both_channels=True,
        answer_text="基于现有资料只能推测到一些内容"
    )
    result_no_penalty = engine._score_confidence(
        wiki_hit_count=1, chunk_hit_count=2,
        top_chunk_score=0.78, hit_overview=False,
        both_channels=True, answer_text="正常回答"
    )
    assert result["score"] < result_no_penalty["score"]


def test_query_with_evidence_no_content(tmp_data_dir):
    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = []
    engine = WikiEngine(MagicMock(), mock_qdrant, tmp_data_dir)
    repo = MagicMock()
    repo.slug = "empty"
    repo.id = 1
    result = engine.query_with_evidence(repo, "alice", "test?")
    assert "markdown" in result
    assert "confidence" in result
    assert result["confidence"]["level"] == "low"
    assert result["wiki_evidence"] == []
    assert result["chunk_evidence"] == []


def test_query_with_evidence_with_pages(tmp_data_dir):
    wiki_dir = os.path.join(tmp_data_dir, "alice", "ev1", "wiki")
    os.makedirs(wiki_dir, exist_ok=True)
    with open(os.path.join(wiki_dir, "index.md"), "w") as f:
        f.write("---\ntitle: 首页\ntype: index\n---\n\n- [C](concept.md)\n")
    with open(os.path.join(wiki_dir, "concept.md"), "w") as f:
        f.write("---\ntitle: Concept\ntype: concept\n---\n\n# Concept\n\nDetails.\n")

    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {"filenames": ["concept.md"]}
    mock_llm.chat.return_value = "Here is the answer."

    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = [{
        "chunk_id": "concept.md#0", "filename": "concept.md",
        "page_title": "Concept", "page_type": "concept",
        "heading": "Details", "chunk_text": "Details here.", "position": 0,
        "score": 0.88,
    }]

    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)
    repo = MagicMock()
    repo.slug = "ev1"
    repo.id = 1
    result = engine.query_with_evidence(repo, "alice", "What is it?")

    assert result["markdown"]
    assert len(result["wiki_evidence"]) >= 1
    assert len(result["chunk_evidence"]) >= 1
    assert result["confidence"]["level"] in ("high", "medium", "low")
    assert "evidence_summary" in result
    assert "wiki_sources" in result
    assert "qdrant_sources" in result


def test_apply_fixes_route(sample_repo, app):
    """apply_fixes route should handle empty issues_json gracefully."""
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/apply-fixes",
        data={"issues_json": "[]"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
