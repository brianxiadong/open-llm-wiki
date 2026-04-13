"""Qdrant vector search with OpenAI-compatible embeddings."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from openai import OpenAI, OpenAIError
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

from exceptions import QdrantServiceError

logger = logging.getLogger(__name__)

_CONTENT_MAX = 5000


class QdrantService:
    def __init__(
        self,
        qdrant_url: str,
        embedding_api_base: str,
        embedding_api_key: str | None,
        embedding_model: str,
        embedding_dimensions: int = 1024,
    ) -> None:
        self._qdrant = QdrantClient(url=qdrant_url)
        self._embedding = OpenAI(
            base_url=embedding_api_base,
            api_key=embedding_api_key or "dummy",
        )
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions

    def _collection_name(self, repo_id: int) -> str:
        return f"repo_{repo_id}"

    @staticmethod
    def _stable_point_id(repo_id: int, filename: str) -> int:
        digest = hashlib.md5(f"{repo_id}:{filename}".encode()).hexdigest()
        return int(digest[:16], 16)

    def _embed(self, text: str) -> list[float]:
        start = time.perf_counter()
        try:
            response = self._embedding.embeddings.create(
                model=self._embedding_model,
                input=text,
            )
        except OpenAIError as e:
            logger.exception("Embedding API error model=%s", self._embedding_model)
            raise QdrantServiceError(str(e)) from e

        if not response.data:
            raise QdrantServiceError("Embedding response contained no data")
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.debug(
                "Embedding ok model=%s total_tokens=%s latency_ms=%.2f",
                self._embedding_model,
                getattr(usage, "total_tokens", None),
                elapsed_ms,
            )
        else:
            logger.debug(
                "Embedding ok model=%s latency_ms=%.2f",
                self._embedding_model,
                elapsed_ms,
            )
        return list(response.data[0].embedding)

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

        try:
            self._qdrant.upsert(collection_name=collection, points=[point])
        except UnexpectedResponse as e:
            logger.exception("Qdrant upsert failed collection=%s", collection)
            raise QdrantServiceError(str(e)) from e
        except Exception as e:
            logger.exception("Qdrant upsert failed collection=%s", collection)
            raise QdrantServiceError(str(e)) from e

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
            try:
                self._qdrant.upsert(collection_name=collection, points=points)
            except Exception as e:
                raise QdrantServiceError(str(e)) from e

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
