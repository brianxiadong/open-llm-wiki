"""Update checking for the confidential desktop client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


def _normalize_version(version: str) -> tuple[int, ...]:
    cleaned = (version or "").strip().lstrip("v")
    parts = []
    for item in cleaned.split("."):
        try:
            parts.append(int(item))
        except ValueError:
            parts.append(0)
    return tuple(parts)


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    update_available: bool
    channel: str
    download_url: str
    signature: str
    notes: str
    pub_date: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "channel": self.channel,
            "download_url": self.download_url,
            "signature": self.signature,
            "notes": self.notes,
            "pub_date": self.pub_date,
        }


def check_for_updates(
    manifest_url: str,
    *,
    current_version: str,
    channel: str,
    platform_name: str,
) -> UpdateCheckResult:
    response = httpx.get(manifest_url, timeout=10)
    response.raise_for_status()
    payload = response.json()
    channel_data = (payload.get("channels") or {}).get(channel) or {}
    latest_version = str(channel_data.get("version") or current_version)
    platform_asset = (channel_data.get("platforms") or {}).get(platform_name) or {}
    return UpdateCheckResult(
        current_version=current_version,
        latest_version=latest_version,
        update_available=_normalize_version(latest_version) > _normalize_version(current_version),
        channel=channel,
        download_url=str(platform_asset.get("url") or ""),
        signature=str(platform_asset.get("signature") or ""),
        notes=str(channel_data.get("notes") or ""),
        pub_date=str(channel_data.get("pub_date") or ""),
    )
