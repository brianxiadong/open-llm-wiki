"""Encrypted local repository for confidential knowledge bases."""

from __future__ import annotations

import io
import json
import shutil
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

from confidential_client.crypto import decrypt_bytes, derive_key, encrypt_bytes, generate_salt
from llmwiki_core.contracts import ConfidentialServices, LocalRepoPaths, RepoRef
from utils import DEFAULT_SCHEMA_MD, ensure_repo_dirs

_MANIFEST_VERSION = 1
_LOCAL_USERNAME = "_confidential"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_repo_numeric_id(repo_uuid: str) -> int:
    digest = sha256(repo_uuid.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


@dataclass(slots=True)
class RepositoryManifest:
    version: int
    repo_uuid: str
    repo_id: int
    name: str
    slug: str
    mode: str
    created_at: str
    updated_at: str
    kdf_salt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepositoryManifest":
        return cls(
            version=int(data["version"]),
            repo_uuid=str(data["repo_uuid"]),
            repo_id=int(data["repo_id"]),
            name=str(data["name"]),
            slug=str(data["slug"]),
            mode=str(data.get("mode", "confidential")),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            kdf_salt=str(data["kdf_salt"]),
        )


@dataclass(slots=True)
class UnlockedWorkspace:
    manifest: RepositoryManifest
    root_dir: Path

    @property
    def workspace_dir(self) -> Path:
        return self.root_dir / "workspace"

    @property
    def services_path(self) -> Path:
        return self.workspace_dir / "config" / "services.json"

    @property
    def history_path(self) -> Path:
        return self.workspace_dir / "state" / "history.json"

    @property
    def repo_paths(self) -> LocalRepoPaths:
        return LocalRepoPaths(
            data_dir=self.workspace_dir / "data",
            username=_LOCAL_USERNAME,
            repo_slug=self.manifest.slug,
        )

    @property
    def repo_ref(self) -> RepoRef:
        return RepoRef(id=self.manifest.repo_id, slug=self.manifest.slug, mode="confidential")

    def load_services(self) -> ConfidentialServices:
        return ConfidentialServices.from_dict(
            json.loads(self.services_path.read_text(encoding="utf-8"))
        )

    def save_services(self, services: ConfidentialServices) -> None:
        self.services_path.parent.mkdir(parents=True, exist_ok=True)
        self.services_path.write_text(
            json.dumps(services.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append_history(self, question: str, answer: str) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        history = self.load_history()
        history.append(
            {
                "question": question,
                "answer": answer,
                "created_at": _utc_now(),
            }
        )
        self.history_path.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_history(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        return json.loads(self.history_path.read_text(encoding="utf-8"))


class ConfidentialRepository:
    """Encrypted local repository bundle."""

    def __init__(self, repo_dir: str | Path) -> None:
        self.repo_dir = Path(repo_dir)
        self.manifest_path = self.repo_dir / "manifest.json"
        self.vault_path = self.repo_dir / "vault.bin"
        self._manifest = RepositoryManifest.from_dict(
            json.loads(self.manifest_path.read_text(encoding="utf-8"))
        )

    @property
    def manifest(self) -> RepositoryManifest:
        return self._manifest

    @classmethod
    def create(
        cls,
        repo_dir: str | Path,
        *,
        name: str,
        slug: str,
        passphrase: str,
        services: ConfidentialServices,
        schema_markdown: str | None = None,
        repo_uuid: str | None = None,
    ) -> "ConfidentialRepository":
        import uuid

        target = Path(repo_dir)
        target.mkdir(parents=True, exist_ok=True)
        repo_uuid = repo_uuid or str(uuid.uuid4())
        manifest = RepositoryManifest(
            version=_MANIFEST_VERSION,
            repo_uuid=repo_uuid,
            repo_id=_stable_repo_numeric_id(repo_uuid),
            name=name,
            slug=slug,
            mode="confidential",
            created_at=_utc_now(),
            updated_at=_utc_now(),
            kdf_salt=generate_salt(),
        )
        repo = cls.__new__(cls)
        repo.repo_dir = target
        repo.manifest_path = target / "manifest.json"
        repo.vault_path = target / "vault.bin"
        repo._manifest = manifest
        repo._write_manifest()
        with repo.unlocked(passphrase) as workspace:
            base = ensure_repo_dirs(
                str(workspace.repo_paths.data_dir),
                workspace.repo_paths.username,
                workspace.repo_paths.repo_slug,
            )
            workspace_root = Path(base)
            workspace.save_services(services)
            workspace.history_path.parent.mkdir(parents=True, exist_ok=True)
            workspace.history_path.write_text("[]", encoding="utf-8")
            (workspace_root / "wiki" / "schema.md").write_text(
                schema_markdown or DEFAULT_SCHEMA_MD,
                encoding="utf-8",
            )
            (workspace_root / "wiki" / "index.md").write_text(
                "---\ntitle: 首页\ntype: index\nupdated: 2026-04-16\n---\n\n# Wiki Index\n\n暂无页面。\n",
                encoding="utf-8",
            )
            (workspace_root / "wiki" / "log.md").write_text(
                (
                    "---\ntitle: Log\ntype: log\nupdated: 2026-04-16\n---\n\n"
                    "# Log\n\n## 初始化\n\n客户端机密知识库已创建。\n"
                ),
                encoding="utf-8",
            )
            (workspace_root / "wiki" / "overview.md").write_text(
                "---\ntitle: 概览\ntype: overview\nupdated: 2026-04-16\n---\n\n# 概览\n\n暂无内容。\n",
                encoding="utf-8",
            )
        return cls(target)

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self._manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _archive_workspace(self, workspace_dir: Path) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            tar.add(workspace_dir, arcname="workspace")
        return buffer.getvalue()

    def _extract_workspace(self, payload: bytes, target_dir: Path) -> None:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
            tar.extractall(target_dir, filter="data")

    @contextmanager
    def unlocked(self, passphrase: str) -> Iterator[UnlockedWorkspace]:
        root_dir = Path(tempfile.mkdtemp(prefix=f"conf_repo_{self._manifest.slug}_"))
        workspace_dir = root_dir / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        try:
            if self.vault_path.exists() and self.vault_path.stat().st_size > 0:
                key = derive_key(passphrase, self._manifest.kdf_salt)
                plain = decrypt_bytes(
                    self.vault_path.read_bytes(),
                    key=key,
                    aad=self._manifest.repo_uuid.encode("utf-8"),
                )
                self._extract_workspace(plain, root_dir)
            yield UnlockedWorkspace(manifest=self._manifest, root_dir=root_dir)
            key = derive_key(passphrase, self._manifest.kdf_salt)
            archive = self._archive_workspace(workspace_dir)
            encrypted = encrypt_bytes(
                archive,
                key=key,
                aad=self._manifest.repo_uuid.encode("utf-8"),
            )
            self.vault_path.write_bytes(encrypted)
            self._manifest.updated_at = _utc_now()
            self._write_manifest()
        finally:
            shutil.rmtree(root_dir, ignore_errors=True)

    def export_bundle(self, output_path: str | Path) -> Path:
        output = Path(output_path)
        with tarfile.open(output, mode="w:gz") as tar:
            tar.add(self.manifest_path, arcname="manifest.json")
            tar.add(self.vault_path, arcname="vault.bin")
        return output

    @classmethod
    def restore(cls, repo_dir: str | Path, bundle_path: str | Path) -> "ConfidentialRepository":
        target = Path(repo_dir)
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(bundle_path, mode="r:gz") as tar:
            tar.extractall(target, filter="data")
        return cls(target)
