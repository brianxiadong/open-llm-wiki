# Feature Wave 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 14 个新功能，覆盖多轮对话、公开访客查询、整库导入、README、重复检测、知识缺口发现、实体去重、审计日志、API Token、健康面板、低置信度汇总、管理后台扩展、语义检索 UI。

**Architecture:** Flask/Jinja2 SSR 单体；所有新功能沿用现有 Blueprint 模式；新数据表通过 `migrations/005_*.sql` 手工迁移；不引入新外部依赖。

**Tech Stack:** Flask, SQLAlchemy, MySQL, Qdrant, Python threading（现有）

---

## 文件变更总览

| 文件 | 变更说明 |
|------|---------|
| `models.py` | 新增 `ConversationSession`、`AuditLog`、`ApiToken` |
| `migrations/005_wave3.sql` | 三张新表 DDL |
| `app.py` | 新增/修改约 25 个路由 |
| `wiki_engine.py` | 新增 `find_gaps()`、`find_entity_duplicates()` |
| `utils.py` | 新增 `file_md5()` |
| `templates/` | 10 个新/改模板 |
| `static/js/chat.js` | 多轮对话上下文管理 |
| `docs/design.md` | 同步更新 |

---

## Task 1：数据模型 + Migration

**Files:**
- Modify: `models.py`
- Create: `migrations/005_wave3.sql`

### 新增三张表

```python
# models.py

class ConversationSession(db.Model):
    """多轮对话会话（存储最近 N 轮消息）"""
    __tablename__ = "conversation_sessions"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    repo_id = db.Column(db.Integer, db.ForeignKey("repos.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    session_key = db.Column(db.String(64), nullable=False, index=True)
    messages_json = db.Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False, default="[]")
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utc_now, onupdate=_utc_now)


class AuditLog(db.Model):
    """审计日志：登录、删库、改 Schema 等关键操作"""
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    username = db.Column(db.String(64), nullable=True)
    action = db.Column(db.String(64), nullable=False, index=True)
    resource_type = db.Column(db.String(32), nullable=True)
    resource_id = db.Column(db.String(128), nullable=True)
    detail = db.Column(db.Text, nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now, index=True)


class ApiToken(db.Model):
    """API Token 机器凭证"""
    __tablename__ = "api_tokens"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    token_hash = db.Column(db.String(256), nullable=False, unique=True)
    last_used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User", backref="api_tokens")
```

### migrations/005_wave3.sql

```sql
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    repo_id        INT NOT NULL,
    user_id        INT NOT NULL,
    session_key    VARCHAR(64) NOT NULL,
    messages_json  LONGTEXT NOT NULL DEFAULT ('[]'),
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_cs_repo (repo_id),
    INDEX idx_cs_user (user_id),
    INDEX idx_cs_key (session_key),
    CONSTRAINT fk_cs_repo FOREIGN KEY (repo_id) REFERENCES repos(id),
    CONSTRAINT fk_cs_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_logs (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT,
    username      VARCHAR(64),
    action        VARCHAR(64) NOT NULL,
    resource_type VARCHAR(32),
    resource_id   VARCHAR(128),
    detail        TEXT,
    ip            VARCHAR(64),
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_al_user (user_id),
    INDEX idx_al_action (action),
    INDEX idx_al_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS api_tokens (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT NOT NULL,
    name         VARCHAR(128) NOT NULL,
    token_hash   VARCHAR(256) NOT NULL UNIQUE,
    last_used_at DATETIME,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active    TINYINT(1) NOT NULL DEFAULT 1,
    INDEX idx_at_user (user_id),
    CONSTRAINT fk_at_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 1: 在 models.py 末尾加三个 ORM 类**（见上方代码）
- [ ] **Step 2: 创建 migrations/005_wave3.sql**（注意 MySQL 5.7 不支持 `DEFAULT ('')`，改用 `NOT NULL DEFAULT '[]'`）
- [ ] **Step 3: 运行测试确认模型可导入**
```bash
python -m pytest tests/test_models.py -v
```
- [ ] **Step 4: 写新模型测试（test_models.py 末尾追加）**
```python
def test_conversation_session_creation(sample_repo, app):
    with app.app_context():
        from models import ConversationSession, User, db
        u = User.query.filter_by(username="alice").first()
        s = ConversationSession(repo_id=sample_repo[1]["id"], user_id=u.id,
                                session_key="test-key-1", messages_json='[]')
        db.session.add(s); db.session.commit()
        assert ConversationSession.query.filter_by(session_key="test-key-1").first()

def test_audit_log_creation(app):
    with app.app_context():
        from models import AuditLog, db
        log = AuditLog(action="login", username="alice", ip="127.0.0.1")
        db.session.add(log); db.session.commit()
        assert AuditLog.query.filter_by(action="login").first()

def test_api_token_creation(sample_repo, app):
    with app.app_context():
        from models import ApiToken, User, db
        u = User.query.filter_by(username="alice").first()
        t = ApiToken(user_id=u.id, name="test-token", token_hash="abc123hash")
        db.session.add(t); db.session.commit()
        assert ApiToken.query.filter_by(name="test-token").first()
```
- [ ] **Step 5: 运行所有测试**
```bash
python -m pytest tests/ --ignore=tests/test_e2e.py -q
```
- [ ] **Step 6: 提交**
```bash
git add models.py migrations/005_wave3.sql tests/test_models.py
git commit -m "feat: add ConversationSession, AuditLog, ApiToken models"
```

---

## Task 2：审计日志 + API Token

**Files:**
- Modify: `app.py`（新增审计辅助函数 + 关键路由埋点 + Token 路由 + Token 认证中间件）
- Modify: `templates/user/settings.html`（Token 管理 UI）
- Create: `templates/admin/audit.html`

### app.py 新增辅助函数和 API Token 支持

**1. 审计日志辅助函数（放在 `_get_repo_or_404` 附近）：**
```python
def _audit(action: str, resource_type: str = None, resource_id: str = None, detail: str = None):
    """写入审计日志（失败静默）"""
    try:
        from models import AuditLog
        ip = request.remote_addr if request else None
        uid = current_user.id if current_user and current_user.is_authenticated else None
        uname = current_user.username if uid else None
        log = AuditLog(user_id=uid, username=uname, action=action,
                       resource_type=resource_type, resource_id=str(resource_id) if resource_id else None,
                       detail=detail, ip=ip)
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.warning("Audit log failed: %s", e)
```

**2. 在以下路由调用 `_audit()`：**
- `login` POST 成功后：`_audit("login")`
- `logout`：`_audit("logout")`
- `delete_repo` POST：`_audit("delete_repo", "repo", repo.id, repo.slug)`
- `delete_source` POST：`_audit("delete_source", "source", source_id)`
- `batch_delete` POST：`_audit("batch_delete_sources", "repo", repo.id)`
- `settings` 保存 schema 时：`_audit("update_schema", "repo", repo.id)`

**3. API Token 认证中间件（在 `create_app` 里的 `before_request`）：**
```python
@app.before_request
def _check_api_token():
    """如果请求携带 Authorization: Bearer <token>，则用 token 认证"""
    if current_user.is_authenticated:
        return
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return
    raw = auth[7:].strip()
    if not raw:
        return
    import hashlib
    from models import ApiToken
    h = hashlib.sha256(raw.encode()).hexdigest()
    token = ApiToken.query.filter_by(token_hash=h, is_active=True).first()
    if token:
        from flask_login import login_user
        login_user(token.user, remember=False)
        token.last_used_at = _utc_now()
        db.session.commit()
