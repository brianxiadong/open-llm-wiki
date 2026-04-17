"""Tests for llmwiki_core.keyword_index BM25 retrieval."""

from __future__ import annotations

from llmwiki_core.keyword_index import KeywordIndex, tokenize


def test_tokenize_english_and_chinese_mixed():
    tokens = tokenize("RAG 和 BM25 的融合策略")
    joined = " ".join(tokens)
    assert "rag" in tokens
    assert "bm25" in tokens
    assert any("融合" in t or "融合策略" in t or "融" in t for t in tokens), joined


def test_tokenize_empty():
    assert tokenize("") == []
    assert tokenize(None) == []  # type: ignore[arg-type]


def test_keyword_index_prefers_matching_docs():
    docs = [
        {"chunk_id": "a#0", "chunk_text": "FlashAttention 由 Dao 等人在 2022 年提出，减少显存访问开销。"},
        {"chunk_id": "b#0", "chunk_text": "Transformer 架构使用多头自注意力机制。"},
        {"chunk_id": "c#0", "chunk_text": "RLHF 分为 SFT、奖励模型和 PPO 三步。"},
    ]
    idx = KeywordIndex()
    hits = idx.search(
        key="test",
        signature="v1",
        query="FlashAttention 2022",
        corpus_loader=lambda: docs,
    )
    assert hits, "expected at least one BM25 hit"
    assert hits[0].doc_id == "a#0"


def test_keyword_index_cache_invalidation():
    docs_v1 = [{"chunk_id": "a#0", "chunk_text": "alpha beta"}]
    docs_v2 = [{"chunk_id": "b#0", "chunk_text": "alpha gamma"}]
    idx = KeywordIndex()
    hits_v1 = idx.search(key="repo:1", signature="v1", query="alpha", corpus_loader=lambda: docs_v1)
    hits_v2 = idx.search(key="repo:1", signature="v2", query="alpha", corpus_loader=lambda: docs_v2)
    assert hits_v1[0].doc_id == "a#0"
    assert hits_v2[0].doc_id == "b#0"


def test_keyword_index_returns_empty_on_unknown_terms():
    docs = [{"chunk_id": "x#0", "chunk_text": "完全无关的内容"}]
    idx = KeywordIndex()
    hits = idx.search(
        key="t",
        signature="1",
        query="zzznonsense",
        corpus_loader=lambda: docs,
    )
    assert hits == []
