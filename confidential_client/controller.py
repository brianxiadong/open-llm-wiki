"""High-level controller used by CLI and desktop GUI."""

from __future__ import annotations

import platform
from pathlib import Path

from confidential_client.health import check_services
from confidential_client.manager import ClientRepoSummary, ClientWorkspaceManager
from confidential_client.runtime import ConfidentialRuntime
from confidential_client.update import UpdateCheckResult, check_for_updates
from confidential_client.version import CLIENT_VERSION
from llmwiki_core.contracts import ConfidentialServices, QueryRunResult


class ConfidentialClientController:
    """Coordinates repository management and local runtime actions."""

    def __init__(self, manager: ClientWorkspaceManager | None = None) -> None:
        self.manager = manager or ClientWorkspaceManager()

    def list_repositories(self) -> list[ClientRepoSummary]:
        return self.manager.list_repositories()

    def create_repository(
        self,
        *,
        name: str,
        slug: str,
        passphrase: str,
        services: ConfidentialServices,
        schema_markdown: str | None = None,
    ) -> ClientRepoSummary:
        return self.manager.create_repository(
            name=name,
            slug=slug,
            passphrase=passphrase,
            services=services,
            schema_markdown=schema_markdown,
        )

    def import_repository(self, bundle_path: str | Path) -> ClientRepoSummary:
        return self.manager.import_repository(bundle_path)

    def export_repository(self, repo_uuid: str, output_path: str | Path | None = None) -> Path:
        return self.manager.export_repository(repo_uuid, output_path)

    def delete_repository(self, repo_uuid: str) -> None:
        self.manager.delete_repository(repo_uuid)

    def load_services(self, repo_uuid: str, passphrase: str) -> ConfidentialServices:
        return self.manager.load_services(repo_uuid, passphrase)

    def update_services(
        self,
        repo_uuid: str,
        *,
        passphrase: str,
        services: ConfidentialServices,
    ) -> ConfidentialServices:
        return self.manager.update_services(repo_uuid, passphrase=passphrase, services=services)

    def check_services(self, services: ConfidentialServices) -> dict:
        return check_services(services)

    def load_client_settings(self) -> dict[str, str]:
        return self.manager.load_client_settings()

    def save_client_settings(self, settings: dict[str, str]) -> dict[str, str]:
        return self.manager.save_client_settings(settings)

    def check_for_updates(
        self,
        *,
        manifest_url: str | None = None,
        channel: str | None = None,
    ) -> UpdateCheckResult:
        settings = self.load_client_settings()
        return check_for_updates(
            manifest_url or settings["update_manifest_url"],
            current_version=CLIENT_VERSION,
            channel=channel or settings["update_channel"],
            platform_name=platform.system().lower(),
        )

    def ingest_file(self, repo_uuid: str, passphrase: str, source_path: str | Path) -> list[dict]:
        runtime = ConfidentialRuntime(self.manager.get_repository(repo_uuid), passphrase)
        return runtime.ingest_file(source_path)

    def query(self, repo_uuid: str, passphrase: str, question: str) -> QueryRunResult:
        runtime = ConfidentialRuntime(self.manager.get_repository(repo_uuid), passphrase)
        history_rows = runtime.load_history()
        history = []
        for item in history_rows[-6:]:
            history.append({"role": "user", "content": item.get("question", "")})
            history.append({"role": "assistant", "content": item.get("answer", "")})
        return runtime.query(question, history=history)

    def history(self, repo_uuid: str, passphrase: str) -> list[dict]:
        runtime = ConfidentialRuntime(self.manager.get_repository(repo_uuid), passphrase)
        return runtime.load_history()
