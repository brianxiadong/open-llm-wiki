# -*- mode: python ; coding: utf-8 -*-

import json
import tempfile
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

from confidential_client.manager import default_services_from_server_config

project_root = Path.cwd()
bundle_defaults_dir = Path(tempfile.mkdtemp(prefix="open-llm-wiki-client-"))
bundle_defaults_path = bundle_defaults_dir / "default-services.json"
local_defaults_path = project_root / "packaging" / "client" / "default-services.local.json"

if local_defaults_path.exists():
    bundle_defaults_path.write_text(local_defaults_path.read_text(encoding="utf-8"), encoding="utf-8")
else:
    bundle_defaults_path.write_text(
        json.dumps(default_services_from_server_config().to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

hiddenimports = collect_submodules("confidential_client") + collect_submodules("llmwiki_core")

a = Analysis(
    ["confidential_client/desktop.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("static", "static"),
        (str(bundle_defaults_path), "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="open-llm-wiki-client",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
