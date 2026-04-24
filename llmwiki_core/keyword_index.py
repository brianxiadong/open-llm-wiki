"""In-memory BM25 keyword index, shared by server and confidential client.

设计目标：
- 与现有 Qdrant dense 检索互补（数字/ID/专名），为 hybrid retrieval 提供关键词通道。
- 语料由调用方通过 ``corpus_loader`` 提供，避免把持久化细节带进核心包：
  - 服务端：`QdrantService.scroll_all_chunks` / `scroll_all_facts` 从 Qdrant payload 拉取；
  - Confidential 客户端：同名方法从本地 SQLite `chunk_map` / `fact_map` 拉取。
- 中文分词使用 ``jieba``（纯 Python 无原生依赖），失败时回退到字符 bi-gram。
- 按 ``(repo_id, signature)`` 惰性缓存；signature 由调用方传入（通常是 chunk 数量或
  ingest 时间戳），变更即失效，无需显式刷新。
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional deps detection
    import jieba  # type: ignore

    _HAS_JIEBA = True
except Exception:  # pragma: no cover
    _HAS_JIEBA = False

try:  # pragma: no cover - optional deps detection
    from rank_bm25 import BM25Okapi  # type: ignore

    _HAS_BM25 = True
except Exception:  # pragma: no cover
    _HAS_BM25 = False


_CN_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_PUNCT_RE = re.compile(r"[\s，。！？、；：,.\!\?;:()（）\[\]【】\"'`~@#\$%\^&\*\-_=\+/\\|<>]+")


def _char_bigrams(text: str) -> list[str]:
    cleaned = _PUNCT_RE.sub("", text)
    if len(cleaned) <= 1:
        return [cleaned] if cleaned else []
    return [cleaned[i:i + 2] for i in range(len(cleaned) - 1)]


def tokenize(text: str) -> list[str]:
    r"""中英文混合分词：英文/数字按 \w+，中文用 jieba 或字符 bi-gram。

    同时保留小写英文 token、字符 bi-gram，以提升跨分词粒度的召回。
    """
    if not text:
        return []
    tokens: list[str] = []
    text = text.strip()
    for word in _WORD_RE.findall(text):
        tokens.append(word.lower())
    cn_only = "".join(_CN_CHAR_RE.findall(text))
    if cn_only:
        if _HAS_JIEBA:
            try:
                for seg in jieba.cut_for_search(cn_only):  # type: ignore[attr-defined]
                    seg = seg.strip()
                    if not seg:
                        continue
                    if len(seg) == 1 and _CN_CHAR_RE.match(seg):
                        continue
                    tokens.append(seg)
            except Exception as exc:  # pragma: no cover
                logger.debug("jieba tokenize failed, fallback to bigrams: %s", exc)
                tokens.extend(_char_bigrams(cn_only))
        else:
            tokens.extend(_char_bigrams(cn_only))
    return tokens


@dataclass
class KeywordHit:
    doc_id: str
    score: float
    payload: dict[str, Any]


class KeywordIndex:
    """惰性构建的 BM25 索引，按 (key, signature) 缓存。

    BM25 原始分数没有统一量纲，不应直接与 cosine 比较；Retriever 层用 RRF
    融合排名而非分数，因此这里只关心相对排序。
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], "KeywordIndex._Built"] = {}
        self._lock = threading.Lock()

    @dataclass
    class _Built:
        docs: list[dict[str, Any]]
        tokens: list[list[str]]
        bm25: Any | None  # None when rank_bm25 不可用 → fallback to naive TF

    def _build(self, docs: list[dict[str, Any]], *, text_key: str) -> "KeywordIndex._Built":
        tokenized = [tokenize(str(d.get(text_key) or "")) for d in docs]
        bm25: Any | None = None
        if _HAS_BM25 and any(tokenized):
            try:
                bm25 = BM25Okapi(tokenized)
            except Exception as exc:  # pragma: no cover
                logger.warning("rank_bm25 build failed: %s", exc)
                bm25 = None
        return self._Built(docs=list(docs), tokens=tokenized, bm25=bm25)

    def search(
        self,
        *,
        key: str,
        signature: str,
        query: str,
        corpus_loader: Callable[[], Iterable[dict[str, Any]]],
        text_key: str = "chunk_text",
        id_key: str = "chunk_id",
        limit: int = 12,
    ) -> list[KeywordHit]:
        if not query:
            return []
        cache_key = (key, signature)
        with self._lock:
            built = self._cache.get(cache_key)
            if built is None:
                docs = [dict(d) for d in corpus_loader()]
                built = self._build(docs, text_key=text_key)
                self._cache[cache_key] = built
        if not built.docs:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores: list[float]
        if built.bm25 is not None:
            try:
                scores = list(built.bm25.get_scores(q_tokens))
            except Exception as exc:  # pragma: no cover
                logger.warning("bm25 scoring failed: %s", exc)
                scores = self._naive_tf_scores(built.tokens, q_tokens)
        else:
            scores = self._naive_tf_scores(built.tokens, q_tokens)

        indexed = [i for i, s in enumerate(scores) if s > 0]
        indexed.sort(key=lambda i: scores[i], reverse=True)
        out: list[KeywordHit] = []
        for i in indexed[:limit]:
            doc = built.docs[i]
            out.append(
                KeywordHit(
                    doc_id=str(doc.get(id_key) or i),
                    score=float(scores[i]),
                    payload=doc,
                )
            )
        return out

    @staticmethod
    def _naive_tf_scores(
        doc_tokens: list[list[str]],
        query_tokens: list[str],
    ) -> list[float]:
        q_set = set(query_tokens)
        return [
            float(sum(1 for t in tokens if t in q_set))
            for tokens in doc_tokens
        ]

    def invalidate(self, key: str) -> None:
        with self._lock:
            stale = [ck for ck in self._cache if ck[0] == key]
            for ck in stale:
                self._cache.pop(ck, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_GLOBAL_INDEX = KeywordIndex()


def global_keyword_index() -> KeywordIndex:
    """进程级单例，Retriever / wiki_engine 默认用它。测试可以另建独立实例。"""
    return _GLOBAL_INDEX


__all__ = [
    "KeywordHit",
    "KeywordIndex",
    "global_keyword_index",
    "tokenize",
]
