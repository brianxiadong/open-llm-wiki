#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${ROOT_DIR}/dist/macos/OpenLLMWikiClient.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
PLIST_TEMPLATE="${ROOT_DIR}/packaging/macos/Info.plist.template"

rm -rf "${APP_DIR}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}"
cp "${PLIST_TEMPLATE}" "${CONTENTS_DIR}/Info.plist"

cat > "${MACOS_DIR}/OpenLLMWikiClient" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
python3 -m confidential_client.desktop "$@"
EOF
chmod +x "${MACOS_DIR}/OpenLLMWikiClient"

echo "Built macOS app bundle at ${APP_DIR}"
