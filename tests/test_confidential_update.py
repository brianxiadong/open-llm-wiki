from __future__ import annotations

from unittest.mock import MagicMock, patch

from confidential_client.controller import ConfidentialClientController
from confidential_client.manager import ClientWorkspaceManager
from confidential_client.update import check_for_updates


def test_check_for_updates_detects_newer_version():
    payload = {
        "channels": {
            "stable": {
                "version": "0.3.0",
                "pub_date": "2026-04-16",
                "notes": "New features",
                "platforms": {
                    "darwin": {"url": "https://example.com/mac.dmg", "signature": "sig"},
                },
            }
        }
    }
    with patch("confidential_client.update.httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = check_for_updates(
            "https://example.com/appcast.json",
            current_version="0.2.0",
            channel="stable",
            platform_name="darwin",
        )

    assert result.update_available is True
    assert result.latest_version == "0.3.0"
    assert result.download_url == "https://example.com/mac.dmg"


def test_manager_persists_client_settings(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    saved = manager.save_client_settings(
        {
            "update_manifest_url": "https://updates.example.com/appcast.json",
            "update_channel": "beta",
        }
    )
    loaded = manager.load_client_settings()

    assert saved["update_channel"] == "beta"
    assert loaded["update_manifest_url"] == "https://updates.example.com/appcast.json"


def test_controller_check_for_updates_uses_settings(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    manager.save_client_settings(
        {
            "update_manifest_url": "https://updates.example.com/appcast.json",
            "update_channel": "stable",
        }
    )
    controller = ConfidentialClientController(manager)
    with patch("confidential_client.controller.check_for_updates") as mock_check:
        mock_check.return_value = MagicMock(to_dict=lambda: {})
        controller.check_for_updates()

    assert mock_check.called
