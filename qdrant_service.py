"""Qdrant vector search with OpenAI-compatible embeddings."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from openai import OpenAI, OpenAIError
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

from exceptions import QdrantServiceError

logger = logging.getLogger(__name__)

_CONTENT_MAX = 5000

DEFAULT_CHUNK_MIN = 400
DEFAULT_CHUNK_MAX = 1200
DEFAULT_CHUNK_OVERLAP = 80
DEFAULT_CHUNK_PAYLOAD_CHARS = 1200

_SENTENCE_END_CHARS = "。！？!?\n"


class QdrantService:
    UPSERT_BATCH_SIZE = 256
    EMBEDDING_BATCH_SIZE = 32
    EMBEDDING_MAX_WORKERS = 4

    def __init__(
        self,
        qdrant_url: str,
        embedding_api_base: str,
        embedding_api_key: str | None,
        embedding_model: str,
        embedding_dimensions: int = 1024,
        *,
        chunk_min: int = DEFAULT_CHUNK_MIN,
        chunk_max: int = DEFAULT_CHUNK_MAX,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self._qdrant = QdrantClient(url=qdrant_url)
        self._embedding_api_base = embedding_api_base
        self._embedding_api_key = embedding_api_key or "dummy"
        self._embedding = self._create_embedding_client()
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions
        self._chunk_min = max(100, int(chunk_min))
        self._chunk_max = max(self._chunk_min + 100, int(chunk_max))
        self._chunk_overlap = max(0, min(int(chunk_overlap), self._chunk_min // 2))

    def _collection_name(self, repo_id: int) -> str:
        return f"repo_{repo_id}"

    def _create_embedding_client(self) -> OpenAI:
        return OpenAI(
            base_url=self._embedding_api_base,
            api_key=self._embedding_api_key,
        )

    def _upsert_points_in_batches(
        self,
        *,
        collection_name: str,
        points: list[PointStruct],
    ) -> None:
        if not points:
            return
        try:
            for start in range(0, len(points), self.UPSERT_BATCH_SIZE):
                batch = points[start:start + self.UPSERT_BATCH_SIZE]
                self._qdrant.upsert(collection_name=collection_name, points=batch)
        except UnexpectedResponse as e:
            logger.exception("Qdrant batch upsert failed collection=%s", collection_name)
            raise QdrantServiceError(str(e)) from e
        except Exception as e:
            logger.exception("Qdrant batch upsert failed collection=%s", collection_name)
            raise QdrantServiceError(str(e)) from e

    @staticmethod
    def _stable_point_id(repo_id: int, filename: str) -> int:
        digest = hashlib.md5(f"{repo_id}:{filename}".encode()).hexdigest()
        return int(digest[:16], 16)

    def _log_embedding_result(self, *, batch_size: int, response: Any, elapsed_ms: float) -> None:
        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.debug(
                "Embedding ok model=%s batch_size=%s total_tokens=%s latency_ms=%.2f",
                self._embedding_model,
                batch_size,
                getattr(usage, "total_tokens", None),
                elapsed_ms,
            )
        else:
            logger.debug(
                "Embedding ok model=%s batch_size=%s latency_ms=%.2f",
                self._embedding_model,
                batch_size,
                elapsed_ms,
            )

    def _embed_batch(
        self,
        texts: list[str],
        *,
        client: OpenAI | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        start = time.perf_counter()
        try:
            response = (client or self._embedding).embeddings.create(
                model=self._embedding_model,
                input=texts[0] if len(texts) == 1 else texts,
            )
        except OpenAIError as e:
            logger.exception("Embedding API error model=%s", self._embedding_model)
            raise QdrantServiceError(str(e)) from e

        if not response.data:
            raise QdrantServiceError("Embedding response contained no data")
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self._log_embedding_result(batch_size=len(texts), response=response, elapsed_ms=elapsed_ms)
        ordered = sorted(response.data, key=lambda item: int(getattr(item, "index", 0)))
        vectors = [list(item.embedding) for item in ordered]
        if len(vectors) != len(texts):
            raise QdrantServiceError(
                f"Embedding response size mismatch: expected {len(texts)}, got {len(vectors)}"
            )
        return vectors

    def _embed(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]

    @staticmethod
    def _normalize_fact_embed_text(record: dict, fact_text: str) -> str:
        """在 embedding 文本前拼入 sheet / row_index 等定位信息，提升字段问答召回。"""
        sheet = str(record.get("sheet") or "").strip()
        row_index = record.get("row_index")
        source_file = str(record.get("source_file") or "").strip()
        prefix_parts: list[str] = []
        if source_file:
            prefix_parts.append(source_file)
        if sheet:
            prefix_parts.append(f"表={sheet}")
        if row_index not in (None, ""):
            prefix_parts.append(f"行={row_index}")
        prefix = " | ".join(prefix_parts)
        if prefix:
            return f"[{prefix}]\n{fact_text}"
        return fact_text

    def _prepare_fact_entries(
        self,
        records: list[dict],
        *,
        source_filename: str,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for record in records:
            fact_text = str(record.get("fact_text") or "").strip()
            if not fact_text:
                continue
            entries.append(
                {
                    "record": record,
                    "fact_text": fact_text,
                    "embed_text": self._normalize_fact_embed_text(record, fact_text),
                }
            )
        return entries

    def _embed_fact_batch_with_fallback(
        self,
        *,
        source_filename: str,
        batch_entries: list[dict[str, Any]],
        use_isolated_client: bool,
    ) -> list[tuple[dict[str, Any], list[float]]]:
        if not batch_entries:
            return []
        client = self._create_embedding_client() if use_isolated_client else self._embedding
        try:
            vectors = self._embed_batch(
                [entry.get("embed_text", entry["fact_text"]) for entry in batch_entries],
                client=client,
            )
            return list(zip(batch_entries, vectors))
        except QdrantServiceError:
            logger.warning(
                "Fact batch embed failed source=%s batch_size=%s; retrying individually",
                source_filename,
                len(batch_entries),
            )
            embedded_entries: list[tuple[dict[str, Any], list[float]]] = []
            for entry in batch_entries:
                record = entry["record"]
                try:
                    vector = self._embed_batch(
                        [entry.get("embed_text", entry["fact_text"])],
                        client=client,
                    )[0]
                except QdrantServiceError:
                    logger.warning(
                        "Fact embed failed source=%s record_id=%s",
                        source_filename,
                        record.get("record_id"),
                    )
                    continue
                embedded_entries.append((entry, vector))
            return embedded_entries

    def _iter_embedded_fact_batches(
        self,
        *,
        source_filename: str,
        entries: list[dict[str, Any]],
    ):
        batch_specs = [
            entries[start:start + self.EMBEDDING_BATCH_SIZE]
            for start in range(0, len(entries), self.EMBEDDING_BATCH_SIZE)
        ]
        if not batch_specs:
            return
        if len(batch_specs) == 1 or self.EMBEDDING_MAX_WORKERS <= 1:
            for batch_entries in batch_specs:
                yield self._embed_fact_batch_with_fallback(
                    source_filename=source_filename,
                    batch_entries=batch_entries,
                    use_isolated_client=False,
                )
            return
        with ThreadPoolExecutor(
            max_workers=min(self.EMBEDDING_MAX_WORKERS, len(batch_specs)),
            thread_name_prefix="fact-embed",
        ) as executor:
            futures = [
                executor.submit(
                    self._embed_fact_batch_with_fallback,
                    source_filename=source_filename,
                    batch_entries=batch_entries,
                    use_isolated_client=True,
                )
                for batch_entries in batch_specs
            ]
            for future in as_completed(futures):
                yield future.result()

    def ensure_collection(self, repo_id: int) -> None:
        name = self._collection_name(repo_id)
        logger.info(
            "Qdrant ensure_collection repo_id=%s name=%s dims=%s",
            repo_id,
            name,
            self._embedding_dimensions,
        )
        try:
            if self._qdrant.collection_exists(collection_name=name):
                logger.debug("Qdrant collection already exists name=%s", name)
                return
            self._qdrant.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=self._embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Qdrant created collection name=%s", name)
        except UnexpectedResponse as e:
            logger.exception("Qdrant ensure_collection failed name=%s", name)
            raise QdrantServiceError(str(e)) from e
        except Exception as e:
            logger.exception("Qdrant ensure_collection failed name=%s", name)
            raise QdrantServiceError(str(e)) from e

    def upsert_page(
        self,
        repo_id: int,
        filename: str,
        title: str,
        page_type: str,
        content: str,
    ) -> None:
        collection = self._collection_name(repo_id)
        logger.info(
            "Qdrant upsert_page repo_id=%s filename=%r collection=%s",
            repo_id,
            filename,
            collection,
        )
        self.ensure_collection(repo_id)

        start = time.perf_counter()
        try:
            vector = self._embed(content)
        except QdrantServiceError:
            raise

        payload_content = content[:_CONTENT_MAX]
        point_id = self._stable_point_id(repo_id, filename)
        point = PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "repo_id": repo_id,
                "filename": filename,
                "title": title,
                "type": page_type,
                "content": payload_content,
            },
        )

        self._upsert_points_in_batches(collection_name=collection, points=[point])

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "Qdrant upsert_page done collection=%s point_id=%s latency_ms=%.2f",
            collection,
            point_id,
            elapsed_ms,
        )

    def search(self, repo_id: int, query: str, limit: int = 10) -> list[dict[str, Any]]:
        collection = self._collection_name(repo_id)
        logger.info(
            "Qdrant search repo_id=%s collection=%s limit=%s",
            repo_id,
            collection,
            limit,
        )
        try:
            exists = self._qdrant.collection_exists(collection_name=collection)
        except Exception as e:
            logger.exception("Qdrant search collection_exists failed collection=%s", collection)
            raise QdrantServiceError(str(e)) from e
        if not exists:
            logger.info("Qdrant search skipped: collection missing collection=%s", collection)
            return []

        start = time.perf_counter()
        try:
            vector = self._embed(query)
        except QdrantServiceError:
            raise

        try:
            results = self._qdrant.query_points(
                collection_name=collection,
                query=vector,
                limit=limit,
            ).points
        except UnexpectedResponse as e:
            logger.exception("Qdrant search failed collection=%s", collection)
            raise QdrantServiceError(str(e)) from e
        except Exception as e:
            logger.exception("Qdrant search failed collection=%s", collection)
            raise QdrantServiceError(str(e)) from e

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        out: list[dict[str, Any]] = []
        for r in results:
            pl = r.payload or {}
            out.append(
                {
                    "filename": pl.get("filename", ""),
                    "title": pl.get("title", ""),
                    "score": r.score,
                }
            )
        logger.info(
            "Qdrant search done collection=%s hits=%s latency_ms=%.2f",
            collection,
            len(out),
            elapsed_ms,
        )
        return out

    def delete_page(self, repo_id: int, filename: str) -> None:
        """Delete a single wiki page vector by filename."""
        name = self._collection_name(repo_id)
        point_id = self._stable_point_id(repo_id, filename)
        logger.info("Qdrant delete_page repo_id=%s filename=%s point_id=%s", repo_id, filename, point_id)
        try:
            from qdrant_client.models import PointIdsList
            self._qdrant.delete(
                collection_name=name,
                points_selector=PointIdsList(points=[point_id]),
            )
        except Exception as e:
            logger.warning("Qdrant delete_page failed filename=%s: %s", filename, e)

    # ── Chunk-level indexing ──────────────────────────────────────────────────

    def _fact_collection_name(self, repo_id: int) -> str:
        return f"repo_{repo_id}_facts"

    @staticmethod
    def _stable_fact_point_id(repo_id: int, source_filename: str, record_id: str) -> int:
        digest = hashlib.md5(f"{repo_id}:{source_filename}:{record_id}".encode()).hexdigest()
        return int(digest[:16], 16)

    def ensure_fact_collection(self, repo_id: int) -> None:
        name = self._fact_collection_name(repo_id)
        try:
            if self._qdrant.collection_exists(collection_name=name):
                return
            self._qdrant.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=self._embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Qdrant created fact collection name=%s", name)
        except Exception as e:
            raise QdrantServiceError(str(e)) from e

    def upsert_fact_records(
        self,
        repo_id: int,
        source_filename: str,
        records: list[dict],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        if not records:
            return
        self.ensure_fact_collection(repo_id)
        collection = self._fact_collection_name(repo_id)
        entries = self._prepare_fact_entries(records, source_filename=source_filename)
        if not entries:
            return
        processed = 0
        total = len(entries)
        for embedded_batch in self._iter_embedded_fact_batches(
            source_filename=source_filename,
            entries=entries,
        ):
            points: list[PointStruct] = []
            for entry, vector in embedded_batch:
                record = entry["record"]
                point_id = self._stable_fact_point_id(
                    repo_id,
                    source_filename,
                    str(record.get("record_id") or ""),
                )
                payload = {
                    "repo_id": repo_id,
                    "record_id": record.get("record_id", ""),
                    "source_file": record.get("source_file", source_filename),
                    "source_markdown_filename": record.get(
                        "source_markdown_filename", source_filename
                    ),
                    "sheet": record.get("sheet", ""),
                    "row_index": record.get("row_index", 0),
                    "fields": record.get("fields", {}),
                    "fact_text": entry["fact_text"][:800],
                }
                points.append(PointStruct(id=point_id, vector=vector, payload=payload))
            if not points:
                continue
            self._upsert_points_in_batches(collection_name=collection, points=points)
            processed += len(points)
            if progress_callback:
                progress_callback(processed, total)

    def search_facts(
        self,
        repo_id: int,
        query: str,
        limit: int = 8,
        *,
        score_threshold: float | None = None,
        oversample: int = 2,
    ) -> list[dict[str, Any]]:
        collection = self._fact_collection_name(repo_id)
        try:
            if not self._qdrant.collection_exists(collection_name=collection):
                return []
        except Exception:
            return []
        fetch_limit = max(limit, limit * max(1, int(oversample)))
        try:
            vector = self._embed(query)
            results = self._qdrant.query_points(
                collection_name=collection,
                query=vector,
                limit=fetch_limit,
            ).points
        except Exception as e:
            raise QdrantServiceError(str(e)) from e
        out = []
        for r in results:
            if score_threshold is not None and (r.score or 0.0) < score_threshold:
                continue
            pl = r.payload or {}
            out.append(
                {
                    "record_id": pl.get("record_id", ""),
                    "source_file": pl.get("source_file", ""),
                    "source_markdown_filename": pl.get("source_markdown_filename", ""),
                    "sheet": pl.get("sheet", ""),
                    "row_index": pl.get("row_index", 0),
                    "fields": pl.get("fields", {}),
                    "fact_text": pl.get("fact_text", ""),
                    "score": r.score,
                }
            )
            if len(out) >= limit:
                break
        return out

    def delete_fact_records(self, repo_id: int, source_filename: str) -> None:
        collection = self._fact_collection_name(repo_id)
        try:
            if not self._qdrant.collection_exists(collection_name=collection):
                return
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            self._qdrant.delete(
                collection_name=collection,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="source_markdown_filename",
                            match=MatchValue(value=source_filename),
                        )
                    ]
                ),
            )
        except Exception as e:
            logger.warning("delete_fact_records failed source=%s: %s", source_filename, e)

    def delete_fact_collection(self, repo_id: int) -> None:
        name = self._fact_collection_name(repo_id)
        try:
            self._qdrant.delete_collection(collection_name=name)
        except Exception as e:
            logger.warning("delete_fact_collection failed repo_id=%s: %s", repo_id, e)

    def _chunk_collection_name(self, repo_id: int) -> str:
        return f"repo_{repo_id}_chunks"

    @staticmethod
    def _stable_chunk_point_id(repo_id: int, filename: str, chunk_id: str) -> int:
        digest = hashlib.md5(f"{repo_id}:{filename}:{chunk_id}".encode()).hexdigest()
        return int(digest[:16], 16)

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """剔除 markdown 的 YAML frontmatter，避免把元数据切进 chunk。"""
        text = content.lstrip("\ufeff")
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end != -1:
                return text[end + 4:].lstrip("\n")
        return text

    @staticmethod
    def _find_sentence_boundary(text: str, upper: int, lower: int) -> int:
        """在 [lower, upper] 区间内往前找一个句子/段落断点，找不到就返回 upper。"""
        if upper >= len(text):
            return len(text)
        window_end = min(upper, len(text))
        window_start = max(lower, 0)
        best = -1
        for ch in _SENTENCE_END_CHARS:
            idx = text.rfind(ch, window_start, window_end)
            if idx > best:
                best = idx
        if best == -1:
            return window_end
        return best + 1

    def _slice_section_body(
        self,
        heading: str,
        text: str,
    ) -> list[tuple[str, str]]:
        """把某个 section 的正文按 chunk_max 切；返回 [(heading, chunk_text), ...]。"""
        text = text.strip()
        if not text:
            return []
        if len(text) <= self._chunk_max:
            return [(heading, text)]

        chunks: list[tuple[str, str]] = []
        cursor = 0
        length = len(text)
        max_len = self._chunk_max
        min_boundary = max(self._chunk_min, max_len - 200)
        while cursor < length:
            remaining = length - cursor
            if remaining <= max_len:
                piece = text[cursor:].strip()
                if piece:
                    chunks.append((heading, piece))
                break
            hard_end = cursor + max_len
            boundary = self._find_sentence_boundary(text, hard_end, cursor + min_boundary)
            if boundary <= cursor:
                boundary = hard_end
            piece = text[cursor:boundary].strip()
            if piece:
                chunks.append((heading, piece))
            next_cursor = max(boundary - self._chunk_overlap, cursor + 1)
            cursor = next_cursor
        return chunks

    def split_page_into_chunks(self, content: str) -> list[dict]:
        """Split Markdown content into heading-aware chunks.

        返回 ``[{"chunk_id", "heading", "chunk_text", "position"}, ...]``；
        - 按 H1~H4 标题切 section，短 section 合并到下一段
        - 超长 section 在句号/换行处切分并保留 overlap
        - 只剔除 frontmatter，保留正文结构
        """
        import re as _re

        text = self._strip_frontmatter(content)
        lines = text.split("\n")
        sections: list[tuple[str, list[str]]] = []
        current_heading = ""
        current_lines: list[str] = []
        for line in lines:
            m = _re.match(r"^#{1,4}\s+(.+)", line)
            if m:
                if current_lines:
                    sections.append((current_heading, current_lines))
                current_heading = m.group(1).strip()
                current_lines = []
            else:
                current_lines.append(line)
        if current_lines:
            sections.append((current_heading, current_lines))

        normalized: list[tuple[str, str]] = []
        for heading, body_lines in sections:
            body = "\n".join(body_lines).strip()
            if not body:
                continue
            normalized.append((heading, body))

        if not normalized:
            return []

        merged: list[tuple[str, str]] = []
        pending_heading = ""
        pending_text = ""
        for heading, body in normalized:
            if pending_text:
                combined_heading = pending_heading or heading
                combined_text = pending_text + "\n\n" + body
                if len(combined_text) <= self._chunk_max:
                    pending_heading = combined_heading
                    pending_text = combined_text
                    continue
                merged.append((pending_heading, pending_text))
                pending_heading = ""
                pending_text = ""
            if len(body) < self._chunk_min:
                pending_heading = heading
                pending_text = body
            else:
                merged.append((heading, body))
        if pending_text:
            merged.append((pending_heading, pending_text))

        out: list[dict] = []
        position = 0
        for heading, body in merged:
            for h, chunk_text in self._slice_section_body(heading, body):
                out.append({
                    "chunk_id": str(position),
                    "heading": h,
                    "chunk_text": chunk_text,
                    "position": position,
                })
                position += 1
        return out

    @staticmethod
    def build_chunk_embed_text(page_title: str, heading: str, chunk_text: str) -> str:
        """把页面标题和小节标题拼到 chunk_text 前作为 embedding 输入，显著提升语义区分度。"""
        parts: list[str] = []
        title_clean = (page_title or "").strip()
        heading_clean = (heading or "").strip()
        if title_clean:
            parts.append(title_clean)
        if heading_clean and heading_clean != title_clean:
            parts.append(heading_clean)
        header = " / ".join(parts)
        body = (chunk_text or "").strip()
        if header:
            return f"{header}\n\n{body}"
        return body

    def ensure_chunk_collection(self, repo_id: int) -> None:
        name = self._chunk_collection_name(repo_id)
        try:
            if self._qdrant.collection_exists(collection_name=name):
                return
            self._qdrant.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=self._embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Qdrant created chunk collection name=%s", name)
        except Exception as e:
            raise QdrantServiceError(str(e)) from e

    def _embed_chunks_batched(
        self,
        chunks: list[dict],
        *,
        page_title: str,
    ) -> list[tuple[dict, list[float]]]:
        """对 chunk 批量 embed；失败时回退到逐条以提高鲁棒性。"""
        if not chunks:
            return []
        texts = [
            self.build_chunk_embed_text(page_title, c.get("heading", ""), c["chunk_text"])
            for c in chunks
        ]
        out: list[tuple[dict, list[float]]] = []
        for start in range(0, len(chunks), self.EMBEDDING_BATCH_SIZE):
            sub_chunks = chunks[start:start + self.EMBEDDING_BATCH_SIZE]
            sub_texts = texts[start:start + self.EMBEDDING_BATCH_SIZE]
            try:
                vectors = self._embed_batch(sub_texts)
                out.extend(zip(sub_chunks, vectors))
            except QdrantServiceError:
                logger.warning("Chunk batch embed failed size=%s; retrying individually", len(sub_chunks))
                for chunk, text in zip(sub_chunks, sub_texts):
                    try:
                        vec = self._embed_batch([text])[0]
                    except QdrantServiceError:
                        logger.warning(
                            "Chunk embed failed chunk_id=%s heading=%r",
                            chunk.get("chunk_id"),
                            chunk.get("heading"),
                        )
                        continue
                    out.append((chunk, vec))
        return out

    def upsert_page_chunks(
        self,
        repo_id: int,
        filename: str,
        title: str,
        page_type: str,
        content: str,
    ) -> None:
        chunks = self.split_page_into_chunks(content)
        if not chunks:
            return
        self.ensure_chunk_collection(repo_id)
        collection = self._chunk_collection_name(repo_id)
        embedded = self._embed_chunks_batched(chunks, page_title=title)
        points: list[PointStruct] = []
        for chunk, vector in embedded:
            point_id = self._stable_chunk_point_id(repo_id, filename, chunk["chunk_id"])
            points.append(PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "repo_id": repo_id,
                    "filename": filename,
                    "page_title": title,
                    "page_type": page_type,
                    "chunk_id": f"{filename}#{chunk['chunk_id']}",
                    "heading": chunk["heading"],
                    "chunk_text": chunk["chunk_text"][:DEFAULT_CHUNK_PAYLOAD_CHARS],
                    "position": chunk["position"],
                },
            ))
        if points:
            self._upsert_points_in_batches(collection_name=collection, points=points)

    def search_chunks(
        self,
        repo_id: int,
        query: str,
        limit: int = 8,
        *,
        score_threshold: float | None = None,
        max_per_file: int | None = None,
        oversample: int = 3,
    ) -> list[dict[str, Any]]:
        """Dense search on chunk collection with score threshold and per-file cap.

        - ``score_threshold``: 低于该 cosine 的命中整个丢弃（默认 None 表示不过滤）
        - ``max_per_file``: 同一 filename 最多保留多少条，避免单页霸榜
        - ``oversample``: 为了能在过滤后仍然取到 ``limit`` 条，Qdrant 端拉取 ``limit * oversample`` 条
        """
        collection = self._chunk_collection_name(repo_id)
        try:
            if not self._qdrant.collection_exists(collection_name=collection):
                return []
        except Exception:
            return []
        fetch_limit = max(limit, limit * max(1, int(oversample)))
        try:
            vector = self._embed(query)
            results = self._qdrant.query_points(
                collection_name=collection,
                query=vector,
                limit=fetch_limit,
            ).points
        except Exception as e:
            raise QdrantServiceError(str(e)) from e

        per_file_count: dict[str, int] = {}
        out: list[dict[str, Any]] = []
        for r in results:
            score = r.score or 0.0
            if score_threshold is not None and score < score_threshold:
                continue
            pl = r.payload or {}
            fn = pl.get("filename", "")
            if max_per_file is not None and fn:
                if per_file_count.get(fn, 0) >= max_per_file:
                    continue
                per_file_count[fn] = per_file_count.get(fn, 0) + 1
            out.append({
                "chunk_id": pl.get("chunk_id", ""),
                "filename": fn,
                "page_title": pl.get("page_title", ""),
                "page_type": pl.get("page_type", ""),
                "heading": pl.get("heading", ""),
                "chunk_text": pl.get("chunk_text", ""),
                "position": pl.get("position", 0),
                "score": score,
            })
            if len(out) >= limit:
                break
        return out

    def scroll_all_chunks(self, repo_id: int) -> list[dict[str, Any]]:
        """拉取某 repo 下所有 chunk payload，供 BM25 等旁路索引构建使用。"""
        collection = self._chunk_collection_name(repo_id)
        try:
            if not self._qdrant.collection_exists(collection_name=collection):
                return []
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        offset: Any = None
        try:
            while True:
                points, next_offset = self._qdrant.scroll(
                    collection_name=collection,
                    limit=512,
                    with_payload=True,
                    with_vectors=False,
                    offset=offset,
                )
                for p in points:
                    pl = p.payload or {}
                    out.append({
                        "chunk_id": pl.get("chunk_id", ""),
                        "filename": pl.get("filename", ""),
                        "page_title": pl.get("page_title", ""),
                        "page_type": pl.get("page_type", ""),
                        "heading": pl.get("heading", ""),
                        "chunk_text": pl.get("chunk_text", ""),
                        "position": pl.get("position", 0),
                    })
                if not next_offset:
                    break
                offset = next_offset
        except Exception as e:
            logger.warning("scroll_all_chunks failed collection=%s: %s", collection, e)
            return []
        return out

    def delete_page_chunks(self, repo_id: int, filename: str) -> None:
        collection = self._chunk_collection_name(repo_id)
        try:
            if not self._qdrant.collection_exists(collection_name=collection):
                return
            from qdrant_client.models import FieldCondition, Filter, MatchValue
            self._qdrant.delete(
                collection_name=collection,
                points_selector=Filter(
                    must=[FieldCondition(key="filename", match=MatchValue(value=filename))]
                ),
            )
        except Exception as e:
            logger.warning("delete_page_chunks failed filename=%s: %s", filename, e)

    def delete_chunk_collection(self, repo_id: int) -> None:
        name = self._chunk_collection_name(repo_id)
        try:
            self._qdrant.delete_collection(collection_name=name)
        except Exception as e:
            logger.warning("delete_chunk_collection failed repo_id=%s: %s", repo_id, e)

    def delete_collection(self, repo_id: int) -> None:
        name = self._collection_name(repo_id)
        logger.info("Qdrant delete_collection repo_id=%s name=%s", repo_id, name)
        try:
            self._qdrant.delete_collection(collection_name=name)
            logger.info("Qdrant delete_collection ok name=%s", name)
        except UnexpectedResponse as e:
            logger.exception("Qdrant delete_collection failed name=%s", name)
            raise QdrantServiceError(str(e)) from e
        except Exception as e:
            logger.exception("Qdrant delete_collection failed name=%s", name)
            raise QdrantServiceError(str(e)) from e
