"""Hybrid retriever shared by server and confidential client.

职责：
- 统一包装 ``QdrantService``（dense）与 ``KeywordIndex``（BM25）召回；chunk 为 dense+BM25+RRF；
  fact 为 dense + ``fact_search_text`` 关键词（BM25）+ 轻量字段精确加分 + RRF 融合。
  对外提供 ``retrieve_chunks`` / ``retrieve_facts``。
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

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .keyword_index import KeywordIndex, global_keyword_index, tokenize

logger = logging.getLogger(__name__)

_SPACE_RE = re.compile(r"\s+")


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

    def scroll_all_facts(self, repo_id: int) -> list[dict[str, Any]]: ...


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
    context_expand_neighbors: int = 0
    enable_fact_keyword: bool = True
    fact_keyword_top_k: int = 100
    fact_keyword_max_records: int = 50000
    fact_search_text_chars: int = 2000

    @classmethod
    def from_config(cls, cfg: Any) -> "RetrievalConfig":
        """从 Flask Config / dict-like 读取参数，缺失时使用默认值。"""
        def _get(name: str, default: Any) -> Any:
            if cfg is None:
                return default
            if isinstance(cfg, dict):
                return cfg.get(name, default)
            return getattr(cfg, name, default)

        def _bool(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            chunk_top_k=int(_get("RAG_CHUNK_TOP_K", cls.chunk_top_k)),
            fact_top_k=int(_get("RAG_FACT_TOP_K", cls.fact_top_k)),
            chunk_score_threshold=float(_get("RAG_CHUNK_SCORE_THRESHOLD", cls.chunk_score_threshold)),
            fact_score_threshold=float(_get("RAG_FACT_SCORE_THRESHOLD", cls.fact_score_threshold)),
            max_chunks_per_file=int(_get("RAG_MAX_CHUNKS_PER_FILE", cls.max_chunks_per_file)),
            rrf_k=int(_get("RAG_RRF_K", cls.rrf_k)),
            enable_bm25=_bool(_get("RAG_ENABLE_BM25", cls.enable_bm25)),
            bm25_top_k=int(_get("RAG_BM25_TOP_K", cls.bm25_top_k)),
            context_expand_neighbors=int(
                _get("RAG_CONTEXT_EXPAND_NEIGHBORS", cls.context_expand_neighbors)
            ),
            enable_fact_keyword=_bool(
                _get("RAG_ENABLE_FACT_KEYWORD", cls.enable_fact_keyword)
            ),
            fact_keyword_top_k=int(
                _get("RAG_FACT_KEYWORD_TOP_K", cls.fact_keyword_top_k)
            ),
            fact_keyword_max_records=int(
                _get("RAG_FACT_KEYWORD_MAX_RECORDS", cls.fact_keyword_max_records)
            ),
            fact_search_text_chars=int(
                _get("RAG_FACT_SEARCH_TEXT_CHARS", cls.fact_search_text_chars)
            ),
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
        expand_neighbors: int | None = None,
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
        neighbor_n = (
            cfg.context_expand_neighbors
            if expand_neighbors is None
            else int(expand_neighbors)
        )
        corpus_docs: list[dict[str, Any]] | None = None

        def _corpus_loader() -> list[dict[str, Any]]:
            nonlocal corpus_docs
            if corpus_docs is None:
                corpus_docs = self._qdrant.scroll_all_chunks(repo_id)
            return list(corpus_docs)

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
                    signature=self._chunks_signature_from_docs(_corpus_loader()),
                    query=query,
                    corpus_loader=_corpus_loader,
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
        primary = self._apply_per_file_cap(fused, per_file=per_file, limit=k)
        if neighbor_n <= 0 or not primary:
            return primary
        try:
            return self._expand_neighbor_chunks(
                primary,
                _corpus_loader(),
                expand_neighbors=neighbor_n,
            )
        except Exception as exc:
            logger.warning("neighbor chunk expansion failed repo_id=%s: %s", repo_id, exc)
            return primary

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
        dense_hits: list[dict[str, Any]] = []
        try:
            dense_hits = self._qdrant.search_facts(
                repo_id=repo_id,
                query=query,
                limit=k,
                score_threshold=thr,
                oversample=2,
            ) or []
        except Exception as exc:
            logger.warning("fact search failed repo_id=%s: %s", repo_id, exc)

        keyword_hits: list[dict[str, Any]] = []
        exact_hits: list[dict[str, Any]] = []
        if cfg.enable_fact_keyword:
            try:
                docs = self._fact_keyword_docs(repo_id)
                if docs:
                    raw = self._kw.search(
                        key=f"{self._repo_key_prefix}:{repo_id}:facts",
                        signature=self._facts_signature_from_docs(docs),
                        query=query,
                        corpus_loader=lambda: list(docs),
                        text_key="fact_search_text",
                        id_key="record_id",
                        limit=max(k, cfg.fact_keyword_top_k),
                    )
                    for h in raw:
                        hit = self._fact_doc_to_hit(h.payload)
                        hit["bm25_score"] = h.score
                        keyword_hits.append(hit)
                    exact_hits = self._exact_fact_hits(
                        docs,
                        query=query,
                        limit=max(k, cfg.fact_keyword_top_k),
                    )
            except Exception as exc:
                logger.warning("fact keyword search failed repo_id=%s: %s", repo_id, exc)

        if not keyword_hits and not exact_hits:
            return dense_hits[:k]
        return self._merge_fact_channels(
            dense_hits=dense_hits,
            keyword_hits=keyword_hits,
            exact_hits=exact_hits,
            limit=k,
            rrf_k=cfg.rrf_k,
        )

    # ── 内部工具 ──────────────────────────────────────────────────────

    def _fact_keyword_docs(self, repo_id: int) -> list[dict[str, Any]]:
        docs = self._qdrant.scroll_all_facts(repo_id)
        if not docs:
            return []
        max_records = int(self._config.fact_keyword_max_records)
        if max_records > 0 and len(docs) > max_records:
            logger.warning(
                "fact keyword search skipped repo_id=%s records=%s max=%s",
                repo_id,
                len(docs),
                max_records,
            )
            return []
        return [self._prepare_fact_doc(d) for d in docs]

    def _prepare_fact_doc(self, doc: dict[str, Any]) -> dict[str, Any]:
        out = self._normalize_fact_hit(doc)
        out["fact_search_text"] = self._build_fact_search_text(
            out,
            limit=max(200, int(self._config.fact_search_text_chars)),
        )
        return out

    @staticmethod
    def _stringify_field_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    @classmethod
    def _build_fact_search_text(cls, doc: dict[str, Any], *, limit: int) -> str:
        fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
        parts: list[str] = []
        for key in ("source_file", "source_markdown_filename", "sheet"):
            value = str(doc.get(key) or "").strip()
            if value:
                parts.append(value)
        row_index = doc.get("row_index")
        if row_index not in (None, ""):
            parts.append(f"row_index={row_index}")
            parts.append(f"行={row_index}")
        for key, value in fields.items():
            key_text = str(key).strip()
            value_text = cls._stringify_field_value(value).strip()
            if key_text and value_text:
                parts.append(f"{key_text}={value_text}")
                parts.append(f"{key_text} {value_text}")
            elif key_text:
                parts.append(key_text)
            elif value_text:
                parts.append(value_text)
        fact_text = str(doc.get("fact_text") or "").strip()
        if fact_text:
            parts.append(fact_text)
        return "\n".join(parts)[:limit]

    @staticmethod
    def _normalize_exact_text(text: Any) -> str:
        return _SPACE_RE.sub("", str(text or "").strip().lower())

    @staticmethod
    def _is_meaningful_exact_term(text: str) -> bool:
        if not text:
            return False
        return len(text) >= 2 or any(ch.isdigit() for ch in text) or text.isascii()

    def _exact_fact_score(self, doc: dict[str, Any], query: str) -> float:
        q_norm = self._normalize_exact_text(query)
        if not q_norm:
            return 0.0
        q_tokens = set(tokenize(query))
        fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
        field_key_hits = 0
        value_hits = 0
        score = 0.0
        for key, value in fields.items():
            key_norm = self._normalize_exact_text(key)
            value_text = self._stringify_field_value(value)
            value_norm = self._normalize_exact_text(value_text)
            key_hit = bool(
                key_norm
                and self._is_meaningful_exact_term(key_norm)
                and (key_norm in q_norm or key_norm in q_tokens)
            )
            value_hit = bool(
                value_norm
                and self._is_meaningful_exact_term(value_norm)
                and (value_norm in q_norm or value_norm in q_tokens)
            )
            if key_hit:
                field_key_hits += 1
            if value_hit:
                value_hits += 1
                score += 1.0
            if key_hit and value_hit:
                score += 0.8
        for key in ("source_file", "source_markdown_filename", "sheet"):
            value_norm = self._normalize_exact_text(doc.get(key))
            if (
                value_norm
                and self._is_meaningful_exact_term(value_norm)
                and value_norm in q_norm
            ):
                value_hits += 1
                score += 0.3
        if value_hits <= 0:
            return 0.0
        score += min(field_key_hits, 3) * 0.2
        return score

    def _exact_fact_hits(
        self,
        docs: list[dict[str, Any]],
        *,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for doc in docs:
            score = self._exact_fact_score(doc, query)
            if score <= 0:
                continue
            hit = self._fact_doc_to_hit(doc)
            hit["exact_score"] = score
            hits.append(hit)
        hits.sort(key=lambda h: float(h.get("exact_score") or 0.0), reverse=True)
        return hits[:limit]

    @staticmethod
    def _fact_key(hit: dict[str, Any]) -> str:
        record_id = str(hit.get("record_id") or "").strip()
        if record_id:
            return record_id
        return (
            f"{hit.get('source_file', '')}|{hit.get('source_markdown_filename', '')}|"
            f"{hit.get('sheet', '')}|{hit.get('row_index', '')}"
        )

    @staticmethod
    def _normalize_fact_hit(hit: dict[str, Any]) -> dict[str, Any]:
        fields = hit.get("fields") if isinstance(hit.get("fields"), dict) else {}
        return {
            "record_id": str(hit.get("record_id") or ""),
            "source_file": str(hit.get("source_file") or ""),
            "source_markdown_filename": str(hit.get("source_markdown_filename") or ""),
            "sheet": str(hit.get("sheet") or ""),
            "row_index": hit.get("row_index", 0),
            "fields": dict(fields),
            "fact_text": str(hit.get("fact_text") or ""),
            "score": float(hit.get("score") or 0.0),
            "dense_score": float(hit.get("dense_score") or hit.get("score") or 0.0),
            "bm25_score": float(hit.get("bm25_score") or hit.get("_bm25") or 0.0),
            "exact_score": float(hit.get("exact_score") or 0.0),
            "fused_score": float(hit.get("fused_score") or 0.0),
            "sources": list(hit.get("sources") or []),
        }

    @classmethod
    def _fact_doc_to_hit(cls, doc: dict[str, Any]) -> dict[str, Any]:
        return cls._normalize_fact_hit(doc)

    @classmethod
    def _merge_fact_channels(
        cls,
        *,
        dense_hits: list[dict[str, Any]],
        keyword_hits: list[dict[str, Any]],
        exact_hits: list[dict[str, Any]],
        limit: int,
        rrf_k: int,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}

        def _ensure(raw: dict[str, Any]) -> dict[str, Any]:
            hit = cls._normalize_fact_hit(raw)
            key = cls._fact_key(hit)
            current = merged.get(key)
            if current is None:
                merged[key] = hit
                return hit
            for field_name in (
                "record_id",
                "source_file",
                "source_markdown_filename",
                "sheet",
                "fact_text",
            ):
                if not current.get(field_name) and hit.get(field_name):
                    current[field_name] = hit[field_name]
            if not current.get("fields") and hit.get("fields"):
                current["fields"] = hit["fields"]
            if current.get("row_index", 0) in (0, None, "") and hit.get("row_index"):
                current["row_index"] = hit["row_index"]
            return current

        def _add_source(hit: dict[str, Any], source: str) -> None:
            sources = hit.setdefault("sources", [])
            if source not in sources:
                sources.append(source)

        for rank, raw in enumerate(dense_hits):
            hit = _ensure(raw)
            dense_score = float(raw.get("dense_score") or raw.get("score") or 0.0)
            hit["score"] = max(float(hit.get("score") or 0.0), dense_score)
            hit["dense_score"] = max(float(hit.get("dense_score") or 0.0), dense_score)
            hit["fused_score"] = float(hit.get("fused_score") or 0.0) + 1.0 / (rrf_k + rank + 1)
            _add_source(hit, "dense")

        for rank, raw in enumerate(keyword_hits):
            hit = _ensure(raw)
            bm25_score = float(raw.get("bm25_score") or raw.get("_bm25") or 0.0)
            hit["bm25_score"] = max(float(hit.get("bm25_score") or 0.0), bm25_score)
            hit["fused_score"] = float(hit.get("fused_score") or 0.0) + 1.0 / (rrf_k + rank + 1)
            _add_source(hit, "keyword")

        for rank, raw in enumerate(exact_hits):
            hit = _ensure(raw)
            exact_score = float(raw.get("exact_score") or 0.0)
            hit["exact_score"] = max(float(hit.get("exact_score") or 0.0), exact_score)
            hit["fused_score"] = (
                float(hit.get("fused_score") or 0.0)
                + 1.0 / (rrf_k + rank + 1)
                + exact_score
            )
            _add_source(hit, "exact")

        out = sorted(
            merged.values(),
            key=lambda h: float(h.get("fused_score") or 0.0),
            reverse=True,
        )
        return out[:limit]

    def _chunks_signature(self, repo_id: int) -> str:
        """签名变了就重建 BM25 缓存。"""
        try:
            return self._chunks_signature_from_docs(self._qdrant.scroll_all_chunks(repo_id))
        except Exception:
            return "empty"

    @staticmethod
    def _chunks_signature_from_docs(docs: list[dict[str, Any]]) -> str:
        if not docs:
            return "empty"
        digest = hashlib.sha1()
        for doc in sorted(
            docs,
            key=lambda d: (
                str(d.get("filename") or ""),
                int(d.get("position") or 0),
                str(d.get("chunk_id") or ""),
            ),
        ):
            parts = (
                str(doc.get("chunk_id") or ""),
                str(doc.get("filename") or ""),
                str(doc.get("position") or 0),
                str(doc.get("page_title") or ""),
                str(doc.get("heading") or ""),
                str(doc.get("chunk_text") or ""),
            )
            digest.update("\x1f".join(parts).encode("utf-8", errors="ignore"))
            digest.update(b"\x1e")
        return f"n={len(docs)};h={digest.hexdigest()[:16]}"

    @staticmethod
    def _facts_signature_from_docs(docs: list[dict[str, Any]]) -> str:
        if not docs:
            return "empty"
        digest = hashlib.sha1()
        for doc in sorted(
            docs,
            key=lambda d: (
                str(d.get("record_id") or ""),
                str(d.get("source_file") or ""),
                int(d.get("row_index") or 0),
            ),
        ):
            fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
            fields_repr = json.dumps(fields, ensure_ascii=False, sort_keys=True)
            parts = (
                str(doc.get("record_id") or ""),
                str(doc.get("source_file") or ""),
                str(doc.get("source_markdown_filename") or ""),
                str(doc.get("sheet") or ""),
                str(doc.get("row_index") or 0),
                fields_repr,
                str(doc.get("fact_text") or ""),
            )
            digest.update("\x1f".join(parts).encode("utf-8", errors="ignore"))
            digest.update(b"\x1e")
        return f"n={len(docs)};h={digest.hexdigest()[:16]}"

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

    @staticmethod
    def _chunk_doc_to_hit(doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "chunk_id": str(doc.get("chunk_id") or ""),
            "filename": str(doc.get("filename") or ""),
            "page_title": str(doc.get("page_title") or ""),
            "page_type": str(doc.get("page_type") or ""),
            "heading": str(doc.get("heading") or ""),
            "chunk_text": str(doc.get("chunk_text") or ""),
            "position": int(doc.get("position") or 0),
            "score": 0.0,
            "dense_score": 0.0,
            "bm25_score": 0.0,
            "fused_score": 0.0,
            "sources": ["neighbor"],
        }

    def _expand_neighbor_chunks(
        self,
        primary_hits: list[dict[str, Any]],
        corpus_docs: list[dict[str, Any]],
        *,
        expand_neighbors: int,
    ) -> list[dict[str, Any]]:
        if expand_neighbors <= 0 or not corpus_docs:
            return primary_hits
        by_file_pos: dict[tuple[str, int], dict[str, Any]] = {}
        for doc in corpus_docs:
            fn = str(doc.get("filename") or "")
            if not fn:
                continue
            try:
                pos = int(doc.get("position") or 0)
            except (TypeError, ValueError):
                continue
            by_file_pos[(fn, pos)] = doc

        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _mark_seen(hit: dict[str, Any]) -> bool:
            key = str(hit.get("chunk_id") or "")
            if not key:
                key = f"{hit.get('filename', '')}#{hit.get('position', 0)}"
            if key in seen:
                return False
            seen.add(key)
            return True

        for hit in primary_hits:
            if _mark_seen(hit):
                out.append(hit)
            fn = str(hit.get("filename") or "")
            if not fn:
                continue
            try:
                pos = int(hit.get("position") or 0)
            except (TypeError, ValueError):
                continue
            for delta in range(-expand_neighbors, expand_neighbors + 1):
                if delta == 0:
                    continue
                doc = by_file_pos.get((fn, pos + delta))
                if not doc:
                    continue
                neighbor = self._chunk_doc_to_hit(doc)
                neighbor["neighbor_of"] = hit.get("chunk_id") or ""
                if _mark_seen(neighbor):
                    out.append(neighbor)
        return out


__all__ = ["ChunkHit", "HybridRetriever", "RetrievalConfig"]
