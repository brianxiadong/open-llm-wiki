"""Confidential local runtime built on the shared engine."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import openpyxl

from confidential_client.qdrant import ConfidentialQdrantService
from confidential_client.repository import ConfidentialRepository
from exceptions import MineruClientError
from llm_client import LLMClient
from llmwiki_core.contracts import QueryRunResult
from mineru_client import MineruClient
from utils import build_tabular_markdown_and_records, write_jsonl
from wiki_engine import WikiEngine

_TEXT_EXTENSIONS = {".md", ".txt"}
_TABULAR_EXTENSIONS = {".csv", ".xlsx", ".xls"}
_MINERU_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg"}


class ConfidentialRuntime:
    """Runs ingest/query entirely on the client."""

    def __init__(self, repository: ConfidentialRepository, passphrase: str) -> None:
        self._repository = repository
        self._passphrase = passphrase

    def _build_engine(self, workspace) -> WikiEngine:
        services = workspace.load_services()
        llm = LLMClient(
            api_base=services.llm_api_base,
            api_key=services.llm_api_key or "dummy",
            model=services.llm_model,
            max_tokens=services.llm_max_tokens,
        )
        qdrant = ConfidentialQdrantService(
            qdrant_map_path=workspace.repo_paths.qdrant_map_path,
            qdrant_url=services.qdrant_url,
            embedding_api_base=services.embedding_api_base,
            embedding_api_key=services.embedding_api_key,
            embedding_model=services.embedding_model,
            embedding_dimensions=services.embedding_dimensions,
        )
        return WikiEngine(llm, qdrant, str(workspace.repo_paths.data_dir))

    def ingest_file(self, source_path: str | Path) -> list[dict[str, Any]]:
        source = Path(source_path)
        with self._repository.unlocked(self._passphrase) as workspace:
            target_name = self._stage_source(workspace, source)
            engine = self._build_engine(workspace)
            return list(
                engine.ingest(
                    workspace.repo_ref,
                    workspace.repo_paths.username,
                    target_name,
                )
            )

    def query(self, question: str, history: list[dict] | None = None) -> QueryRunResult:
        with self._repository.unlocked(self._passphrase) as workspace:
            engine = self._build_engine(workspace)
            result = engine.query_with_evidence(
                workspace.repo_ref,
                workspace.repo_paths.username,
                question,
                wiki_base_url="",
                history=history,
            )
            workspace.append_history(question, result.get("answer") or result.get("markdown") or "")
            return QueryRunResult.from_engine_result(result)

    def load_history(self) -> list[dict[str, Any]]:
        with self._repository.unlocked(self._passphrase) as workspace:
            return workspace.load_history()

    def export_bundle(self, output_path: str | Path) -> Path:
        return self._repository.export_bundle(output_path)

    def _stage_source(self, workspace, source_path: Path) -> str:
        repo_paths = workspace.repo_paths
        raw_dir = repo_paths.raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        ext = source_path.suffix.lower()
        if ext in _TEXT_EXTENSIONS:
            target = raw_dir / source_path.name
            target.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
            return target.name
        if ext == ".csv":
            return self._stage_csv(repo_paths, source_path)
        if ext in {".xlsx", ".xls"}:
            return self._stage_excel(repo_paths, source_path)
        if ext in _MINERU_EXTENSIONS:
            return self._stage_mineru(workspace, source_path)
        raise ValueError(f"Unsupported source extension: {source_path.suffix}")

    def _stage_csv(self, repo_paths, source_path: Path) -> str:
        with source_path.open("r", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        tables = [{"name": "CSV", "rows": rows}]
        md_name = f"{source_path.stem}.md"
        markdown, records = build_tabular_markdown_and_records(
            source_filename=source_path.name,
            source_markdown_filename=md_name,
            tables=tables,
        )
        (repo_paths.raw_dir / md_name).write_text(markdown, encoding="utf-8")
        repo_paths.facts_records_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(str(repo_paths.facts_records_dir / f"{source_path.stem}.jsonl"), records)
        return md_name

    def _stage_excel(self, repo_paths, source_path: Path) -> str:
        workbook = openpyxl.load_workbook(source_path, data_only=True)
        tables = []
        for sheet in workbook.worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            tables.append({"name": sheet.title, "rows": rows})
        md_name = f"{source_path.stem}.md"
        markdown, records = build_tabular_markdown_and_records(
            source_filename=source_path.name,
            source_markdown_filename=md_name,
            tables=tables,
        )
        (repo_paths.raw_dir / md_name).write_text(markdown, encoding="utf-8")
        repo_paths.facts_records_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(str(repo_paths.facts_records_dir / f"{source_path.stem}.jsonl"), records)
        return md_name

    def _stage_mineru(self, workspace, source_path: Path) -> str:
        services = workspace.load_services()
        if not services.mineru_api_url:
            raise MineruClientError("mineru_api_url is required for this file type")
        target_original = workspace.repo_paths.raw_dir / source_path.name
        target_original.parent.mkdir(parents=True, exist_ok=True)
        target_original.write_bytes(source_path.read_bytes())
        client = MineruClient(services.mineru_api_url)
        result = client.parse_file(str(target_original))
        md_content = str(result.get("md_content", "")).strip()
        if not md_content:
            raise MineruClientError("MinerU returned empty md_content")
        md_name = f"{source_path.name}.md"
        (workspace.repo_paths.raw_dir / md_name).write_text(md_content, encoding="utf-8")
        return md_name
