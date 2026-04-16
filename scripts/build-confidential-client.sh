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
  - tkinter available in the Python runtime
  - dependencies installed from requirements.txt

This package is a thin local launcher for the confidential client.
For a fully self-contained binary, add a PyInstaller/Nuitka stage later.
EOF

cp "${ROOT_DIR}/requirements.txt" "${BUILD_DIR}/requirements.txt"

echo "Built client launcher at ${BUILD_DIR}"