```

**4. API Token 管理路由（挂在 user_bp 下）：**
```python
@user_bp.route("/settings/tokens", methods=["GET"])
@login_required
def list_tokens():
    tokens = ApiToken.query.filter_by(user_id=current_user.id).order_by(ApiToken.created_at.desc()).all()
    return render_template("user/tokens.html", tokens=tokens)

@user_bp.route("/settings/tokens/create", methods=["POST"])
@login_required
def create_token():
    import secrets, hashlib
    name = request.form.get("name", "").strip()
    if not name:
        flash("Token 名称不能为空", "error")
        return redirect(url_for("user.list_tokens"))
    raw = secrets.token_urlsafe(32)
    h = hashlib.sha256(raw.encode()).hexdigest()
    t = ApiToken(user_id=current_user.id, name=name, token_hash=h)
    db.session.add(t); db.session.commit()
    flash(f"Token 已创建（只显示一次）：{raw}", "success")
    _audit("create_api_token", "api_token", t.id, name)
    return redirect(url_for("user.list_tokens"))

@user_bp.route("/settings/tokens/<int:token_id>/revoke", methods=["POST"])
@login_required
def revoke_token(token_id):
    t = ApiToken.query.filter_by(id=token_id, user_id=current_user.id).first_or_404()
    t.is_active = False
    db.session.commit()
    _audit("revoke_api_token", "api_token", token_id)
    flash("Token 已吊销", "success")
    return redirect(url_for("user.list_tokens"))
```

**5. 管理员审计日志路由（admin_bp 下）：**
```python
@admin_bp.route("/audit")
@login_required
def audit_log():
    _require_admin()
    page = request.args.get("page", 1, type=int)
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=50)
    return render_template("admin/audit.html", logs=logs)
```

### templates/user/tokens.html（新建）
```html
{% extends "base.html" %}
{% block title %}API Tokens - Open LLM Wiki{% endblock %}
{% block content %}
<h2>API Tokens</h2>
<article>
  <form method="POST" action="{{ url_for('user.create_token') }}" style="display:flex;gap:0.5rem;margin-bottom:1rem;">
    <input type="text" name="name" placeholder="Token 名称（如 my-script）" required style="flex:1;">
    <button type="submit" class="action-btn action-btn-primary">创建</button>
  </form>
  {% if tokens %}
  <table>
    <thead><tr><th>名称</th><th>创建时间</th><th>最后使用</th><th>状态</th><th>操作</th></tr></thead>
    <tbody>
    {% for t in tokens %}
    <tr>
      <td>{{ t.name }}</td>
      <td>{{ t.created_at.strftime('%Y-%m-%d') }}</td>
      <td>{{ t.last_used_at.strftime('%Y-%m-%d %H:%M') if t.last_used_at else '从未' }}</td>
      <td>{% if t.is_active %}<span class="badge badge-source">有效</span>{% else %}<span class="badge badge-muted">已吊销</span>{% endif %}</td>
      <td>{% if t.is_active %}
        <form method="POST" action="{{ url_for('user.revoke_token', token_id=t.id) }}" style="display:inline;">
          <button type="submit" class="btn-sm" style="color:var(--error);" onclick="return confirm('确认吊销？')">吊销</button>
        </form>
      {% endif %}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p style="color:var(--pico-muted-color);">还没有 Token，创建一个以供脚本调用。</p>
  {% endif %}
</article>
<details style="margin-top:1rem;"><summary>使用方法</summary>
<pre style="font-size:0.85rem;background:var(--pico-card-background-color);padding:1rem;border-radius:6px;">curl -H "Authorization: Bearer &lt;your-token&gt;" \
  https://your-domain/alice/my-repo/query \
  -H "Content-Type: application/json" \
  -d '{"q":"你的问题"}'</pre>
</details>
{% endblock %}
```

### templates/admin/audit.html（新建）
```html
{% extends "base.html" %}
{% block title %}审计日志 - 管理后台{% endblock %}
{% block content %}
<h2>审计日志</h2>
<article style="padding:0;">
  <table>
    <thead><tr><th>时间</th><th>用户</th><th>操作</th><th>资源</th><th>IP</th><th>详情</th></tr></thead>
    <tbody>
    {% for log in logs.items %}
    <tr>
      <td style="white-space:nowrap;font-size:0.85rem;">{{ log.created_at.strftime('%m-%d %H:%M') }}</td>
      <td>{{ log.username or '-' }}</td>
      <td><span class="badge badge-muted">{{ log.action }}</span></td>
      <td style="font-size:0.85rem;">{{ log.resource_type or '' }} {{ log.resource_id or '' }}</td>
      <td style="font-size:0.82rem;color:var(--pico-muted-color);">{{ log.ip or '-' }}</td>
      <td style="font-size:0.82rem;max-width:200px;overflow:hidden;text-overflow:ellipsis;">{{ log.detail or '' }}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</article>
<div style="display:flex;gap:0.5rem;justify-content:center;margin-top:1rem;">
  {% if logs.has_prev %}<a href="?page={{ logs.prev_num }}" class="btn-sm">上一页</a>{% endif %}
  <span style="padding:0.3rem 0.6rem;">{{ logs.page }}/{{ logs.pages }}</span>
  {% if logs.has_next %}<a href="?page={{ logs.next_num }}" class="btn-sm">下一页</a>{% endif %}
