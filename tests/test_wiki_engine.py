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

    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [analyze_json, plan_json]
    mock_llm.chat.side_effect = [page_md, index_md]

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
