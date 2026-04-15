#!/usr/bin/env bash
# =============================================================
#  agent-browser E2E 测试
#
#  用法:
#    ./scripts/ab-e2e-test.sh                          # 测试线上
#    ./scripts/ab-e2e-test.sh http://localhost:5000     # 测试本地
#    ./scripts/ab-e2e-test.sh <url> <user> <pass>       # 指定账号
#
#  每个 test_* 函数是一个测试用例，有 PASS/FAIL 输出。
#  失败时自动截图保存到 tests/screenshots/fail_*.png
# =============================================================
set -uo pipefail

export AGENT_BROWSER_DEFAULT_TIMEOUT=60000

BASE_URL="${1:-http://172.36.164.85:5000}"
USER="${2:-e2e_$$}"
EMAIL="${USER}@example.com"
PASS="${3:-e2ePass1234}"
REPO_SLUG="${AB_E2E_REPO_SLUG:-ab-test-$$}"
USE_EXISTING_USER=0
[ "$#" -ge 2 ] && USE_EXISTING_USER=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SHOT_DIR="$SCRIPT_DIR/../tests/screenshots"
mkdir -p "$SHOT_DIR"

PASSED=0
FAILED=0
ERRORS=""
USER_REGISTERED=0
REPO_CREATED=0

# ── 工具函数 ─────────────────────────────────────────────────

cleanup_test_user() {
  [ "${USER_REGISTERED:-0}" -eq 1 ] || return 0
  nav "$BASE_URL/user/settings"
  agent-browser fill 'input[name="confirm_username"]' "$USER" 2>/dev/null || true
  agent-browser fill 'input[name="delete_password"]' "$PASS" 2>/dev/null || true
  agent-browser click '[data-testid="delete-account-submit"]' 2>/dev/null || true
  agent-browser wait --load networkidle 2>/dev/null || true
}

cleanup_test_repo() {
  [ "${USER_REGISTERED:-0}" -eq 1 ] && return 0
  [ "${REPO_CREATED:-0}" -eq 1 ] || return 0
  nav "$BASE_URL/$USER/$REPO_SLUG/settings"
  if ! assert_url_contains "$REPO_SLUG" "清理仓库进入设置页"; then
    nav "$BASE_URL/login"
    agent-browser fill 'input[name="username"]' "$USER" 2>/dev/null || true
    agent-browser fill 'input[name="password"]' "$PASS" 2>/dev/null || true
    agent-browser click 'button[type="submit"]' 2>/dev/null || true
    agent-browser wait --load networkidle 2>/dev/null || true
    nav "$BASE_URL/$USER/$REPO_SLUG/settings"
  fi
  agent-browser eval "
    (function () {
      var form = document.querySelector('form[action$=\"/$USER/$REPO_SLUG/delete\"]');
      if (!form) return false;
      form.submit();
      return true;
    })();
  " 2>/dev/null || true
  agent-browser wait --load networkidle 2>/dev/null || true
}

cleanup() {
  cleanup_test_repo
  cleanup_test_user
  agent-browser close 2>/dev/null || true
}
trap cleanup EXIT

fail_shot() {
  agent-browser screenshot "$SHOT_DIR/fail_${1}.png" 2>/dev/null || true
}

# 断言：页面 URL 包含指定字符串
assert_url_contains() {
  local expected="$1" label="${2:-URL check}"
  local actual
  actual=$(agent-browser get url 2>/dev/null | tr -d '"')
  if [[ "$actual" == *"$expected"* ]]; then
    return 0
  else
    echo "    ✗ $label: URL 期望包含 '$expected', 实际 '$actual'"
    return 1
  fi
}

# 断言：元素存在且数量 >= min
assert_count_gte() {
  local selector="$1" min="$2" label="${3:-count check}"
  local count
  count=$(agent-browser get count "$selector" 2>/dev/null | tr -d '"' | grep -oE '[0-9]+' | head -1)
  count=${count:-0}
  if [ "$count" -ge "$min" ]; then
    return 0
  else
    echo "    ✗ $label: '$selector' 期望 >= $min 个, 实际 $count 个"
    return 1
  fi
}

# 断言：元素存在（count >= 1）
assert_exists() {
  assert_count_gte "$1" 1 "${2:-element exists}"
}

# 断言：元素文本包含指定字符串
assert_text_contains() {
  local selector="$1" expected="$2" label="${3:-text check}"
  local actual
  actual=$(agent-browser get text "$selector" 2>/dev/null)
  if [[ "$actual" == *"$expected"* ]]; then
    return 0
  else
    echo "    ✗ $label: 期望包含 '$expected', 实际 '${actual:0:80}'"
    return 1
  fi
}

