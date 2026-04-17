from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path


def test_build_confidential_client_script_creates_launcher(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    build_dir = repo_root / "dist" / "confidential-client"
    if build_dir.exists():
        for path in sorted(build_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()

    env = dict(os.environ)
    env["PYTHON_BIN"] = "python3"
    subprocess.run(
        ["bash", str(repo_root / "scripts" / "build-confidential-client.sh")],
        cwd=repo_root,
        check=True,
        env=env,
    )

    launcher = build_dir / "run-client.sh"
    readme = build_dir / "README.txt"
    requirements = build_dir / "requirements.txt"
    default_services = build_dir / "default-services.json"

    assert launcher.exists()
    assert readme.exists()
    assert requirements.exists()
    assert default_services.exists()
    assert launcher.stat().st_mode & stat.S_IXUSR
    assert "confidential_client.desktop" in launcher.read_text(encoding="utf-8")
    assert "qdrant_url" in json.loads(default_services.read_text(encoding="utf-8"))


def test_binary_packaging_assets_exist():
    repo_root = Path(__file__).resolve().parents[1]
    spec_file = repo_root / "packaging" / "confidential-client.spec"
    build_script = repo_root / "scripts" / "build-confidential-client-binary.sh"
    macos_build = repo_root / "scripts" / "build-macos-app.sh"
    windows_build = repo_root / "scripts" / "build-windows-installer.ps1"
    sign_macos = repo_root / "scripts" / "sign-macos-client.sh"
    sign_windows = repo_root / "scripts" / "sign-windows-client.ps1"
    macos_plist = repo_root / "packaging" / "macos" / "Info.plist.template"
    windows_iss = repo_root / "packaging" / "windows" / "open-llm-wiki-client.iss"
    appcast = repo_root / "packaging" / "appcast.sample.json"

    assert spec_file.exists()
    assert build_script.exists()
    assert macos_build.exists()
    assert windows_build.exists()
    assert sign_macos.exists()
    assert sign_windows.exists()
    assert macos_plist.exists()
    assert windows_iss.exists()
    assert appcast.exists()
    spec_content = spec_file.read_text(encoding="utf-8")
    assert '"confidential_client" / "desktop.py"' in spec_content
    assert "default-services.json" in spec_content
    assert "pyinstaller" in build_script.read_text(encoding="utf-8").lower()
    assert "codesign" in sign_macos.read_text(encoding="utf-8")
    assert "signtool sign" in sign_windows.read_text(encoding="utf-8").lower()


def test_binary_packaging_spec_bootstraps_project_root():
    repo_root = Path(__file__).resolve().parents[1]
    spec_file = repo_root / "packaging" / "confidential-client.spec"
    content = spec_file.read_text(encoding="utf-8")

    assert "Path(SPECPATH).resolve().parent" in content
    assert "sys.path.insert(0, str(project_root))" in content
    assert "pathex=[str(project_root)]" in content


def test_binary_packaging_spec_uses_onedir_layout_for_windows_installer():
    repo_root = Path(__file__).resolve().parents[1]
    spec_file = repo_root / "packaging" / "confidential-client.spec"
    windows_iss = repo_root / "packaging" / "windows" / "open-llm-wiki-client.iss"
    spec_content = spec_file.read_text(encoding="utf-8")
    iss_content = windows_iss.read_text(encoding="utf-8")

    assert "exclude_binaries=True" in spec_content
    assert "COLLECT(" in spec_content
    assert 'name="open-llm-wiki-client"' in spec_content
    assert r'open-llm-wiki-client\*' in iss_content
