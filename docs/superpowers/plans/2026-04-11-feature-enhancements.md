# Feature Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完善和扩展 Open LLM Wiki 平台，按优先级实现 15 个缺失功能。

**Architecture:** Flask + Jinja2 SSR 单体应用，后台任务用 Python threading，数据库 MySQL + SQLAlchemy，文件存储本地磁盘，前端原生 JS（无框架）。

**Tech Stack:** Flask, SQLAlchemy, Jinja2, Qdrant, EasyMDE (markdown editor CDN), Python threading

---

## 项目结构说明

```
open-llm-wiki/
├── app.py              # 所有路由（单文件，按 Blueprint 分组）
├── wiki_engine.py      # WikiEngine: ingest / query / lint 核心逻辑
├── models.py           # User, Repo, Task ORM 模型
├── utils.py            # render_markdown, list_wiki_pages 等工具
├── config.py           # 配置
├── static/
│   ├── css/style.css   # 主样式
│   ├── css/chat.css    # 对话区样式
│   └── js/
│       ├── app.js      # 全局 JS（flash 关闭等）
│       └── chat.js     # 对话区 JS
├── templates/
│   ├── base.html
│   ├── wiki/page.html  # Wiki 页面视图
│   ├── repo/dashboard.html
│   └── ...
├── docs/design.md      # 设计文档（每次变更必须同步更新）
└── tests/              # pytest 测试
```

**关键规则（来自 AGENTS.md）：**
- 任何代码变更必须同步更新 `docs/design.md`
- 新增路由必须有对应路由测试（至少验证状态码）
- 数据库表变更需新建 `migrations/xxx.sql`
- 不加叙述性注释

---

## Task 1: Wiki 页面手动编辑

**Files:**
- Modify: `app.py` — 新增 `wiki.edit_page` (GET/POST) 和 `wiki.delete_page` (POST) 路由
- Modify: `templates/wiki/page.html` — 加编辑/删除按钮
- Create: `templates/wiki/edit.html` — EasyMDE 编辑器页面
- Modify: `static/css/style.css` — 编辑器样式（最少）
- Modify: `docs/design.md` — 更新路由表 §6.1 和页面列表 §6.2
- Modify: `tests/test_routes.py` — 新增编辑/删除路由测试

**规范：**
- GET `/<username>/<repo_slug>/wiki/<page_slug>/edit` → 显示编辑器（仅 owner）
- POST `/<username>/<repo_slug>/wiki/<page_slug>/edit` → 保存内容（覆写文件）并 upsert Qdrant
- POST `/<username>/<repo_slug>/wiki/<page_slug>/delete` → 删除文件 + Qdrant 向量
- 编辑器使用 EasyMDE（CDN），带预览功能
- 保存后 redirect 到 view_page

- [ ] **Step 1: 写路由测试（先让测试失败）**

```python
# tests/test_routes.py 新增
def test_edit_wiki_page_get(client, auth_user, sample_repo_with_page):
    username, repo_slug, page_slug = sample_repo_with_page
    rv = client.get(f"/{username}/{repo_slug}/wiki/{page_slug}/edit")
    assert rv.status_code == 200

def test_edit_wiki_page_post(client, auth_user, sample_repo_with_page):
    username, repo_slug, page_slug = sample_repo_with_page
    rv = client.post(
        f"/{username}/{repo_slug}/wiki/{page_slug}/edit",
        data={"content": "---\ntitle: Test\ntype: concept\n---\n\n# Test"},
        follow_redirects=True,
    )
    assert rv.status_code == 200

def test_delete_wiki_page(client, auth_user, sample_repo_with_page):
    username, repo_slug, page_slug = sample_repo_with_page
    rv = client.post(
        f"/{username}/{repo_slug}/wiki/{page_slug}/delete",
        follow_redirects=True,
    )
    assert rv.status_code == 200
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Volumes/mydata/codes/github/brianxiadong/open-llm-wiki
python -m pytest tests/test_routes.py -k "edit_wiki or delete_wiki" -v 2>&1 | head -40
```

Expected: FAIL (route not found, 404)

- [ ] **Step 3: 在 app.py 的 wiki_bp 中新增路由**

在 `wiki_bp` 的 `log_page` 路由之后（约第 701 行）新增：

```python
@wiki_bp.route("/<username>/<repo_slug>/wiki/<page_slug>/edit", methods=["GET", "POST"])
@login_required
def edit_page(username, repo_slug, page_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    _require_owner(repo)
    wiki_dir = os.path.join(
        get_repo_path(Config.DATA_DIR, username, repo_slug), "wiki"
    )
    filepath = os.path.join(wiki_dir, f"{page_slug}.md")
    if not os.path.isfile(filepath):
        abort(404)

    if request.method == "POST":
        content = request.form.get("content", "")
        if not content.strip():
            flash("内容不能为空", "error")
            return redirect(request.url)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        if current_app.qdrant:
            try:
                fm, _ = render_markdown(content)
                current_app.qdrant.upsert_page(
                    repo_id=repo.id,
                    filename=f"{page_slug}.md",
                    title=fm.get("title", page_slug),
                    page_type=fm.get("type", "unknown"),
                    content=content,
                )
            except Exception:
                logger.warning("Qdrant upsert failed for edited page %s", page_slug)
        _sync_repo_counts(repo, username)
        flash("页面已保存", "success")
        return redirect(
            url_for("wiki.view_page", username=username, repo_slug=repo_slug, page_slug=page_slug)
        )

    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()
    return render_template(
        "wiki/edit.html",
        username=username,
        repo=repo,
        page_slug=page_slug,
        content=raw,
    )


@wiki_bp.route("/<username>/<repo_slug>/wiki/<page_slug>/delete", methods=["POST"])
@login_required
def delete_page(username, repo_slug, page_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    _require_owner(repo)
    wiki_dir = os.path.join(
        get_repo_path(Config.DATA_DIR, username, repo_slug), "wiki"
    )
    filepath = os.path.join(wiki_dir, f"{page_slug}.md")
    if os.path.isfile(filepath):
        os.remove(filepath)
        if current_app.qdrant:
            try:
                current_app.qdrant.delete_page(repo.id, f"{page_slug}.md")
            except Exception:
                pass
        _sync_repo_counts(repo, username)
        flash(f"页面 {page_slug} 已删除", "success")
    else:
        flash("页面不存在", "error")
    return redirect(
        url_for("repo.dashboard", username=username, repo_slug=repo_slug)
    )
```