# 断言：元素可见（宽高 > 0）
assert_visible() {
  local selector="$1" label="${2:-visible check}"
  assert_eval "
    var el = document.querySelector('$selector');
    el && el.offsetWidth > 0 && el.offsetHeight > 0;
  " "$label"
}

# 断言：JS 表达式返回 truthy
assert_eval() {
  local js="$1" label="${2:-eval check}"
  local result
  result=$(agent-browser eval "$js" 2>/dev/null | tr -d '"')
  if [ "$result" = "true" ] || [ "$result" = "1" ]; then
    return 0
  else
    echo "    ✗ $label: JS 返回 '$result'"
    return 1
  fi
}

# 导航到 URL 并等待加载
nav() {
  agent-browser open "$1" 2>/dev/null
  agent-browser wait --load networkidle 2>/dev/null
  sleep 0.8
}

submit_form() {
  local selector="${1:-form}"
  agent-browser eval "
    (function () {
      var form = document.querySelector('$selector');
      if (!form) return false;
      if (form.requestSubmit) form.requestSubmit();
      else form.submit();
      return true;
    })();
  " 2>/dev/null
}

# 运行一个测试
run_test() {
  local name="$1"
  shift
  echo -n "  ▸ $name ... "
  if "$@" 2>/dev/null; then
    echo "✓ PASS"
    PASSED=$((PASSED + 1))
  else
    echo "✗ FAIL"
    fail_shot "$name"
    FAILED=$((FAILED + 1))
    ERRORS="$ERRORS\n  ✗ $name"
  fi
}

# ── 测试用例 ─────────────────────────────────────────────────

test_login_page() {
  nav "$BASE_URL/login"
  assert_text_contains "h2" "欢迎回来" "标题" &&
  assert_exists 'input[name="username"]' "用户名输入框" &&
  assert_exists 'input[name="password"]' "密码输入框" &&
  assert_exists 'button[type="submit"]' "提交按钮"
}

test_register_page() {
  nav "$BASE_URL/register"
  assert_text_contains "h2" "创建账号" "标题" &&
  assert_exists 'input[name="confirm_password"]' "确认密码框"
}

test_register_user() {
  [ "${USE_EXISTING_USER:-0}" -eq 1 ] && return 0
  nav "$BASE_URL/register"
  agent-browser fill 'input[name="username"]' "$USER" 2>/dev/null
  agent-browser fill 'input[name="email"]' "$EMAIL" 2>/dev/null || true
  agent-browser fill 'input[name="display_name"]' "E2E测试" 2>/dev/null
  agent-browser fill 'input[name="password"]' "$PASS" 2>/dev/null
  agent-browser fill 'input[name="confirm_password"]' "$PASS" 2>/dev/null
  submit_form 'form' >/dev/null
  agent-browser wait --load networkidle 2>/dev/null
  sleep 1
  if assert_url_contains "$USER" "注册后跳转"; then
    USER_REGISTERED=1
    return 0
  fi
  return 1
}

test_login() {
  nav "$BASE_URL/login"
  agent-browser fill 'input[name="username"]' "$USER" 2>/dev/null
  agent-browser fill 'input[name="password"]' "$PASS" 2>/dev/null
  submit_form 'form' >/dev/null
  agent-browser wait --load networkidle 2>/dev/null
  sleep 1
  assert_url_contains "$USER" "登录后跳转"
}

test_create_repo() {
  nav "$BASE_URL/repos/new"
  agent-browser fill 'input[name="name"]' "AB测试知识库" 2>/dev/null
  agent-browser fill 'input[name="slug"]' "$REPO_SLUG" 2>/dev/null
  agent-browser fill 'textarea[name="description"]' "agent-browser E2E" 2>/dev/null
  submit_form 'form' >/dev/null
  agent-browser wait --load networkidle 2>/dev/null
  sleep 1
  if assert_url_contains "$REPO_SLUG" "创建后跳转"; then
    REPO_CREATED=1
    return 0
  fi
  return 1
}

test_dashboard_layout() {
  nav "$BASE_URL/$USER/$REPO_SLUG"
  assert_exists ".kb-sidebar" "左侧栏" &&
  assert_exists ".kb-chat" "对话区" &&
  assert_exists "#chat-input" "聊天输入框"
}

test_dashboard_upload_zone() {
  nav "$BASE_URL/$USER/$REPO_SLUG"
  assert_exists ".kb-upload-zone" "上传区域"
}

test_dashboard_icons_render() {
  nav "$BASE_URL/$USER/$REPO_SLUG"
  assert_eval "document.querySelectorAll('svg.lucide').length >= 4" "SVG 图标渲染"
}

test_dashboard_more_menu() {
  nav "$BASE_URL/$USER/$REPO_SLUG"
  assert_eval "
    var menu = document.querySelector('.kb-more-menu');
    menu && menu.querySelectorAll('a').length >= 4;
  " "更多菜单包含次要功能"
}

