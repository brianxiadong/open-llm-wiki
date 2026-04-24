"""Tests for QdrantService chunk indexing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def qdrant_service():
    mock_client = MagicMock()
    mock_client.collection_exists.return_value = False
    with patch("qdrant_service.QdrantClient", return_value=mock_client), \
         patch("qdrant_service.OpenAI") as mock_openai:
        mock_embed_resp = MagicMock()
        mock_embed_resp.data = [MagicMock(embedding=[0.1] * 128)]
        mock_embed_resp.usage = MagicMock(total_tokens=10)
        mock_openai.return_value.embeddings.create.return_value = mock_embed_resp
        from qdrant_service import QdrantService
        svc = QdrantService(
            "http://fake:6333", "http://fake-embed", "", "test-model", 128
        )
        svc._qdrant = mock_client
        yield svc, mock_client


def test_chunk_collection_name(qdrant_service):
    svc, _ = qdrant_service
    assert svc._chunk_collection_name(1) == "repo_1_chunks"
    assert svc._chunk_collection_name(42) == "repo_42_chunks"


def test_fact_collection_name(qdrant_service):
    svc, _ = qdrant_service
    assert svc._fact_collection_name(1) == "repo_1_facts"
    assert svc._fact_collection_name(42) == "repo_42_facts"


def test_split_page_into_chunks_basic(qdrant_service):
    svc, _ = qdrant_service
    content = (
        "---\ntitle: Test\ntype: concept\n---\n\n"
        "# Section A\n\nThis is section A content with enough text to form a chunk. "
        + "word " * 50
        + "\n\n## Section B\n\nThis is section B content. "
        + "word " * 50
    )
    chunks = svc.split_page_into_chunks(content)
    assert len(chunks) >= 1
    for c in chunks:
        assert "chunk_id" in c
        assert "heading" in c
        assert "chunk_text" in c
        assert "position" in c
        assert len(c["chunk_text"]) <= 800


def test_split_empty_content(qdrant_service):
    svc, _ = qdrant_service
    chunks = svc.split_page_into_chunks("")
    assert chunks == []


def test_split_no_headings(qdrant_service):
    svc, _ = qdrant_service
    content = "Some plain text.\n\nMore paragraphs here. " + "word " * 60
    chunks = svc.split_page_into_chunks(content)
    assert len(chunks) >= 1


def test_stable_chunk_point_id_deterministic(qdrant_service):
    svc, _ = qdrant_service
    id1 = svc._stable_chunk_point_id(1, "page.md", "0")
    id2 = svc._stable_chunk_point_id(1, "page.md", "0")
    id3 = svc._stable_chunk_point_id(1, "page.md", "1")
    assert id1 == id2
    assert id1 != id3
    assert isinstance(id1, int)


def test_upsert_page_chunks_calls_qdrant(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    svc.upsert_page_chunks(
        repo_id=1,
        filename="test.md",
        title="Test",
        page_type="concept",
        content="## Section\n\n" + "word " * 80,
    )
    assert mock_client.upsert.called


def test_upsert_page_chunks_empty_content(qdrant_service):
    svc, mock_client = qdrant_service
    svc.upsert_page_chunks(
        repo_id=1, filename="empty.md", title="Empty", page_type="concept", content=""
    )
    assert not mock_client.upsert.called


def test_search_chunks_returns_empty_when_no_collection(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = False
    result = svc.search_chunks(repo_id=1, query="test")
    assert result == []


def test_search_chunks_returns_structured_results(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    mock_hit = MagicMock()
    mock_hit.score = 0.92
    mock_hit.payload = {
        "chunk_id": "test.md#0",
        "filename": "test.md",
        "page_title": "Test",
        "page_type": "concept",
        "heading": "Section A",
        "chunk_text": "Some text here",
        "position": 0,
    }
    mock_client.query_points.return_value = MagicMock(points=[mock_hit])
    results = svc.search_chunks(repo_id=1, query="test query")
    assert len(results) == 1
    assert results[0]["chunk_id"] == "test.md#0"
    assert results[0]["score"] == 0.92
    assert results[0]["heading"] == "Section A"


def test_upsert_page_chunks_uses_batch_embed_and_injects_heading(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    with patch.object(svc, "_embed_batch") as mock_batch:
        mock_batch.return_value = [[0.1] * 128, [0.2] * 128]
        svc.upsert_page_chunks(
            repo_id=7,
            filename="x.md",
            title="My Page",
            page_type="concept",
            content="# Heading A\n\n" + "alpha " * 120 + "\n\n## Heading B\n\n" + "beta " * 120,
        )
    assert mock_batch.called
    texts = mock_batch.call_args_list[0][0][0]
    assert any("My Page" in t for t in texts), f"page_title not injected into embed text: {texts}"
    assert any("Heading A" in t or "Heading B" in t for t in texts)


def test_search_chunks_applies_score_threshold(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    low = MagicMock(score=0.10, payload={"chunk_id": "a#0", "filename": "a.md", "chunk_text": "t"})
    high = MagicMock(score=0.80, payload={"chunk_id": "b#0", "filename": "b.md", "chunk_text": "t"})
    mock_client.query_points.return_value = MagicMock(points=[low, high])
    out = svc.search_chunks(repo_id=1, query="q", limit=5, score_threshold=0.5)
    assert [h["chunk_id"] for h in out] == ["b#0"]


def test_search_chunks_applies_max_per_file(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    hits = [
        MagicMock(score=0.9, payload={"chunk_id": "a#0", "filename": "a.md", "chunk_text": "1"}),
        MagicMock(score=0.85, payload={"chunk_id": "a#1", "filename": "a.md", "chunk_text": "2"}),
        MagicMock(score=0.80, payload={"chunk_id": "a#2", "filename": "a.md", "chunk_text": "3"}),
        MagicMock(score=0.70, payload={"chunk_id": "b#0", "filename": "b.md", "chunk_text": "4"}),
    ]
    mock_client.query_points.return_value = MagicMock(points=hits)
    out = svc.search_chunks(repo_id=1, query="q", limit=5, max_per_file=2)
    assert [h["chunk_id"] for h in out] == ["a#0", "a#1", "b#0"]


def test_scroll_all_chunks_iterates_pages(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    point = MagicMock()
    point.payload = {"chunk_id": "a#0", "filename": "a.md", "chunk_text": "hi", "position": 0}
    mock_client.scroll.side_effect = [([point], None)]
    out = svc.scroll_all_chunks(repo_id=42)
    assert out == [{
        "chunk_id": "a#0",
        "filename": "a.md",
        "page_title": "",
        "page_type": "",
        "heading": "",
        "chunk_text": "hi",
        "position": 0,
    }]


def test_scroll_all_facts_iterates_pages(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    point = MagicMock()
    point.payload = {
        "record_id": "rec1",
        "source_file": "s.csv",
        "source_markdown_filename": "s.csv",
        "sheet": "S1",
        "row_index": 2,
        "fields": {"地区": "华东"},
        "fact_text": "note",
    }
    mock_client.scroll.side_effect = [([point], None)]
    out = svc.scroll_all_facts(repo_id=7)
    assert out == [{
        "record_id": "rec1",
        "source_file": "s.csv",
        "source_markdown_filename": "s.csv",
        "sheet": "S1",
        "row_index": 2,
        "fields": {"地区": "华东"},
        "fact_text": "note",
    }]


def test_fact_embed_text_contains_sheet_and_row(qdrant_service):
    svc, _ = qdrant_service
    text = svc._normalize_fact_embed_text(
        {"source_file": "s.csv", "sheet": "Q1", "row_index": 5},
        "地区=华东; 收入=1200",
    )
    assert text.startswith("[s.csv | 表=Q1 | 行=5]\n")
    assert "地区=华东" in text


def test_split_page_into_chunks_respects_overlap(qdrant_service):
    svc, _ = qdrant_service
    svc._chunk_min = 200
    svc._chunk_max = 400
    svc._chunk_overlap = 60
    body = "句子。" * 400
    chunks = svc.split_page_into_chunks(f"# Big\n\n{body}")
    assert len(chunks) >= 2
    prev = chunks[0]["chunk_text"]
    nxt = chunks[1]["chunk_text"]
    # 相邻 chunk 必须有尾-首重叠，至少几个字符
    assert any(prev[-i:] == nxt[:i] for i in range(8, 40))


def test_delete_page_chunks_no_collection(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = False
    svc.delete_page_chunks(repo_id=1, filename="page.md")
    assert not mock_client.delete.called


def test_delete_page_chunks_with_collection(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    svc.delete_page_chunks(repo_id=1, filename="page.md")
    assert mock_client.delete.called


def test_upsert_fact_records_calls_qdrant(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True

    svc.upsert_fact_records(
        repo_id=1,
        source_filename="sales.md",
        records=[
            {
                "record_id": "csv:2",
                "source_file": "sales.csv",
                "source_markdown_filename": "sales.md",
                "sheet": "CSV",
                "row_index": 2,
                "fields": {"地区": "华东", "收入": 1200},
                "fact_text": "来源=sales.csv; 表=CSV; 行=2; 地区=华东; 收入=1200",
            }
        ],
    )

    assert mock_client.upsert.called


def test_embed_chunks_batched_runs_multiple_batches(qdrant_service):
    """chunks 超过 batch size 时，多批次都会被 embed 且结果齐全。"""
    import threading
    import time

    svc, _ = qdrant_service
    svc.EMBEDDING_BATCH_SIZE = 2
    chunks = [
        {"chunk_id": str(i), "heading": f"H{i}", "chunk_text": f"text {i}", "position": i}
        for i in range(5)
    ]

    active = {"n": 0, "peak": 0}
    lock = threading.Lock()

    def fake_embed_batch(texts, *, client=None):
        with lock:
            active["n"] += 1
            if active["n"] > active["peak"]:
                active["peak"] = active["n"]
        time.sleep(0.05)
        with lock:
            active["n"] -= 1
        return [[0.1] * 128 for _ in texts]

    with patch.object(svc, "_embed_batch", side_effect=fake_embed_batch):
        out = svc._embed_chunks_batched(chunks, page_title="T")

    assert len(out) == 5
    returned_ids = {c["chunk_id"] for c, _ in out}
    assert returned_ids == {"0", "1", "2", "3", "4"}
    assert active["peak"] >= 2, "chunk batches should have run concurrently"


def test_search_facts_returns_structured_results(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    mock_hit = MagicMock()
    mock_hit.score = 0.96
    mock_hit.payload = {
        "record_id": "csv:2",
        "source_file": "sales.csv",
        "source_markdown_filename": "sales.md",
        "sheet": "CSV",
        "row_index": 2,
        "fields": {"地区": "华东", "收入": 1200},
        "fact_text": "来源=sales.csv; 表=CSV; 行=2; 地区=华东; 收入=1200",
    }
    mock_client.query_points.return_value = MagicMock(points=[mock_hit])

    results = svc.search_facts(repo_id=1, query="华东收入是多少")

    assert len(results) == 1
    assert results[0]["record_id"] == "csv:2"
    assert results[0]["sheet"] == "CSV"
    assert results[0]["fields"]["收入"] == 1200
    assert results[0]["score"] == 0.96