- [ ] **Step 4: 创建 templates/wiki/edit.html**

```html
{% extends "base.html" %}

{% block title %}编辑 {{ page_slug }} - Open LLM Wiki{% endblock %}

{% block head %}
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/easymde/dist/easymde.min.css">
{% endblock %}

{% block content %}
<nav aria-label="面包屑导航">
  <ul class="breadcrumb">
    <li><a href="{{ url_for('repo.dashboard', username=username, repo_slug=repo.slug) }}">{{ repo.name }}</a></li>
    <li><a href="{{ url_for('wiki.view_page', username=username, repo_slug=repo.slug, page_slug=page_slug) }}">{{ page_slug }}</a></li>
    <li>编辑</li>
  </ul>
</nav>

<div class="wiki-edit-container">
  <h2>编辑页面: {{ page_slug }}</h2>
  <form method="POST" id="edit-form">
    <textarea name="content" id="wiki-editor">{{ content }}</textarea>
    <div class="edit-actions">
      <button type="submit" class="btn-primary">
        <i class="lucide-save" aria-hidden="true"></i> 保存
      </button>
      <a href="{{ url_for('wiki.view_page', username=username, repo_slug=repo.slug, page_slug=page_slug) }}"
         class="btn-secondary">取消</a>
    </div>
  </form>
</div>
{% endblock %}

{% block scripts %}
<script src="https://cdn.jsdelivr.net/npm/easymde/dist/easymde.min.js"></script>
<script>
  var easyMDE = new EasyMDE({
    element: document.getElementById('wiki-editor'),
    spellChecker: false,
    autosave: { enabled: true, uniqueId: 'wiki-edit-{{ page_slug }}' },
    toolbar: ['bold','italic','heading','|','quote','unordered-list','ordered-list','|',
              'link','image','table','|','preview','side-by-side','fullscreen','|','guide'],
  });
</script>
{% endblock %}
```

- [ ] **Step 5: 在 templates/wiki/page.html 的 header 里加编辑/删除按钮**

在 `</header>` 前插入：
```html
{% if is_owner %}
<div class="page-actions">
  <a href="{{ url_for('wiki.edit_page', username=username, repo_slug=repo.slug, page_slug=request.view_args.page_slug) }}"
     class="btn-sm btn-secondary">
    <i class="lucide-pencil" aria-hidden="true"></i> 编辑
  </a>
  <form method="POST"
        action="{{ url_for('wiki.delete_page', username=username, repo_slug=repo.slug, page_slug=request.view_args.page_slug) }}"
        style="display:inline"
        onsubmit="return confirm('确定要删除此页面吗？')">
    <button type="submit" class="btn-sm btn-danger">
      <i class="lucide-trash-2" aria-hidden="true"></i> 删除
    </button>
  </form>
</div>
{% endif %}
```

注意：在 `view_page` 路由里需要把 `page_slug` 传给模板：当前模板没有 `page_slug` 变量，需要在 `render_template` 调用中加 `page_slug=page_slug`。

- [ ] **Step 6: 运行测试确认通过**

```bash
python -m pytest tests/test_routes.py -k "edit_wiki or delete_wiki" -v
```

Expected: PASS

- [ ] **Step 7: 更新 docs/design.md**

在 §6.1 路由设计 中加入：
- `GET/POST /<username>/<repo_slug>/wiki/<page_slug>/edit` — 编辑 Wiki 页面
- `POST /<username>/<repo_slug>/wiki/<page_slug>/delete` — 删除 Wiki 页面

在 §6.2 页面设计中加入：
- `templates/wiki/edit.html` — EasyMDE markdown 编辑器页面

- [ ] **Step 8: 运行全量测试确认无回归**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

- [ ] **Step 9: Commit**

```bash
git add app.py templates/wiki/edit.html templates/wiki/page.html static/css/style.css docs/design.md tests/test_routes.py
git commit -m "feat: add wiki page manual edit and delete"
```

---

## Task 2: Overview.md 摄入后自动更新

**Files:**
- Modify: `wiki_engine.py` — `ingest()` 收尾阶段新增 overview.md 更新步骤
- Modify: `tests/test_wiki_engine.py` — 新增 overview 更新测试
- Modify: `docs/design.md` — 更新 §4.1 摄入流程说明

**规范：**
- 在 ingest 流程的 Step 7（rebuild index.md）之后，新增 Step 8：用 LLM 重写 overview.md
- overview.md 提示词：综合所有已有 Wiki 页面摘要，生成全局高层综述
- 只在 `total_pages > 0`（有实际变更）时才更新 overview

- [ ] **Step 1: 写测试**

```python
# tests/test_wiki_engine.py 新增
def test_ingest_updates_overview(wiki_engine_fixture, tmp_wiki_dir):
    """overview.md should be updated after a non-empty ingest."""
    # Setup: existing overview with placeholder
    overview_path = os.path.join(tmp_wiki_dir, "overview.md")
    with open(overview_path, "w") as f:
        f.write("---\ntitle: 概览\ntype: overview\n---\n\n暂无概览内容。\n")
    # Run ingest (mocked LLM returns non-empty content)
    # ... (follows existing test fixture pattern in the file)
    # Assert overview.md was modified
    with open(overview_path) as f:
        content = f.read()
    assert "暂无概览内容" not in content  # should be replaced
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_wiki_engine.py -k "overview" -v
```

