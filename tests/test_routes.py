"""Flask route tests using conftest fixtures."""

import io
from unittest.mock import patch


# -- Auth ------------------------------------------------------------------


def test_login_page_get(client):
    resp = client.get("/login")
    assert resp.status_code == 200


def test_register_page_get(client):
    resp = client.get("/register")
    assert resp.status_code == 200


def test_register_and_login(client, app):
    with app.app_context():
        from models import User

        assert User.query.filter_by(username="routeuser1").first() is None

    resp = client.post(
        "/register",
        data={
            "username": "routeuser1",
            "display_name": "Route User",
            "password": "password123",
            "confirm_password": "password123",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    resp2 = client.post(
        "/login",
        data={"username": "routeuser1", "password": "password123"},
        follow_redirects=False,
    )
    assert resp2.status_code == 302


def test_register_short_password(client):
    resp = client.post(
        "/register",
        data={
            "username": "shortuser",
            "display_name": "S",
            "password": "short",
            "confirm_password": "short",
        },
    )
    assert resp.status_code == 200


def test_register_mismatch_password(client):
    resp = client.post(
        "/register",
        data={
            "username": "mismatchuser",
            "display_name": "M",
            "password": "password123",
            "confirm_password": "password999",
        },
    )
    assert resp.status_code == 200


def test_login_wrong_password(client, app):
    with app.app_context():
        from models import User, db

        u = User(username="wrongpwuser", display_name="W")
        u.set_password("rightpass123")
        db.session.add(u)
        db.session.commit()

    resp = client.post(
        "/login",
        data={"username": "wrongpwuser", "password": "wrong!!!"},
    )
    assert resp.status_code == 200


def test_logout(auth_client):
    resp = auth_client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in (resp.headers.get("Location") or "")


# -- Repo ------------------------------------------------------------------


def test_repo_list(auth_client):
    resp = auth_client.get("/alice", follow_redirects=True)
    assert resp.status_code == 200


def test_create_repo(auth_client):
    resp = auth_client.post(
        "/repos/new",
        data={
            "name": "Created KB",
            "slug": "created-kb",
            "description": "from test",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    resp2 = auth_client.get("/alice", follow_redirects=True)
    assert resp2.status_code == 200
    assert b"created-kb" in resp2.data or b"Created KB" in resp2.data


def test_repo_dashboard(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}")
    assert resp.status_code == 200


def test_repo_settings_get(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/settings")
    assert resp.status_code == 200


def test_repo_delete(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(f"/alice/{slug}/delete", follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers.get("Location") or ""
    assert "/alice" in loc


# -- Wiki ------------------------------------------------------------------


def test_wiki_overview(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/wiki/overview")
    assert resp.status_code == 200


def test_wiki_nonexistent(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/wiki/nonexistent-page-xyz")
    assert resp.status_code == 404


def test_wiki_graph(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/graph")
    assert resp.status_code == 200


# -- Source ----------------------------------------------------------------


def test_sources_list(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/sources")
    assert resp.status_code == 200


def test_upload_md_file(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    url = f"/alice/{slug}/sources/upload"
    data = {"file": (io.BytesIO(b"# Test content\n"), "test.md")}
    resp = client.post(url, data=data, content_type="multipart/form-data", follow_redirects=False)
    assert resp.status_code == 302


def test_upload_no_file(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    url = f"/alice/{slug}/sources/upload"
    resp = client.post(url, data={}, follow_redirects=False)
    assert resp.status_code == 302


# -- Ops -------------------------------------------------------------------


def test_query_page(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/query")
    assert resp.status_code == 200


def test_query_api(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake = {
        "answer": "Test **answer**",
        "referenced_pages": ["overview.md"],
        "suggested_filename": None,
    }
    with patch.object(app.wiki_engine, "query", return_value=fake):
        resp = client.post(f"/alice/{slug}/query", json={"q": "test"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert "html" in data
    assert "markdown" in data


def test_lint_page(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/lint")
    assert resp.status_code == 200


# -- Health & access -------------------------------------------------------


def test_health(client, app):
    with patch.object(app.mineru, "health_check", return_value=True):
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert "status" in data


def test_protected_route_redirect(client):
    resp = client.get("/alice/test-kb/sources", follow_redirects=False)
    assert resp.status_code == 302
    assert "login" in (resp.headers.get("Location") or "").lower()


def test_index_redirect(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "login" in (resp.headers.get("Location") or "").lower()


# -- Wiki edit/delete ------------------------------------------------------


def test_wiki_edit_page_get(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/wiki/overview/edit")
    assert resp.status_code == 200


def test_wiki_edit_page_post_saves(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    new_content = "---\ntitle: 概览\ntype: overview\nupdated: 2026-01-01\n---\n\n# 已编辑\n"
    resp = client.post(
        f"/alice/{slug}/wiki/overview/edit",
        data={"content": new_content},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers.get("Location") or ""
    assert "overview" in loc


def test_wiki_edit_page_empty_content(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/wiki/overview/edit",
        data={"content": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200


def test_wiki_delete_page(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    import os

    from config import Config

    wiki_dir = os.path.join(Config.DATA_DIR, "alice", slug, "wiki")
    test_page = os.path.join(wiki_dir, "to-delete.md")
    with open(test_page, "w") as f:
        f.write("---\ntitle: Delete Me\ntype: concept\n---\n\n# Test\n")
    resp = client.post(
        f"/alice/{slug}/wiki/to-delete/delete",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert not os.path.exists(test_page)


def test_wiki_delete_nonexistent_page(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/wiki/nonexistent-xyz/delete",
        follow_redirects=True,
    )
    assert resp.status_code == 200


def test_query_stream_route_no_q(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/query/stream")
    assert resp.status_code == 400


def test_query_stream_route_with_q(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    from unittest.mock import patch

    def fake_query_stream(repo, username, question):
        yield {"event": "progress", "data": {"message": "检索中", "percent": 10}}
        yield {"event": "answer_chunk", "data": {"chunk": "Hello"}}
        yield {"event": "done", "data": {"answer": "Hello", "wiki_sources": [],
                                          "qdrant_sources": [], "referenced_pages": []}}

    with patch.object(app.wiki_engine, "query_stream", side_effect=fake_query_stream):
        resp = client.get(f"/alice/{slug}/query/stream?q=test")
    assert resp.status_code == 200
    assert b"event: done" in resp.data


def test_query_api_render_only(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/query",
        json={"q": "test", "_rendered_answer": "# Hello\n\nWorld"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "<h1" in data["html"]


def test_wiki_search_no_query(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/wiki/search")
    assert resp.status_code == 200


def test_wiki_search_with_query(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/wiki/search?q=概览")
    assert resp.status_code == 200
    assert "概览".encode() in resp.data


def test_wiki_search_no_results(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/wiki/search?q=zzznomatchxxx")
    assert resp.status_code == 200


# -- Batch ingest ----------------------------------------------------------

def test_batch_ingest_empty(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(f"/alice/{slug}/sources/batch-ingest",
                       data={}, follow_redirects=True)
    assert resp.status_code == 200


# -- Retry task ------------------------------------------------------------

def test_retry_task_not_failed(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from models import Task, db
        task = Task(repo_id=repo_info["id"], type="ingest", status="queued", input_data="test.md")
        db.session.add(task)
        db.session.commit()
        tid = task.id
    resp = client.post(f"/api/tasks/{tid}/retry")
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data


def test_retry_task_failed(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from models import Task, db
        task = Task(repo_id=repo_info["id"], type="ingest", status="failed",
                    input_data="fail.md", progress_msg="error")
        db.session.add(task)
        db.session.commit()
        tid = task.id
    resp = client.post(f"/api/tasks/{tid}/retry")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    with app.app_context():
        from models import Task
        t = Task.query.get(tid)
        assert t.status == "queued"
        assert t.progress == 0


# -- Export ZIP ------------------------------------------------------------

def test_export_wiki_zip(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/wiki/export.zip")
    assert resp.status_code == 200
    assert resp.content_type == "application/zip"
    assert b"PK" in resp.data  # ZIP magic bytes

# -- URL import ------------------------------------------------------------

def test_import_url_empty(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/sources/import-url",
        data={"url": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200


def test_import_url_invalid(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/sources/import-url",
        data={"url": "not-a-url"},
        follow_redirects=True,
    )
    assert resp.status_code == 200


def test_import_url_mocked(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    from unittest.mock import patch
    with patch("trafilatura.fetch_url", return_value="<html>test</html>"), \
         patch("trafilatura.extract", return_value="# Test Page\n\nContent here."):
        resp = client.post(
            f"/alice/{slug}/sources/import-url",
            data={"url": "https://example.com/test-article"},
            follow_redirects=True,
        )
    assert resp.status_code == 200


# -- Admin -----------------------------------------------------------------


def test_admin_dashboard_as_admin(app):
    from config import Config
    Config.ADMIN_USERNAME = "alice"
    client = app.test_client()
    with app.app_context():
        from models import User, db
        if not User.query.filter_by(username="alice").first():
            u = User(username="alice", display_name="Alice")
            u.set_password("password123")
            db.session.add(u)
            db.session.commit()
    client.post("/login", data={"username": "alice", "password": "password123"})
    resp = client.get("/admin/")
    assert resp.status_code == 200


def test_admin_dashboard_unauthorized(client):
    resp = client.get("/admin/", follow_redirects=False)
    assert resp.status_code == 302


def test_admin_dashboard_non_admin(auth_client, app):
    from config import Config
    Config.ADMIN_USERNAME = "other_admin_user"
    resp = auth_client.get("/admin/")
    assert resp.status_code == 403


# -- Global search ---------------------------------------------------------


def test_global_search_no_query(auth_client):
    resp = auth_client.get("/alice/search")
    assert resp.status_code == 200


def test_global_search_with_query(sample_repo):
    client, repo_info = sample_repo
    resp = client.get("/alice/search?q=overview")
    assert resp.status_code == 200


def test_global_search_unauthorized(client, app):
    with app.app_context():
        from models import User, db
        if not User.query.filter_by(username="alice").first():
            u = User(username="alice", display_name="Alice")
            u.set_password("password123")
            db.session.add(u)
            db.session.commit()
    resp = client.get("/alice/search?q=test", follow_redirects=False)
    assert resp.status_code in (302, 403)



# -- query_api evidence schema -------------------------------------------

def test_query_api_returns_confidence(sample_repo, app):
    """query API must return confidence, wiki_evidence, chunk_evidence fields."""
    from unittest.mock import patch
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake = {
        "markdown": "Test answer",
        "confidence": {"level": "medium", "score": 0.6, "reasons": ["命中 1 个页面"]},
        "wiki_evidence": [{"filename": "overview.md", "title": "概览",
                           "type": "overview", "url": "/test", "reason": "高层概览页命中"}],
        "chunk_evidence": [],
        "evidence_summary": "基于 1 个页面生成。",
        "referenced_pages": ["overview.md"],
        "wiki_sources": ["overview.md"],
        "qdrant_sources": [],
    }
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=fake):
        resp = client.post(f"/alice/{slug}/query", json={"q": "test"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "confidence" in data
    assert data["confidence"]["level"] == "medium"
    assert "wiki_evidence" in data
    assert "chunk_evidence" in data
    assert "html" in data
    assert "wiki_sources" in data
    assert "qdrant_sources" in data


def test_query_api_render_only_returns_confidence(sample_repo):
    """Render-only branch must also return confidence and evidence fields."""
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/query",
        json={
            "q": "test",
            "_rendered_answer": "# Hello",
            "_confidence": {"level": "low", "score": 0.2, "reasons": []},
            "_wiki_evidence": [],
            "_chunk_evidence": [],
            "_evidence_summary": "",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "html" in data
    assert "confidence" in data
    assert data["confidence"]["level"] == "low"


def test_query_stream_done_has_evidence(sample_repo, app):
    """SSE done event must include confidence and evidence fields."""
    from unittest.mock import patch
    client, repo_info = sample_repo
    slug = repo_info["slug"]

    def fake_stream(repo, username, question):
        yield {"event": "progress", "data": {"message": "检索中", "percent": 10}}
        yield {"event": "answer_chunk", "data": {"chunk": "Hi"}}
        yield {"event": "done", "data": {
            "answer": "Hi", "markdown": "Hi",
            "confidence": {"level": "low", "score": 0.1, "reasons": []},
            "wiki_evidence": [], "chunk_evidence": [],
            "evidence_summary": "暂无证据。",
            "wiki_sources": [], "qdrant_sources": [],
            "referenced_pages": [],
        }}

    with patch.object(app.wiki_engine, "query_stream", side_effect=fake_stream):
        resp = client.get(f"/alice/{slug}/query/stream?q=test")
    assert resp.status_code == 200
    assert b"confidence" in resp.data
    assert b"wiki_evidence" in resp.data
    assert b"chunk_evidence" in resp.data


# -- API Token -----------------------------------------------------------------


def test_list_tokens_page(auth_client):
    resp = auth_client.get("/user/settings/tokens")
    assert resp.status_code == 200
    assert "API Tokens" in resp.data.decode("utf-8")


def test_create_and_revoke_token(auth_client, app):
    resp = auth_client.post(
        "/user/settings/tokens/create",
        data={"name": "ci-token"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        from models import ApiToken
        t = ApiToken.query.filter_by(name="ci-token").first()
        assert t is not None
        assert t.is_active is True
        token_id = t.id

    resp2 = auth_client.post(
        f"/user/settings/tokens/{token_id}/revoke",
        follow_redirects=True,
    )
    assert resp2.status_code == 200
    with app.app_context():
        from models import ApiToken
        t2 = ApiToken.query.get(token_id)
        assert t2.is_active is False


def test_api_token_bearer_auth(app, sample_repo):
    import hashlib
    import secrets
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from models import ApiToken, User, db
        u = User.query.filter_by(username="alice").first()
        raw = secrets.token_urlsafe(32)
        h = hashlib.sha256(raw.encode()).hexdigest()
        t = ApiToken(user_id=u.id, name="bearer-test", token_hash=h)
        db.session.add(t)
        db.session.commit()
    resp = app.test_client().get(
        f"/alice/{slug}/tasks",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200


# -- Audit Log -----------------------------------------------------------------


def test_admin_audit_log_accessible(auth_client, app):
    with app.app_context():
        from config import Config
        Config.ADMIN_USERNAME = "alice"
    resp = auth_client.get("/admin/audit")
    assert resp.status_code == 200
    assert "审计" in resp.data.decode("utf-8")
