"""Qdrant adapter for confidential client mode."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qdrant_client.models import PointIdsList, PointStruct

from qdrant_service import QdrantService


class ConfidentialQdrantService(QdrantService):
    """Qdrant service that stores opaque payloads and resolves metadata locally."""

    def __init__(
        self,
        qdrant_map_path: str | Path,
        *,
        qdrant_url: str,
        embedding_api_base: str,
        embedding_api_key: str | None,
        embedding_model: str,
        embedding_dimensions: int = 1024,
    ) -> None:
        super().__init__(
            qdrant_url=qdrant_url,
            embedding_api_base=embedding_api_base,
            embedding_api_key=embedding_api_key,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )
        self._map_path = Path(qdrant_map_path)
        self._map_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_map_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._map_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_map_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS page_map (
                    repo_id INTEGER NOT NULL,
                    point_id INTEGER NOT NULL PRIMARY KEY,
                    filename TEXT NOT NULL,
                    title TEXT NOT NULL,
                    page_type TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunk_map (
                    repo_id INTEGER NOT NULL,
                    point_id INTEGER NOT NULL PRIMARY KEY,
                    filename TEXT NOT NULL,
                    page_title TEXT NOT NULL,
                    page_type TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    heading TEXT NOT NULL,
                    chunk_text TEXT NOT NULL,
                    position INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fact_map (
                    repo_id INTEGER NOT NULL,
                    point_id INTEGER NOT NULL PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    source_markdown_filename TEXT NOT NULL,
                    sheet TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    fields_json TEXT NOT NULL,
                    fact_text TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _safe_point_id(raw_point_id: int) -> int:
        return int(raw_point_id & ((1 << 63) - 1))

    def upsert_page(
        self,
        repo_id: int,
        filename: str,
        title: str,
        page_type: str,
        content: str,
    ) -> None:
        collection = self._collection_name(repo_id)
        self.ensure_collection(repo_id)
        vector = self._embed(content)
        point_id = self._safe_point_id(self._stable_point_id(repo_id, filename))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO page_map(repo_id, point_id, filename, title, page_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (repo_id, point_id, filename, title, page_type),
            )
        self._qdrant.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "repo_id": repo_id,
                        "point_ref": f"page:{point_id}",
                        "kind": "page",
                    },
                )
            ],
        )

    def search(self, repo_id: int, query: str, limit: int = 10) -> list[dict[str, Any]]:
        collection = self._collection_name(repo_id)
        try:
            if not self._qdrant.collection_exists(collection_name=collection):
                return []
            vector = self._embed(query)
            results = self._qdrant.query_points(
                collection_name=collection,
                query=vector,
                limit=limit,
            ).points
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        point_ids = [int(hit.id) for hit in results]
        if not point_ids:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT point_id, filename, title
                FROM page_map
                WHERE repo_id = ? AND point_id IN ({",".join("?" * len(point_ids))})
                """,
                [repo_id, *point_ids],
            ).fetchall()
        row_map = {int(row["point_id"]): row for row in rows}
        return [
            {
                "filename": row_map[int(hit.id)]["filename"],
                "title": row_map[int(hit.id)]["title"],
                "score": hit.score,
            }
            for hit in results
            if int(hit.id) in row_map
        ]

    def delete_page(self, repo_id: int, filename: str) -> None:
        point_id = self._safe_point_id(self._stable_point_id(repo_id, filename))
        with self._connect() as conn:
            conn.execute("DELETE FROM page_map WHERE repo_id = ? AND point_id = ?", (repo_id, point_id))
        self._qdrant.delete(
            collection_name=self._collection_name(repo_id),
            points_selector=PointIdsList(points=[point_id]),
        )

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
        points = []
        with self._connect() as conn:
            conn.execute("DELETE FROM chunk_map WHERE repo_id = ? AND filename = ?", (repo_id, filename))
            for chunk in chunks:
                vector = self._embed(chunk["chunk_text"])
                point_id = self._safe_point_id(
                    self._stable_chunk_point_id(repo_id, filename, chunk["chunk_id"])
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO chunk_map(
                        repo_id, point_id, filename, page_title, page_type, chunk_id, heading, chunk_text, position
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        repo_id,
                        point_id,
                        filename,
                        title,
                        page_type,
                        f"{filename}#{chunk['chunk_id']}",
                        chunk["heading"],
                        chunk["chunk_text"][:800],
                        int(chunk["position"]),
                    ),
                )
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "repo_id": repo_id,
                            "point_ref": f"chunk:{point_id}",
                            "kind": "chunk",
                        },
                    )
                )
        if points:
            self._upsert_points_in_batches(
                collection_name=self._chunk_collection_name(repo_id),
                points=points,
            )

    def search_chunks(self, repo_id: int, query: str, limit: int = 8) -> list[dict[str, Any]]:
        collection = self._chunk_collection_name(repo_id)
        try:
            if not self._qdrant.collection_exists(collection_name=collection):
                return []
            vector = self._embed(query)
            results = self._qdrant.query_points(
                collection_name=collection,
                query=vector,
                limit=limit,
            ).points
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        point_ids = [int(hit.id) for hit in results]
        if not point_ids:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT point_id, filename, page_title, page_type, chunk_id, heading, chunk_text, position
                FROM chunk_map
                WHERE repo_id = ? AND point_id IN ({",".join("?" * len(point_ids))})
                """,
                [repo_id, *point_ids],
            ).fetchall()
        row_map = {int(row["point_id"]): row for row in rows}
        out: list[dict[str, Any]] = []
        for hit in results:
            row = row_map.get(int(hit.id))
            if row is None:
                continue
            out.append(
                {
                    "chunk_id": row["chunk_id"],
                    "filename": row["filename"],
                    "page_title": row["page_title"],
                    "page_type": row["page_type"],
                    "heading": row["heading"],
                    "chunk_text": row["chunk_text"],
                    "position": row["position"],
                    "score": hit.score,
                }
            )
        return out

    def delete_page_chunks(self, repo_id: int, filename: str) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT point_id FROM chunk_map WHERE repo_id = ? AND filename = ?",
                (repo_id, filename),
            ).fetchall()
            conn.execute("DELETE FROM chunk_map WHERE repo_id = ? AND filename = ?", (repo_id, filename))
        point_ids = [int(row["point_id"]) for row in rows]
        if point_ids:
            self._qdrant.delete(
                collection_name=self._chunk_collection_name(repo_id),
                points_selector=PointIdsList(points=point_ids),
            )

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
        entries = self._prepare_fact_entries(records, source_filename=source_filename)
        if not entries:
            return
        processed = 0
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM fact_map WHERE repo_id = ? AND source_markdown_filename = ?",
                (repo_id, source_filename),
            )
            total = len(entries)
            for embedded_batch in self._iter_embedded_fact_batches(
                source_filename=source_filename,
                entries=entries,
            ):
                points: list[PointStruct] = []
                for entry, vector in embedded_batch:
                    record = entry["record"]
                    point_id = self._safe_point_id(
                        self._stable_fact_point_id(
                            repo_id,
                            source_filename,
                            str(record.get("record_id") or ""),
                        )
                    )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO fact_map(
                            repo_id, point_id, record_id, source_file, source_markdown_filename,
                            sheet, row_index, fields_json, fact_text
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            repo_id,
                            point_id,
                            str(record.get("record_id") or ""),
                            str(record.get("source_file", source_filename)),
                            str(record.get("source_markdown_filename", source_filename)),
                            str(record.get("sheet", "")),
                            int(record.get("row_index", 0)),
                            json.dumps(record.get("fields", {}), ensure_ascii=False),
                            entry["fact_text"][:800],
                        ),
                    )
                    points.append(
                        PointStruct(
                            id=point_id,
                            vector=vector,
                            payload={
                                "repo_id": repo_id,
                                "point_ref": f"fact:{point_id}",
                                "kind": "fact",
                            },
                        )
                    )
                if not points:
                    continue
                self._upsert_points_in_batches(
                    collection_name=self._fact_collection_name(repo_id),
                    points=points,
                )
                processed += len(points)
                if progress_callback:
                    progress_callback(processed, total)

    def search_facts(
        self,
        repo_id: int,
        query: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        collection = self._fact_collection_name(repo_id)
        try:
            if not self._qdrant.collection_exists(collection_name=collection):
                return []
            vector = self._embed(query)
            results = self._qdrant.query_points(
                collection_name=collection,
                query=vector,
                limit=limit,
            ).points
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        point_ids = [int(hit.id) for hit in results]
        if not point_ids:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT point_id, record_id, source_file, source_markdown_filename, sheet,
                       row_index, fields_json, fact_text
                FROM fact_map
                WHERE repo_id = ? AND point_id IN ({",".join("?" * len(point_ids))})
                """,
                [repo_id, *point_ids],
            ).fetchall()
        row_map = {int(row["point_id"]): row for row in rows}
        out: list[dict[str, Any]] = []
        for hit in results:
            row = row_map.get(int(hit.id))
            if row is None:
                continue
            out.append(
                {
                    "record_id": row["record_id"],
                    "source_file": row["source_file"],
                    "source_markdown_filename": row["source_markdown_filename"],
                    "sheet": row["sheet"],
                    "row_index": row["row_index"],
                    "fields": json.loads(row["fields_json"]),
                    "fact_text": row["fact_text"],
                    "score": hit.score,
                }
            )
        return out

    def delete_fact_records(self, repo_id: int, source_filename: str) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT point_id FROM fact_map WHERE repo_id = ? AND source_markdown_filename = ?",
                (repo_id, source_filename),
            ).fetchall()
            conn.execute(
                "DELETE FROM fact_map WHERE repo_id = ? AND source_markdown_filename = ?",
                (repo_id, source_filename),
            )
        point_ids = [int(row["point_id"]) for row in rows]
        if point_ids:
            self._qdrant.delete(
                collection_name=self._fact_collection_name(repo_id),
                points_selector=PointIdsList(points=point_ids),
            )

    def _wrap_error(self, exc: Exception) -> Exception:
        from exceptions import QdrantServiceError

        return QdrantServiceError(str(exc))