- [ ] **Step 3: 在 wiki_engine.py `ingest()` 方法末尾（`yield _progress("done"...)`前）新增**

在约第 373 行（`_write_file(log_path, ...)`）之后，`yield _progress("done"...)` 之前：

```python
        # -- 9. Update overview.md ----------------------------------------
        if total_pages > 0:
            yield _progress("finalize", 94, "Updating overview.md …")
            overview_path = os.path.join(wiki_dir, "overview.md")
            existing_overview = _read_file(overview_path)

            page_summaries_for_overview = "\n".join(
                f"- [{p['title']}]({p['filename']}) (type: {p['type']})"
                for p in list_wiki_pages(wiki_dir)
                if p["filename"] not in ("log.md", "schema.md", "overview.md")
            )

            new_overview = self._chat_text(
                system=system_base,
                user=(
                    "请生成或更新 Wiki 的 overview.md 全局概览页面。\n"
                    "包含 YAML frontmatter (title: 概览, type: overview, updated: 今天日期)。\n"
                    "内容要求：对知识库的整体高层综述，涵盖主要主题、核心发现、重要实体，\n"
                    "适合作为新读者的入口。\n\n"
                    f"--- 当前所有页面 ---\n{page_summaries_for_overview or '(暂无页面)'}\n\n"
                    f"--- 最新摄入来源 ---\n{source_filename}\n\n"
                    f"--- 摄入分析摘要 ---\n{json.dumps(analysis, ensure_ascii=False)}"
                ),
            )
            if new_overview:
                _write_file(overview_path, new_overview)
                if self._qdrant:
                    try:
                        fm_ov, _ = render_markdown(new_overview)
                        self._qdrant.upsert_page(
                            repo_id=repo_id,
                            filename="overview.md",
                            title=fm_ov.get("title", "概览"),
                            page_type="overview",
                            content=new_overview,
                        )
                    except QdrantServiceError as exc:
                        logger.error("Vector upsert failed for overview.md: %s", exc)
```

- [ ] **Step 4: 运行测试**

```bash
python -m pytest tests/test_wiki_engine.py -v --tb=short
```

- [ ] **Step 5: 更新 docs/design.md §4.1 摄入流程**

在 Step 7 之后加 Step 8 (Update overview.md)：
```
Step 8: 更新 overview.md
  LLM 综合所有 Wiki 页面生成全局概览
  → 写入 overview.md + Qdrant upsert
```

- [ ] **Step 6: Commit**

```bash
git add wiki_engine.py tests/test_wiki_engine.py docs/design.md
git commit -m "feat: auto-update overview.md after ingest"
```

---

## Task 3: Lint 自动修复

**Files:**
- Modify: `wiki_engine.py` — 新增 `apply_fixes()` 方法
- Modify: `app.py` — 实现 `apply_fixes` 路由（替换占位）
- Modify: `templates/ops/lint.html` — has_fixes=True 时显示修复按钮
- Modify: `tests/test_wiki_engine.py` — 新增 apply_fixes 测试
- Modify: `docs/design.md` — 更新 §4.3

**规范：**
- `apply_fixes` 接受 lint 报告中的 issues，让 LLM 逐个修复
- 修复类型：orphan（添加到 index.md 链接）、missing_link（在相关页面添加链接）、bad_frontmatter（修复 frontmatter）
- contradiction 类型：标记但不自动修复（需人工确认）
- 返回：已修复的文件列表

- [ ] **Step 1: 写测试**

```python
# tests/test_wiki_engine.py
def test_apply_fixes_bad_frontmatter(wiki_engine_fixture, tmp_wiki_dir):
    """apply_fixes should fix pages with missing frontmatter."""
    bad_page = os.path.join(tmp_wiki_dir, "bad-page.md")
    with open(bad_page, "w") as f:
        f.write("# Missing Frontmatter\n\nSome content here.\n")
    issues = [{"type": "bad_frontmatter", "page": "bad-page.md",
               "description": "Missing required frontmatter fields"}]
    # result = wiki_engine_fixture.apply_fixes(mock_repo, username, issues)
    # assert "bad-page.md" in result.get("fixed", [])
```

- [ ] **Step 2: 在 wiki_engine.py 新增 `apply_fixes()` 方法**

