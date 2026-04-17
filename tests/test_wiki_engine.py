"""Tests for WikiEngine with mocked LLM and Qdrant."""

import json
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


def test_query_stream_evidence_urls_include_repo_prefix(tmp_data_dir):
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
    mock_llm.chat_json.return_value = {"filenames": ["concept.md"]}
    mock_llm.chat_stream.return_value = iter(["Here", " is", " answer"])

    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = [{
        "chunk_id": "concept.md#0",
        "filename": "concept.md",
        "page_title": "Concept",
        "heading": "Intro",
        "chunk_text": "Details.",
        "score": 0.92,
    }]

    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)
    repo = MagicMock()
    repo.slug = "r1"
    repo.id = 42

    events = list(engine.query_stream(repo, "alice", "What is it?"))
    done = next(e for e in events if e["event"] == "done")

    assert done["data"]["wiki_evidence"][0]["url"] == "/alice/r1/wiki/concept"
    assert done["data"]["chunk_evidence"][0]["url"] == "/alice/r1/wiki/concept"


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


def test_ingest_indexes_fact_records_if_present(tmp_data_dir):
    base = os.path.join(tmp_data_dir, "alice", "facts-kb")
    os.makedirs(os.path.join(base, "raw"), exist_ok=True)
    os.makedirs(os.path.join(base, "wiki"), exist_ok=True)
    os.makedirs(os.path.join(base, "facts", "records"), exist_ok=True)

    with open(os.path.join(base, "raw", "sales.md"), "w", encoding="utf-8") as f:
        f.write("# sales\n\n| 地区 | 收入 |\n| --- | --- |\n| 华东 | 1200 |\n")
    with open(os.path.join(base, "facts", "records", "sales.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "record_id": "csv:2",
            "source_file": "sales.csv",
            "source_markdown_filename": "sales.md",
            "sheet": "CSV",
            "row_index": 2,
            "fields": {"地区": "华东", "收入": 1200},
            "fact_text": "来源=sales.csv; 表=CSV; 行=2; 地区=华东; 收入=1200",
        }, ensure_ascii=False) + "\n")

    for name, body in [
        ("schema.md", "# Schema\n\nRules here.\n"),
        ("index.md", "---\ntitle: Home\ntype: index\nupdated: 2026-01-01\n---\n\n# Index\n"),
        ("log.md", "---\ntitle: Log\ntype: log\n---\n\n# Log\n"),
    ]:
        with open(os.path.join(base, "wiki", name), "w", encoding="utf-8") as f:
            f.write(body)

    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [
        {"summary": "sales", "key_entities": [], "key_concepts": [], "main_findings": []},
        {"pages_to_create": [], "pages_to_update": []},
    ]
    mock_llm.chat.side_effect = [
        "---\ntitle: 首页\ntype: index\nupdated: 2026-01-01\n---\n\n# 首页\n",
    ]
    mock_qdrant = MagicMock()
    progress_events = []

    def fake_upsert_fact_records(**kwargs):
        kwargs["progress_callback"](10, 100)
        kwargs["progress_callback"](80, 100)

    mock_qdrant.upsert_fact_records.side_effect = fake_upsert_fact_records

    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)
    repo = MagicMock()
    repo.slug = "facts-kb"
    repo.id = 7

    events = list(engine.ingest(repo, "alice", "sales.md", progress_callback=progress_events.append))

    assert any(e.get("phase") == "done" for e in events)
    mock_qdrant.upsert_fact_records.assert_called_once()
    assert any(event.get("message") == "Indexing 10/100 fact records …" for event in progress_events)
    assert any(event.get("message") == "Indexing 80/100 fact records …" for event in progress_events)


