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
    ) -> None:
        self._qdrant = QdrantClient(url=qdrant_url)
        self._embedding_api_base = embedding_api_base
        self._embedding_api_key = embedding_api_key or "dummy"
        self._embedding = self._create_embedding_client()
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions

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
                [entry["fact_text"] for entry in batch_entries],
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
                    vector = self._embed_batch([entry["fact_text"]], client=client)[0]
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
        self, repo_id: int, query: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        collection = self._fact_collection_name(repo_id)
        try:
            if not self._qdrant.collection_exists(collection_name=collection):
                return []
        except Exception:
            return []
        try:
            vector = self._embed(query)
            results = self._qdrant.query_points(
                collection_name=collection,
                query=vector,
                limit=limit,
            ).points
        except Exception as e:
            raise QdrantServiceError(str(e)) from e
        out = []
        for r in results:
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

    def split_page_into_chunks(self, content: str) -> list[dict]:
        """Split Markdown content into section chunks (400-800 chars each).

        Returns list of {"chunk_id": str, "heading": str, "chunk_text": str, "position": int}.
        """
        import re as _re

        lines = content.split("\n")
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

        chunks: list[dict] = []
        position = 0
        pending_heading = ""
        pending_text = ""
        last_heading = sections[-1][0] if sections else None

        for heading, body_lines in sections:
            text = "\n".join(body_lines).strip()
            if not text:
                continue
            combined = pending_text + ("\n" + text if pending_text else text)
            combined_heading = pending_heading or heading
            if len(combined) < 400 and heading != last_heading:
                pending_heading = combined_heading
                pending_text = combined
                continue
            if len(combined) <= 800:
                chunks.append({"chunk_id": str(position), "heading": combined_heading,
                               "chunk_text": combined[:800], "position": position})
                position += 1
                pending_heading = ""
                pending_text = ""
            else:
                if pending_text:
                    chunks.append({"chunk_id": str(position), "heading": pending_heading,
                                   "chunk_text": pending_text[:800], "position": position})
                    position += 1
                    pending_heading = ""
                    pending_text = ""
                for i in range(0, len(text), 800):
                    piece = text[i:i + 800]
                    chunks.append({"chunk_id": str(position), "heading": heading,
                                   "chunk_text": piece, "position": position})
                    position += 1
        if pending_text:
            chunks.append({"chunk_id": str(position), "heading": pending_heading,
                           "chunk_text": pending_text[:800], "position": position})
        return chunks

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
        points = []
        for chunk in chunks:
            try:
                vector = self._embed(chunk["chunk_text"])
            except QdrantServiceError:
                logger.warning("Chunk embed failed filename=%s chunk_id=%s", filename, chunk["chunk_id"])
                continue
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
                    "chunk_text": chunk["chunk_text"][:800],
                    "position": chunk["position"],
                },
            ))
        if points:
            self._upsert_points_in_batches(collection_name=collection, points=points)

    def search_chunks(
        self, repo_id: int, query: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        collection = self._chunk_collection_name(repo_id)
        try:
            if not self._qdrant.collection_exists(collection_name=collection):
                return []
        except Exception:
            return []
        try:
            vector = self._embed(query)
            results = self._qdrant.query_points(
                collection_name=collection,
                query=vector,
                limit=limit,
            ).points
        except Exception as e:
            raise QdrantServiceError(str(e)) from e
        out = []
        for r in results:
            pl = r.payload or {}
            out.append({
                "chunk_id": pl.get("chunk_id", ""),
                "filename": pl.get("filename", ""),
                "page_title": pl.get("page_title", ""),
                "page_type": pl.get("page_type", ""),
                "heading": pl.get("heading", ""),
                "chunk_text": pl.get("chunk_text", ""),
                "position": pl.get("position", 0),
                "score": r.score,
            })
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