```python
def apply_fixes(
    self,
    repo: Any,
    username: str,
    issues: list[dict],
) -> dict[str, Any]:
    """Apply automatic fixes for lint issues where possible.

    Skips contradiction issues (require human review).
    Returns {"fixed": [...], "skipped": [...], "errors": [...]}.
    """
    repo_slug = repo.slug
    wiki_dir = self._wiki_dir(username, repo_slug)
    schema_content = _read_file(self._schema_path(username, repo_slug))

    system_base = (
        "你是一个 Wiki 维护者。修复以下 Wiki 页面的结构问题，保持原有内容不变。\n\n"
        + (schema_content or "")
    )

    fixed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for issue in issues:
        issue_type = issue.get("type", "")
        page_file = issue.get("page", "")
        description = issue.get("description", "")

        if issue_type == "contradiction":
            skipped.append(page_file or "unknown")
            continue

        if not page_file:
            skipped.append("unknown")
            continue

        filepath = os.path.join(wiki_dir, page_file)
        existing_content = _read_file(filepath)
        if not existing_content:
            errors.append(f"{page_file}: file not found")
            continue

        try:
            if issue_type == "bad_frontmatter":
                fixed_content = self._chat_text(
                    system=system_base,
                    user=(
                        f"请修复以下 Wiki 页面的 frontmatter 问题。\n"
                        f"问题描述: {description}\n"
                        f"要求: 添加或修复 YAML frontmatter (title, type, updated 字段)，"
                        "保持正文内容完全不变。\n\n"
                        f"--- 当前内容 ({page_file}) ---\n{existing_content}"
                    ),
                )
            elif issue_type in ("orphan", "missing_link"):
                index_content = _read_file(self._index_path(username, repo_slug))
                fixed_content = self._chat_text(
                    system=system_base,
                    user=(
                        f"请修复以下 Wiki 页面的链接问题。\n"
                        f"问题类型: {issue_type}\n"
                        f"问题描述: {description}\n"
                        "对于孤立页面：在 index.md 中添加对应链接。\n"
                        "对于缺失链接：在合适位置添加交叉引用。\n\n"
                        f"--- 待修复页面 ({page_file}) ---\n{existing_content}\n\n"
                        f"--- index.md ---\n{index_content or '(空)'}"
                    ),
                )
            elif issue_type == "wrong_type":
                fixed_content = self._chat_text(
                    system=system_base,
                    user=(
                        f"请修复以下 Wiki 页面的 type 字段。\n"
                        f"问题描述: {description}\n"
                        "只修改 frontmatter 中的 type 字段，其余内容保持不变。\n\n"
                        f"--- 当前内容 ({page_file}) ---\n{existing_content}"
                    ),
                )
            else:
                skipped.append(page_file)
                continue

            if fixed_content:
                _write_file(filepath, fixed_content)
                if self._qdrant:
                    try:
                        fm, _ = render_markdown(fixed_content)
                        self._qdrant.upsert_page(
                            repo_id=repo.id,
                            filename=page_file,
                            title=fm.get("title", page_file),
                            page_type=fm.get("type", "unknown"),
                            content=fixed_content,
                        )
                    except QdrantServiceError:
                        pass
                fixed.append(page_file)
            else:
                errors.append(f"{page_file}: LLM returned empty content")
        except Exception as exc:
            logger.exception("apply_fixes failed for %s: %s", page_file, exc)
            errors.append(f"{page_file}: {exc}")

    return {"fixed": fixed, "skipped": skipped, "errors": errors}
```

- [ ] **Step 3: 更新 app.py 的 apply_fixes 路由**

将 `apply_fixes` 路由（约第 1277 行）从占位实现替换为：

```python
@ops_bp.route("/<username>/<repo_slug>/apply-fixes", methods=["POST"])
@login_required
def apply_fixes(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    _require_owner(repo)

    issues_json = request.form.get("issues_json", "[]")
    try:
        issues = json.loads(issues_json)
    except json.JSONDecodeError:
        issues = []

    if not issues:
        flash("没有可修复的问题", "info")
        return redirect(url_for("ops.lint", username=username, repo_slug=repo_slug))

    try:
        result = current_app.wiki_engine.apply_fixes(repo, username, issues)
    except Exception as exc:
        logger.exception("apply_fixes failed for repo %s", repo.id)
        flash(f"修复失败: {exc}", "error")
        return redirect(url_for("ops.lint", username=username, repo_slug=repo_slug))

    fixed_count = len(result.get("fixed", []))
    skipped_count = len(result.get("skipped", []))
    flash(
        f"已修复 {fixed_count} 个问题，跳过 {skipped_count} 个（矛盾类问题需人工审查）",
        "success" if fixed_count > 0 else "info",
    )
    return redirect(url_for("ops.lint", username=username, repo_slug=repo_slug))
```

- [ ] **Step 4: 更新 templates/ops/lint.html — 修复按钮传递 issues_json**

找到 `apply-fixes` form 并更新，使其传递 issues JSON：

在 lint.html 的 form 中加隐藏字段：
```html
<form method="POST" action="{{ url_for('ops.apply_fixes', username=username, repo_slug=repo.slug) }}">
  <input type="hidden" name="issues_json" id="issues-json-input" value="">
  <button type="submit" class="btn-primary" onclick="prepareIssues()">
    <i class="lucide-wrench" aria-hidden="true"></i> 自动修复可修复项
  </button>
</form>
<script>
function prepareIssues() {
  var issues = {{ (report.contradictions + report.orphan_pages + report.missing_refs)|tojson }};
  document.getElementById('issues-json-input').value = JSON.stringify(issues);
}
</script>
```

- [ ] **Step 5: 运行测试**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 6: 更新 docs/design.md §4.3**

- [ ] **Step 7: Commit**

```bash
git add wiki_engine.py app.py templates/ops/lint.html tests/test_wiki_engine.py docs/design.md
git commit -m "feat: implement lint auto-fix"
```

---

## Task 4: 查询流式响应（SSE Streaming）

**Files:**
- Modify: `wiki_engine.py` — 新增 `query_stream()` 生成器方法
- Modify: `app.py` — 新增 `ops.query_stream` SSE 路由
- Modify: `static/js/chat.js` — 切换到 EventSource 消费流式响应
- Modify: `tests/test_routes.py` — 新增流式查询路由测试
- Modify: `docs/design.md` — 更新 §4.2 查询流程

**规范：**
- 新增路由 `GET /<username>/<repo_slug>/query/stream?q=...` 返回 `text/event-stream`
- SSE 事件：`progress`（检索阶段）、`answer_chunk`（LLM token）、`done`、`error`
- 前端改用 `EventSource` + `fetch` streaming 替换原来的 fetch POST
- 保留原有 POST 路由作为降级方案（不删除）
- LLMClient 需支持 `stream=True` 参数

**注意：** LLMClient 已有 `chat()` 方法，需新增 `chat_stream()` 支持 OpenAI streaming。

- [ ] **Step 1: 检查 llm_client.py**

```bash
cat /Volumes/mydata/codes/github/brianxiadong/open-llm-wiki/llm_client.py
```

- [ ] **Step 2: 在 llm_client.py 新增 `chat_stream()` 方法**

