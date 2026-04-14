#!/usr/bin/env bash
# llmwiki 恢复脚本
# 用法：bash scripts/restore.sh <备份目录路径>
# 示例：bash scripts/restore.sh /var/backups/llmwiki/20260414_030000
#
# 恢复步骤：
#   1. 停止服务
#   2. 恢复数据库（清空重建）
#   3. 恢复数据目录
#   4. 重启服务

set -euo pipefail

BACKUP_PATH="${1:-}"
if [[ -z "${BACKUP_PATH}" || ! -d "${BACKUP_PATH}" ]]; then
  echo "用法: bash scripts/restore.sh <备份目录路径>"
  echo "可用备份:"
  ls /var/backups/llmwiki/ 2>/dev/null | grep "^20" | sort -r | head -10
  exit 1
fi

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${APP_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a; source "${ENV_FILE}"; set +a
fi

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_NAME="${DB_NAME:-llmwiki}"
DB_USER="${DB_USER:-}"
DB_PASSWORD="${DB_PASSWORD:-}"
DATA_DIR="${DATA_DIR:-${APP_DIR}/data}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "=== llmwiki restore from: ${BACKUP_PATH} ==="

# 安全确认
echo ""
echo "  !! 警告：这将覆盖当前数据库和数据目录 !!"
echo "  备份路径: ${BACKUP_PATH}"
echo "  数据库:   ${DB_NAME}@${DB_HOST}"
echo "  数据目录: ${DATA_DIR}"
echo ""
read -r -p "确认恢复？输入 yes 继续: " CONFIRM
if [[ "${CONFIRM}" != "yes" ]]; then
  echo "已取消"
  exit 0
fi

# ── 1. 停止服务 ────────────────────────────────────────────────────────────
log "Stopping llmwiki service..."
systemctl stop llmwiki 2>/dev/null && log "Service stopped" || log "WARN: service not running"

# ── 2. 恢复数据库 ──────────────────────────────────────────────────────────
if [[ -f "${BACKUP_PATH}/db.sql.gz" ]]; then
  log "Restoring database ${DB_NAME}..."
  MYSQL_PWD="${DB_PASSWORD}" mysql \
    -h "${DB_HOST}" -P "${DB_PORT}" -u "${DB_USER}" \
    -e "DROP DATABASE IF EXISTS \`${DB_NAME}\`; CREATE DATABASE \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
  gunzip -c "${BACKUP_PATH}/db.sql.gz" | \
    MYSQL_PWD="${DB_PASSWORD}" mysql -h "${DB_HOST}" -P "${DB_PORT}" -u "${DB_USER}" "${DB_NAME}"
  log "Database restore done"
else
  log "WARN: db.sql.gz not found in backup, skipping database restore"
fi

# ── 3. 恢复数据目录 ────────────────────────────────────────────────────────
if [[ -f "${BACKUP_PATH}/data.tar.gz" ]]; then
  log "Restoring data directory to $(dirname "${DATA_DIR}")..."
  rm -rf "${DATA_DIR}"
  tar xzf "${BACKUP_PATH}/data.tar.gz" -C "$(dirname "${DATA_DIR}")"
  log "Data directory restore done"
else
  log "WARN: data.tar.gz not found in backup, skipping data restore"
fi

# ── 4. 重启服务 ────────────────────────────────────────────────────────────
log "Starting llmwiki service..."
systemctl start llmwiki
sleep 3
if systemctl is-active --quiet llmwiki; then
  log "=== restore complete, service is running ==="
else
  log "ERROR: service failed to start, check: journalctl -u llmwiki -n 50"
  exit 1
fi