</div>
{% endblock %}
```

在 `templates/admin/dashboard.html` 导航处加：
```html
<a href="{{ url_for('admin.audit_log') }}" class="btn-sm">审计日志</a>
```

在 `templates/base.html` 用户菜单中（settings 链接附近）加：
```html
<a href="{{ url_for('user.list_tokens') }}">API Tokens</a>
```

- [ ] **Step 1: 在 app.py 加 `_audit()` 辅助函数并在关键路由埋点**
- [ ] **Step 2: 加 API Token 认证中间件（`before_request`）**
- [ ] **Step 3: 加 API Token CRUD 路由（user_bp）和管理员审计日志路由（admin_bp）**
- [ ] **Step 4: 创建 templates/user/tokens.html**
- [ ] **Step 5: 创建 templates/admin/audit.html**
- [ ] **Step 6: 在 base.html 用户菜单加 API Tokens 链接**
- [ ] **Step 7: 在 admin/dashboard.html 加审计日志链接**
- [ ] **Step 8: 写测试**
```python
# tests/test_routes.py 末尾追加
def test_create_and_revoke_token(auth_client, app):
    resp = auth_client.post("/settings/tokens/create", data={"name": "test-tok"}, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        from models import ApiToken
        t = ApiToken.query.filter_by(name="test-tok").first()
        assert t and t.is_active
    resp2 = auth_client.post(f"/settings/tokens/{t.id}/revoke", follow_redirects=True)
    assert resp2.status_code == 200
    with app.app_context():
        from models import ApiToken
        t2 = ApiToken.query.get(t.id)
        assert not t2.is_active

def test_api_token_auth(app, sample_repo):
    import secrets, hashlib
    client, repo_info = sample_repo
    with app.app_context():
        from models import ApiToken, User, db
        u = User.query.filter_by(username="alice").first()
        raw = secrets.token_urlsafe(32)
        h = hashlib.sha256(raw.encode()).hexdigest()
        t = ApiToken(user_id=u.id, name="ci-token", token_hash=h)
        db.session.add(t); db.session.commit()
    resp = app.test_client().get(
        f"/alice/{repo_info['slug']}/tasks",
        headers={"Authorization": f"Bearer {raw}"}
    )
    assert resp.status_code == 200

def test_admin_audit_log_accessible(auth_client, app):
    with app.app_context():
        from config import Config
        Config.ADMIN_USERNAME = "alice"
    resp = auth_client.get("/admin/audit")
    assert resp.status_code == 200
```
- [ ] **Step 9: 运行测试**
```bash
python -m pytest tests/ --ignore=tests/test_e2e.py -q
```
- [ ] **Step 10: 提交**
```bash
git add app.py templates/user/tokens.html templates/admin/audit.html templates/admin/dashboard.html templates/base.html tests/test_routes.py
git commit -m "feat: API token auth and audit log"
```

---

## Task 3：公开库访客只读提问

**Files:**
- Modify: `app.py`（query_api、query_stream、wiki view_page 路由放开访客访问）

### 修改要点

当前 `query_api`、`query_stream`、`view_page`、`list_sources`、`wiki search` 均有 `@login_required`。对于 `is_public=True` 的仓库，需要允许未登录访客访问。

**方法：** 将相关路由改为可选认证。移除 `@login_required`，改为手动判断：
```python
# 对于 wiki.view_page, ops.query, ops.query_stream, ops.query_api, wiki.search
# 在函数内部:
user, repo = _get_repo_or_404(username, repo_slug)
if not repo.is_public:
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if current_user.id != repo.user_id:
        abort(403)
```

涉及路由：
- `wiki.view_page` — 已是访客可读，保持
- `ops.query` GET（query 页面）
- `ops.query_api` POST
- `ops.query_stream` GET
- `wiki.search`

对访客查询：`query_with_evidence` 中 `user_id` 改为可 None（QueryLog 中 `user_id` 可 NULL，对应 schema 需要允许 null）。

**migration**: 修改 `query_logs.user_id` 允许 NULL（在 005_wave3.sql 末尾加）：
```sql
ALTER TABLE query_logs MODIFY user_id INT NULL;
```

app.py `query_api` 写入 QueryLog 时：
```python
uid = current_user.id if current_user.is_authenticated else None
```

- [ ] **Step 1: 在 `migrations/005_wave3.sql` 末尾加 `ALTER TABLE query_logs MODIFY user_id INT NULL;`**
- [ ] **Step 2: 修改 `ops.query`（GET）、`ops.query_api`（POST）、`ops.query_stream`（GET）、`wiki.search`：移除 `@login_required`，改为上方逻辑**
- [ ] **Step 3: `query_api` 写 QueryLog 时 uid 改为 `current_user.id if current_user.is_authenticated else None`**
- [ ] **Step 4: 写测试**
```python
def test_public_repo_query_no_login(app, sample_repo):
    """公开仓库，未登录访客可以 POST query"""
    from unittest.mock import patch
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    # 先设为公开
    with app.app_context():
        from models import Repo, db
        r = Repo.query.filter_by(slug=slug).first()
        r.is_public = True; db.session.commit()
    fake = {"markdown":"answer","confidence":{"level":"low","score":0.1,"reasons":[]},"wiki_evidence":[],"chunk_evidence":[],"evidence_summary":"","referenced_pages":[],"wiki_sources":[],"qdrant_sources":[]}
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=fake):
        resp = app.test_client().post(f"/alice/{slug}/query", json={"q":"test"})
    assert resp.status_code == 200