```python
def chat_stream(self, messages: list[dict], temperature: float = 0.7):
    """Stream chat completion, yields text chunks."""
    import httpx
    headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
    payload = {
        "model": self._model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    with httpx.stream("POST", f"{self._api_base}/chat/completions",
                      json=payload, headers=headers, timeout=120) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
```

- [ ] **Step 3: 在 wiki_engine.py 新增 `query_stream()` 方法**

```python
def query_stream(self, repo, username: str, question: str):
    """Stream query: yields SSE-format dicts for progress and answer chunks."""
    repo_slug = repo.slug
    repo_id = repo.id
    wiki_dir = self._wiki_dir(username, repo_slug)
    schema_content = _read_file(self._schema_path(username, repo_slug))
    index_content = _read_file(self._index_path(username, repo_slug))
    system_base = (
        "你是一个 Wiki 知识助手。根据 Wiki 内容准确回答问题，并引用来源页面。\n\n"
        + (schema_content or "")
    )

    yield {"event": "progress", "data": {"message": "正在检索相关页面…", "percent": 10}}

    wiki_filenames: list[str] = []
    if index_content:
        pick_result = self._chat_json(
            system=system_base,
            user=(
                "根据用户问题，从索引中选出最相关的页面（最多 8 个）。\n"
                '返回 JSON: {"filenames": ["a.md", "b.md"]}\n\n'
                f"--- 问题 ---\n{question}\n\n--- index.md ---\n{index_content}"
            ),
            default={"filenames": []},
        )
        wiki_filenames = pick_result.get("filenames", [])

    qdrant_filenames: list[str] = []
    try:
        hits = self._qdrant.search(repo_id=repo_id, query=question, limit=8)
        qdrant_filenames = [h["filename"] for h in hits if h.get("filename")]
    except QdrantServiceError:
        pass

    yield {"event": "progress", "data": {"message": "正在读取页面内容…", "percent": 40}}

    seen: set[str] = set()
    merged: list[str] = []
    for fn in wiki_filenames + qdrant_filenames:
        if fn not in seen:
            seen.add(fn)
            merged.append(fn)

    page_contents: dict[str, str] = {}
    for fn in merged:
        content = _read_file(os.path.join(wiki_dir, fn))
        if content:
            page_contents[fn] = content

    if not page_contents:
        yield {"event": "done", "data": {
            "answer": "暂无相关 Wiki 内容。",
            "wiki_sources": [], "qdrant_sources": [],
            "referenced_pages": [],
        }}
        return

    context_parts = [f"=== {fn} ===\n{content[:6000]}"
                     for fn, content in page_contents.items()]
    context_block = "\n\n".join(context_parts)

    yield {"event": "progress", "data": {"message": "正在生成回答…", "percent": 60}}

    messages = [
        {"role": "system", "content": system_base},
        {"role": "user", "content": (
            "根据以下 Wiki 页面回答用户问题。使用 Markdown 格式。\n\n"
            f"--- 问题 ---\n{question}\n\n"
            f"--- Wiki 页面内容 ---\n{context_block}"
        )},
    ]

    answer_chunks: list[str] = []
    try:
        for chunk in self._llm.chat_stream(messages):
            answer_chunks.append(chunk)
            yield {"event": "answer_chunk", "data": {"chunk": chunk}}
    except Exception as exc:
        logger.error("stream query failed: %s", exc)
        yield {"event": "error", "data": {"message": str(exc)}}
        return

    loaded = set(page_contents.keys())
    yield {"event": "done", "data": {
        "answer": "".join(answer_chunks),
        "wiki_sources": [f for f in wiki_filenames if f in loaded],
        "qdrant_sources": [f for f in qdrant_filenames if f in loaded],
        "referenced_pages": list(loaded),
    }}
```

- [ ] **Step 4: 在 app.py 的 ops_bp 新增流式路由**

在 `query_api` 路由之后新增：