test_sources_empty() {
  nav "$BASE_URL/$USER/$REPO_SLUG/sources"
  assert_exists ".empty-state" "空状态提示" &&
  assert_exists ".upload-card" "上传卡片"
}

test_upload_selection_state() {
  nav "$BASE_URL/$USER/$REPO_SLUG/sources"
  local tmpfile="/tmp/ab-e2e-select-$$.md"
  echo -e "# Selected File\n\nPending upload state.\n" > "$tmpfile"
  agent-browser upload '#file-input' "$tmpfile" 2>/dev/null
  sleep 0.5
  local ok=0
  assert_visible "#file-selected" "已选文件态可见" &&
  assert_visible '#file-selected button[type="submit"]' "确认上传按钮可见" &&
  assert_eval "
    var drop = document.getElementById('drop-zone');
    drop && getComputedStyle(drop).display === 'none';
  " "拖拽区隐藏" &&
  assert_eval "
    var txt = document.getElementById('file-name-display');
    txt && txt.textContent.indexOf('ab-e2e-select-$$.md') !== -1;
  " "文件名显示正确" &&
  ok=1
  rm -f "$tmpfile"
  [ "$ok" -eq 1 ]
}

test_url_import_disclosure() {
  nav "$BASE_URL/$USER/$REPO_SLUG/sources"
  agent-browser click '.url-import-summary' 2>/dev/null
  sleep 0.4
  assert_eval "
    var details = document.querySelector('.url-import-section');
    var input = document.querySelector('.url-import-form input[name=\"url\"]');
    details && details.open && input && input.offsetHeight > 0;
  " "URL 导入展开后表单可见"
}

test_upload_file() {
  nav "$BASE_URL/$USER/$REPO_SLUG/sources"
  # 创建临时文件
  local tmpfile="/tmp/ab-e2e-test-doc.md"
  echo -e "# E2E Test Doc\n\nContent for agent-browser testing.\n\n## Key Points\n\n- Point A\n- Point B" > "$tmpfile"
  agent-browser upload 'input[type="file"]' "$tmpfile" 2>/dev/null
  sleep 0.5
  agent-browser click '.upload-card button[type="submit"]' 2>/dev/null
  agent-browser wait --load networkidle 2>/dev/null
  sleep 1
  rm -f "$tmpfile"
  assert_url_contains "/sources" "上传后跳转" &&
  assert_eval "document.body.innerText.includes('ab-e2e-test-doc.md')" "文件出现在列表"
}

test_upload_auto_queues_task() {
  nav "$BASE_URL/$USER/$REPO_SLUG/tasks"
  assert_eval "
    var body = document.body.innerText;
    body.includes('ab-e2e-test-doc') && (
      body.includes('排队') || body.includes('处理') ||
      body.includes('完成') || body.includes('失败') ||
      body.includes('queued') || body.includes('running') ||
      body.includes('done') || body.includes('ingest')
    );
  " "任务队列中存在摄入任务"
}

test_batch_actions_state() {
  nav "$BASE_URL/$USER/$REPO_SLUG/sources"
  assert_eval "
    var del = document.getElementById('batch-delete-btn');
    var ingest = document.getElementById('batch-ingest-btn');
    del && del.disabled && ingest && ingest.disabled;
  " "批量按钮默认禁用" || return 1
  agent-browser click '.source-cb' 2>/dev/null || return 1
  sleep 0.4
  assert_eval "
    var del = document.getElementById('batch-delete-btn');
    var ingest = document.getElementById('batch-ingest-btn');
    del && !del.disabled && ingest && !ingest.disabled &&
      del.innerText.indexOf('(1)') !== -1 &&
      ingest.innerText.indexOf('(1)') !== -1;
  " "勾选后批量按钮启用并显示数量"
}

test_task_queue_page() {
  nav "$BASE_URL/$USER/$REPO_SLUG/tasks"
  assert_text_contains "h2" "任务队列" "页面标题" &&
  assert_exists "table" "任务表格"
}

test_no_nested_forms() {
  nav "$BASE_URL/$USER/$REPO_SLUG/sources"
  assert_eval "
    var forms = document.querySelectorAll('form');
    var nested = false;
    forms.forEach(function(f) {
      if (f.querySelector('form')) nested = true;
    });
    !nested;
  " "无嵌套 form"
}

test_wiki_overview() {
  nav "$BASE_URL/$USER/$REPO_SLUG/wiki/overview"
  assert_exists ".wiki-page-layout" "页面布局" &&
  assert_exists ".rendered-content" "渲染内容"
}

test_graph_page() {
  nav "$BASE_URL/$USER/$REPO_SLUG/graph"
  assert_exists "#graph-container" "图谱容器"
}