def test_query_with_evidence_returns_fact_evidence(tmp_data_dir):
    wiki_dir = os.path.join(tmp_data_dir, "alice", "facts-only", "wiki")
    os.makedirs(wiki_dir, exist_ok=True)
    with open(os.path.join(wiki_dir, "schema.md"), "w", encoding="utf-8") as f:
        f.write("# Schema\n")
    with open(os.path.join(wiki_dir, "index.md"), "w", encoding="utf-8") as f:
        f.write("---\ntitle: 首页\ntype: index\n---\n\n# 首页\n")

    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {"filenames": []}
    mock_llm.chat.return_value = "华东地区 2024Q4 收入为 1200。"

    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = []
    mock_qdrant.search_facts.return_value = [
        {
            "record_id": "csv:2",
            "source_file": "sales.csv",
            "source_markdown_filename": "sales.md",
            "sheet": "CSV",
            "row_index": 2,
            "fields": {"地区": "华东", "收入": 1200},
            "fact_text": "来源=sales.csv; 表=CSV; 行=2; 地区=华东; 收入=1200",
            "score": 0.96,
        }
    ]

    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)
    repo = MagicMock()
    repo.slug = "facts-only"
    repo.id = 9

    result = engine.query_with_evidence(repo, "alice", "华东地区 2024Q4 收入是多少？", "/alice/facts-only/wiki")

    assert result["fact_evidence"]
    assert result["fact_evidence"][0]["fields"]["收入"] == 1200
    assert "结构化事实" in result["evidence_summary"]


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


def test_ingest_runs_multiple_pages_concurrently(tmp_data_dir):
    """ingest_llm_concurrency>1 时，多页应同时触发 LLM 调用（峰值并发 >= 2）。"""
    import threading
    import time

    base = os.path.join(tmp_data_dir, "alice", "concurrency")
    os.makedirs(os.path.join(base, "raw"), exist_ok=True)
    os.makedirs(os.path.join(base, "wiki"), exist_ok=True)
    with open(os.path.join(base, "raw", "doc.md"), "w", encoding="utf-8") as f:
        f.write("# Source\n\nbody\n")
    for name, body in [
        ("schema.md", "# Schema\n"),
        ("index.md", "---\ntitle: x\ntype: index\n---\n\n"),
    ]:
        with open(os.path.join(base, "wiki", name), "w", encoding="utf-8") as f:
            f.write(body)

    analyze_json = {"summary": "s", "key_entities": [], "key_concepts": [], "main_findings": []}
    plan_json = {
        "pages_to_create": [
            {"filename": f"p{i}.md", "title": f"P{i}", "type": "concept", "reason": "r"}
            for i in range(3)
        ],
        "pages_to_update": [],
    }

    active = {"n": 0, "peak": 0}
    lock = threading.Lock()

    def slow_chat(*args, **kwargs):
        with lock:
            active["n"] += 1
            active["peak"] = max(active["peak"], active["n"])
        time.sleep(0.15)
        with lock:
            active["n"] -= 1
        return "---\ntitle: p\ntype: concept\nupdated: 2026-01-01\n---\n\n# P\n\nBody.\n"

    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [analyze_json, plan_json]
    mock_llm.chat.side_effect = slow_chat

    mock_qdrant = MagicMock()
    engine = WikiEngine(
        mock_llm, mock_qdrant, tmp_data_dir,
        ingest_llm_concurrency=3,
        ingest_index_concurrency=3,
    )
    repo = MagicMock()
    repo.slug = "concurrency"
    repo.id = 42

    events = list(engine.ingest(repo, "alice", "doc.md"))

    assert any(e.get("phase") == "done" for e in events)
    wiki_dir = os.path.join(base, "wiki")
    for i in range(3):
        assert os.path.isfile(os.path.join(wiki_dir, f"p{i}.md")), f"page p{i}.md not created"
    assert active["peak"] >= 2, f"page generation should run concurrently, peak={active['peak']}"
    # 3 个新页面 + 可能的 overview.md
    assert mock_qdrant.upsert_page.call_count >= 3
    assert mock_qdrant.upsert_page_chunks.call_count >= 3


