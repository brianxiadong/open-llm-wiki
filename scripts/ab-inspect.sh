#!/usr/bin/env bash
# =============================================================
#  agent-browser 页面巡检脚本（Snapshot + 可选截图）
#
#  用法:
#    ./scripts/ab-inspect.sh                        # 默认巡检线上
#    ./scripts/ab-inspect.sh http://localhost:5001   # 巡检本地
#    ./scripts/ab-inspect.sh <url> <user> <pass>     # 指定账号
#
#  核心机制: 利用 agent-browser 的 snapshot -i 输出精简的
#  可交互元素列表（@refs），AI Agent 可据此操作页面。
#  每步仅 200-400 tokens，远低于传统 Playwright MCP 方案。
# =============================================================
set -uo pipefail

export AGENT_BROWSER_DEFAULT_TIMEOUT=60000

BASE_URL="${1:-http://172.36.164.85:5000}"
USER="${2:-uitest}"
PASS="${3:-test12345}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SHOT_DIR="$SCRIPT_DIR/../tests/screenshots"
mkdir -p "$SHOT_DIR"

log() { echo ""; echo "═══ $* ═══"; }

cleanup() { agent-browser close 2>/dev/null || true; }
trap cleanup EXIT

shot() {
  agent-browser screenshot "$1" 2>/dev/null && echo "  📸 $1" || echo "  ⚠ 截图跳过"
}

inspect() {
  local name="$1" url="$2" file="$3"
  log "$name"
  agent-browser open "$url" 2>&1
  agent-browser wait --load networkidle 2>&1
  sleep 1
  agent-browser snapshot -i 2>&1
  shot "$SHOT_DIR/$file"
  sleep 0.5
}

# ── 登录 ─────────────────────────────────────────────────────
log "登录页"
agent-browser open "$BASE_URL/login" 2>&1
agent-browser wait --load networkidle 2>&1
sleep 1
agent-browser snapshot -i 2>&1
shot "$SHOT_DIR/ab_01_login.png"

log "执行登录"
agent-browser fill 'input[name="username"]' "$USER" 2>&1
agent-browser fill 'input[name="password"]' "$PASS" 2>&1
agent-browser eval "document.querySelector('form').submit()" 2>&1
agent-browser wait --load networkidle 2>&1
sleep 1

agent-browser snapshot -i 2>&1
shot "$SHOT_DIR/ab_02_repos.png"

# ── 获取知识库 URL ───────────────────────────────────────────
REPO_URL=$(agent-browser eval "
  var a = document.querySelector('.repo-card-link');
  a ? a.href : ''
" 2>/dev/null | tr -d '"')

if [ -z "$REPO_URL" ]; then
  echo "⚠ 未找到知识库"; exit 0
fi

# ── 逐页巡检 ─────────────────────────────────────────────────
inspect "Dashboard"    "$REPO_URL"                 "ab_03_dashboard.png"
inspect "文档管理"      "${REPO_URL}/sources"       "ab_04_sources.png"
inspect "知识查询"      "${REPO_URL}/query"         "ab_05_query.png"
inspect "关系图谱"      "${REPO_URL}/graph"         "ab_06_graph.png"
inspect "Wiki 概览"     "${REPO_URL}/wiki/overview" "ab_07_wiki.png"
inspect "知识库设置"    "${REPO_URL}/settings"      "ab_08_settings.png"
inspect "用户设置"      "$BASE_URL/user/settings"   "ab_09_user_settings.png"
inspect "404 页面"      "$BASE_URL/not-found-xyz"   "ab_10_404.png"

# ── 汇总 ─────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════"
echo "  巡检完成！"
SHOTS=$(ls -1 "$SHOT_DIR"/ab_*.png 2>/dev/null | wc -l | tr -d ' ')
echo "  截图: ${SHOTS} 张 → $SHOT_DIR/"
echo "════════════════════════════════════════════"