test_query_page() {
  nav "$BASE_URL/$USER/$REPO_SLUG/query"
  assert_exists "#query-input" "查询输入框" &&
  assert_exists "#query-submit" "查询按钮" &&
  assert_visible "#query-input" "输入框可见"
}

test_dashboard_chat_input() {
  nav "$BASE_URL/$USER/$REPO_SLUG"
  assert_exists "#chat-input" "对话输入框" &&
  assert_exists "#chat-submit" "发送按钮" &&
  assert_visible "#chat-input" "输入框可见"
}

test_repo_settings() {
  nav "$BASE_URL/$USER/$REPO_SLUG/settings"
  assert_exists 'input[name="name"]' "名称输入框" &&
  assert_exists 'textarea' "描述文本框"
}

test_user_settings() {
  nav "$BASE_URL/user/settings"
  assert_exists 'input[name="display_name"]' "显示名称" &&
  assert_exists 'input[name="confirm_username"]' "删除账号确认输入框"
}

test_health_endpoint() {
  nav "$BASE_URL/health"
  assert_eval "document.body.innerText.includes('status')" "返回 JSON"
}

test_404_page() {
  nav "$BASE_URL/not-found-e2e-xyz"
  assert_exists ".error-page" "错误页面"
}

test_no_broken_layout() {
  nav "$BASE_URL/$USER/$REPO_SLUG"
  assert_eval "
    var sb = document.querySelector('.kb-sidebar');
    var chat = document.querySelector('.kb-chat');
    sb && sb.offsetWidth > 150 && chat && chat.offsetWidth > 400;
  " "布局尺寸正常"
}

test_breadcrumb_navigation() {
  nav "$BASE_URL/$USER/$REPO_SLUG/wiki/overview"
  assert_count_gte ".breadcrumb li" 3 "面包屑 >= 3 级"
}

test_nav_brand_link() {
  nav "$BASE_URL/$USER/$REPO_SLUG"
  agent-browser click ".brand" 2>/dev/null
  agent-browser wait --load networkidle 2>/dev/null
  sleep 0.5
  assert_url_contains "$USER" "Logo 跳转仓库列表"
}

# ── 主流程 ───────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  agent-browser E2E 测试                          ║"
echo "║  目标: $BASE_URL"
echo "║  用户: $USER"
echo "╚══════════════════════════════════════════════════╝"
echo ""

echo "── 认证 ──"
run_test "登录页渲染"        test_login_page
run_test "注册页渲染"        test_register_page
run_test "注册用户"          test_register_user
run_test "登录"              test_login

echo ""
echo "── 仓库管理 ──"
run_test "创建知识库"        test_create_repo
run_test "Dashboard 布局"   test_dashboard_layout
run_test "上传区域"          test_dashboard_upload_zone
run_test "SVG 图标渲染"      test_dashboard_icons_render
run_test "更多菜单"          test_dashboard_more_menu
run_test "布局尺寸检查"      test_no_broken_layout

echo ""
echo "── 文档管理 ──"
run_test "空文档列表"        test_sources_empty
run_test "上传选择态"        test_upload_selection_state
run_test "URL 导入展开"      test_url_import_disclosure
run_test "上传文件"          test_upload_file
run_test "批量按钮状态"      test_batch_actions_state
run_test "上传自动排队"      test_upload_auto_queues_task
run_test "无嵌套 form"       test_no_nested_forms

echo ""
echo "── 任务队列 ──"
run_test "任务队列页面"      test_task_queue_page

echo ""
echo "── Wiki 浏览 ──"
run_test "Wiki 概览页"       test_wiki_overview
run_test "关系图谱"          test_graph_page
run_test "面包屑导航"        test_breadcrumb_navigation

echo ""
echo "── 查询与设置 ──"
run_test "查询页面"          test_query_page
run_test "Dashboard 对话框"  test_dashboard_chat_input
run_test "仓库设置"          test_repo_settings
run_test "用户设置"          test_user_settings

echo ""
echo "── 其他 ──"
run_test "健康检查 API"      test_health_endpoint
run_test "404 错误页"        test_404_page
run_test "Logo 导航"         test_nav_brand_link

# ── 汇总 ─────────────────────────────────────────────────────

TOTAL=$((PASSED + FAILED))
echo ""
echo "══════════════════════════════════════════════════"
echo "  结果: $PASSED/$TOTAL 通过, $FAILED 失败"
if [ "$FAILED" -gt 0 ]; then
  echo -e "  失败用例:$ERRORS"
  echo "  失败截图: $SHOT_DIR/fail_*.png"
fi

SHOTS=$(ls -1 "$SHOT_DIR"/fail_*.png 2>/dev/null | wc -l | tr -d ' ')
[ "$SHOTS" -gt 0 ] && echo "  失败截图: $SHOTS 张"
echo "══════════════════════════════════════════════════"

exit "$FAILED"
