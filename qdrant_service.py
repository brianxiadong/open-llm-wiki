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
            results = self._qdrant.search(
                collection_name=collection,
                query_vector=vector,
                limit=limit,
            )
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
