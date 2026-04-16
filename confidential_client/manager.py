"""Client-side workspace management for confidential repositories."""

from __future__ import annotations

import json
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from confidential_client.repository import ConfidentialRepository
from confidential_client.version import DEFAULT_UPDATE_CHANNEL, DEFAULT_UPDATE_MANIFEST_URL
from config import Config
from llmwiki_core.contracts import ConfidentialServices


def default_client_home() -> Path:
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        return home / "Library" / "Application Support" / "OpenLLMWikiClient"
    if system == "Windows":
        appdata = Path.home() / "AppData" / "Local"
        return appdata / "OpenLLMWikiClient"
    return home / ".open-llm-wiki-client"


def default_services_from_server_config() -> ConfidentialServices:
    return ConfidentialServices(
        llm_api_base=Config.LLM_API_BASE,
        llm_api_key=Config.LLM_API_KEY,
        llm_model=Config.LLM_MODEL,
        llm_max_tokens=Config.LLM_MAX_TOKENS,
        embedding_api_base=Config.EMBEDDING_API_BASE,
        embedding_api_key=Config.EMBEDDING_API_KEY,
        embedding_model=Config.EMBEDDING_MODEL,
        embedding_dimensions=Config.EMBEDDING_DIMENSIONS,
        qdrant_url=Config.QDRANT_URL,
        mineru_api_url=Config.MINERU_API_URL,
    )


@dataclass(frozen=True, slots=True)
class ClientRepoSummary:
    repo_uuid: str
    repo_id: int
    name: str
    slug: str
    mode: str
    storage_mode: str
    repo_dir: Path
    updated_at: str


