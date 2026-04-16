#!/usr/bin/env bash
set -euo pipefail

APP_PATH="${1:-dist/macos/OpenLLMWikiClient.app}"
IDENTITY="${CODESIGN_IDENTITY:-}"
ENTITLEMENTS="${CODESIGN_ENTITLEMENTS:-}"

if [[ -z "${IDENTITY}" ]]; then
  echo "CODESIGN_IDENTITY is not set" >&2
  exit 1
fi

if [[ -n "${ENTITLEMENTS}" ]]; then
  codesign --force --deep --timestamp --options runtime --entitlements "${ENTITLEMENTS}" --sign "${IDENTITY}" "${APP_PATH}"
else
  codesign --force --deep --timestamp --options runtime --sign "${IDENTITY}" "${APP_PATH}"
fi

echo "Signed macOS client: ${APP_PATH}"
