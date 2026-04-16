#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist/confidential-client-binary"
BUILD_DIR="${ROOT_DIR}/build/confidential-client-binary"
SPEC_FILE="${ROOT_DIR}/packaging/confidential-client.spec"
PYINSTALLER_BIN="${PYINSTALLER_BIN:-pyinstaller}"

if ! command -v "${PYINSTALLER_BIN}" >/dev/null 2>&1; then
  echo "pyinstaller not found. Install it in your build environment first." >&2
  exit 1
fi

rm -rf "${DIST_DIR}" "${BUILD_DIR}"
mkdir -p "${DIST_DIR}" "${BUILD_DIR}"

"${PYINSTALLER_BIN}" \
  --noconfirm \
  --clean \
  --distpath "${DIST_DIR}" \
  --workpath "${BUILD_DIR}" \
  "${SPEC_FILE}"

echo "Built binary client at ${DIST_DIR}"
