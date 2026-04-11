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
