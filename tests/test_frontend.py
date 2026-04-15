"""Frontend HTML structure tests using Flask test client.

These verify HTML correctness without a browser — fast, local, CI-friendly.
Catches structural bugs like nested forms, missing elements, broken links.
"""

from __future__ import annotations

import re


# ── Helpers ──────────────────────────────────────────────────

def _login(client, app):
    with app.app_context():
        from models import User, db
        if not User.query.filter_by(username="fe_alice").first():
            u = User(username="fe_alice", email="fe_alice@example.com", display_name="FE Alice")
            u.set_password("pass1234")
            u.email_verified = True
            db.session.add(u)
            db.session.commit()
    client.post("/login", data={"username": "fe_alice", "password": "pass1234"})


def _create_repo(client):
    client.post("/repos/new", data={
        "name": "FE Test KB",
        "slug": "fe-test",
        "description": "Frontend test repo",
    })


def _html(resp) -> str:
    return resp.data.decode("utf-8")


def _assert_no_external_assets(html: str) -> None:
    asset_urls = re.findall(
        r"""<(?:link|script)\b[^>]+(?:href|src)=["']([^"']+)["']""",
        html,
        re.IGNORECASE,
    )
    external = [url for url in asset_urls if url.startswith(("http://", "https://", "//"))]
    assert external == [], f"Unexpected external assets: {external}"


# ── Auth pages ───────────────────────────────────────────────

def test_login_has_form(client):
    html = _html(client.get("/login"))
    assert '<input' in html and 'name="username"' in html
    assert 'name="password"' in html
    assert '<button' in html or 'type="submit"' in html


def test_base_template_does_not_load_google_fonts(client):
    html = _html(client.get("/login"))
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    _assert_no_external_assets(html)


def test_register_has_confirm_password(client):
    html = _html(client.get("/register"))
    assert 'name="confirm_password"' in html


def test_register_has_email_field(client):
    html = _html(client.get("/register"))
    assert 'name="email"' in html


def test_forgot_password_has_email_field(client):
    html = _html(client.get("/forgot-password"))
    assert 'name="email"' in html


# ── Dashboard ────────────────────────────────────────────────