def test_ingest_handles_single_page_generation_failure(tmp_data_dir):
    """单页生成失败不应阻塞其他页面。"""
    base = os.path.join(tmp_data_dir, "alice", "fail-iso")
    os.makedirs(os.path.join(base, "raw"), exist_ok=True)
    os.makedirs(os.path.join(base, "wiki"), exist_ok=True)
    with open(os.path.join(base, "raw", "doc.md"), "w", encoding="utf-8") as f:
        f.write("# S\n\nbody\n")
    with open(os.path.join(base, "wiki", "schema.md"), "w", encoding="utf-8") as f:
        f.write("# s\n")

    plan_json = {
        "pages_to_create": [
            {"filename": "ok1.md", "title": "O", "type": "concept", "reason": "r"},
            {"filename": "bad.md", "title": "B", "type": "concept", "reason": "r"},
            {"filename": "ok2.md", "title": "O2", "type": "concept", "reason": "r"},
        ],
        "pages_to_update": [],
    }
    analyze_json = {"summary": "", "key_entities": [], "key_concepts": [], "main_findings": []}

    call_count = {"n": 0}

    def flaky_chat(*args, **kwargs):
        call_count["n"] += 1
        messages = args[0] if args else kwargs.get("messages", [])
        user_content = messages[1].get("content", "") if len(messages) > 1 else ""
        if "文件名: bad.md" in user_content:
            raise RuntimeError("boom")
        return "---\ntitle: t\ntype: concept\n---\n\n# T\n"

    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [analyze_json, plan_json]
    mock_llm.chat.side_effect = flaky_chat

    mock_qdrant = MagicMock()
    engine = WikiEngine(
        mock_llm, mock_qdrant, tmp_data_dir,
        ingest_llm_concurrency=3,
        ingest_index_concurrency=3,
    )
    repo = MagicMock()
    repo.slug = "fail-iso"
    repo.id = 99

    events = list(engine.ingest(repo, "alice", "doc.md"))

    wiki_dir = os.path.join(base, "wiki")
    assert os.path.isfile(os.path.join(wiki_dir, "ok1.md"))
    assert os.path.isfile(os.path.join(wiki_dir, "ok2.md"))
    assert not os.path.isfile(os.path.join(wiki_dir, "bad.md"))
    assert any("Failed to create bad.md" in e.get("message", "") for e in events)
    assert any(e.get("phase") == "done" for e in events)


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



# ---------------------------------------------------------------------------
# Prompt guard / comparison template / citation postcheck integration tests
# ---------------------------------------------------------------------------


def _build_engine_with_guard(tmp_data_dir, mock_llm, mock_qdrant, **overrides):
    """Helper: 构造开启 guard / 对比模板 / 引用校验的 WikiEngine。"""
    kwargs = dict(
        enable_prompt_guard=True,
        enable_comparison_template=True,
        comparison_min_dimensions=3,
        citation_postcheck=True,
        citation_penalty=0.25,
    )
    kwargs.update(overrides)
    return WikiEngine(mock_llm, mock_qdrant, tmp_data_dir, **kwargs)


def _make_ae_repo(tmp_data_dir, slug="guard-repo"):
    wiki_dir = os.path.join(tmp_data_dir, "alice", slug, "wiki")
    os.makedirs(wiki_dir, exist_ok=True)
    with open(os.path.join(wiki_dir, "index.md"), "w") as f:
        f.write("---\ntitle: 首页\ntype: index\n---\n\n- [AE350](ae350-overview.md)\n- [AE650](ae650-overview.md)\n")
    with open(os.path.join(wiki_dir, "ae350-overview.md"), "w") as f:
        f.write("---\ntitle: AE350\ntype: product\n---\n\n# AE350\n\n拾音距离 5 米。\n")
    with open(os.path.join(wiki_dir, "ae650-overview.md"), "w") as f:
        f.write("---\ntitle: AE650\ntype: product\n---\n\n# AE650\n\n拾音距离 8 米。\n")
    repo = MagicMock()
    repo.slug = slug
    repo.id = 1
    return repo


