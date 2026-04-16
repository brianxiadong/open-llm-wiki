"""Confidential local runtime built on the shared engine."""

from __future__ import annotations

import csv
import shutil
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import openpyxl

from confidential_client.qdrant import ConfidentialQdrantService
from confidential_client.repository import ConfidentialRepository
from exceptions import MineruClientError
from llm_client import LLMClient
from llmwiki_core.contracts import QueryRunResult
from mineru_client import MineruClient
from utils import build_tabular_markdown_and_records, list_wiki_pages, render_markdown, write_jsonl
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
        qdrant = self._build_qdrant(workspace)
        return WikiEngine(llm, qdrant, str(workspace.repo_paths.data_dir))

    def _build_qdrant(self, workspace) -> ConfidentialQdrantService:
        services = workspace.load_services()
        return ConfidentialQdrantService(
            qdrant_map_path=workspace.repo_paths.qdrant_map_path,
            qdrant_url=services.qdrant_url,
            embedding_api_base=services.embedding_api_base,
            embedding_api_key=services.embedding_api_key,
            embedding_model=services.embedding_model,
            embedding_dimensions=services.embedding_dimensions,
        )

    def ingest_file(
        self,
        source_path: str | Path,
        on_event=None,
    ) -> list[dict[str, Any]]:
        source = Path(source_path)
        with self._repository.unlocked(self._passphrase) as workspace:
            self._save_document_state(
                workspace,
                source_name=source.name,
                source_path=source,
                status="processing",
                progress=0,
                progress_message="已接收文档，准备处理",
                processed_filename="",
            )
            target_name = ""
            try:
                target_name = self._stage_source(workspace, source)
                engine = self._build_engine(workspace)
                events: list[dict[str, Any]] = []
                def _record_event(event: dict[str, Any], *, collect: bool) -> None:
                    normalized = self._normalize_ingest_event(
                        source_name=source.name,
                        processed_filename=target_name,
                        event=event,
                    )
                    self._save_document_state(
                        workspace,
                        source_name=source.name,
                        source_path=source,
                        status=normalized["status"],
                        progress=normalized["progress"],
                        progress_message=normalized["message"],
                        processed_filename=target_name,
                        created_pages=list(normalized.get("created") or []),
                        updated_pages=list(normalized.get("updated") or []),
                    )
                    if collect:
                        events.append(normalized)
                    if on_event:
                        on_event(normalized)

                for event in engine.ingest(
                        workspace.repo_ref,
                        workspace.repo_paths.username,
                        target_name,
                        progress_callback=lambda event: _record_event(event, collect=False),
                    ):
                    _record_event(event, collect=True)
                return events
            except Exception as exc:
                self._save_document_state(
                    workspace,
                    source_name=source.name,
                    source_path=source,
                    status="failed",
                    progress=0,
                    progress_message=str(exc),
                    processed_filename=target_name,
                )
                if on_event:
                    on_event(
                        {
                            "phase": "error",
                            "progress": 0,
                            "message": str(exc),
                            "filename": source.name,
                            "processed_filename": target_name,
                            "status": "failed",
                            "updated_at": self._utc_now(),
                        }
                    )
                raise

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

    def list_documents(self) -> list[dict[str, Any]]:
        with self._repository.unlocked(self._passphrase) as workspace:
            documents = workspace.load_documents()
        documents.sort(
            key=lambda item: (
                str(item.get("updated_at") or item.get("last_ingested_at") or ""),
                str(item.get("filename") or ""),
            ),
            reverse=True,
        )
        return documents

    def delete_document(self, filename: str) -> list[dict[str, Any]]:
        target_name = str(filename or "").strip()
        if not target_name:
            raise ValueError("请提供要删除的文档名")
        with self._repository.unlocked(self._passphrase) as workspace:
            documents = workspace.load_documents()
            target = next((item for item in documents if item.get("filename") == target_name), None)
            if target is None:
                raise FileNotFoundError(f"文档不存在：{target_name}")

            qdrant = self._build_qdrant(workspace)
            repo_id = workspace.repo_ref.id
            processed_filename = str(target.get("processed_filename") or target_name)
            affected_pages = self._resolve_affected_pages(workspace, target)

            for page_filename in affected_pages:
                self._delete_wiki_page(workspace, qdrant, repo_id, page_filename)

            self._delete_source_artifacts(workspace, target_name, processed_filename)
            self._delete_fact_records(workspace, qdrant, repo_id, processed_filename)
            self._remove_ingest_log_entry(workspace, processed_filename)

            remaining_documents = [
                item for item in documents
                if item.get("filename") != target_name
            ]
            workspace.save_documents(remaining_documents)
            self._rebuild_index_and_overview(workspace, qdrant)

        return self.list_documents()

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
        parse_source, cleanup_dir = self._prepare_mineru_source(source_path)
        client = MineruClient(services.mineru_api_url)
        try:
            result = client.parse_file(str(parse_source))
            md_content = str(result.get("md_content", "")).strip()
            if not md_content:
                raise MineruClientError("MinerU returned empty md_content")
            md_name = f"{source_path.name}.md"
            (workspace.repo_paths.raw_dir / md_name).write_text(md_content, encoding="utf-8")
            return md_name
        finally:
            if cleanup_dir is not None:
                cleanup_dir.cleanup()

    def _prepare_mineru_source(self, source_path: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
        if source_path.suffix.lower() != ".doc":
            return source_path, None

        converter = shutil.which("textutil")
        if converter:
            return self._convert_doc_with_textutil(source_path, converter)

        converter = shutil.which("soffice") or shutil.which("libreoffice")
        if converter:
            return self._convert_doc_with_libreoffice(source_path, converter)

        raise MineruClientError(
            "当前环境无法直接解析 .doc 文件，请先转换为 .docx 或 PDF；"
            "或在本机安装 textutil / LibreOffice 以启用自动转换。"
        )

    def _convert_doc_with_textutil(
        self,
        source_path: Path,
        converter: str,
    ) -> tuple[Path, tempfile.TemporaryDirectory]:
        temp_dir = tempfile.TemporaryDirectory(prefix="confidential-doc-")
        output_path = Path(temp_dir.name) / f"{source_path.stem}.docx"
        try:
            subprocess.run(
                [converter, "-convert", "docx", "-output", str(output_path), str(source_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            temp_dir.cleanup()
            raise MineruClientError(f".doc 转换失败：{exc.stderr.strip() or exc.stdout.strip() or exc}") from exc
        if not output_path.exists():
            temp_dir.cleanup()
            raise MineruClientError(".doc 转换失败：未生成 docx 文件")
        return output_path, temp_dir

    def _convert_doc_with_libreoffice(
        self,
        source_path: Path,
        converter: str,
    ) -> tuple[Path, tempfile.TemporaryDirectory]:
        temp_dir = tempfile.TemporaryDirectory(prefix="confidential-doc-")
        output_path = Path(temp_dir.name) / f"{source_path.stem}.docx"
        try:
            subprocess.run(
                [converter, "--headless", "--convert-to", "docx", "--outdir", temp_dir.name, str(source_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            temp_dir.cleanup()
            raise MineruClientError(f".doc 转换失败：{exc.stderr.strip() or exc.stdout.strip() or exc}") from exc
        if not output_path.exists():
            temp_dir.cleanup()
            raise MineruClientError(".doc 转换失败：未生成 docx 文件")
        return output_path, temp_dir

    def _normalize_ingest_event(
        self,
        *,
        source_name: str,
        processed_filename: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        progress = int(event.get("progress", 0) or 0)
        phase = str(event.get("phase", "") or "")
        status = "processing"
        if phase == "done" or progress >= 100:
            status = "ready"
        elif phase == "error":
            status = "failed"
        return {
            **event,
            "filename": source_name,
            "processed_filename": processed_filename,
            "status": status,
            "updated_at": self._utc_now(),
        }

    def _resolve_affected_pages(self, workspace, document: dict[str, Any]) -> list[str]:
        pages = self._unique_markdown_names(document.get("affected_pages") or [])
        if pages:
            return pages
        created = self._unique_markdown_names(document.get("created_pages") or [])
        updated = self._unique_markdown_names(document.get("updated_pages") or [])
        pages = self._unique_markdown_names(created + updated)
        if pages:
            return pages
        processed_filename = str(document.get("processed_filename") or document.get("filename") or "")
        return self._logged_affected_pages(workspace, processed_filename)

    def _logged_affected_pages(self, workspace, processed_filename: str) -> list[str]:
        log_path = workspace.repo_paths.wiki_dir / "log.md"
        if not log_path.exists() or not processed_filename:
            return []
        current_source = ""
        created: list[str] = []
        updated: list[str] = []
        matches: list[str] = []
        for raw_line in log_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("## "):
                if current_source == processed_filename:
                    matches = self._unique_markdown_names(created + updated)
                current_source = self._logged_source_name(line)
                created = []
                updated = []
                continue
            if current_source != processed_filename:
                continue
            if line.startswith("- Created:"):
                created = self._split_logged_pages(line.split(":", 1)[1])
            elif line.startswith("- Updated:"):
                updated = self._split_logged_pages(line.split(":", 1)[1])
        if current_source == processed_filename:
            matches = self._unique_markdown_names(created + updated)
        return matches

    def _delete_wiki_page(self, workspace, qdrant, repo_id: int, filename: str) -> None:
        page_name = str(filename or "").strip()
        if not page_name:
            return
        if qdrant is not None:
            self._delete_page_vectors(qdrant, repo_id, page_name)
        page_path = workspace.repo_paths.wiki_dir / page_name
        if page_path.exists():
            page_path.unlink()

    def _delete_source_artifacts(self, workspace, source_filename: str, processed_filename: str) -> None:
        candidates = {
            workspace.repo_paths.raw_dir / source_filename,
            workspace.repo_paths.raw_dir / processed_filename,
        }
        for path in candidates:
            if path.exists() and path.is_file():
                path.unlink()
        facts_path = workspace.repo_paths.facts_records_dir / f"{Path(processed_filename).stem}.jsonl"
        if facts_path.exists():
            facts_path.unlink()

    def _delete_fact_records(self, workspace, qdrant, repo_id: int, processed_filename: str) -> None:
        if qdrant is None:
            return
        collection_name = qdrant._fact_collection_name(repo_id)
        if not qdrant._qdrant.collection_exists(collection_name=collection_name):
            return
        qdrant.delete_fact_records(repo_id, processed_filename)

    def _delete_page_vectors(self, qdrant, repo_id: int, filename: str) -> None:
        page_collection = qdrant._collection_name(repo_id)
        if qdrant._qdrant.collection_exists(collection_name=page_collection):
            qdrant.delete_page(repo_id, filename)
        chunk_collection = qdrant._chunk_collection_name(repo_id)
        if qdrant._qdrant.collection_exists(collection_name=chunk_collection):
            qdrant.delete_page_chunks(repo_id, filename)

    def _remove_ingest_log_entry(self, workspace, processed_filename: str) -> None:
        log_path = workspace.repo_paths.wiki_dir / "log.md"
        if not log_path.exists() or not processed_filename:
            return
        kept: list[str] = []
        skipping = False
        for raw_line in log_path.read_text(encoding="utf-8").splitlines():
            if raw_line.startswith("## "):
                skipping = self._logged_source_name(raw_line.strip()) == processed_filename
            if not skipping:
                kept.append(raw_line)
        content = "\n".join(kept).rstrip()
        if content:
            log_path.write_text(f"{content}\n", encoding="utf-8")

    def _rebuild_index_and_overview(self, workspace, qdrant) -> None:
        wiki_dir = workspace.repo_paths.wiki_dir
        pages = list_wiki_pages(str(wiki_dir))
        user_pages = [
            page for page in pages
            if page["filename"] not in {"index.md", "log.md", "schema.md", "overview.md"}
        ]

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for page in user_pages:
            grouped[str(page.get("type") or "unknown")].append(page)
        lines = [
            "---",
            "title: 首页",
            "type: index",
            f"updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "---",
            "",
            "# Wiki Index",
            "",
        ]
        if not user_pages:
            lines.append("暂无页面。")
        else:
            for page_type in sorted(grouped):
                lines.append(f"## {page_type}")
                lines.append("")
                for page in sorted(grouped[page_type], key=lambda item: item["title"]):
                    lines.append(f"- [{page['title']}]({page['filename']})")
                lines.append("")
        (wiki_dir / "index.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

        overview_lines = [
            "---",
            "title: 概览",
            "type: overview",
            f"updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "---",
            "",
            "# 概览",
            "",
            f"当前共 {len(user_pages)} 个知识页面。",
            "",
        ]
        if not user_pages:
            overview_lines.append("暂无内容。")
        else:
            overview_lines.append("## 页面列表")
            overview_lines.append("")
            for page in sorted(user_pages, key=lambda item: item["title"]):
                overview_lines.append(f"- {page['title']}（{page['filename']}）")
        overview_content = "\n".join(overview_lines).rstrip() + "\n"
        overview_path = wiki_dir / "overview.md"
        overview_path.write_text(overview_content, encoding="utf-8")
        if qdrant is not None:
            repo_id = workspace.repo_ref.id
            fm, _ = render_markdown(overview_content)
            qdrant.upsert_page(
                repo_id=repo_id,
                filename="overview.md",
                title=str(fm.get("title", "概览")),
                page_type="overview",
                content=overview_content,
            )
            qdrant.upsert_page_chunks(
                repo_id=repo_id,
                filename="overview.md",
                title=str(fm.get("title", "概览")),
                page_type="overview",
                content=overview_content,
            )

    def _logged_source_name(self, heading: str) -> str:
        marker = "Ingested `"
        if marker not in heading:
            return ""
        return heading.split(marker, 1)[1].split("`", 1)[0].strip()

    def _split_logged_pages(self, value: str) -> list[str]:
        text = str(value or "").strip()
        if not text or text.lower() == "none":
            return []
        return [item.strip() for item in text.split(",") if item.strip()]

    def _unique_markdown_names(self, names: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in names:
            name = str(raw or "").strip()
            if (
                not name
                or not name.endswith(".md")
                or name in {"index.md", "log.md", "schema.md", "overview.md"}
                or name in seen
            ):
                continue
            seen.add(name)
            out.append(name)
        return out

    def _save_document_state(
        self,
        workspace,
        *,
        source_name: str,
        source_path: Path,
        status: str,
        progress: int,
        progress_message: str,
        processed_filename: str,
        created_pages: list[str] | None = None,
        updated_pages: list[str] | None = None,
    ) -> None:
        documents = workspace.load_documents()
        for item in documents:
            if item.get("filename") == source_name:
                target = item
                break
        else:
            target = {
                "filename": source_name,
                "processed_filename": processed_filename,
                "file_ext": source_path.suffix.lower(),
                "size_bytes": 0,
                "size_display": "-",
                "status": "processing",
                "progress": 0,
                "progress_message": "",
                "created_at": self._utc_now(),
                "updated_at": self._utc_now(),
                "last_ingested_at": "",
                "created_pages": [],
                "updated_pages": [],
                "affected_pages": [],
            }
            documents.append(target)
        target["processed_filename"] = processed_filename
        target["file_ext"] = source_path.suffix.lower()
        size_bytes = source_path.stat().st_size if source_path.exists() else 0
        target["size_bytes"] = size_bytes
        target["size_display"] = f"{(size_bytes / 1024):.1f} KB" if size_bytes else "-"
        target["status"] = status
        target["progress"] = max(0, min(int(progress), 100))
        target["progress_message"] = progress_message
        target["updated_at"] = self._utc_now()
        if status == "ready":
            target["last_ingested_at"] = target["updated_at"]
        if created_pages is not None:
            target["created_pages"] = self._unique_markdown_names(created_pages)
        if updated_pages is not None:
            target["updated_pages"] = self._unique_markdown_names(updated_pages)
        target["affected_pages"] = self._unique_markdown_names(
            list(target.get("created_pages") or []) + list(target.get("updated_pages") or [])
        )
        workspace.save_documents(documents)

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
