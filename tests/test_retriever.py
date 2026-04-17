"""Tests for llmwiki_core.retrieval.HybridRetriever."""

from __future__ import annotations

from typing import Any

from llmwiki_core.keyword_index import KeywordIndex
from llmwiki_core.retrieval import HybridRetriever, RetrievalConfig


class _FakeQdrant:
    """Minimal QdrantService-like stub for retriever tests."""

    def __init__(
        self,
        dense: list[dict[str, Any]] | None = None,
        facts: list[dict[str, Any]] | None = None,
        chunks_corpus: list[dict[str, Any]] | None = None,
    ) -> None:
        self._dense = dense or []
        self._facts = facts or []
        self._corpus = chunks_corpus or []
        self.dense_calls: list[dict[str, Any]] = []
        self.fact_calls: list[dict[str, Any]] = []

    def search_chunks(self, repo_id, query, limit=8, **kwargs):
        self.dense_calls.append({"query": query, "limit": limit, **kwargs})
        return list(self._dense)

    def search_facts(self, repo_id, query, limit=8, **kwargs):
        self.fact_calls.append({"query": query, "limit": limit, **kwargs})
        return list(self._facts)

    def scroll_all_chunks(self, repo_id):
        return list(self._corpus)


def _chunk(chunk_id: str, filename: str, position: int, text: str, score: float = 0.0):
    return {
        "chunk_id": chunk_id,
        "filename": filename,
        "page_title": filename.replace(".md", ""),
        "page_type": "concept",
        "heading": "",
        "chunk_text": text,
        "position": position,
        "score": score,
    }


def test_retriever_returns_dense_results_when_bm25_disabled():
    dense = [
        _chunk("a.md#0", "a.md", 0, "FlashAttention 减少显存访问", score=0.9),
        _chunk("b.md#0", "b.md", 0, "Transformer 多头注意力", score=0.8),
    ]
    retriever = HybridRetriever(
        qdrant=_FakeQdrant(dense=dense),
        config=RetrievalConfig(enable_bm25=False, chunk_top_k=2, max_chunks_per_file=10),
    )
    out = retriever.retrieve_chunks(repo_id=1, query="flash attention 显存")
    assert [h["chunk_id"] for h in out] == ["a.md#0", "b.md#0"]
    assert out[0]["score"] == 0.9
    assert out[0]["sources"] == ["dense"]


def test_retriever_applies_per_file_cap():
    dense = [
        _chunk("a.md#0", "a.md", 0, "x", score=0.95),
        _chunk("a.md#1", "a.md", 1, "y", score=0.90),
        _chunk("a.md#2", "a.md", 2, "z", score=0.85),
        _chunk("b.md#0", "b.md", 0, "other", score=0.80),
    ]
    retriever = HybridRetriever(
        qdrant=_FakeQdrant(dense=dense),
        config=RetrievalConfig(enable_bm25=False, chunk_top_k=5, max_chunks_per_file=2),
    )
    out = retriever.retrieve_chunks(repo_id=1, query="q")
    files = [h["filename"] for h in out]
    assert files.count("a.md") == 2
    assert "b.md" in files


def test_retriever_rrf_fuses_dense_and_bm25():
    # dense 排名：a > b; BM25 排名：b > a → 融合后 b 胜出（两个来源都有）
    dense = [
        _chunk("a.md#0", "a.md", 0, "dense-first", score=0.9),
        _chunk("b.md#0", "b.md", 0, "second", score=0.7),
    ]
    corpus = [
        {"chunk_id": "b.md#0", "filename": "b.md", "page_title": "B", "page_type": "concept",
         "heading": "", "chunk_text": "FlashAttention 2022 Dao", "position": 0},
        {"chunk_id": "a.md#0", "filename": "a.md", "page_title": "A", "page_type": "concept",
         "heading": "", "chunk_text": "无关内容", "position": 0},
    ]
    kw = KeywordIndex()
    retriever = HybridRetriever(
        qdrant=_FakeQdrant(dense=dense, chunks_corpus=corpus),
        config=RetrievalConfig(enable_bm25=True, chunk_top_k=2, max_chunks_per_file=10, bm25_top_k=5),
        keyword_index=kw,
    )
    out = retriever.retrieve_chunks(repo_id=1, query="FlashAttention 2022")
    b = next(h for h in out if h["chunk_id"] == "b.md#0")
    assert set(b["sources"]) == {"dense", "bm25"}
    assert out[0]["chunk_id"] == "b.md#0"


def test_retriever_falls_back_to_dense_when_bm25_crashes():
    class _BadQdrant(_FakeQdrant):
        def scroll_all_chunks(self, repo_id):
            raise RuntimeError("nope")

    dense = [_chunk("a.md#0", "a.md", 0, "t", score=0.5)]
    retriever = HybridRetriever(
        qdrant=_BadQdrant(dense=dense),
        config=RetrievalConfig(enable_bm25=True, chunk_top_k=3, max_chunks_per_file=10),
    )
    out = retriever.retrieve_chunks(repo_id=1, query="anything")
    assert [h["chunk_id"] for h in out] == ["a.md#0"]


def test_retriever_fact_path_passes_threshold_kwarg():
    fq = _FakeQdrant(facts=[{"record_id": "r1", "score": 0.91}])
    retriever = HybridRetriever(
        qdrant=fq,
        config=RetrievalConfig(fact_top_k=5, fact_score_threshold=0.42),
    )
    out = retriever.retrieve_facts(repo_id=1, query="q")
    assert out and out[0]["record_id"] == "r1"
    call = fq.fact_calls[0]
    assert call["limit"] == 5
    assert call["score_threshold"] == 0.42


def test_retriever_config_from_flask_like_config():
    class _Cfg:
        RAG_CHUNK_TOP_K = 7
        RAG_FACT_TOP_K = 4
        RAG_CHUNK_SCORE_THRESHOLD = 0.2
        RAG_FACT_SCORE_THRESHOLD = 0.3
        RAG_MAX_CHUNKS_PER_FILE = 3
        RAG_RRF_K = 90
        RAG_ENABLE_BM25 = False

    cfg = RetrievalConfig.from_config(_Cfg)
    assert cfg.chunk_top_k == 7
    assert cfg.fact_top_k == 4
    assert cfg.chunk_score_threshold == 0.2
    assert cfg.max_chunks_per_file == 3
    assert cfg.rrf_k == 90
    assert cfg.enable_bm25 is False