def test_query_with_evidence_injects_guard_when_context_present(tmp_data_dir):
    """有 context 时，system prompt 必须包含 guard 核心规则。"""
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {"filenames": ["ae350-overview.md", "ae650-overview.md"]}
    mock_llm.chat.return_value = "回答。依据：ae350-overview.md。"

    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = [
        {"chunk_id": "ae350-overview.md#0", "filename": "ae350-overview.md",
         "page_title": "AE350", "heading": "", "chunk_text": "拾音 5 米", "position": 0, "score": 0.88},
        {"chunk_id": "ae650-overview.md#0", "filename": "ae650-overview.md",
         "page_title": "AE650", "heading": "", "chunk_text": "拾音 8 米", "position": 0, "score": 0.85},
    ]

    engine = _build_engine_with_guard(tmp_data_dir, mock_llm, mock_qdrant)
    repo = _make_ae_repo(tmp_data_dir)

    engine.query_with_evidence(repo, "alice", "AE350 和 AE650 的区别")

    # _chat_text -> llm.chat(messages=...)
    calls = mock_llm.chat.call_args_list
    assert calls, "LLM chat should have been called"
    # 取最后一次（生成答案那次）的 messages
    messages = calls[-1].args[0] if calls[-1].args else calls[-1].kwargs["messages"]
    system_content = messages[0]["content"]
    user_content = messages[1]["content"]
    assert "字段级严格" in system_content
    assert "不跨实体传染" in system_content
    assert "可引用的来源列表" in user_content
    assert "ae350-overview.md" in user_content
    assert "ae650-overview.md" in user_content


def test_query_with_evidence_comparison_intent_uses_comparison_template(tmp_data_dir):
    """对比问题应走对比模板（包含『维度对比表』『资料缺口』等段）。"""
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {"filenames": ["ae350-overview.md", "ae650-overview.md"]}
    mock_llm.chat.return_value = "对比回答"

    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = [
        {"chunk_id": "ae350-overview.md#0", "filename": "ae350-overview.md",
         "page_title": "AE350", "heading": "", "chunk_text": "5 米", "position": 0, "score": 0.80},
    ]
    engine = _build_engine_with_guard(tmp_data_dir, mock_llm, mock_qdrant)
    repo = _make_ae_repo(tmp_data_dir, slug="guard-cmp")

    result = engine.query_with_evidence(repo, "alice", "AE350 和 AE650 哪款更适合大会议室")

    assert result.get("intent") == "comparison"
    calls = mock_llm.chat.call_args_list
    messages = calls[-1].args[0] if calls[-1].args else calls[-1].kwargs["messages"]
    user_content = messages[1]["content"]
    assert "维度对比表" in user_content
    assert "资料缺口" in user_content
    assert "退化规则" in user_content


def test_query_with_evidence_generic_intent_skips_comparison_template(tmp_data_dir):
    """非对比问题走通用模板，不应出现『维度对比表』段。"""
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {"filenames": ["ae350-overview.md"]}
    mock_llm.chat.return_value = "这是答案"

    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = [
        {"chunk_id": "ae350-overview.md#0", "filename": "ae350-overview.md",
         "page_title": "AE350", "heading": "", "chunk_text": "5 米", "position": 0, "score": 0.80},
    ]
    engine = _build_engine_with_guard(tmp_data_dir, mock_llm, mock_qdrant)
    repo = _make_ae_repo(tmp_data_dir, slug="guard-gen")

    result = engine.query_with_evidence(repo, "alice", "AE350 的拾音距离是多少")

    assert result.get("intent") == "generic"
    calls = mock_llm.chat.call_args_list
    messages = calls[-1].args[0] if calls[-1].args else calls[-1].kwargs["messages"]
    user_content = messages[1]["content"]
    assert "维度对比表" not in user_content


