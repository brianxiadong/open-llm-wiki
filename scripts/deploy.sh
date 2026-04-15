#!/usr/bin/env bash
# 部署脚本：打包代码并推送到服务器，不覆盖服务器上的 .env
# 凭据：在项目根目录 .env 中配置 DEPLOY_HOST / DEPLOY_PORT / DEPLOY_USER / DEPLOY_PASSWORD
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$PROJECT_ROOT/.env"
  set +a
fi

SERVER_HOST="${DEPLOY_HOST:-172.36.164.85}"
SERVER_PORT="${DEPLOY_PORT:-2234}"
SERVER_USER="${DEPLOY_USER:-root}"
SERVER_PATH="${DEPLOY_PATH:-/opt/open-llm-wiki}"
TARBALL="/tmp/llmwiki-deploy.tar.gz"

if [ -z "${DEPLOY_PASSWORD:-}" ]; then
  echo "错误: 请在 .env 中设置 DEPLOY_PASSWORD（或导出环境变量）" >&2
  exit 1
fi

echo "→ 打包代码..."
tar czf "$TARBALL" \
  --exclude=".venv" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude=".env" \
  --exclude="data" \
  --exclude=".git" \
  --exclude="**/._*" \
  --exclude="*.tar.gz" \
  -C "$PROJECT_ROOT" .

echo "→ 上传到服务器..."
sshpass -p "$DEPLOY_PASSWORD" scp -o StrictHostKeyChecking=no -P "$SERVER_PORT" \
  "$TARBALL" "${SERVER_USER}@${SERVER_HOST}:/tmp/"

echo "→ 解压并重启..."
sshpass -p "$DEPLOY_PASSWORD" ssh -o StrictHostKeyChecking=no -p "$SERVER_PORT" \
  "${SERVER_USER}@${SERVER_HOST}" "
    cd $SERVER_PATH
    tar xzf /tmp/llmwiki-deploy.tar.gz --exclude='**/._*' 2>/dev/null
    install -m 644 deploy/llmwiki.service /etc/systemd/system/llmwiki.service
    .venv/bin/python manage.py migrate 2>&1 | grep -E '迁移|migration|error|Error' | head -10
    systemctl daemon-reload
    systemctl restart llmwiki
    sleep 3
    systemctl show llmwiki -p EnvironmentFiles | grep '/opt/open-llm-wiki/.env'
    curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:5000/health
  "

echo "✓ 部署完成"