class ClientWorkspaceManager:
    """Owns the local client home and repository inventory."""

    def __init__(self, client_home: str | Path | None = None) -> None:
        self.client_home = Path(client_home) if client_home else default_client_home()
        self.repos_dir = self.client_home / "repos"
        self.exports_dir = self.client_home / "exports"
        self.logs_dir = self.client_home / "logs"
        self.config_path = self.client_home / "config.json"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def list_repositories(self) -> list[ClientRepoSummary]:
        repos: list[ClientRepoSummary] = []
        for manifest_path in sorted(self.repos_dir.glob("*/manifest.json")):
            repo = ConfidentialRepository(manifest_path.parent)
            manifest = repo.manifest
            repos.append(
                ClientRepoSummary(
                    repo_uuid=manifest.repo_uuid,
                    repo_id=manifest.repo_id,
                    name=manifest.name,
                    slug=manifest.slug,
                    mode=manifest.mode,
                    storage_mode=manifest.storage_mode,
                    repo_dir=manifest_path.parent,
                    updated_at=manifest.updated_at,
                )
            )
        repos.sort(key=lambda item: item.updated_at, reverse=True)
        return repos

    def create_repository(
        self,
        *,
        name: str,
        slug: str,
        passphrase: str,
        services: ConfidentialServices,
        storage_mode: str = "encrypted",
        schema_markdown: str | None = None,
    ) -> ClientRepoSummary:
        import uuid

        repo_uuid = str(uuid.uuid4())
        repo_dir = self.repos_dir / repo_uuid
        repo = ConfidentialRepository.create(
            repo_dir,
            name=name,
            slug=slug,
            passphrase=passphrase,
            services=services,
            storage_mode=storage_mode,
            schema_markdown=schema_markdown,
            repo_uuid=repo_uuid,
        )
        return self._summary_for(repo)

    def import_repository(self, bundle_path: str | Path) -> ClientRepoSummary:
        import tempfile

        temp_dir = Path(tempfile.mkdtemp(prefix="conf_repo_import_", dir=self.repos_dir))
        repo = ConfidentialRepository.restore(temp_dir, bundle_path)
        final_dir = self.repos_dir / repo.manifest.repo_uuid
        if final_dir.exists():
            raise FileExistsError(f"Repository already exists: {repo.manifest.repo_uuid}")
        temp_dir.rename(final_dir)
        repo = ConfidentialRepository(final_dir)
        return self._summary_for(repo)

    def export_repository(self, repo_uuid: str, output_path: str | Path | None = None) -> Path:
        repo = self.get_repository(repo_uuid)
        destination = Path(output_path) if output_path else self.exports_dir / f"{repo.manifest.slug}.tgz"
        return repo.export_bundle(destination)

    def delete_repository(self, repo_uuid: str) -> None:
        repo_dir = self._find_repo_dir(repo_uuid)
        shutil.rmtree(repo_dir, ignore_errors=True)

    def get_repository(self, repo_uuid: str) -> ConfidentialRepository:
        repo_dir = self._find_repo_dir(repo_uuid)
        return ConfidentialRepository(repo_dir)

    def load_services(self, repo_uuid: str, passphrase: str) -> ConfidentialServices:
        repo = self.get_repository(repo_uuid)
        with repo.unlocked(passphrase) as workspace:
            return workspace.load_services()

    def update_services(
        self,
        repo_uuid: str,
        *,
        passphrase: str,
        services: ConfidentialServices,
    ) -> ConfidentialServices:
        repo = self.get_repository(repo_uuid)
        with repo.unlocked(passphrase) as workspace:
            workspace.save_services(services)
            return workspace.load_services()

    def load_client_settings(self) -> dict[str, str]:
        if not self.config_path.exists():
            return {
                "update_manifest_url": DEFAULT_UPDATE_MANIFEST_URL,
                "update_channel": DEFAULT_UPDATE_CHANNEL,
            }
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        return {
            "update_manifest_url": str(data.get("update_manifest_url") or DEFAULT_UPDATE_MANIFEST_URL),
            "update_channel": str(data.get("update_channel") or DEFAULT_UPDATE_CHANNEL),
        }

    def save_client_settings(self, settings: dict[str, str]) -> dict[str, str]:
        merged = {
            **self.load_client_settings(),
            **settings,
        }
        self.config_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return merged

    def load_default_services(self) -> ConfidentialServices:
        for path in self._default_services_candidates():
            if path.exists():
                return ConfidentialServices.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
        return default_services_from_server_config()

    def list_documents(self, repo_uuid: str, passphrase: str) -> list[dict]:
        repo = self.get_repository(repo_uuid)
        with repo.unlocked(passphrase) as workspace:
            documents = workspace.load_documents()
        documents.sort(
            key=lambda item: (
                str(item.get("updated_at") or item.get("last_ingested_at") or ""),
                str(item.get("filename") or ""),
            ),
            reverse=True,
        )
        return documents

    def _summary_for(self, repo: ConfidentialRepository) -> ClientRepoSummary:
        manifest = repo.manifest
        return ClientRepoSummary(
            repo_uuid=manifest.repo_uuid,
            repo_id=manifest.repo_id,
            name=manifest.name,
            slug=manifest.slug,
            mode=manifest.mode,
            storage_mode=manifest.storage_mode,
            repo_dir=repo.repo_dir,
            updated_at=manifest.updated_at,
        )

    def _find_repo_dir(self, repo_uuid: str) -> Path:
        candidate = self.repos_dir / repo_uuid
        if candidate.exists():
            return candidate
        for item in self.list_repositories():
            if item.repo_uuid == repo_uuid:
                return item.repo_dir
        raise FileNotFoundError(f"Repository not found: {repo_uuid}")

    def _default_services_candidates(self) -> list[Path]:
        project_root = Path(__file__).resolve().parent.parent
        module_dir = Path(__file__).resolve().parent
        executable_dir = Path(sys.executable).resolve().parent
        return [
            project_root / "packaging" / "client" / "default-services.local.json",
            module_dir / "default-services.local.json",
            executable_dir / "default-services.json",
            self.client_home / "private" / "default-services.json",
        ]