def test_query_with_evidence_citation_postcheck_downgrades_confidence(tmp_data_dir):
    """LLM 编造文件名时，citation_postcheck 应扣分并写入 validation 字段。"""
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {"filenames": ["ae350-overview.md"]}
    # 答案里引用了一个根本不存在的 fake-manual.md
    mock_llm.chat.return_value = "详见 ae350-overview.md 和 fake-manual.md 中描述。"

    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = [
        {"chunk_id": "ae350-overview.md#0", "filename": "ae350-overview.md",
         "page_title": "AE350", "heading": "", "chunk_text": "拾音 5 米", "position": 0, "score": 0.90},
    ]
    engine = _build_engine_with_guard(tmp_data_dir, mock_llm, mock_qdrant)
    repo = _make_ae_repo(tmp_data_dir, slug="guard-cite")

    result = engine.query_with_evidence(repo, "alice", "AE350 的拾音距离")

    validation = result.get("citation_validation")
    assert validation is not None
    assert validation["ok"] is False
    assert "fake-manual.md" in validation["unknown"]
    # reasons 里应该提到疑似编造
    assert any("疑似编造" in r for r in result["confidence"]["reasons"])


def test_query_with_evidence_guard_skipped_when_context_empty(tmp_data_dir):
    """完全无命中时走空 context 分支，不应注入 guard（也不会调用 LLM）。"""
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {"filenames": []}
    mock_llm.chat.return_value = "应当不被调用"

    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = []
    mock_qdrant.search_facts.return_value = []

    engine = _build_engine_with_guard(tmp_data_dir, mock_llm, mock_qdrant)
    wiki_dir = os.path.join(tmp_data_dir, "alice", "empty-guard", "wiki")
    os.makedirs(wiki_dir, exist_ok=True)
    with open(os.path.join(wiki_dir, "index.md"), "w") as f:
        f.write("---\ntitle: 首页\ntype: index\n---\n\n(空)\n")
    repo = MagicMock()
    repo.slug = "empty-guard"
    repo.id = 99

    result = engine.query_with_evidence(repo, "alice", "随便问点什么")
    assert "缺少相关" in result["markdown"] or "缺少" in result["markdown"] or "暂无相关" in result["markdown"]


def test_comparison_template_disabled_by_flag(tmp_data_dir):
    """关闭 enable_comparison_template 后，对比类问题也走通用模板。"""
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {"filenames": ["ae350-overview.md"]}
    mock_llm.chat.return_value = "answer"
    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = [
        {"chunk_id": "ae350-overview.md#0", "filename": "ae350-overview.md",
         "page_title": "AE350", "heading": "", "chunk_text": "x", "position": 0, "score": 0.80},
    ]
    engine = _build_engine_with_guard(
        tmp_data_dir, mock_llm, mock_qdrant, enable_comparison_template=False,
    )
    repo = _make_ae_repo(tmp_data_dir, slug="guard-disabled-cmp")

    result = engine.query_with_evidence(repo, "alice", "AE350 vs AE650 哪个好")

    assert result["intent"] == "generic"
    calls = mock_llm.chat.call_args_list
    messages = calls[-1].args[0] if calls[-1].args else calls[-1].kwargs["messages"]
    user_content = messages[1]["content"]
    assert "维度对比表" not in user_content


def test_prompt_guard_disabled_by_flag(tmp_data_dir):
    """关闭 enable_prompt_guard 后，system prompt 不包含 guard 规则。"""
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {"filenames": ["ae350-overview.md"]}
    mock_llm.chat.return_value = "answer"
    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = [
        {"chunk_id": "ae350-overview.md#0", "filename": "ae350-overview.md",
         "page_title": "AE350", "heading": "", "chunk_text": "x", "position": 0, "score": 0.80},
    ]
    engine = _build_engine_with_guard(
        tmp_data_dir, mock_llm, mock_qdrant, enable_prompt_guard=False,
    )
    repo = _make_ae_repo(tmp_data_dir, slug="guard-disabled")

    engine.query_with_evidence(repo, "alice", "什么是 AE350")

    calls = mock_llm.chat.call_args_list
    messages = calls[-1].args[0] if calls[-1].args else calls[-1].kwargs["messages"]
    system_content = messages[0]["content"]
    assert "字段级严格" not in system_content
    assert "不跨实体传染" not in system_content