def test_private_repo_query_no_login_redirects(app, sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = app.test_client().post(f"/alice/{slug}/query", json={"q":"test"})
    assert resp.status_code in (302, 401, 403)
```
- [ ] **Step 5: 运行测试通过**
- [ ] **Step 6: 提交**
```bash
git add app.py migrations/005_wave3.sql tests/test_routes.py
git commit -m "feat: public repo guest query access"
```

---

## Task 4：多轮对话会话

**Files:**
- Modify: `app.py`（新增 session CRUD API）
- Modify: `static/js/chat.js`（携带 session_key 和历史消息）
- Modify: `wiki_engine.py`（`query_with_evidence` 接受 `history` 参数）

### 设计

- 前端维护 `session_key`（localStorage，每个 repo 一个，`session_{repoSlug}_{date}`）
- 发送查询时携带 `session_key`，后端从 DB 取历史，追加本次 Q/A 后写回
- 会话最多保存最近 10 轮（20 条消息）
- LLM 查询时将历史消息作为上下文（前 3 轮，避免超长）

### app.py 新增路由（ops_bp 下）

```python
@ops_bp.route("/<username>/<repo_slug>/session", methods=["GET"])
@login_required
def get_session(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    key = request.args.get("key", "")
    if not key:
        return jsonify(messages=[])
    from models import ConversationSession
    s = ConversationSession.query.filter_by(repo_id=repo.id, user_id=user.id, session_key=key).first()
    msgs = json.loads(s.messages_json) if s else []
    return jsonify(messages=msgs)

@ops_bp.route("/<username>/<repo_slug>/session/clear", methods=["POST"])
@login_required
def clear_session(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    key = (request.get_json(silent=True) or {}).get("key", "")
    if key:
        from models import ConversationSession
        s = ConversationSession.query.filter_by(repo_id=repo.id, user_id=user.id, session_key=key).first()
        if s:
            s.messages_json = "[]"; db.session.commit()
    return jsonify(ok=True)
```

在 `query_api` POST 中追加会话存储逻辑：
```python
# 读取 session_key
session_key = data.get("session_key", "")
history = []
if session_key and current_user.is_authenticated:
    from models import ConversationSession
    s = ConversationSession.query.filter_by(
        repo_id=repo.id, user_id=current_user.id, session_key=session_key
    ).first()
    if s:
        history = json.loads(s.messages_json)

# 调用 query_with_evidence 时传入 history
result = current_app.wiki_engine.query_with_evidence(
    repo, username, question,
    wiki_base_url=_wiki_base_url(username, repo_slug),
    history=history[-6:],  # 最近 3 轮
)

# 写回会话
if session_key and current_user.is_authenticated:
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": result.get("markdown", "")[:2000]})
    history = history[-20:]  # 最多 10 轮
    from models import ConversationSession
    s = ConversationSession.query.filter_by(
        repo_id=repo.id, user_id=current_user.id, session_key=session_key
    ).first()
    if s:
        s.messages_json = json.dumps(history, ensure_ascii=False)
    else:
        s = ConversationSession(repo_id=repo.id, user_id=current_user.id,
                                session_key=session_key, messages_json=json.dumps(history, ensure_ascii=False))
        db.session.add(s)
    db.session.commit()
```

### wiki_engine.py query_with_evidence 签名更新

```python
def query_with_evidence(
    self,
    repo: Any,
    username: str,
    question: str,
    wiki_base_url: str = "",
    history: list[dict] | None = None,
) -> dict[str, Any]:
```

在构建 `system_base` 之后、生成 answer 之前，如果 history 非空则在 context_block 前加历史：
```python
history_block = ""
if history:
    parts = []
    for msg in history[-6:]:
        role = "用户" if msg.get("role") == "user" else "助手"
        parts.append(f"{role}：{msg.get('content','')[:500]}")
    history_block = "--- 对话历史 ---\n" + "\n".join(parts) + "\n\n"

answer = self._chat_text(
    system=system_base,
    user=(
        "根据以下 Wiki 页面和原文片段回答用户问题，使用 Markdown 格式。\n"
        "若证据不足，请使用指定提示语。\n\n"
        + history_block +
        f"--- 问题 ---\n{question}\n\n"
        f"--- Wiki 内容 ---\n{context_block}"
    ),
)
```

### chat.js 更新

```javascript
// 在 form.addEventListener('submit') 中，生成/获取 session_key
var SESSION_KEY = 'session_' + (cfg.repoSlug || 'default') + '_' + new Date().toISOString().slice(0,10);

// 非 SSE fallback POST 时追加 session_key
body: JSON.stringify({ q: q, session_key: SESSION_KEY })

// SSE done 事件的 render-only POST 时追加 session_key
body: JSON.stringify({ q: q, _rendered_answer: answer, session_key: SESSION_KEY, ... })
```

在 chat 输入框旁加"清空会话"按钮：
```html
<button type="button" id="clear-session-btn" title="清空对话历史" style="...">
  <i data-lucide="refresh-ccw" aria-hidden="true"></i>
</button>
```

- [ ] **Step 1: wiki_engine.py `query_with_evidence` 加 `history` 参数和历史上下文拼接**
- [ ] **Step 2: app.py `query_api` 加会话读写逻辑**
- [ ] **Step 3: app.py 加 `get_session`、`clear_session` 路由**
- [ ] **Step 4: chat.js 加 `SESSION_KEY` 生成和携带逻辑，加清空会话按钮**
- [ ] **Step 5: 写测试**
```python
def test_query_api_stores_session(sample_repo, app):
    from unittest.mock import patch
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake = {"markdown":"answer","confidence":{"level":"low","score":0.1,"reasons":[]},"wiki_evidence":[],"chunk_evidence":[],"evidence_summary":"","referenced_pages":[],"wiki_sources":[],"qdrant_sources":[]}
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=fake):
        resp = client.post(f"/alice/{slug}/query", json={"q":"hello", "session_key":"test-sess-1"})
    assert resp.status_code == 200
    with app.app_context():
        from models import ConversationSession
        s = ConversationSession.query.filter_by(session_key="test-sess-1").first()
        assert s is not None
        import json as _j
        msgs = _j.loads(s.messages_json)
        assert any(m["content"] == "hello" for m in msgs)

def test_clear_session(sample_repo, app):
    from unittest.mock import patch
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from models import ConversationSession, User, db
        u = User.query.filter_by(username="alice").first()
        s = ConversationSession(repo_id=repo_info["id"], user_id=u.id,
                                session_key="clr-sess", messages_json='[{"role":"user","content":"hi"}]')
        db.session.add(s); db.session.commit()
    resp = client.post(f"/alice/{slug}/session/clear", json={"key":"clr-sess"})
    assert resp.status_code == 200
    with app.app_context():
        from models import ConversationSession
        s2 = ConversationSession.query.filter_by(session_key="clr-sess").first()
        assert s2.messages_json == "[]"
```
- [ ] **Step 6: 运行测试通过**
- [ ] **Step 7: 提交**
```bash
git add app.py wiki_engine.py static/js/chat.js tests/test_routes.py tests/test_wiki_engine.py
git commit -m "feat: multi-turn conversation sessions"
```

---

## Task 5：整库导入/克隆 (Import ZIP)

**Files:**
- Modify: `app.py`（新增 import.zip 路由）
- Modify: `templates/repo/settings.html`（加导入入口）

### 路由设计

```
POST /<username>/<repo_slug>/import.zip
Content-Type: multipart/form-data
file: <zip file>
```

逻辑：
1. 接收 ZIP，解压到 `temp/import_<uuid>/`
2. 验证 ZIP 结构：需含 `wiki/` 目录（和/或 `raw/` 目录）
3. 清空或合并（提供选项：`mode=replace|merge`）
4. 将 `wiki/` 中每个 `.md` 文件复制到 `data/<user>/<repo>/wiki/`
5. 将 `raw/` 中文件复制到 `data/<user>/<repo>/raw/`
6. 创建后台任务 `type=rebuild_index`，异步重建 Qdrant page+chunk index
7. 清理 temp 目录

### app.py 新增路由（repo_bp 下）

```python
@repo_bp.route("/<username>/<repo_slug>/import.zip", methods=["POST"])
@login_required
def import_zip(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    _require_owner(repo)
    f = request.files.get("file")
    if not f or not f.filename.endswith(".zip"):
        flash("请上传 .zip 文件", "error")
        return redirect(url_for("repo.settings", username=username, repo_slug=repo_slug))

    import zipfile, uuid as _uuid, shutil as _shutil
    mode = request.form.get("mode", "merge")
    base = get_repo_path(Config.DATA_DIR, username, repo_slug)
    tmp = os.path.join(base, f"temp_import_{_uuid.uuid4().hex[:8]}")
    os.makedirs(tmp, exist_ok=True)
    try:
        zip_path = os.path.join(tmp, "upload.zip")
        f.save(zip_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)
        os.remove(zip_path)

        # 找 wiki/ 目录
        wiki_src = os.path.join(tmp, "wiki")
        raw_src = os.path.join(tmp, "raw")
        if not os.path.isdir(wiki_src):
            # 有些导出可能是 <repo>/wiki/ 嵌套一层
            for sub in os.listdir(tmp):
                candidate = os.path.join(tmp, sub, "wiki")
                if os.path.isdir(candidate):
                    wiki_src = candidate
                    raw_src = os.path.join(tmp, sub, "raw")
                    break

        if not os.path.isdir(wiki_src):
            flash("ZIP 中未找到 wiki/ 目录", "error")
            return redirect(url_for("repo.settings", username=username, repo_slug=repo_slug))

        wiki_dst = os.path.join(base, "wiki")
        raw_dst = os.path.join(base, "raw")
        os.makedirs(wiki_dst, exist_ok=True)
        os.makedirs(raw_dst, exist_ok=True)

        if mode == "replace":
            for fn in os.listdir(wiki_dst):
                fp = os.path.join(wiki_dst, fn)
                if os.path.isfile(fp): os.remove(fp)

        imported_wiki = 0
        for fn in os.listdir(wiki_src):
            if fn.endswith(".md"):
                _shutil.copy2(os.path.join(wiki_src, fn), os.path.join(wiki_dst, fn))
                imported_wiki += 1
        if os.path.isdir(raw_src):
            for fn in os.listdir(raw_src):
                src_fp = os.path.join(raw_src, fn)
                if os.path.isfile(src_fp):
                    _shutil.copy2(src_fp, os.path.join(raw_dst, fn))

        # 提交后台重建任务
        task = Task(repo_id=repo.id, type="rebuild_index",
                    status="queued", input_data="import_zip")
        db.session.add(task); db.session.commit()
        _sync_repo_counts(repo, username)
        _audit("import_zip", "repo", repo.id, f"mode={mode} wiki_pages={imported_wiki}")
        flash(f"导入完成，共 {imported_wiki} 个 Wiki 页面。索引重建已加入队列。", "success")
    except Exception as exc:
        logger.exception("import_zip failed")
        flash(f"导入失败：{exc}", "error")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)
    return redirect(url_for("repo.dashboard", username=username, repo_slug=repo_slug))
```

### task_worker.py 新增 rebuild_index 任务处理

```python
# task_worker.py 的 _process_task 中，在 elif task.type == "lint": 前加：
elif task.type == "rebuild_index":
    # 重建 page + chunk 索引
    wiki_dir = os.path.join(data_dir, username, repo.slug, "wiki")
    pages = list_wiki_pages(wiki_dir)
    for page in pages:
        fpath = os.path.join(wiki_dir, page["filename"])
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            app.qdrant.upsert_page(repo_id=repo.id, filename=page["filename"],
                                   title=page["title"], page_type=page["type"], content=content)
            app.qdrant.upsert_page_chunks(repo_id=repo.id, filename=page["filename"],
                                          title=page["title"], page_type=page["type"], content=content)
        except Exception as e:
            logger.warning("rebuild_index failed for %s: %s", page["filename"], e)
    output = {"rebuilt": len(pages)}
```

在 `templates/repo/settings.html` 中加导入区块：
```html
<section>
  <h3>导入 Wiki（ZIP）</h3>
  <p style="color:var(--pico-muted-color);font-size:0.9rem;">从导出的 ZIP 文件恢复或迁移知识库。</p>
  <form method="POST" enctype="multipart/form-data"
        action="{{ url_for('repo.import_zip', username=username, repo_slug=repo.slug) }}">
    <div style="display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center;">
      <input type="file" name="file" accept=".zip" required>
      <select name="mode">
        <option value="merge">合并（保留现有页面）</option>
        <option value="replace">替换（清空后导入）</option>
      </select>
      <button type="submit" class="action-btn action-btn-primary">导入</button>
    </div>
  </form>
</section>
```

- [ ] **Step 1: app.py 加 `import_zip` 路由（repo_bp 下）**
- [ ] **Step 2: task_worker.py 加 `rebuild_index` 任务处理逻辑**
- [ ] **Step 3: templates/repo/settings.html 加导入区块**
- [ ] **Step 4: 写测试**
```python
def test_import_zip_route(sample_repo, app):
    import io, zipfile
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("wiki/test-page.md", "---\ntitle: Test\ntype: concept\n---\n\n# Test\n")
    buf.seek(0)
    resp = client.post(f"/alice/{slug}/import.zip",
                       data={"file": (buf, "export.zip"), "mode": "merge"},
                       content_type="multipart/form-data", follow_redirects=True)
    assert resp.status_code == 200
    import os
    from config import Config
    from utils import get_repo_path
    with app.app_context():
        base = get_repo_path(Config.DATA_DIR, "alice", slug)
        assert os.path.isfile(os.path.join(base, "wiki", "test-page.md"))
```
- [ ] **Step 5: 运行测试通过**
- [ ] **Step 6: 提交**
```bash
git add app.py task_worker.py templates/repo/settings.html tests/test_routes.py
git commit -m "feat: import ZIP to restore/clone wiki"
```

---

## Task 6：仓库 README 说明页

**Files:**
- Modify: `app.py`（新增 edit_readme 路由）
- Modify: `templates/repo/dashboard.html`（展示 README）
- Modify: `templates/repo/settings.html`（编辑 README）

### 设计

README 存储在文件 `data/<user>/<repo>/README.md`（不在 wiki/ 目录内，不参与索引）。

### app.py

```python
@repo_bp.route("/<username>/<repo_slug>/readme", methods=["POST"])
@login_required
def save_readme(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    _require_owner(repo)
    content = request.form.get("readme", "").strip()
    base = get_repo_path(Config.DATA_DIR, username, repo_slug)
    readme_path = os.path.join(base, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(content)
    flash("README 已保存", "success")
    return redirect(url_for("repo.settings", username=username, repo_slug=repo_slug))
```

在 `repo.dashboard` 路由中读取并传入 README：
```python
readme_path = os.path.join(base, "README.md")
readme_html = ""
if os.path.isfile(readme_path):
    raw_readme = open(readme_path, encoding="utf-8").read()
    from utils import render_markdown
    _, readme_html = render_markdown(raw_readme)
```

在 `templates/repo/dashboard.html` 的知识库信息卡片中展示 README：
```html
{% if readme_html %}
<div class="kb-readme rendered-content" style="margin-top:0.75rem;padding:0.75rem;background:var(--pico-card-background-color);border-radius:6px;font-size:0.9rem;">
  {{ readme_html|safe }}
</div>
{% endif %}
```

在 `templates/repo/settings.html` 中加 README 编辑区：
```html
<section>
  <h3>知识库说明（README）</h3>
  <form method="POST" action="{{ url_for('repo.save_readme', username=username, repo_slug=repo.slug) }}">
    <textarea name="readme" rows="8" placeholder="用 Markdown 描述这个知识库的用途、内容范围、使用说明等…"
              style="width:100%;font-family:monospace;">{{ readme_content or '' }}</textarea>
    <button type="submit" class="action-btn action-btn-primary" style="margin-top:0.5rem;">保存 README</button>
  </form>
</section>
```

- [ ] **Step 1: app.py 加 `save_readme` 路由，dashboard 路由读取 readme_html**
- [ ] **Step 2: settings.html 加 README 编辑区（注意在 `settings` 路由中也读取 readme_content）**
- [ ] **Step 3: dashboard.html 展示 readme_html**
- [ ] **Step 4: 写测试并运行通过**
```python
def test_save_and_show_readme(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(f"/alice/{slug}/readme",
                       data={"readme":"# 说明\n\n这是测试知识库。"}, follow_redirects=True)
    assert resp.status_code == 200
    resp2 = client.get(f"/alice/{slug}")
    assert "说明" in resp2.data.decode("utf-8")
```
- [ ] **Step 5: 提交**
```bash
git add app.py templates/repo/dashboard.html templates/repo/settings.html tests/test_routes.py
git commit -m "feat: repo README page"
```

---

## Task 7：重复来源检测

**Files:**
- Modify: `utils.py`（新增 `file_md5()`）
- Modify: `app.py`（upload 时检测重复）

### utils.py

```python
def file_md5(path: str) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
```

### app.py upload 路由（在 `uploaded.save()` 后检查）

```python
# 检测重复（对 md/txt 直接保存的情形）
if ext in ("md", "txt"):
    save_path = os.path.join(raw_dir, safe_name)
    # 先暂存到 temp 判断重复
    temp_check = os.path.join(raw_dir, f"_tmp_{safe_name}")
    uploaded.save(temp_check)
    new_md5 = file_md5(temp_check)
    dup = None
    for existing in os.listdir(raw_dir):
        if existing.startswith("_tmp_") or existing == safe_name:
            continue
        ep = os.path.join(raw_dir, existing)
        if os.path.isfile(ep):
            try:
                if file_md5(ep) == new_md5:
                    dup = existing
                    break
            except Exception:
                pass
    if dup:
        os.remove(temp_check)
        flash(f"文件内容与已有文件「{dup}」重复，跳过上传。", "warning")
        return redirect(url_for("source.list_sources", username=username, repo_slug=repo_slug))
    os.rename(temp_check, save_path)
    saved_name = safe_name
```

对 MinerU 文件也加同样的 MD5 重复检测（基于 temp_path 的 MD5 与 originals/ 目录比对）。

- [ ] **Step 1: utils.py 加 `file_md5()`**
- [ ] **Step 2: app.py upload 路由加重复检测**
- [ ] **Step 3: 写测试**
```python
def test_duplicate_upload_warns(sample_repo):
    import io
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    content = b"# Duplicate Content\n\nSame text.\n"
    resp1 = client.post(f"/alice/{slug}/sources/upload",
                        data={"file": (io.BytesIO(content), "dup-test.md")},
                        content_type="multipart/form-data", follow_redirects=True)
    assert resp1.status_code == 200
    resp2 = client.post(f"/alice/{slug}/sources/upload",
                        data={"file": (io.BytesIO(content), "dup-test2.md")},
                        content_type="multipart/form-data", follow_redirects=True)
    assert "重复" in resp2.data.decode("utf-8")
```
- [ ] **Step 4: 运行测试通过**
- [ ] **Step 5: 提交**
```bash
git add utils.py app.py tests/test_routes.py
git commit -m "feat: duplicate source detection on upload"
```

---

## Task 8：知识缺口发现 + 实体对齐去重

**Files:**
- Modify: `wiki_engine.py`（新增 `find_gaps()`、`find_entity_duplicates()`）
- Modify: `app.py`（新增 `/insights`、`/entity-check` 路由）
- Create: `templates/ops/insights.html`
- Create: `templates/ops/entity_check.html`

### wiki_engine.py 新增方法

```python
def find_gaps(
    self,
    repo: Any,
    username: str,
    query_logs: list[dict],  # [{"question": "...", "confidence": "low"}]
) -> dict[str, Any]:
    """分析查询日志和现有 Wiki，找出知识缺口并提出补充建议。"""
    wiki_dir = self._wiki_dir(username, repo.slug)
    pages = list_wiki_pages(wiki_dir)
    schema_content = _read_file(self._schema_path(username, repo.slug))

    pages_summary = "\n".join(
        f"- {p['filename']}: {p['title']} (type: {p['type']})"
        for p in pages if p["filename"] not in ("log.md", "schema.md")
    )
    low_conf_q = "\n".join(
        f"- {q['question']}" for q in query_logs[:30]
    )

    result = self._chat_json(
        system=(
            "你是一个知识库分析师。根据用户的问题历史和当前 Wiki 内容，找出知识缺口。\n\n"
            + (schema_content or "")
        ),
        user=(
            "分析以下低置信度问题（Wiki 无法很好回答的问题）和现有 Wiki 页面，"
            "识别知识缺口并给出具体补充建议。\n\n"
            '返回 JSON：\n'
            '{"gaps": [{"topic": "主题名称", "description": "缺口说明", '
            '"suggested_sources": ["建议来源类型"], "priority": "high|medium|low"}], '
            '"summary": "总体分析"}\n\n'
            f"--- 低置信度问题（近期无法很好回答的） ---\n{low_conf_q or '(无)'}\n\n"
            f"--- 现有 Wiki 页面 ---\n{pages_summary or '(空)'}"
        ),
        default={"gaps": [], "summary": "暂无分析数据"},
    )
    return result


def find_entity_duplicates(
    self,
    repo: Any,
    username: str,
) -> dict[str, Any]:
    """识别 Wiki 中可能重复或指代同一概念的页面。"""
    wiki_dir = self._wiki_dir(username, repo.slug)
    pages = list_wiki_pages(wiki_dir)
    schema_content = _read_file(self._schema_path(username, repo.slug))

    pages_detail = []
    for p in pages:
        if p["filename"] in ("log.md", "schema.md", "index.md"):
            continue
        content = _read_file(os.path.join(wiki_dir, p["filename"]))
        fm, _ = render_markdown(content)
        tags = fm.get("tags", [])
        pages_detail.append(
            f"- {p['filename']}: {p['title']} (type: {p['type']}, tags: {tags})"
        )

    result = self._chat_json(
        system=(
            "你是一个 Wiki 质量审查员。识别可能重复的页面并建议合并。\n\n"
            + (schema_content or "")
        ),
        user=(
            "分析以下 Wiki 页面列表，找出可能指代同一概念或高度重叠的页面组。\n\n"
            '返回 JSON：\n'
            '{"duplicate_groups": [{"pages": ["a.md", "b.md"], '
            '"reason": "重复原因", "suggestion": "建议操作"}], '
            '"total_issues": 0}\n\n'
            f"--- Wiki 页面列表 ---\n" + "\n".join(pages_detail)
        ),
        default={"duplicate_groups": [], "total_issues": 0},
    )
    return result
```

### app.py 新增路由（ops_bp 下）

```python
@ops_bp.route("/<username>/<repo_slug>/insights")
@login_required
def insights(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    from models import QueryLog
    low_conf_logs = (
        QueryLog.query
        .filter_by(repo_id=repo.id, confidence="low")
        .order_by(QueryLog.created_at.desc())
        .limit(50)
        .all()
    )
    query_log_data = [{"question": q.question, "confidence": q.confidence} for q in low_conf_logs]
    gaps = None
    if request.method == "POST" or request.args.get("analyze"):
        gaps = current_app.wiki_engine.find_gaps(repo, username, query_log_data)
    return render_template("ops/insights.html", username=username, repo=repo,
                           low_conf_logs=low_conf_logs, gaps=gaps)

@ops_bp.route("/<username>/<repo_slug>/entity-check")
@login_required
def entity_check(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    result = None
    if request.args.get("analyze"):
        result = current_app.wiki_engine.find_entity_duplicates(repo, username)
    return render_template("ops/entity_check.html", username=username, repo=repo, result=result)
```

### templates/ops/insights.html

```html
{% extends "base.html" %}
{% block title %}知识缺口分析 - {{ repo.name }}{% endblock %}
{% block content %}
<nav aria-label="面包屑导航"><ul class="breadcrumb">
  <li><a href="{{ url_for('repo.dashboard', username=username, repo_slug=repo.slug) }}">{{ repo.name }}</a></li>
  <li>知识缺口分析</li>
</ul></nav>
<header class="page-header">
  <h2><i class="lucide-search-x" aria-hidden="true"></i> 知识缺口分析</h2>
  <a href="?analyze=1" class="action-btn action-btn-primary">
    <i class="lucide-sparkles" aria-hidden="true"></i> 开始分析
  </a>
</header>

<article>
  <h3>低置信度问题（{{ low_conf_logs|length }} 条）</h3>
  {% if low_conf_logs %}
  <ul style="font-size:0.9rem;">
    {% for q in low_conf_logs[:20] %}
    <li style="margin-bottom:0.3rem;">
      <span style="color:var(--pico-muted-color);font-size:0.82rem;">{{ q.created_at.strftime('%m-%d') }}</span>
      {{ q.question }}
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p style="color:var(--pico-muted-color);">暂无低置信度查询记录。</p>
  {% endif %}
</article>

{% if gaps %}
<article>
  <h3>分析结果</h3>
  <p>{{ gaps.summary }}</p>
  {% if gaps.gaps %}
  <div style="display:flex;flex-direction:column;gap:0.75rem;margin-top:1rem;">
    {% for gap in gaps.gaps %}
    <div style="padding:0.75rem;border:1px solid var(--pico-muted-border-color);border-radius:6px;">
      <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem;">
        <strong>{{ gap.topic }}</strong>
        <span class="badge badge-{{ 'error' if gap.priority=='high' else 'muted' }}">{{ gap.priority }}</span>
      </div>
      <p style="margin:0;font-size:0.9rem;color:var(--pico-muted-color);">{{ gap.description }}</p>
      {% if gap.suggested_sources %}
      <p style="margin:0.3rem 0 0;font-size:0.85rem;">建议来源：{{ gap.suggested_sources|join('、') }}</p>
      {% endif %}
    </div>
    {% endfor %}
  </div>
  {% else %}
  <p>未发现明显知识缺口。</p>
  {% endif %}
</article>
{% endif %}
{% endblock %}
```

### templates/ops/entity_check.html

```html
{% extends "base.html" %}
{% block title %}实体去重检查 - {{ repo.name }}{% endblock %}
{% block content %}
<nav aria-label="面包屑导航"><ul class="breadcrumb">
  <li><a href="{{ url_for('repo.dashboard', username=username, repo_slug=repo.slug) }}">{{ repo.name }}</a></li>
  <li>实体去重检查</li>
</ul></nav>
<header class="page-header">
  <h2><i class="lucide-git-merge" aria-hidden="true"></i> 实体去重检查</h2>
  <a href="?analyze=1" class="action-btn action-btn-primary">
    <i class="lucide-sparkles" aria-hidden="true"></i> 开始检测
  </a>
</header>
{% if result %}
<article>
  <h3>检测结果（{{ result.total_issues }} 组潜在重复）</h3>
  {% if result.duplicate_groups %}
  {% for grp in result.duplicate_groups %}
  <div style="padding:0.75rem;border:1px solid var(--pico-muted-border-color);border-radius:6px;margin-bottom:0.75rem;">
    <div style="margin-bottom:0.3rem;">
      {% for pg in grp.pages %}
      <a href="{{ url_for('wiki.view_page', username=username, repo_slug=repo.slug, page_slug=pg.replace('.md','')) }}" class="badge badge-muted" style="margin-right:0.3rem;">{{ pg }}</a>
      {% endfor %}
    </div>
    <p style="margin:0;font-size:0.9rem;color:var(--pico-muted-color);">{{ grp.reason }}</p>
    <p style="margin:0.3rem 0 0;font-size:0.85rem;">建议：{{ grp.suggestion }}</p>
  </div>
  {% endfor %}
  {% else %}
  <p>未发现重复实体页面。</p>
  {% endif %}
</article>
{% else %}
<article><p style="color:var(--pico-muted-color);">点击「开始检测」分析 Wiki 中可能重复的概念页面。</p></article>
{% endif %}
{% endblock %}
```

在 `templates/repo/dashboard.html` 的操作下拉菜单里加两个入口：
```html
<a href="{{ url_for('ops.insights', username=username, repo_slug=repo.slug) }}">知识缺口分析</a>
<a href="{{ url_for('ops.entity_check', username=username, repo_slug=repo.slug) }}">实体去重检查</a>
```

- [ ] **Step 1: wiki_engine.py 加 `find_gaps()` 和 `find_entity_duplicates()`**
- [ ] **Step 2: app.py 加 `insights` 和 `entity_check` 路由**
- [ ] **Step 3: 创建 templates/ops/insights.html**
- [ ] **Step 4: 创建 templates/ops/entity_check.html**
- [ ] **Step 5: dashboard.html 加链接入口**
- [ ] **Step 6: 写测试**
```python
def test_insights_page_accessible(sample_repo):
    client, repo_info = sample_repo
    resp = client.get(f"/alice/{repo_info['slug']}/insights")
    assert resp.status_code == 200

def test_entity_check_page_accessible(sample_repo):
    client, repo_info = sample_repo
    resp = client.get(f"/alice/{repo_info['slug']}/entity-check")
    assert resp.status_code == 200
```
- [ ] **Step 7: 运行测试通过**
- [ ] **Step 8: 提交**
```bash
git add wiki_engine.py app.py templates/ops/insights.html templates/ops/entity_check.html templates/repo/dashboard.html tests/test_routes.py
git commit -m "feat: knowledge gap discovery and entity dedup check"
```

---

## Task 9：语义检索 UI

**Files:**
- Modify: `app.py`（新增 `/search/semantic` 路由）
- Create: `templates/wiki/semantic_search.html`

### app.py

```python
@wiki_bp.route("/<username>/<repo_slug>/search/semantic")
@login_required
def semantic_search(username, repo_slug):
    user, repo = _get_repo_or_404(username, repo_slug)
    q = request.args.get("q", "").strip()
    results = []
    if q and current_app.qdrant:
        try:
            results = current_app.qdrant.search_chunks(repo_id=repo.id, query=q, limit=15)
        except Exception as exc:
            flash(f"语义检索失败：{exc}", "error")
    return render_template("wiki/semantic_search.html", username=username, repo=repo,
                           q=q, results=results)
```

### templates/wiki/semantic_search.html

```html
{% extends "base.html" %}
{% block title %}语义检索 - {{ repo.name }}{% endblock %}
{% block content %}
<nav aria-label="面包屑导航"><ul class="breadcrumb">
  <li><a href="{{ url_for('repo.dashboard', username=username, repo_slug=repo.slug) }}">{{ repo.name }}</a></li>
  <li>语义检索</li>
</ul></nav>
<header class="page-header">
  <h2><i class="lucide-brain" aria-hidden="true"></i> 语义检索</h2>
</header>
<article>
  <form method="GET" style="display:flex;gap:0.5rem;margin-bottom:1.5rem;">
    <input type="text" name="q" value="{{ q }}" placeholder="输入任意描述，按语义相似度搜索原文片段…"
           style="flex:1;" autofocus>
    <button type="submit" class="action-btn action-btn-primary">搜索</button>
  </form>
  {% if q %}
  <p style="color:var(--pico-muted-color);font-size:0.9rem;">找到 {{ results|length }} 个相关片段</p>
  {% if results %}
  <div style="display:flex;flex-direction:column;gap:0.75rem;">
    {% for r in results %}
    <div style="padding:0.75rem;border:1px solid var(--pico-muted-border-color);border-radius:6px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.4rem;">
        <a href="{{ url_for('wiki.view_page', username=username, repo_slug=repo.slug, page_slug=r.filename.replace('.md','')) }}"
           style="font-weight:600;">{{ r.page_title or r.filename }}</a>
        <span style="font-size:0.8rem;color:var(--pico-muted-color);">
          相似度 {{ (r.score * 100)|round|int }}%
          {% if r.heading %} · § {{ r.heading }}{% endif %}
        </span>
      </div>
      <p style="margin:0;font-size:0.88rem;color:var(--pico-muted-color);
                overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;">
        {{ r.chunk_text[:300] }}
      </p>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <p>未找到相关片段。请先摄入文档以建立语义索引。</p>
  {% endif %}
  {% endif %}
</article>
{% endblock %}
```

在 `templates/base.html` 导航加语义检索入口（仅在 repo 上下文），或在 `templates/repo/dashboard.html` 工具栏加链接：
```html
<a href="{{ url_for('wiki.semantic_search', username=username, repo_slug=repo.slug) }}"
   title="语义检索" class="icon-btn">
  <i data-lucide="brain" aria-hidden="true"></i>
</a>
```

- [ ] **Step 1: app.py 加 `semantic_search` 路由**
- [ ] **Step 2: 创建 templates/wiki/semantic_search.html**
- [ ] **Step 3: dashboard.html 加语义检索入口**
- [ ] **Step 4: 写测试**
```python
def test_semantic_search_accessible(sample_repo):
    client, repo_info = sample_repo
    resp = client.get(f"/alice/{repo_info['slug']}/search/semantic?q=test")
    assert resp.status_code == 200
    assert "语义检索" in resp.data.decode("utf-8")
```
- [ ] **Step 5: 运行测试通过**
- [ ] **Step 6: 提交**
```bash
git add app.py templates/wiki/semantic_search.html templates/repo/dashboard.html tests/test_routes.py
git commit -m "feat: semantic search UI using chunk index"
```

---

## Task 10：统一健康面板 + 管理后台扩展 + 低置信度汇总

**Files:**
- Modify: `app.py`（admin_bp 新增路由 + 扩展 dashboard）
- Modify: `templates/admin/dashboard.html`（扩展）
- Create: `templates/admin/query_stats.html`

### admin_bp 路由扩展

```python
@admin_bp.route("/health")
@login_required
def health_detail():
    _require_admin()
    import httpx as _httpx
    from config import Config
    checks = {}
    services = {
        "qdrant": (Config.QDRANT_URL + "/healthz", "GET"),
        "mineru": (Config.MINERU_API_URL + "/health", "GET"),
        "embedding": (Config.EMBEDDING_API_BASE.rstrip("/") + "/models", "GET"),
    }
    for name, (url, method) in services.items():
        try:
            r = _httpx.request(method, url, timeout=5)
            checks[name] = {"status": "ok" if r.status_code < 400 else "error",
                            "latency_ms": round(r.elapsed.total_seconds() * 1000)}
        except Exception as e:
            checks[name] = {"status": "error", "error": str(e)[:80]}
    # Task queue stats
    from models import Task
    checks["task_queue"] = {
        "queued": Task.query.filter_by(status="queued").count(),
        "running": Task.query.filter_by(status="running").count(),
        "failed_24h": Task.query.filter(
            Task.status == "failed",
            Task.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0)
        ).count(),
    }
    return render_template("admin/health.html", checks=checks)


@admin_bp.route("/query-stats")
@login_required
def query_stats():
    _require_admin()
    from models import QueryLog
    from sqlalchemy import func
    total = QueryLog.query.count()
    by_conf = db.session.query(QueryLog.confidence, func.count()).group_by(QueryLog.confidence).all()
    by_repo = (db.session.query(Repo.name, func.count(QueryLog.id))
               .join(QueryLog, QueryLog.repo_id == Repo.id)
               .group_by(Repo.id).order_by(func.count(QueryLog.id).desc()).limit(10).all())
    recent_low = (QueryLog.query.filter_by(confidence="low")
                  .order_by(QueryLog.created_at.desc()).limit(20).all())
    return render_template("admin/query_stats.html", total=total,
                           by_conf=by_conf, by_repo=by_repo, recent_low=recent_low)
```

创建 `templates/admin/health.html`（简单服务状态卡片）和 `templates/admin/query_stats.html`（查询统计表）。

在 `templates/admin/dashboard.html` 中加导航链接：
```html
<div style="display:flex;gap:0.5rem;margin-bottom:1.5rem;">
  <a href="{{ url_for('admin.health_detail') }}" class="action-btn">服务健康</a>
  <a href="{{ url_for('admin.query_stats') }}" class="action-btn">查询统计</a>
  <a href="{{ url_for('admin.audit_log') }}" class="action-btn">审计日志</a>
</div>
```

- [ ] **Step 1: app.py admin_bp 加 `health_detail`、`query_stats` 路由**
- [ ] **Step 2: 创建 templates/admin/health.html**
- [ ] **Step 3: 创建 templates/admin/query_stats.html**
- [ ] **Step 4: 更新 templates/admin/dashboard.html 加导航**
- [ ] **Step 5: 写测试**
```python
def test_admin_health_accessible(auth_client, app):
    with app.app_context():
        from config import Config; Config.ADMIN_USERNAME = "alice"
    resp = auth_client.get("/admin/health")
    assert resp.status_code == 200

def test_admin_query_stats_accessible(auth_client, app):
    with app.app_context():
        from config import Config; Config.ADMIN_USERNAME = "alice"
    resp = auth_client.get("/admin/query-stats")
    assert resp.status_code == 200
```
- [ ] **Step 6: 运行所有测试**
```bash
python -m pytest tests/ --ignore=tests/test_e2e.py -q
```
- [ ] **Step 7: 全量部署提交**
```bash
git add app.py models.py wiki_engine.py utils.py templates/ static/ migrations/ tests/
git commit -m "feat: admin health panel, query stats, low-confidence insights"
```

---

## 最终文档同步

- [ ] **Step 1: 更新 docs/design.md**
  - §5.1 加三张新表（conversation_sessions, audit_logs, api_tokens）
  - §6.1 加所有新路由
  - §8.x 说明重复检测、知识缺口、语义检索

- [ ] **Step 2: 运行全量测试**
```bash
python -m pytest tests/ --ignore=tests/test_e2e.py -q
```
Expected: 全部通过

- [ ] **Step 3: 最终提交**
```bash
git add docs/design.md
git commit -m "docs: sync design.md for feature wave 3"
git push origin main
```
