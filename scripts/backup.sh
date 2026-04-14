#!/usr/bin/env bash
# llmwiki 备份脚本：MySQL 数据库 + 知识库数据文件
# 用法：
#   手动：bash scripts/backup.sh
#   cron 每天凌晨 3 点：0 3 * * * /opt/open-llm-wiki/scripts/backup.sh >>/var/log/llmwiki-backup.log 2>&1
#
# 环境变量（从 .env 自动读取）：
#   DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD
#   BACKUP_DIR      备份目录，默认 /var/backups/llmwiki
#   BACKUP_RETAIN   保留天数，默认 7

set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${APP_DIR}/.env"

# 读 .env
if [[ -f "${ENV_FILE}" ]]; then
  set -a; source "${ENV_FILE}"; set +a
fi

BACKUP_DIR="${BACKUP_DIR:-/var/backups/llmwiki}"
BACKUP_RETAIN="${BACKUP_RETAIN:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/${TIMESTAMP}"

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_NAME="${DB_NAME:-llmwiki}"
DB_USER="${DB_USER:-}"
DB_PASSWORD="${DB_PASSWORD:-}"
DATA_DIR="${DATA_DIR:-${APP_DIR}/data}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "=== llmwiki backup start ==="
log "Target: ${BACKUP_PATH}"

mkdir -p "${BACKUP_PATH}"

# ── 1. MySQL 备份 ──────────────────────────────────────────────────────────
log "Dumping database ${DB_NAME}..."
if ! command -v mysqldump >/dev/null 2>&1; then
  log "ERROR: mysqldump not found. Install: apt-get install -y default-mysql-client"
  exit 1
fi

MYSQL_PWD="${DB_PASSWORD}" mysqldump \
  -h "${DB_HOST}" -P "${DB_PORT}" \
  -u "${DB_USER}" \
  --single-transaction --routines --triggers \
  "${DB_NAME}" | gzip > "${BACKUP_PATH}/db.sql.gz"

DB_SIZE=$(du -sh "${BACKUP_PATH}/db.sql.gz" | cut -f1)
log "Database backup done: db.sql.gz (${DB_SIZE})"

# ── 2. 数据目录备份 ────────────────────────────────────────────────────────
if [[ -d "${DATA_DIR}" ]]; then
  log "Archiving data directory: ${DATA_DIR}..."
  tar czf "${BACKUP_PATH}/data.tar.gz" -C "$(dirname "${DATA_DIR}")" "$(basename "${DATA_DIR}")"
  DATA_SIZE=$(du -sh "${BACKUP_PATH}/data.tar.gz" | cut -f1)
  log "Data backup done: data.tar.gz (${DATA_SIZE})"
else
  log "WARN: DATA_DIR not found: ${DATA_DIR}, skipping"
fi

# ── 3. 写元数据文件 ────────────────────────────────────────────────────────
cat > "${BACKUP_PATH}/meta.txt" <<EOF
timestamp=${TIMESTAMP}
db_name=${DB_NAME}
data_dir=${DATA_DIR}
app_dir=${APP_DIR}
EOF

# ── 4. 清理过期备份 ────────────────────────────────────────────────────────
log "Removing backups older than ${BACKUP_RETAIN} days..."
find "${BACKUP_DIR}" -maxdepth 1 -type d -name "20*" \
  -mtime "+${BACKUP_RETAIN}" -exec rm -rf {} + 2>/dev/null || true

REMAINING=$(ls "${BACKUP_DIR}" | grep -c "^20" || true)
log "Remaining backup sets: ${REMAINING}"

TOTAL_SIZE=$(du -sh "${BACKUP_DIR}" | cut -f1)
log "=== backup complete: ${BACKUP_PATH} | total backup size: ${TOTAL_SIZE} ==="
