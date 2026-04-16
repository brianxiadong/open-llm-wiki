#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/dist/confidential-client"
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "${BUILD_DIR}"

cat > "${BUILD_DIR}/run-client.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"
python3 -m confidential_client.desktop "$@"
EOF
chmod +x "${BUILD_DIR}/run-client.sh"

cat > "${BUILD_DIR}/README.txt" <<EOF
Open LLM Wiki Confidential Client
=================================

Platform: ${PLATFORM}

Run:
  ./run-client.sh

Requirements:
  - Python 3.11+
  - pywebview available in the Python runtime
  - system WebView runtime available (macOS WebKit / Windows WebView2)
  - dependencies installed from requirements.txt

This package is a thin local launcher for the confidential client.
It includes a bundled default-services.json derived from the build machine config.
For a fully self-contained binary, add a PyInstaller/Nuitka stage later.
EOF

cp "${ROOT_DIR}/requirements.txt" "${BUILD_DIR}/requirements.txt"

ROOT_DIR_ENV="${ROOT_DIR}" BUILD_DIR_ENV="${BUILD_DIR}" "${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

from confidential_client.manager import default_services_from_server_config

root_dir = Path(os.environ["ROOT_DIR_ENV"])
build_dir = Path(os.environ["BUILD_DIR_ENV"])
local_defaults_path = root_dir / "packaging" / "client" / "default-services.local.json"
bundle_defaults_path = build_dir / "default-services.json"

if local_defaults_path.exists():
    bundle_defaults_path.write_text(local_defaults_path.read_text(encoding="utf-8"), encoding="utf-8")
else:
    bundle_defaults_path.write_text(
        json.dumps(default_services_from_server_config().to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
PY

echo "Built client launcher at ${BUILD_DIR}"
