from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from confidential_client.cli import cli
from confidential_client.controller import ConfidentialClientController
from confidential_client.manager import ClientWorkspaceManager
from llmwiki_core.contracts import ConfidentialServices


def _services() -> ConfidentialServices:
    return ConfidentialServices(
        llm_api_base="http://fake-llm",
        llm_api_key="key",
        llm_model="test-model",
        llm_max_tokens=1024,
        embedding_api_base="http://fake-embed",
        embedding_api_key="",
        embedding_model="test-embed",
        embedding_dimensions=128,
        qdrant_url="http://fake-qdrant:6333",
        mineru_api_url="http://fake-mineru:8000",
    )


def _write_services_file(path: Path) -> None:
    path.write_text(json.dumps(_services().to_dict(), ensure_ascii=False), encoding="utf-8")


def test_workspace_manager_create_update_export_import_delete(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")

    summary = manager.create_repository(
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )
    repos = manager.list_repositories()
    assert len(repos) == 1
    assert repos[0].repo_uuid == summary.repo_uuid

    services = manager.load_services(summary.repo_uuid, "secret-pass")
    assert services.qdrant_url == "http://fake-qdrant:6333"

    updated = ConfidentialServices.from_dict(
        {**services.to_dict(), "mineru_api_url": "http://changed-mineru:8000"}
    )
    manager.update_services(summary.repo_uuid, passphrase="secret-pass", services=updated)
    assert manager.load_services(summary.repo_uuid, "secret-pass").mineru_api_url == "http://changed-mineru:8000"

    bundle_path = manager.export_repository(summary.repo_uuid)
    assert bundle_path.exists()

    manager.delete_repository(summary.repo_uuid)
    imported = manager.import_repository(bundle_path)
    assert imported.repo_uuid == summary.repo_uuid
    remaining = manager.list_repositories()
    assert len(remaining) == 1
    assert remaining[0].repo_uuid == imported.repo_uuid


def test_controller_query_builds_history(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    summary = manager.create_repository(
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )
    controller = ConfidentialClientController(manager)

    runtime = MagicMock()
    runtime.load_history.return_value = [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q2", "answer": "A2"},
    ]
    runtime.query.return_value = MagicMock(answer="A3", confidence={"level": "high"})

    with patch("confidential_client.controller.ConfidentialRuntime", return_value=runtime):
        controller.query(summary.repo_uuid, "secret-pass", "Q3")

    runtime.query.assert_called_once()
    history = runtime.query.call_args.kwargs["history"]
    assert history == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": "A2"},
    ]


def test_health_checks_report_service_status():
    with patch("confidential_client.health.httpx.get") as mock_get, \
         patch("confidential_client.health.OpenAI") as mock_openai:
        mock_get.return_value = MagicMock(status_code=200)
        embed_resp = MagicMock()
        embed_resp.data = [MagicMock(embedding=[0.1, 0.2])]
        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="pong"))]
        client = mock_openai.return_value
        client.embeddings.create.return_value = embed_resp
        client.chat.completions.create.return_value = llm_resp

        controller = ConfidentialClientController(ClientWorkspaceManager(Path("/tmp") / "client-health"))
        result = controller.check_services(_services())

    assert result["qdrant"]["ok"] is True
    assert result["mineru"]["ok"] is True
    assert result["embedding"]["ok"] is True
    assert result["llm"]["ok"] is True


def test_controller_client_settings_round_trip(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    controller = ConfidentialClientController(manager)

    saved = controller.save_client_settings(
        {
            "update_manifest_url": "https://updates.example.com/appcast.json",
            "update_channel": "beta",
        }
    )
    loaded = controller.load_client_settings()

    assert saved["update_channel"] == "beta"
    assert loaded["update_manifest_url"] == "https://updates.example.com/appcast.json"


def test_cli_list_import_and_health(tmp_path):
    services_path = tmp_path / "services.json"
    _write_services_file(services_path)
    repo_dir = tmp_path / "repo"
    bundle_path = tmp_path / "repo.tgz"

    runner = CliRunner()
    create_result = runner.invoke(
        cli,
        [
            "create",
            str(repo_dir),
            "--name",
            "CLI Repo",
            "--slug",
            "cli-repo",
            "--passphrase",
            "secret-pass",
            "--services-file",
            str(services_path),
        ],
    )
    assert create_result.exit_code == 0

    export_result = runner.invoke(cli, ["export", str(repo_dir), str(bundle_path)])
    assert export_result.exit_code == 0

    with patch("confidential_client.cli.ConfidentialClientController") as mock_controller:
        controller = mock_controller.return_value
        controller.list_repositories.return_value = [
            SimpleNamespace(repo_uuid="u1", name="R1", slug="s1", updated_at="2026-04-16T00:00:00Z")
        ]
        controller.import_repository.return_value = SimpleNamespace(repo_uuid="u2", name="R2", slug="s2")
        controller.check_services.return_value = {"qdrant": {"ok": True, "message": "HTTP 200"}}
        controller.check_for_updates.return_value = SimpleNamespace(
            to_dict=lambda: {"latest_version": "0.3.0", "update_available": True}
        )

        list_result = runner.invoke(cli, ["list"])
        import_result = runner.invoke(cli, ["import", str(bundle_path)])
        health_result = runner.invoke(cli, ["health", "--services-file", str(services_path)])
        version_result = runner.invoke(cli, ["version"])
        update_result = runner.invoke(cli, ["update-check", "--manifest-url", "https://updates.example.com/appcast.json"])

    assert list_result.exit_code == 0
    assert import_result.exit_code == 0
    assert health_result.exit_code == 0
    assert version_result.exit_code == 0
    assert update_result.exit_code == 0
    assert '"repo_uuid": "u1"' in list_result.output
    assert '"repo_uuid": "u2"' in import_result.output
    assert '"ok": true' in health_result.output.lower()
    assert "Open LLM Wiki Confidential Client" in version_result.output
    assert '"update_available": true' in update_result.output.lower()