```python
@ops_bp.route("/<username>/<repo_slug>/query/stream", methods=["GET"])
@login_required
def query_stream(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    question = request.args.get("q", "").strip()
    if not question:
        return jsonify(error="请输入问题"), 400

    def generate():
        import time
        try:
            for event_dict in current_app.wiki_engine.query_stream(repo, username, question):
                event = event_dict["event"]
                data = json.dumps(event_dict["data"], ensure_ascii=False)
                yield f"event: {event}\ndata: {data}\n\n"
        except Exception as exc:
            logger.exception("query_stream error for repo %s", repo.id)
            yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 5: 更新 static/js/chat.js — 使用 EventSource 流式消费**

将 `form.addEventListener('submit', ...)` 中的 fetch 部分替换为 EventSource 方案：

```javascript
form.addEventListener('submit', function (e) {
  e.preventDefault();
  var q = input.value.trim();
  if (!q || isLoading) return;

  addUserMessage(q);
  input.value = '';
  input.style.height = 'auto';
  isLoading = true;
  submitBtn.disabled = true;
  addLoadingMessage();

  var streamUrl = cfg.queryStreamUrl + '?q=' + encodeURIComponent(q);
  var es = new EventSource(streamUrl);
  var answerChunks = [];
  var progressEl = null;

  es.addEventListener('progress', function(e) {
    var d = JSON.parse(e.data);
    var loading = document.getElementById('chat-loading');
    if (loading) {
      var hint = loading.querySelector('.chat-wait-hint');
      if (hint) { hint.hidden = false; hint.textContent = d.message; }
    }
  });

  es.addEventListener('answer_chunk', function(e) {
    var d = JSON.parse(e.data);
    answerChunks.push(d.chunk);
    // Update streaming display
    var loading = document.getElementById('chat-loading');
    if (loading) {
      var content = loading.querySelector('.chat-msg-content');
      if (content) {
        if (!progressEl) {
          content.innerHTML = '<div class="streaming-answer"></div>';
          progressEl = content.querySelector('.streaming-answer');
        }
        progressEl.textContent = answerChunks.join('');
      }
    }
  });

  es.addEventListener('done', function(e) {
    es.close();
    removeLoadingMessage();
    var d = JSON.parse(e.data);
    var answer = d.answer || answerChunks.join('');
    // Render markdown
    fetch(cfg.queryUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({q: '__render_only__', answer: answer,
                             wiki_sources: d.wiki_sources || [],
                             qdrant_sources: d.qdrant_sources || []})
    }).then(function(r) { return r.json(); })
      .then(function(data) {
        addAIMessage(data.html || '', data.references || [],
          data.markdown || '', data.wiki_sources || [], data.qdrant_sources || []);
      }).catch(function() {
        addAIMessage('<p>' + escapeHtml(answer) + '</p>', [], answer, [], []);
      });
    isLoading = false;
    submitBtn.disabled = false;
    input.focus();
  });

  es.addEventListener('error', function(e) {
    es.close();
    removeLoadingMessage();
    var msg = '查询失败，请稍后重试';
    try { msg = JSON.parse(e.data).message || msg; } catch(err) {}
    addErrorMessage(msg);
    isLoading = false;
    submitBtn.disabled = false;
  });

  es.onerror = function() {
    if (es.readyState === EventSource.CLOSED) return;
    es.close();
    removeLoadingMessage();
    addErrorMessage('连接中断，请重试');
    isLoading = false;
    submitBtn.disabled = false;
  };
});
```

**注意：** 需要在 dashboard.html 的 `__chatConfig` 中加 `queryStreamUrl`。

- [ ] **Step 6: 更新 dashboard.html 的 chatConfig**

```javascript
window.__chatConfig = {
  queryUrl: '{{ url_for("ops.query_api", username=username, repo_slug=repo.slug) }}',
  queryStreamUrl: '{{ url_for("ops.query_stream", username=username, repo_slug=repo.slug) }}',
  saveUrl: '{{ url_for("ops.save_query_page", username=username, repo_slug=repo.slug) }}',
  wikiBaseUrl: '/{{ username }}/{{ repo.slug }}/wiki/'
};
```

- [ ] **Step 7: 写路由测试**

```python
def test_query_stream_route(client, auth_user, sample_repo):
    username, repo_slug = sample_repo
    rv = client.get(f"/{username}/{repo_slug}/query/stream?q=test")
    assert rv.status_code in (200, 400)
```

- [ ] **Step 8: 运行测试**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 9: 更新 docs/design.md §4.2**

- [ ] **Step 10: Commit**

```bash
git add wiki_engine.py app.py static/js/chat.js templates/repo/dashboard.html llm_client.py tests/test_routes.py docs/design.md
git commit -m "feat: streaming query response via SSE"
```

---

## Task 5: Wiki 全文关键词搜索

**Files:**
- Modify: `app.py` — 新增 `wiki.search` 路由
- Create: `templates/wiki/search.html` — 搜索结果页
- Modify: `templates/base.html` — 导航栏加搜索入口（或在 dashboard 加）
- Modify: `tests/test_routes.py` — 搜索路由测试
- Modify: `docs/design.md` — 更新路由表

**规范：**
- `GET /<username>/<repo_slug>/wiki/search?q=keyword` — 全文搜索
- 在 wiki 目录下的所有 .md 文件中做大小写不敏感的关键词匹配
- 返回匹配页面列表，并高亮匹配片段（取关键词前后 100 字符）
- 无需 LLM，纯文本搜索

- [ ] **Step 1: 写测试**

```python
def test_wiki_search_route(client, auth_user, sample_repo_with_page):
    username, repo_slug, _ = sample_repo_with_page
    rv = client.get(f"/{username}/{repo_slug}/wiki/search?q=test")
    assert rv.status_code == 200
