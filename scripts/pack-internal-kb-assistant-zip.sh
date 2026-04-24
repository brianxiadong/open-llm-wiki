#!/usr/bin/env bash
# 打包「内部知识库使用小助手」Wiki，供设置页「导入 Wiki（ZIP）」上传。
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/internal-kb-assistant-wiki.zip}"
cd "$ROOT/seed/internal-kb-assistant"
zip -r "$OUT" wiki
echo "→ 已生成: $OUT"
