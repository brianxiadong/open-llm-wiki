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
        facts_corpus: list[dict[str, Any]] | None = None,
    ) -> None:
        self._dense = dense or []
        self._facts = facts or []
        self._corpus = chunks_corpus or []
        self._facts_corpus = list(facts_corpus) if facts_corpus is not None else []
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

    def scroll_all_facts(self, repo_id):
        return list(self._facts_corpus)


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


def test_retriever_bm25_signature_changes_when_chunk_text_changes():
    corpus = [_chunk("a.md#0", "a.md", 0, "oldtoken only")]
    fq = _FakeQdrant(chunks_corpus=corpus)
    retriever = HybridRetriever(
        qdrant=fq,
        config=RetrievalConfig(
            enable_bm25=True,
            chunk_top_k=3,
            max_chunks_per_file=10,
            bm25_top_k=5,
            context_expand_neighbors=0,
        ),
        keyword_index=KeywordIndex(),
    )

    assert retriever.retrieve_chunks(repo_id=1, query="newtoken") == []

    fq._corpus = [_chunk("a.md#0", "a.md", 0, "newtoken now present")]
    out = retriever.retrieve_chunks(repo_id=1, query="newtoken")

    assert [h["chunk_id"] for h in out] == ["a.md#0"]
    assert out[0]["sources"] == ["bm25"]


def test_retriever_expands_neighbor_chunks_from_corpus():
    dense = [_chunk("a.md#1", "a.md", 1, "main hit", score=0.9)]
    corpus = [
        _chunk("a.md#0", "a.md", 0, "before"),
        _chunk("a.md#1", "a.md", 1, "main hit"),
        _chunk("a.md#2", "a.md", 2, "after"),
        _chunk("a.md#3", "a.md", 3, "too far"),
    ]
    retriever = HybridRetriever(
        qdrant=_FakeQdrant(dense=dense, chunks_corpus=corpus),
        config=RetrievalConfig(
            enable_bm25=False,
            chunk_top_k=1,
            max_chunks_per_file=10,
            context_expand_neighbors=1,
        ),
    )

    out = retriever.retrieve_chunks(repo_id=1, query="main")

    assert [h["chunk_id"] for h in out] == ["a.md#1", "a.md#0", "a.md#2"]
    assert out[1]["sources"] == ["neighbor"]
    assert out[1]["neighbor_of"] == "a.md#1"


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


def _fact_row(
    record_id: str,
    *,
    fields: dict[str, str],
    fact_text: str = "",
    score: float = 0.0,
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "source_file": "sales.csv",
        "source_markdown_filename": "sales.csv",
        "sheet": "Q1",
        "row_index": 3,
        "fields": fields,
        "fact_text": fact_text,
        "score": score,
        "dense_score": score,
    }


def test_retriever_fact_keyword_exact_can_outrank_dense_only():
    """Dense 命中无关行时，关键词+字段精确应把正确行提前。"""
    dense_wrong = _fact_row("r_wrong", fields={}, fact_text="无关合计", score=0.92)
    corpus = [
        {
            "record_id": "r_wrong",
            "source_file": "sales.csv",
            "source_markdown_filename": "sales.csv",
            "sheet": "Q1",
            "row_index": 1,
            "fields": {},
            "fact_text": "无关合计",
        },
        {
            "record_id": "r_right",
            "source_file": "sales.csv",
            "source_markdown_filename": "sales.csv",
            "sheet": "Q1",
            "row_index": 3,
            "fields": {"地区": "华东", "收入": "1200"},
            "fact_text": "",
        },
    ]
    fq = _FakeQdrant(facts=[dense_wrong], facts_corpus=corpus)
    retriever = HybridRetriever(
        qdrant=fq,
        config=RetrievalConfig(
            fact_top_k=5,
            fact_score_threshold=0.0,
            enable_fact_keyword=True,
            fact_keyword_top_k=20,
            rrf_k=60,
        ),
        keyword_index=KeywordIndex(),
    )
    out = retriever.retrieve_facts(repo_id=1, query="华东 收入 1200")
    assert out[0]["record_id"] == "r_right"
    assert "exact" in (out[0].get("sources") or [])
    assert out[0].get("exact_score", 0) > 0


def test_retriever_fact_skips_keyword_when_over_max_records():
    many = [
        {
            "record_id": f"r{i}",
            "source_file": "x.csv",
            "source_markdown_filename": "x.csv",
            "sheet": "S",
            "row_index": i,
            "fields": {"k": str(i)},
            "fact_text": "",
        }
        for i in range(5)
    ]
    fq = _FakeQdrant(
        facts=[_fact_row("r0", fields={"k": "0"}, score=0.9)],
        facts_corpus=many,
    )
    retriever = HybridRetriever(
        qdrant=fq,
        config=RetrievalConfig(
            fact_top_k=3,
            fact_score_threshold=0.0,
            enable_fact_keyword=True,
            fact_keyword_max_records=3,
        ),
    )
    out = retriever.retrieve_facts(repo_id=1, query="华东")
    assert len(out) == 1
    assert out[0]["record_id"] == "r0"
    # 超限时走纯 dense 短路返回，不经过 _merge_fact_channels，可能无 sources 字段
    assert "keyword" not in (out[0].get("sources") or [])
    assert "exact" not in (out[0].get("sources") or [])


def test_retriever_config_from_flask_like_config():
    class _Cfg:
        RAG_CHUNK_TOP_K = 7
        RAG_FACT_TOP_K = 4
        RAG_CHUNK_SCORE_THRESHOLD = 0.2
        RAG_FACT_SCORE_THRESHOLD = 0.3
        RAG_MAX_CHUNKS_PER_FILE = 3
        RAG_RRF_K = 90
        RAG_ENABLE_BM25 = False
        RAG_ENABLE_FACT_KEYWORD = False
        RAG_FACT_KEYWORD_TOP_K = 50
        RAG_FACT_KEYWORD_MAX_RECORDS = 10000
        RAG_FACT_SEARCH_TEXT_CHARS = 1500
        RAG_CONTEXT_EXPAND_NEIGHBORS = 2

    cfg = RetrievalConfig.from_config(_Cfg)
    assert cfg.chunk_top_k == 7
    assert cfg.fact_top_k == 4
    assert cfg.chunk_score_threshold == 0.2
    assert cfg.max_chunks_per_file == 3
    assert cfg.rrf_k == 90
    assert cfg.enable_bm25 is False
    assert cfg.enable_fact_keyword is False
    assert cfg.fact_keyword_top_k == 50
    assert cfg.fact_keyword_max_records == 10000
    assert cfg.fact_search_text_chars == 1500
    assert cfg.context_expand_neighbors == 2
