"""Hybrid retriever shared by server and confidential client.

职责：
- 统一包装 ``QdrantService``（dense）与 ``KeywordIndex``（BM25）两路召回，对外提供
  ``retrieve_chunks`` / ``retrieve_facts`` 两个语义清晰的方法。
- Reciprocal Rank Fusion (RRF) 做排名融合——dense 和 BM25 的原始分数量纲不同，
  融合分数=多路排名的加权倒数和，能避免量纲引入的偏差。
- 支持 per-file 限流、score 阈值、neighbor chunk 扩展，让 Prompt 里的 context
  更聚焦、信息更密。
- 可选 HyDE：调用方传入 ``hyde_text`` 时，直接用它做 dense 检索的查询文本（但
  BM25 仍用原始问题，避免假想答案污染关键字排序）。

该模块只依赖 ``QdrantService`` 的只读方法和 ``KeywordIndex``，不依赖 Flask/LLM，
可被任何运行时（服务端 HTTP、桌面 GUI）直接复用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from .keyword_index import KeywordIndex, global_keyword_index

logger = logging.getLogger(__name__)


class _QdrantLike(Protocol):
    """QdrantService 的最小接口子集，避免 llmwiki_core 反向依赖 qdrant_service.py。"""

    def search_chunks(
        self,
        repo_id: int,
        query: str,
        limit: int = ...,
        *,
        score_threshold: float | None = ...,
        max_per_file: int | None = ...,
        oversample: int = ...,
    ) -> list[dict[str, Any]]: ...

    def search_facts(
        self,
        repo_id: int,
        query: str,
        limit: int = ...,
        *,
        score_threshold: float | None = ...,
        oversample: int = ...,
    ) -> list[dict[str, Any]]: ...

    def scroll_all_chunks(self, repo_id: int) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class RetrievalConfig:
    chunk_top_k: int = 12
    fact_top_k: int = 12
    chunk_score_threshold: float = 0.35
    fact_score_threshold: float = 0.40
    max_chunks_per_file: int = 2
    rrf_k: int = 60
    enable_bm25: bool = True
    bm25_top_k: int = 20

    @classmethod
    def from_config(cls, cfg: Any) -> "RetrievalConfig":
        """从 Flask Config / dict-like 读取参数，缺失时使用默认值。"""
        def _get(name: str, default: Any) -> Any:
            if cfg is None:
                return default
            if isinstance(cfg, dict):
                return cfg.get(name, default)
            return getattr(cfg, name, default)

        return cls(
            chunk_top_k=int(_get("RAG_CHUNK_TOP_K", cls.chunk_top_k)),
            fact_top_k=int(_get("RAG_FACT_TOP_K", cls.fact_top_k)),
            chunk_score_threshold=float(_get("RAG_CHUNK_SCORE_THRESHOLD", cls.chunk_score_threshold)),
            fact_score_threshold=float(_get("RAG_FACT_SCORE_THRESHOLD", cls.fact_score_threshold)),
            max_chunks_per_file=int(_get("RAG_MAX_CHUNKS_PER_FILE", cls.max_chunks_per_file)),
            rrf_k=int(_get("RAG_RRF_K", cls.rrf_k)),
            enable_bm25=bool(_get("RAG_ENABLE_BM25", cls.enable_bm25)),
            bm25_top_k=int(_get("RAG_BM25_TOP_K", cls.bm25_top_k)),
        )


@dataclass
class ChunkHit:
    chunk_id: str
    filename: str
    page_title: str = ""
    page_type: str = ""
    heading: str = ""
    chunk_text: str = ""
    position: int = 0
    dense_score: float = 0.0
    bm25_score: float = 0.0
    fused_score: float = 0.0
    sources: list[str] = field(default_factory=list)  # e.g. ["dense", "bm25"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "filename": self.filename,
            "page_title": self.page_title,
            "page_type": self.page_type,
            "heading": self.heading,
            "chunk_text": self.chunk_text,
            "position": self.position,
            "score": self.dense_score,  # 兼容历史字段：confidence 规则仍按 cosine 量纲
            "dense_score": self.dense_score,
            "bm25_score": self.bm25_score,
            "fused_score": self.fused_score,
            "sources": list(self.sources),
        }


class HybridRetriever:
    def __init__(
        self,
        qdrant: _QdrantLike,
        *,
        config: RetrievalConfig | None = None,
        keyword_index: KeywordIndex | None = None,
        repo_key_prefix: str = "repo",
    ) -> None:
        self._qdrant = qdrant
        self._config = config or RetrievalConfig()
        self._kw = keyword_index or global_keyword_index()
        self._repo_key_prefix = repo_key_prefix

    @property
    def config(self) -> RetrievalConfig:
        return self._config

    # ── Chunk 检索 ────────────────────────────────────────────────────

    def retrieve_chunks(
        self,
        repo_id: int,
        query: str,
        *,
        top_k: int | None = None,
        score_threshold: float | None = None,
        max_per_file: int | None = None,
        dense_query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid chunk retrieval (dense + BM25 RRF)。

        ``dense_query`` 用于 HyDE：向量检索用生成的假想答案、BM25 仍用 ``query``。
        """
        if not query:
            return []
        cfg = self._config
        k = int(top_k if top_k is not None else cfg.chunk_top_k)
        thr = cfg.chunk_score_threshold if score_threshold is None else score_threshold
        per_file = cfg.max_chunks_per_file if max_per_file is None else max_per_file

        dense_q = dense_query or query
        dense_hits: list[dict[str, Any]] = []
        try:
            dense_hits = self._qdrant.search_chunks(
                repo_id=repo_id,
                query=dense_q,
                limit=max(k * 2, cfg.bm25_top_k),
                score_threshold=thr,
                max_per_file=None,  # 融合之前先不限流，避免 RRF 排序被破坏
                oversample=3,
            ) or []
        except Exception as exc:
            logger.warning("dense chunk search failed repo_id=%s: %s", repo_id, exc)

        bm25_hits: list[dict[str, Any]] = []
        if cfg.enable_bm25:
            try:
                raw = self._kw.search(
                    key=f"{self._repo_key_prefix}:{repo_id}:chunks",
                    signature=self._chunks_signature(repo_id),
                    query=query,
                    corpus_loader=lambda: self._qdrant.scroll_all_chunks(repo_id),
                    text_key="chunk_text",
                    id_key="chunk_id",
                    limit=cfg.bm25_top_k,
                )
                for h in raw:
                    doc = h.payload
                    bm25_hits.append({
                        "chunk_id": doc.get("chunk_id", h.doc_id),
                        "filename": doc.get("filename", ""),
                        "page_title": doc.get("page_title", ""),
                        "page_type": doc.get("page_type", ""),
                        "heading": doc.get("heading", ""),
                        "chunk_text": doc.get("chunk_text", ""),
                        "position": doc.get("position", 0),
                        "score": 0.0,
                        "_bm25": h.score,
                    })
            except Exception as exc:
                logger.warning("bm25 chunk search failed repo_id=%s: %s", repo_id, exc)

        fused = self._fuse_chunks(dense_hits, bm25_hits, rrf_k=cfg.rrf_k)
        return self._apply_per_file_cap(fused, per_file=per_file, limit=k)

    # ── Fact 检索 ─────────────────────────────────────────────────────

    def retrieve_facts(
        self,
        repo_id: int,
        query: str,
        *,
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        if not query:
            return []
        cfg = self._config
        k = int(top_k if top_k is not None else cfg.fact_top_k)
        thr = cfg.fact_score_threshold if score_threshold is None else score_threshold
        try:
            return self._qdrant.search_facts(
                repo_id=repo_id,
                query=query,
                limit=k,
                score_threshold=thr,
                oversample=2,
            ) or []
        except Exception as exc:
            logger.warning("fact search failed repo_id=%s: %s", repo_id, exc)
            return []

    # ── 内部工具 ──────────────────────────────────────────────────────

    def _chunks_signature(self, repo_id: int) -> str:
        """签名变了就重建 BM25 缓存。简单起见：corpus 大小 + 最大 position。"""
        try:
            docs = self._qdrant.scroll_all_chunks(repo_id)
        except Exception:
            return "empty"
        if not docs:
            return "empty"
        max_pos = max(int(d.get("position", 0) or 0) for d in docs)
        return f"n={len(docs)};p={max_pos}"

    @staticmethod
    def _fuse_chunks(
        dense_hits: list[dict[str, Any]],
        bm25_hits: list[dict[str, Any]],
        *,
        rrf_k: int,
    ) -> list[ChunkHit]:
        """Reciprocal Rank Fusion: score = Σ 1/(rrf_k + rank_i)。"""
        merged: dict[str, ChunkHit] = {}

        def _key(h: dict[str, Any]) -> str:
            cid = str(h.get("chunk_id") or "")
            if cid:
                return cid
            return f"{h.get('filename', '')}#{h.get('position', 0)}"

        for rank, h in enumerate(dense_hits):
            key = _key(h)
            hit = merged.get(key)
            if hit is None:
                hit = ChunkHit(
                    chunk_id=str(h.get("chunk_id") or key),
                    filename=str(h.get("filename") or ""),
                    page_title=str(h.get("page_title") or ""),
                    page_type=str(h.get("page_type") or ""),
                    heading=str(h.get("heading") or ""),
                    chunk_text=str(h.get("chunk_text") or ""),
                    position=int(h.get("position") or 0),
                )
                merged[key] = hit
            hit.dense_score = max(hit.dense_score, float(h.get("score") or 0.0))
            hit.fused_score += 1.0 / (rrf_k + rank + 1)
            if "dense" not in hit.sources:
                hit.sources.append("dense")

        for rank, h in enumerate(bm25_hits):
            key = _key(h)
            hit = merged.get(key)
            if hit is None:
                hit = ChunkHit(
                    chunk_id=str(h.get("chunk_id") or key),
                    filename=str(h.get("filename") or ""),
                    page_title=str(h.get("page_title") or ""),
                    page_type=str(h.get("page_type") or ""),
                    heading=str(h.get("heading") or ""),
                    chunk_text=str(h.get("chunk_text") or ""),
                    position=int(h.get("position") or 0),
                )
                merged[key] = hit
            hit.bm25_score = max(hit.bm25_score, float(h.get("_bm25") or 0.0))
            hit.fused_score += 1.0 / (rrf_k + rank + 1)
            if "bm25" not in hit.sources:
                hit.sources.append("bm25")

        return sorted(merged.values(), key=lambda x: x.fused_score, reverse=True)

    @staticmethod
    def _apply_per_file_cap(
        hits: list[ChunkHit],
        *,
        per_file: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        per_file_count: dict[str, int] = {}
        for hit in hits:
            if per_file is not None and hit.filename:
                if per_file_count.get(hit.filename, 0) >= per_file:
                    continue
                per_file_count[hit.filename] = per_file_count.get(hit.filename, 0) + 1
            out.append(hit.to_dict())
            if len(out) >= limit:
                break
        return out


__all__ = ["ChunkHit", "HybridRetriever", "RetrievalConfig"]