def test_dashboard_has_chat_and_sidebar(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test"))
    assert "kb-sidebar" in html, "Should have sidebar"
    assert "kb-chat" in html or "chat-area" in html, "Should have chat area"
    assert "chat-input" in html, "Should have chat input"


def test_dashboard_has_task_queue_link(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test"))
    assert "/tasks" in html, "Dashboard should link to task queue (in dropdown)"


def test_dashboard_has_doc_management_link(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test"))
    assert "/sources" in html, "Dashboard should link to doc management"


def test_dashboard_exposes_action_panel_groups(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test"))
    assert "更多操作" in html
    assert "文档管理" in html
    assert "任务队列" in html
    assert "维护检查" in html


def test_dashboard_chat_tools_have_visible_labels(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test"))
    assert "历史记录" in html
    assert "清空对话" in html


def test_public_dashboard_hides_session_bar_for_guest(client, app):
    _login(client, app)
    _create_repo(client)
    client.post(
        "/fe_alice/fe-test/settings",
        data={"action": "update_info", "name": "FE Test KB", "description": "", "is_public": "on"},
    )
    client.get("/logout")
    html = _html(client.get("/fe_alice/fe-test"))
    assert "chat-session-bar" not in html


# ── Source list: no nested forms ─────────────────────────────

def _count_nested_forms(html: str) -> int:
    depth = 0
    max_depth = 0
    for m in re.finditer(r"</?form\b", html, re.IGNORECASE):
        if m.group().startswith("<f") or m.group().startswith("<F"):
            depth += 1
            max_depth = max(max_depth, depth)
        else:
            depth = max(0, depth - 1)
    return max_depth


def test_source_list_no_nested_forms(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/sources"))
    assert _count_nested_forms(html) <= 1, "No nested <form> tags allowed"


def test_source_list_has_upload_zone(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/sources"))
    assert 'type="file"' in html, "Should have file input"
    assert "upload" in html.lower(), "Should have upload section"


def test_public_source_list_accessible_to_guest(client, app):
    _login(client, app)
    _create_repo(client)
    client.post(
        "/fe_alice/fe-test/settings",
        data={"action": "update_info", "name": "FE Test KB", "description": "", "is_public": "on"},
    )
    client.get("/logout")
    html = _html(client.get("/fe_alice/fe-test/sources"))
    assert "文档管理" in html
    assert 'type="file"' not in html


# ── Query page ───────────────────────────────────────────────

def test_query_page_has_input_and_button(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/query"))
    assert 'id="query-input"' in html, "Missing #query-input"
    assert 'id="query-submit"' in html, "Missing #query-submit"


def test_query_input_not_hidden(client, app):
    """The query input must not be display:none or type=hidden."""
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/query"))
    assert 'type="hidden"' not in html.split('id="query-input"')[0].rsplit("<input", 1)[-1], \
        "query-input should not be hidden"


# ── Wiki page ────────────────────────────────────────────────

def test_wiki_overview_renders(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/wiki/overview"))
    assert "rendered-content" in html
    assert "概览" in html


def test_wiki_page_has_breadcrumb(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/wiki/overview"))
    assert "breadcrumb" in html


# ── Graph ────────────────────────────────────────────────────

def test_graph_has_container(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/graph"))
    assert "graph-container" in html
    _assert_no_external_assets(html)


def test_wiki_edit_page_uses_local_assets(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/wiki/overview/edit"))
    assert "easymde.min.css" in html
    assert "easymde.min.js" in html
    _assert_no_external_assets(html)


# ── Settings ─────────────────────────────────────────────────

def test_repo_settings_has_name_field(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/settings"))
    assert 'name="name"' in html


def test_repo_settings_form_includes_update_info_action(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/settings"))
    assert 'name="action" value="update_info"' in html


def test_user_settings_has_display_name(client, app):
    _login(client, app)
    html = _html(client.get("/user/settings"))
    assert 'name="display_name"' in html


def test_user_settings_has_delete_account_form(client, app):
    _login(client, app)
    html = _html(client.get("/user/settings"))
    assert 'name="action" value="delete_account"' in html
    assert 'name="confirm_username"' in html
    assert 'name="delete_password"' in html


# ── Task queue ───────────────────────────────────────────────

def test_task_queue_page_accessible(client, app):
    _login(client, app)
    _create_repo(client)
    html = _html(client.get("/fe_alice/fe-test/tasks"))
    assert "任务队列" in html


def test_task_queue_has_cancel_button_for_active_task(client, app):
    import io

    _login(client, app)
    _create_repo(client)
    client.post(
        "/fe_alice/fe-test/sources/upload",
        data={"file": (io.BytesIO(b"# task"), "queue.md")},
        content_type="multipart/form-data",
    )
    html = _html(client.get("/fe_alice/fe-test/tasks"))
    assert "取消" in html


# ── Error pages ──────────────────────────────────────────────

def test_404_has_error_page(client):
    resp = client.get("/nonexistent-xyz-123")
    assert resp.status_code == 404
    assert "error-page" in _html(resp)


# ── Health endpoint ──────────────────────────────────────────

def test_health_returns_json(client):
    resp = client.get("/health")
    assert resp.status_code in (200, 503), f"Unexpected status {resp.status_code}"
    assert "status" in _html(resp)


# ── Upload creates task ──────────────────────────────────────

def test_upload_creates_queued_task(client, app):
    import io
    _login(client, app)
    _create_repo(client)
    resp = client.post(
        "/fe_alice/fe-test/sources/upload",
        data={"file": (io.BytesIO(b"# Frontend test\n\nContent."), "fe-test-doc.md")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        from models import Task
        task = Task.query.filter_by(input_data="fe-test-doc.md").first()
        assert task is not None, "Upload should auto-create ingest task"
        assert task.status == "queued"


# ── All form actions point to valid routes ───────────────────

def test_all_forms_have_valid_action(client, app):
    """Every <form> action should be a valid-looking URL, not empty."""
    _login(client, app)
    _create_repo(client)
    pages = [
        "/fe_alice/fe-test/sources",
        "/fe_alice/fe-test/query",
        "/fe_alice/fe-test/settings",
    ]
    for page in pages:
        html = _html(client.get(page))
        actions = re.findall(r'<form[^>]*action="([^"]*)"', html)
        for action in actions:
            assert action.startswith("/") or action.startswith("http"), \
                f"Form action '{action}' on {page} looks invalid"
