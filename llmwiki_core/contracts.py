"""Shared repo/runtime contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RepoRef:
    """Runner-agnostic repo identity."""

    id: int
    slug: str
    mode: str = "normal"


@dataclass(frozen=True, slots=True)
class LocalRepoPaths:
    """Filesystem layout used by both server and confidential client."""

    data_dir: Path
    username: str
    repo_slug: str

    @property
    def base_dir(self) -> Path:
        return self.data_dir / self.username / self.repo_slug

    @property
    def raw_dir(self) -> Path:
        return self.base_dir / "raw"

    @property
    def wiki_dir(self) -> Path:
        return self.base_dir / "wiki"

    @property
    def facts_records_dir(self) -> Path:
        return self.base_dir / "facts" / "records"

    @property
    def sessions_dir(self) -> Path:
        return self.base_dir / "sessions"

    @property
    def qdrant_map_path(self) -> Path:
        return self.base_dir / "qdrant-map.sqlite"


@dataclass(frozen=True, slots=True)
class ConfidentialServices:
    """External services used by confidential client runtime."""

    llm_api_base: str
    llm_api_key: str
    llm_model: str
    llm_max_tokens: int
    embedding_api_base: str
    embedding_api_key: str
    embedding_model: str
    embedding_dimensions: int
    qdrant_url: str
    mineru_api_url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfidentialServices":
        return cls(
            llm_api_base=str(data.get("llm_api_base", "")).strip(),
            llm_api_key=str(data.get("llm_api_key", "")).strip(),
            llm_model=str(data.get("llm_model", "")).strip(),
            llm_max_tokens=int(data.get("llm_max_tokens", 4000)),
            embedding_api_base=str(data.get("embedding_api_base", "")).strip(),
            embedding_api_key=str(data.get("embedding_api_key", "")).strip(),
            embedding_model=str(data.get("embedding_model", "")).strip(),
            embedding_dimensions=int(data.get("embedding_dimensions", 1024)),
            qdrant_url=str(data.get("qdrant_url", "")).strip(),
            mineru_api_url=str(data.get("mineru_api_url", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "llm_api_base": self.llm_api_base,
            "llm_api_key": self.llm_api_key,
            "llm_model": self.llm_model,
            "llm_max_tokens": self.llm_max_tokens,
            "embedding_api_base": self.embedding_api_base,
            "embedding_api_key": self.embedding_api_key,
            "embedding_model": self.embedding_model,
            "embedding_dimensions": self.embedding_dimensions,
            "qdrant_url": self.qdrant_url,
            "mineru_api_url": self.mineru_api_url,
        }


@dataclass(frozen=True, slots=True)
class QueryRunResult:
    """Portable query result shape for shared runtimes."""

    answer: str
    confidence: dict[str, Any]
    wiki_evidence: list[dict[str, Any]]
    chunk_evidence: list[dict[str, Any]]
    fact_evidence: list[dict[str, Any]]
    referenced_pages: list[str]
    evidence_summary: str

    @classmethod
    def from_engine_result(cls, data: dict[str, Any]) -> "QueryRunResult":
        return cls(
            answer=str(data.get("answer") or data.get("markdown") or ""),
            confidence=dict(data.get("confidence") or {}),
            wiki_evidence=list(data.get("wiki_evidence") or []),
            chunk_evidence=list(data.get("chunk_evidence") or []),
            fact_evidence=list(data.get("fact_evidence") or []),
            referenced_pages=list(data.get("referenced_pages") or []),
            evidence_summary=str(data.get("evidence_summary") or ""),
        )
