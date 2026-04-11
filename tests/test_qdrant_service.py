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
    mock_client.search.return_value = [mock_hit]
    results = svc.search_chunks(repo_id=1, query="test query")
    assert len(results) == 1
    assert results[0]["chunk_id"] == "test.md#0"
    assert results[0]["score"] == 0.92
    assert results[0]["heading"] == "Section A"


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