```

- [ ] **Step 2: 在 app.py wiki_bp 新增搜索路由**

在 `delete_page` 路由之后新增：

```python
@wiki_bp.route("/<username>/<repo_slug>/wiki/search")
def search(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    query = request.args.get("q", "").strip()
    results = []
    if query:
        wiki_dir = os.path.join(
            get_repo_path(Config.DATA_DIR, username, repo_slug), "wiki"
        )
        if os.path.isdir(wiki_dir):
            query_lower = query.lower()
            for filename in sorted(os.listdir(wiki_dir)):
                if not filename.endswith(".md"):
                    continue
                filepath = os.path.join(wiki_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                except OSError:
                    continue
                content_lower = content.lower()
                if query_lower not in content_lower:
                    continue
                fm, _ = render_markdown(content)
                # Find snippet around first match
                idx = content_lower.find(query_lower)
                start = max(0, idx - 100)
                end = min(len(content), idx + len(query) + 100)
                snippet = content[start:end].replace("\n", " ")
                if start > 0:
                    snippet = "…" + snippet
                if end < len(content):
                    snippet += "…"
                results.append({
                    "slug": filename.replace(".md", ""),
                    "title": fm.get("title", filename.replace(".md", "")),
                    "type": fm.get("type", "unknown"),
                    "snippet": snippet,
                    "match_count": content_lower.count(query_lower),
                })
        results.sort(key=lambda r: r["match_count"], reverse=True)

    return render_template(
        "wiki/search.html",
        username=username,
        repo=repo,
        query=query,
        results=results,
        is_owner=_is_owner(repo),
    )
```

- [ ] **Step 3: 创建 templates/wiki/search.html**

- [ ] **Step 4: 运行测试并 commit**

---

## Task 6: 批量摄入 / 多文件上传

**Files:**
- Modify: `app.py` — 新增 `source.batch_ingest` 路由；修改 `upload` 支持多文件
- Modify: `templates/source/list.html` — 加"批量摄入"按钮
- Modify: `tests/test_routes.py` — 批量摄入路由测试
- Modify: `docs/design.md`

**规范：**
- `POST /<username>/<repo_slug>/sources/batch-ingest` — 对所有已上传但未摄入的文件批量创建 ingest 任务
- 接受可选 `files` 参数（已选文件名列表）；不传则对全部未摄入文件操作
- 返回排队的任务数量

- [ ] **Step 1: 写测试**

```python
def test_batch_ingest(client, auth_user, sample_repo):
    username, repo_slug = sample_repo
    rv = client.post(f"/{username}/{repo_slug}/sources/batch-ingest",
                     data={}, follow_redirects=True)
    assert rv.status_code == 200
```

- [ ] **Step 2: 在 app.py source_bp 新增批量摄入路由**

```python
@source_bp.route("/<username>/<repo_slug>/sources/batch-ingest", methods=["POST"])
@login_required
def batch_ingest(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    _require_owner(repo)
    raw_dir = os.path.join(
        get_repo_path(Config.DATA_DIR, username, repo_slug), "raw"
    )
    selected = request.form.getlist("files")
    ingested_files = {
        t.input_data
        for t in Task.query.filter_by(repo_id=repo.id, type="ingest", status="done").all()
        if t.input_data
    }
    queued_files = {
        t.input_data
        for t in Task.query.filter(
            Task.repo_id == repo.id,
            Task.type == "ingest",
            Task.status.in_(["queued", "running"]),
        ).all()
        if t.input_data
    }
    all_sources = list_raw_sources(raw_dir)
    queued_count = 0
    for src in all_sources:
        fn = src["filename"]
        if selected and fn not in selected:
            continue
        if fn in ingested_files or fn in queued_files:
            continue
        task = Task(repo_id=repo.id, type="ingest", status="queued", input_data=fn)
        db.session.add(task)
        queued_count += 1
    if queued_count:
        db.session.commit()
    flash(f"已排队 {queued_count} 个摄入任务", "success" if queued_count else "info")
    return redirect(url_for("source.list_sources", username=username, repo_slug=repo_slug))
```

- [ ] **Step 3: 更新 templates/source/list.html — 加批量摄入按钮**

- [ ] **Step 4: 运行测试并 commit**

---

## Task 7: 失败任务重试

**Files:**
- Modify: `app.py` — 新增 `ops.retry_task` 路由
- Modify: `templates/ops/tasks.html` — 对 failed 任务显示重试按钮
- Modify: `tests/test_routes.py`
- Modify: `docs/design.md`

**规范：**
- `POST /api/tasks/<task_id>/retry` — 将 failed 任务重置为 queued，清空 progress
- 只有任务 owner 可以重试

- [ ] **Step 1: 写测试**

- [ ] **Step 2: 新增路由**

```python
@ops_bp.route("/api/tasks/<int:task_id>/retry", methods=["POST"])
@login_required
def retry_task(task_id):
    task = Task.query.get_or_404(task_id)
    repo = Repo.query.get_or_404(task.repo_id)
    if current_user.id != repo.user_id:
        abort(403)
    if task.status != "failed":
        return jsonify(error="只能重试失败的任务"), 400
    task.status = "queued"
    task.progress = 0
    task.progress_msg = ""
    task.started_at = None
    task.finished_at = None
    db.session.commit()
    return jsonify(ok=True, task_id=task.id)
```

- [ ] **Step 3: 更新 tasks.html — 失败任务加重试按钮**

- [ ] **Step 4: 运行测试并 commit**

---

## Task 8: Wiki 导出（ZIP）

**Files:**
- Modify: `app.py` — 新增 `wiki.export_zip` 路由
- Modify: `templates/repo/dashboard.html` — 加导出按钮到 dropdown
- Modify: `docs/design.md`

**规范：**
- `GET /<username>/<repo_slug>/wiki/export.zip` — 打包所有 wiki/*.md 为 zip 下载
- 使用 Python `zipfile` 标准库，无需新依赖
- Content-Disposition: attachment; filename="{repo_slug}-wiki.zip"

- [ ] **Step 1: 写路由**

```python
@wiki_bp.route("/<username>/<repo_slug>/wiki/export.zip")
@login_required
def export_zip(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    _require_owner(repo)
    import io, zipfile
    wiki_dir = os.path.join(
        get_repo_path(Config.DATA_DIR, username, repo_slug), "wiki"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.isdir(wiki_dir):
            for fname in sorted(os.listdir(wiki_dir)):
                if fname.endswith(".md"):
                    fpath = os.path.join(wiki_dir, fname)
                    zf.write(fpath, fname)
    buf.seek(0)
    return Response(
        buf.read(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{repo_slug}-wiki.zip"'},
    )
```

- [ ] **Step 2: 写测试、更新模板、commit**

---

## Task 9: URL / 网页导入

**Files:**
- Modify: `app.py` — 新增 `source.import_url` 路由
- Modify: `templates/source/list.html` — 加 URL 导入表单
- Modify: `requirements.txt` — 加 `trafilatura` 依赖（网页正文提取）
- Modify: `docs/design.md`

**规范：**
- `POST /<username>/<repo_slug>/sources/import-url` — 接受 `url` 字段
- 用 `trafilatura` 提取网页正文，转为 Markdown，存入 raw/
- 文件名从 URL 生成（slugify domain + path）
- 自动排队摄入任务

- [ ] **Step 1: 安装依赖**

```bash
pip install trafilatura
echo "trafilatura" >> requirements.txt
```

- [ ] **Step 2: 写路由**

```python
@source_bp.route("/<username>/<repo_slug>/sources/import-url", methods=["POST"])
@login_required
def import_url(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    _require_owner(repo)
    url = request.form.get("url", "").strip()
    if not url:
        flash("URL 不能为空", "error")
        return redirect(url_for("source.list_sources", username=username, repo_slug=repo_slug))

    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            flash("无法获取页面内容", "error")
            return redirect(url_for("source.list_sources", username=username, repo_slug=repo_slug))
        text = trafilatura.extract(downloaded, output_format="markdown",
                                   include_links=True, include_images=False)
        if not text:
            flash("无法提取页面正文", "error")
            return redirect(url_for("source.list_sources", username=username, repo_slug=repo_slug))
    except Exception as exc:
        flash(f"导入失败: {exc}", "error")
        return redirect(url_for("source.list_sources", username=username, repo_slug=repo_slug))

    from urllib.parse import urlparse
    parsed = urlparse(url)
    path_slug = slugify(parsed.netloc + parsed.path)[:60]
    filename = f"{path_slug or 'imported'}.md"
    raw_dir = os.path.join(get_repo_path(Config.DATA_DIR, username, repo_slug), "raw")
    os.makedirs(raw_dir, exist_ok=True)

    counter = 0
    save_name = filename
    while os.path.exists(os.path.join(raw_dir, save_name)):
        counter += 1
        save_name = f"{path_slug}-{counter}.md"

    header = f"---\ntitle: {url}\nsource_url: {url}\n---\n\n"
    with open(os.path.join(raw_dir, save_name), "w", encoding="utf-8") as f:
        f.write(header + text)

    task = Task(repo_id=repo.id, type="ingest", status="queued", input_data=save_name)
    db.session.add(task)
    db.session.commit()
    _sync_repo_counts(repo, username)
    flash(f"已导入 {save_name}，摄入任务已排队", "success")
    return redirect(url_for("source.list_sources", username=username, repo_slug=repo_slug))
```

- [ ] **Step 3: 写测试、更新模板、commit**

---

## Task 10: 知识库公开/私有（可见性）

**Files:**
- Modify: `models.py` — `Repo` 加 `is_public` 字段
- Create: `migrations/003_repo_is_public.sql`
- Modify: `app.py` — 各路由按可见性检查；允许未登录访问公开库
- Modify: `templates/repo/settings.html` — 可见性开关
- Modify: `templates/repo/list.html` — 显示公开/私有标识
- Modify: `docs/design.md` §5.1

**规范：**
- `Repo.is_public` 默认 False
- 公开仓库：任何人可读（dashboard、wiki 页面、graph），无需登录
- 私有仓库：只有 owner 可访问
- `_require_owner` / `_is_owner` 逻辑不变（只控制写操作）
- 需要修改所有读操作路由去掉 `@login_required` + 加可见性检查

- [ ] **Step 1: 写迁移 SQL**

```sql
-- migrations/003_repo_is_public.sql
ALTER TABLE repos ADD COLUMN is_public TINYINT(1) NOT NULL DEFAULT 0;
```

- [ ] **Step 2: 更新 models.py**

```python
is_public = db.Column(db.Boolean, nullable=False, default=False)
```

- [ ] **Step 3: 修改路由可见性检查、settings 页、commit**

---

## Task 11: 查询历史（本地存储）

**Files:**
- Modify: `static/js/chat.js` — 用 localStorage 存储查询历史
- Modify: `templates/repo/dashboard.html` — 加历史记录侧边栏/下拉

**规范：**
- 用 `localStorage` 存储最近 20 条查询（不需要后端）
- 按 repo slug 隔离 key
- 历史记录显示在输入框下方，点击可复现查询

- [ ] **Step 1: 在 chat.js 新增历史模块**

- [ ] **Step 2: 更新 dashboard.html UI，commit**

---

## Task 12: Schema 模板库

**Files:**
- Modify: `app.py` — `new_repo` 路由新增 `schema_template` 参数
- Modify: `utils.py` — 新增多个预设 schema 常量
- Modify: `templates/repo/new.html` — 加模板选择下拉
- Modify: `docs/design.md`

**规范：**
- 提供 3-4 个预设模板：通用、学术研究、产品文档、技术笔记
- 在新建仓库页面可选择
- 不影响现有默认行为

- [ ] **Step 1: 在 utils.py 新增 schema 常量**

- [ ] **Step 2: 更新 new.html + new_repo 路由，commit**

---

## Task 13: 管理后台（简版）

**Files:**
- Modify: `app.py` — 新增 `admin_bp` Blueprint，包含用户列表和统计
- Create: `templates/admin/dashboard.html`
- Modify: `config.py` — 新增 `ADMIN_USERNAME` 配置
- Modify: `docs/design.md`

**规范：**
- `GET /admin` → 管理后台（仅 ADMIN_USERNAME 用户可访问）
- 显示：用户数、仓库总数、任务统计（成功/失败）、磁盘占用
- 简单权限检查：`current_user.username == Config.ADMIN_USERNAME`

- [ ] **Step 1: 写路由和模板，commit**

---

## Task 14: 全局跨仓库搜索

**Files:**
- Modify: `app.py` — 新增 `user.global_search` 路由
- Create: `templates/user/search.html`
- Modify: `templates/base.html` — 全局搜索入口
- Modify: `docs/design.md`

**规范：**
- `GET /<username>/search?q=keyword` — 在该用户所有仓库的 wiki 中搜索
- 结果按仓库分组显示
- 复用 wiki search 的纯文本匹配逻辑

- [ ] **Step 1: 写路由和模板，commit**

---

## Task 15: 全量测试 + 文档最终同步

- [ ] **Step 1: 运行全量测试套件**

```bash
cd /Volumes/mydata/codes/github/brianxiadong/open-llm-wiki
python -m pytest tests/ -v 2>&1
```

Expected: 全部通过

- [ ] **Step 2: 检查 docs/design.md 是否涵盖所有变更**

- [ ] **Step 3: 检查 .env.example 同步**

- [ ] **Step 4: 最终 commit**

```bash
git add -A
git commit -m "docs: sync design.md with all feature enhancements"
```
