"""Flask route tests using conftest fixtures."""

import io
import json
import os
from unittest.mock import patch

from click.testing import CliRunner


def _create_verified_user(app, username: str, email: str, password: str = "password123"):
    with app.app_context():
        from models import User, db

        user = User(username=username, email=email, display_name=username)
        user.set_password(password)
        user.email_verified = True
        db.session.add(user)
        db.session.commit()
        return user.id


def _login_as(client, username: str, password: str = "password123"):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=False)


# -- Auth ------------------------------------------------------------------


def test_login_page_get(client):
    resp = client.get("/login")
    assert resp.status_code == 200


def test_register_page_get(client):
    resp = client.get("/register")
    assert resp.status_code == 200


def test_register_and_login(client, app):
    from app import _build_verification_token

    with app.app_context():
        from models import User

        assert User.query.filter_by(username="routeuser1").first() is None

    with patch("mailer.Mailer.send_email_verification") as send_mail:
        resp = client.post(
            "/register",
            data={
                "username": "routeuser1",
                "email": "routeuser1@example.com",
                "display_name": "Route User",
                "password": "password123",
                "confirm_password": "password123",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 302
    send_mail.assert_called_once()

    with app.app_context():
        from models import User

        user = User.query.filter_by(username="routeuser1").first()
        assert user is not None
        assert user.email_verified is False
        token = _build_verification_token(user)

    verify_resp = client.get(f"/verify-email/{token}", follow_redirects=False)
    assert verify_resp.status_code == 302

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
            "email": "short@example.com",
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
            "email": "mismatch@example.com",
            "display_name": "M",
            "password": "password123",
            "confirm_password": "password999",
        },
    )
    assert resp.status_code == 200


def test_login_wrong_password(client, app):
    with app.app_context():
        from models import User, db

        u = User(username="wrongpwuser", email="wrongpw@example.com", display_name="W")
        u.set_password("rightpass123")
        u.email_verified = True
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


def test_join_repo_by_access_code_mounts_shared_repo(sample_repo, app):
    owner_client, repo_info = sample_repo
    slug = repo_info["slug"]

    owner_client.post(
        f"/alice/{slug}/settings",
        data={"action": "create_share_code", "share_role": "viewer"},
        follow_redirects=True,
    )

    with app.app_context():
        from models import RepoMember, RepoShareCode

        share_code = RepoShareCode.query.filter_by(repo_id=repo_info["id"]).first()
        assert share_code is not None
        code = share_code.code
        assert RepoMember.query.count() == 0

    share_client = app.test_client()
    _create_verified_user(app, "bob", "bob@example.com")
    _login_as(share_client, "bob")
    resp = share_client.post(
        "/repos/join",
        data={"access_code": code},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "共享给我的知识库" in html
    assert "Test KB" in html

    with app.app_context():
        from models import RepoMember, RepoShareCode

        member = RepoMember.query.filter_by(repo_id=repo_info["id"]).first()
        share_code = RepoShareCode.query.filter_by(repo_id=repo_info["id"]).first()
        assert member is not None
        assert member.role == "viewer"
        assert share_code.use_count == 1


def test_shared_viewer_cannot_edit_repo(sample_repo, app):
    owner_client, repo_info = sample_repo
    slug = repo_info["slug"]

    owner_client.post(
        f"/alice/{slug}/settings",
        data={"action": "create_share_code", "share_role": "viewer"},
        follow_redirects=True,
    )

    with app.app_context():
        from models import RepoShareCode

        share_code = RepoShareCode.query.filter_by(repo_id=repo_info["id"]).first()
        code = share_code.code

    share_client = app.test_client()
    _create_verified_user(app, "viewer_user", "viewer@example.com")
    _login_as(share_client, "viewer_user")
    share_client.post("/repos/join", data={"access_code": code}, follow_redirects=True)

    assert share_client.get(f"/alice/{slug}").status_code == 200
    assert share_client.get(f"/alice/{slug}/query").status_code == 200
    assert share_client.get(f"/alice/{slug}/wiki/overview/edit").status_code == 403
    assert share_client.post(
        f"/alice/{slug}/query/save",
        data={"content": "# Saved", "query": "viewer save"},
        follow_redirects=False,
    ).status_code == 403


def test_shared_editor_can_edit_repo(sample_repo, app):
    owner_client, repo_info = sample_repo
    slug = repo_info["slug"]

    owner_client.post(
        f"/alice/{slug}/settings",
        data={"action": "create_share_code", "share_role": "editor"},
        follow_redirects=True,
    )

    with app.app_context():
        from models import RepoShareCode

        share_code = RepoShareCode.query.filter_by(repo_id=repo_info["id"]).first()
        code = share_code.code

    share_client = app.test_client()
    _create_verified_user(app, "editor_user", "editor@example.com")
    _login_as(share_client, "editor_user")
    share_client.post("/repos/join", data={"access_code": code}, follow_redirects=True)

    edit_get = share_client.get(f"/alice/{slug}/wiki/overview/edit")
    assert edit_get.status_code == 200

    save_resp = share_client.post(
        f"/alice/{slug}/wiki/overview/edit",
        data={"content": "---\ntitle: 概览\ntype: overview\n---\n\n# 共享编辑\n"},
        follow_redirects=False,
    )
    assert save_resp.status_code == 302

    query_save = share_client.post(
        f"/alice/{slug}/query/save",
        data={"content": "# Saved from editor", "query": "editor save"},
        follow_redirects=False,
    )
    assert query_save.status_code == 302


def test_user_settings_update_profile(auth_client, app):
    resp = auth_client.post(
        "/user/settings",
        data={"action": "update_profile", "display_name": "Alice Beta", "email": "alice.beta@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        from models import User

        user = User.query.filter_by(username="alice").first()
        assert user is not None
        assert user.display_name == "Alice Beta"
        assert user.email == "alice.beta@example.com"


def test_user_settings_change_password(auth_client, app):
    resp = auth_client.post(
        "/user/settings",
        data={
            "action": "change_password",
            "old_password": "password123",
            "new_password": "newpass123",
            "confirm_password": "newpass123",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        from models import User

        user = User.query.filter_by(username="alice").first()
        assert user is not None
        assert user.check_password("newpass123") is True


def test_user_settings_delete_account_requires_exact_username(auth_client, app):
    resp = auth_client.post(
        "/user/settings",
        data={
            "action": "delete_account",
            "confirm_username": "alice-typo",
            "delete_password": "password123",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        from models import User

        user = User.query.filter_by(username="alice").first()
        assert user is not None


def test_user_settings_delete_account_cascades_related_data(auth_client, app):
    from config import Config
    from utils import get_repo_path

    with app.app_context():
        from models import ApiToken, AuditLog, ConversationSession, QueryFeedback, QueryLog, Repo, Task, User, db

        user = User.query.filter_by(username="alice").first()
        assert user is not None

        repo = Repo(user_id=user.id, name="Delete KB", slug="delete-kb", description="cleanup target")
        db.session.add(repo)
        db.session.flush()

        db.session.add(Task(repo_id=repo.id, type="ingest", status="done", input_data="cleanup.md"))
        db.session.add(
            ConversationSession(
                repo_id=repo.id,
                user_id=user.id,
                session_key="cleanup-session",
                messages_json='[{"role":"user","content":"cleanup"}]',
            )
        )
        db.session.add(ApiToken(user_id=user.id, name="cleanup token", token_hash="tok-hash"))
        db.session.add(
            QueryLog(
                trace_id="trace-delete-account",
                repo_id=repo.id,
                user_id=user.id,
                question="cleanup?",
            )
        )
        db.session.add(
            QueryFeedback(
                trace_id="trace-delete-account",
                repo_id=repo.id,
                user_id=user.id,
                rating="good",
            )
        )
        db.session.add(
            AuditLog(
                user_id=user.id,
                username=user.username,
                action="pre_delete_check",
            )
        )
        db.session.commit()

        repo_path = get_repo_path(Config.DATA_DIR, user.username, repo.slug)
        os.makedirs(os.path.join(repo_path, "raw"), exist_ok=True)
        with open(os.path.join(repo_path, "raw", "cleanup.md"), "w", encoding="utf-8") as fh:
            fh.write("# cleanup")

    with patch.object(app.qdrant, "delete_collection") as delete_collection, \
         patch.object(app.qdrant, "delete_chunk_collection") as delete_chunk_collection, \
         patch.object(app.qdrant, "delete_fact_collection") as delete_fact_collection:
        resp = auth_client.post(
            "/user/settings",
            data={
                "action": "delete_account",
                "confirm_username": "alice",
                "delete_password": "password123",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert "/login" in (resp.headers.get("Location") or "")
    delete_collection.assert_called_once_with(repo.id)
    delete_chunk_collection.assert_called_once_with(repo.id)
    delete_fact_collection.assert_called_once_with(repo.id)

    with app.app_context():
        from models import ApiToken, AuditLog, ConversationSession, QueryFeedback, QueryLog, Repo, Task, User

        assert User.query.filter_by(username="alice").first() is None
        assert Repo.query.filter_by(slug="delete-kb").first() is None
        assert Task.query.count() == 0
        assert ConversationSession.query.count() == 0
        assert ApiToken.query.count() == 0
        assert QueryLog.query.count() == 0
        assert QueryFeedback.query.count() == 0
        delete_audit = AuditLog.query.filter_by(action="delete_account").first()
        assert delete_audit is not None
        assert delete_audit.user_id is None
        assert delete_audit.username == "alice"
        assert not os.path.isdir(os.path.join(Config.DATA_DIR, "alice"))


def test_repo_settings_update_info_and_schema(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    schema_content = "# Custom Schema\n\n- concept\n- guide\n"

    resp = client.post(
        f"/alice/{slug}/settings",
        data={
            "action": "update_info",
            "name": "Updated KB",
            "description": "Updated description",
            "is_public": "on",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    resp2 = client.post(
        f"/alice/{slug}/settings",
        data={"action": "update_schema", "schema_content": schema_content},
        follow_redirects=True,
    )
    assert resp2.status_code == 200

    with app.app_context():
        from config import Config
        from models import Repo

        repo = Repo.query.filter_by(id=repo_info["id"]).first()
        assert repo is not None
        assert repo.name == "Updated KB"
        assert repo.description == "Updated description"
        assert repo.is_public is True

        schema_path = os.path.join(Config.DATA_DIR, "alice", slug, "wiki", "schema.md")
        with open(schema_path, "r", encoding="utf-8") as f:
            assert f.read() == schema_content


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


def test_wiki_overview_recreated_when_missing(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    import os

    from config import Config
    from models import Repo, db

    overview_path = os.path.join(Config.DATA_DIR, "alice", slug, "wiki", "overview.md")
    os.remove(overview_path)

    with app.app_context():
        repo = Repo.query.filter_by(slug=slug).first()
        repo.page_count = 0
        db.session.commit()

    resp = client.get(f"/alice/{slug}/wiki/overview")
    assert resp.status_code == 200
    assert os.path.isfile(overview_path)
    assert "暂无概览内容" in resp.data.decode("utf-8")

    with app.app_context():
        repo = Repo.query.filter_by(slug=slug).first()
        assert repo.page_count >= 1


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


def test_public_sources_list_accessible_without_login(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    client.post(
        f"/alice/{slug}/settings",
        data={"action": "update_info", "name": "Test KB", "description": "", "is_public": "on"},
    )
    client.get("/logout")
    resp = client.get(f"/alice/{slug}/sources")
    assert resp.status_code == 200


def test_upload_md_file(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    url = f"/alice/{slug}/sources/upload"
    data = {"file": (io.BytesIO(b"# Test content\n"), "test.md")}
    resp = client.post(url, data=data, content_type="multipart/form-data", follow_redirects=False)
    assert resp.status_code == 302


def test_upload_csv_creates_markdown_and_fact_records(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    url = f"/alice/{slug}/sources/upload"
    csv_bytes = "地区,收入,增长率\n华东,1200,12%\n华南,980,8%\n".encode("utf-8")

    resp = client.post(
        url,
        data={"file": (io.BytesIO(csv_bytes), "sales.csv")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )

    assert resp.status_code == 302

    with app.app_context():
        from config import Config
        from models import Task
        from utils import read_jsonl

        base = os.path.join(Config.DATA_DIR, "alice", slug)
        raw_markdown = os.path.join(base, "raw", "sales.md")
        facts_path = os.path.join(base, "facts", "records", "sales.jsonl")
        assert os.path.exists(raw_markdown)
        assert os.path.exists(facts_path)
        facts = read_jsonl(facts_path)
        assert len(facts) == 2
        assert facts[0]["fields"]["地区"] == "华东"

        task = Task.query.filter_by(repo_id=repo_info["id"]).order_by(Task.id.desc()).first()
        assert task is not None
        assert task.input_data == "sales.md"


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


def test_forgot_password_sends_reset_email(client, app):
    with app.app_context():
        from models import User, db

        user = User(username="mailuser", email="mailuser@example.com", display_name="Mail User")
        user.set_password("password123")
        user.email_verified = True
        db.session.add(user)
        db.session.commit()

    with patch.object(app.mailer, "send_password_reset") as send_mail:
        resp = client.post(
            "/forgot-password",
            data={"email": "mailuser@example.com"},
            follow_redirects=False,
        )

    assert resp.status_code == 302
    send_mail.assert_called_once()


def test_forgot_password_shows_error_when_mail_service_is_disabled(client, app):
    original_host = app.mailer._host
    app.mailer._host = ""
    try:
        with patch.object(app.mailer, "send_password_reset") as send_mail:
            resp = client.post(
                "/forgot-password",
                data={"email": "mailuser@example.com"},
                follow_redirects=False,
            )
    finally:
        app.mailer._host = original_host

    assert resp.status_code == 200
    assert "邮件服务未配置，暂时无法发送找回密码邮件" in resp.data.decode("utf-8")
    send_mail.assert_not_called()


def test_manage_check_reports_missing_mail_configuration():
    from manage import cli

    runner = CliRunner()
    with patch.dict(
        os.environ,
        {
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "3306",
            "DB_USER": "root",
            "DB_PASSWORD": "secret",
            "DB_NAME": "llmwiki",
            "MINERU_API_URL": "http://mineru.local",
            "QDRANT_URL": "http://qdrant.local",
            "EMBEDDING_API_BASE": "http://embed.local/v1",
            "EMBEDDING_MODEL": "bge-m3",
            "EMBEDDING_API_KEY": "",
            "MAIL_HOST": "",
            "MAIL_USERNAME": "",
            "MAIL_PASSWORD": "",
            "MAIL_FROM": "",
        },
        clear=False,
    ), patch("pymysql.connect"), patch("manage.httpx.get") as http_get, patch("openai.OpenAI") as openai_cls:
        http_get.return_value.status_code = 200
        openai_cls.return_value.embeddings.create.return_value = object()
        result = runner.invoke(cli, ["check"])

    assert result.exit_code == 0
    assert "✗ mail: fail: missing MAIL_HOST, MAIL_USERNAME, MAIL_PASSWORD, MAIL_FROM" in result.output
    assert "✓ mysql: ok" in result.output


def test_register_requires_mail_service_for_email_verification(client, app):
    original_host = app.mailer._host
    app.mailer._host = ""
    try:
        resp = client.post(
            "/register",
            data={
                "username": "nomailuser",
                "email": "nomail@example.com",
                "display_name": "No Mail",
                "password": "password123",
                "confirm_password": "password123",
            },
            follow_redirects=False,
        )
    finally:
        app.mailer._host = original_host

    assert resp.status_code == 200
    assert "邮件服务未配置，暂时无法完成注册" in resp.data.decode("utf-8")


def test_login_blocks_unverified_user_and_resends_verification_email(client, app):
    with app.app_context():
        from models import User, db

        user = User(username="pendinguser", email="pending@example.com", display_name="Pending User")
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()

    with patch("mailer.Mailer.send_email_verification") as send_mail:
        resp = client.post(
            "/login",
            data={"username": "pendinguser", "password": "password123"},
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert "账号尚未完成邮箱验证，已重新发送验证邮件，请查收邮箱" in resp.data.decode("utf-8")
    send_mail.assert_called_once()


def test_verify_email_marks_user_verified(client, app):
    with app.app_context():
        from app import _build_verification_token
        from models import User, db

        user = User(username="verifyuser", email="verify@example.com", display_name="Verify User")
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        token = _build_verification_token(user)

    resp = client.get(f"/verify-email/{token}", follow_redirects=False)
    assert resp.status_code == 302

    with app.app_context():
        from models import User

        user = User.query.filter_by(username="verifyuser").first()
        assert user is not None
        assert user.email_verified is True
        assert user.email_verified_at is not None


def test_reset_password_flow(client, app):
    with app.app_context():
        from models import User, db
        from app import _build_reset_token

        user = User(username="resetuser", email="resetuser@example.com")
        user.set_password("password123")
        user.email_verified = True
        db.session.add(user)
        db.session.commit()
        token = _build_reset_token(user)

    resp = client.post(
        f"/reset-password/{token}",
        data={"password": "newpassword123", "confirm_password": "newpassword123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        from models import User

        user = User.query.filter_by(username="resetuser").first()
        assert user is not None
        assert user.check_password("newpassword123") is True


def test_query_api(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake = {
        "markdown": "Test **answer**",
        "confidence": {"level": "medium", "score": 0.6, "reasons": ["test"]},
        "wiki_evidence": [],
        "chunk_evidence": [],
        "fact_evidence": [],
        "evidence_summary": "test summary",
        "referenced_pages": ["overview.md"],
        "wiki_sources": ["overview.md"],
        "qdrant_sources": [],
    }
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=fake):
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


def test_protected_route_redirect(sample_repo):
    client, repo_info = sample_repo
    client.get("/logout")
    resp = client.get(f"/{repo_info['username']}/{repo_info['slug']}/sources", follow_redirects=False)
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


def test_wiki_edit_page_recreates_missing_overview(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    import os

    from config import Config

    overview_path = os.path.join(Config.DATA_DIR, "alice", slug, "wiki", "overview.md")
    os.remove(overview_path)

    resp = client.get(f"/alice/{slug}/wiki/overview/edit")
    assert resp.status_code == 200
    assert os.path.isfile(overview_path)


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


def test_wiki_log_redirects_to_log_page(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/log", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/alice/{slug}/wiki/log")


def test_wiki_pages_grouped_view(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.get(f"/alice/{slug}/wiki/pages")
    assert resp.status_code == 200
    assert "概览" in resp.data.decode("utf-8")


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


def test_view_source_renders_markdown(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from config import Config

        raw_dir = os.path.join(Config.DATA_DIR, "alice", slug, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, "source-view.md"), "w", encoding="utf-8") as f:
            f.write("# Source View\n\nRendered content.")

    resp = client.get(f"/alice/{slug}/sources/source-view.md")
    assert resp.status_code == 200
    assert "Source View" in resp.data.decode("utf-8")


def test_upload_xhr_returns_json(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/sources/upload",
        data={"file": (io.BytesIO(b"# Ajax upload\n"), "xhr-upload.md")},
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert data["ok"] is True
    assert data["filename"] == "xhr-upload.md"
    assert isinstance(data["task_id"], int)


def test_upload_excel_creates_markdown_and_fact_records(sample_repo, app):
    import openpyxl

    client, repo_info = sample_repo
    slug = repo_info["slug"]
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Summary"
    ws1.append(["地区", "收入"])
    ws1.append(["华北", 100])
    ws2 = wb.create_sheet("Detail")
    ws2.append(["产品", "销量"])
    ws2.append(["A", 88])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    resp = client.post(
        f"/alice/{slug}/sources/upload",
        data={"file": (buf, "report.xlsx")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        from config import Config
        from utils import read_jsonl

        base = os.path.join(Config.DATA_DIR, "alice", slug)
        assert os.path.isfile(os.path.join(base, "raw", "report.md"))
        records = read_jsonl(os.path.join(base, "facts", "records", "report.jsonl"))
        assert len(records) == 2
        assert records[0]["sheet"] == "Summary"
        assert records[1]["sheet"] == "Detail"


def test_upload_pdf_via_mineru_saves_original_and_markdown(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with patch.object(app.mineru, "parse_file", return_value={"md_content": "# Parsed PDF\n\nBody"}):
        resp = client.post(
            f"/alice/{slug}/sources/upload",
            data={"file": (io.BytesIO(b"%PDF-1.4 fake"), "paper.pdf")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
    assert resp.status_code == 302

    with app.app_context():
        from config import Config

        base = os.path.join(Config.DATA_DIR, "alice", slug, "raw")
        assert os.path.isfile(os.path.join(base, "paper.md"))
        assert os.path.isfile(os.path.join(base, "originals", "paper.pdf"))


def test_download_source_prefers_original_file(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from config import Config

        raw_dir = os.path.join(Config.DATA_DIR, "alice", slug, "raw")
        originals_dir = os.path.join(raw_dir, "originals")
        os.makedirs(originals_dir, exist_ok=True)
        with open(os.path.join(raw_dir, "manual.md"), "w", encoding="utf-8") as f:
            f.write("# Manual\n")
        with open(os.path.join(originals_dir, "manual.pdf"), "wb") as f:
            f.write(b"original pdf bytes")

    resp = client.get(f"/alice/{slug}/sources/manual.md/download")
    assert resp.status_code == 200
    assert resp.data == b"original pdf bytes"
    assert "manual.pdf" in resp.headers["Content-Disposition"]


def test_delete_source_removes_file_and_related_wiki(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from config import Config

        base = os.path.join(Config.DATA_DIR, "alice", slug)
        raw_dir = os.path.join(base, "raw")
        wiki_dir = os.path.join(base, "wiki")
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, "sales.md"), "w", encoding="utf-8") as f:
            f.write("# Sales\n")
        with open(os.path.join(wiki_dir, "sales.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: Sales\ntype: source\nsource: sales.md\n---\n\n# Sales\n")
        with open(os.path.join(wiki_dir, "sales-summary.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: Sales Summary\ntype: concept\nsource: sales.md\n---\n\n# Summary\n")

    resp = client.post(f"/alice/{slug}/sources/sales.md/delete", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        from config import Config

        base = os.path.join(Config.DATA_DIR, "alice", slug)
        assert not os.path.exists(os.path.join(base, "raw", "sales.md"))
        assert not os.path.exists(os.path.join(base, "wiki", "sales.md"))
        assert not os.path.exists(os.path.join(base, "wiki", "sales-summary.md"))


def test_rename_source_moves_file_and_queues_reingest(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from config import Config

        raw_dir = os.path.join(Config.DATA_DIR, "alice", slug, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, "old-name.md"), "w", encoding="utf-8") as f:
            f.write("# Old\n")

    resp = client.post(
        f"/alice/{slug}/sources/old-name.md/rename",
        data={"new_name": "new-name.md"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        from config import Config
        from models import Task

        raw_dir = os.path.join(Config.DATA_DIR, "alice", slug, "raw")
        assert not os.path.exists(os.path.join(raw_dir, "old-name.md"))
        assert os.path.exists(os.path.join(raw_dir, "new-name.md"))
        task = Task.query.filter_by(repo_id=repo_info["id"], input_data="new-name.md").order_by(Task.id.desc()).first()
        assert task is not None
        assert task.status == "queued"


def test_batch_delete_removes_selected_sources(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from config import Config

        raw_dir = os.path.join(Config.DATA_DIR, "alice", slug, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        for name in ("a.md", "b.md"):
            with open(os.path.join(raw_dir, name), "w", encoding="utf-8") as f:
                f.write(f"# {name}\n")

    resp = client.post(
        f"/alice/{slug}/sources/batch-delete",
        data={"files": ["a.md", "b.md"]},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        from config import Config

        raw_dir = os.path.join(Config.DATA_DIR, "alice", slug, "raw")
        assert not os.path.exists(os.path.join(raw_dir, "a.md"))
        assert not os.path.exists(os.path.join(raw_dir, "b.md"))


def test_batch_ingest_skips_done_and_queued_files(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from config import Config
        from models import Task, db

        raw_dir = os.path.join(Config.DATA_DIR, "alice", slug, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        for name in ("done.md", "queued.md", "fresh.md"):
            with open(os.path.join(raw_dir, name), "w", encoding="utf-8") as f:
                f.write(f"# {name}\n")
        db.session.add(Task(repo_id=repo_info["id"], type="ingest", status="done", input_data="done.md"))
        db.session.add(Task(repo_id=repo_info["id"], type="ingest", status="queued", input_data="queued.md"))
        db.session.commit()

    resp = client.post(
        f"/alice/{slug}/sources/batch-ingest",
        data={"files": ["done.md", "queued.md", "fresh.md"]},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        from models import Task

        queued = Task.query.filter_by(repo_id=repo_info["id"], type="ingest", input_data="fresh.md").all()
        assert len(queued) == 1


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


def test_ingest_progress_page_accessible(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from models import Task, db

        task = Task(repo_id=repo_info["id"], type="ingest", status="queued", input_data="doc.md")
        db.session.add(task)
        db.session.commit()
        tid = task.id

    resp = client.get(f"/alice/{slug}/ingest-progress/{tid}")
    assert resp.status_code == 200
    assert "正在处理" in resp.data.decode("utf-8")


def test_task_status_api_returns_json(sample_repo, app):
    client, repo_info = sample_repo
    with app.app_context():
        from models import Task, db

        task = Task(
            repo_id=repo_info["id"],
            type="ingest",
            status="running",
            input_data="doc.md",
            progress=55,
            progress_msg="halfway",
        )
        db.session.add(task)
        db.session.commit()
        tid = task.id

    resp = client.get(f"/api/tasks/{tid}/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {
        "id": tid,
        "status": "running",
        "progress": 55,
        "progress_msg": "halfway",
        "input_data": "doc.md",
        "cancel_requested": False,
    }


def test_task_stream_done_event(sample_repo, app):
    client, repo_info = sample_repo
    with app.app_context():
        from models import Task, db

        task = Task(
            repo_id=repo_info["id"],
            type="ingest",
            status="done",
            input_data="doc.md",
            progress=100,
            progress_msg="finished",
        )
        db.session.add(task)
        db.session.commit()
        tid = task.id

    resp = client.get(f"/task/{tid}/stream")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "event: progress" in body
    assert "event: done" in body
    assert "finished" in body


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
            u = User(username="alice", email="alice-admin@example.com", display_name="Alice")
            u.set_password("password123")
            u.email_verified = True
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
            u = User(username="alice", email="alice-search@example.com", display_name="Alice")
            u.set_password("password123")
            db.session.add(u)
            db.session.commit()
    resp = client.get("/alice/search?q=test", follow_redirects=False)
    assert resp.status_code in (302, 403)



# -- query_api evidence schema -------------------------------------------

def test_query_api_returns_confidence(sample_repo, app):
    """query API must return confidence, wiki_evidence, chunk_evidence, fact_evidence fields."""
    from unittest.mock import patch
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake = {
        "markdown": "Test answer",
        "confidence": {"level": "medium", "score": 0.6, "reasons": ["命中 1 个页面"]},
        "wiki_evidence": [{"filename": "overview.md", "title": "概览",
                           "type": "overview", "url": "/test", "reason": "高层概览页命中"}],
        "chunk_evidence": [],
        "fact_evidence": [{"record_id": "csv:2", "source_file": "sales.csv", "score": 0.96}],
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
    assert "fact_evidence" in data
    assert "html" in data
    assert "wiki_sources" in data
    assert "qdrant_sources" in data


def test_query_api_render_only_returns_confidence(sample_repo):
    """Render-only branch must also return confidence and all evidence fields."""
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
            "_fact_evidence": [{"record_id": "csv:2", "source_file": "sales.csv"}],
            "_evidence_summary": "",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "html" in data
    assert "confidence" in data
    assert "fact_evidence" in data
    assert data["confidence"]["level"] == "low"


def test_query_api_render_only_stores_session_and_updates_title(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    question = "2024 Q4 市场趋势总结"
    resp = client.post(
        f"/alice/{slug}/query",
        json={
            "q": question,
            "session_key": "stream-sess-1",
            "_rendered_answer": "# 回答\n\n这里是流式查询后的回答。",
            "_confidence": {"level": "medium", "score": 0.6, "reasons": []},
            "_wiki_evidence": [],
            "_chunk_evidence": [],
            "_fact_evidence": [{"record_id": "csv:2", "source_file": "sales.csv"}],
            "_evidence_summary": "",
            "_wiki_sources": [],
            "_qdrant_sources": [],
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        from models import ConversationSession
        import json as _j
        cs = ConversationSession.query.filter_by(session_key="stream-sess-1").first()
        assert cs is not None
        assert cs.title == question
        msgs = _j.loads(cs.messages_json)
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == question
        assert msgs[1]["role"] == "assistant"
        assert "流式查询后的回答" in msgs[1]["content"]


def test_query_stream_done_has_evidence(sample_repo, app):
    """SSE done event must include confidence and all evidence fields."""
    from unittest.mock import patch
    client, repo_info = sample_repo
    slug = repo_info["slug"]

    def fake_stream(repo, username, question):
        yield {"event": "progress", "data": {"message": "检索中", "percent": 10}}
        yield {"event": "answer_chunk", "data": {"chunk": "Hi"}}
        yield {"event": "done", "data": {
            "answer": "Hi", "markdown": "Hi",
            "confidence": {"level": "low", "score": 0.1, "reasons": []},
            "wiki_evidence": [], "chunk_evidence": [], "fact_evidence": [],
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
    assert b"fact_evidence" in resp.data


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


def test_admin_health_accessible(auth_client, app):
    with app.app_context():
        from config import Config
        Config.ADMIN_USERNAME = "alice"
    resp = auth_client.get("/admin/health")
    assert resp.status_code == 200
    assert "健康" in resp.data.decode("utf-8")


def test_admin_query_stats_accessible(auth_client, app):
    with app.app_context():
        from config import Config
        Config.ADMIN_USERNAME = "alice"
    resp = auth_client.get("/admin/query-stats")
    assert resp.status_code == 200
    assert "查询统计" in resp.data.decode("utf-8")


def test_admin_feedbacks_accessible(auth_client, app):
    with app.app_context():
        from config import Config
        Config.ADMIN_USERNAME = "alice"
    resp = auth_client.get("/admin/feedbacks")
    assert resp.status_code == 200
    assert "用户反馈" in resp.data.decode("utf-8")


def test_admin_query_logs_filter_and_trace_detail(auth_client, app, sample_repo):
    with app.app_context():
        from config import Config
        from models import QueryLog, Repo, User, db

        Config.ADMIN_USERNAME = "alice"
        user = User.query.filter_by(username="alice").first()
        repo = Repo.query.filter_by(slug=sample_repo[1]["slug"]).first()
        ql = QueryLog(
            trace_id="trace-admin-1",
            repo_id=repo.id,
            user_id=user.id,
            question="Why is revenue rising?",
            answer_preview="Revenue is rising",
            full_answer="# Revenue\n\nRising steadily.",
            confidence="low",
            retrieval_json=json.dumps({"wiki": [], "chunks": [], "facts": []}, ensure_ascii=False),
        )
        db.session.add(ql)
        db.session.commit()

        raw_dir = os.path.join(Config.DATA_DIR, "alice", repo.slug, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, "source-a.md"), "w", encoding="utf-8") as f:
            f.write("# Source A\n")

    logs_resp = auth_client.get("/admin/query-logs?q=revenue&conf=low&user=alice")
    assert logs_resp.status_code == 200
    assert "Why is revenue rising?" in logs_resp.data.decode("utf-8")

    trace_resp = auth_client.get("/admin/trace/trace-admin-1")
    assert trace_resp.status_code == 200
    body = trace_resp.data.decode("utf-8")
    assert "Revenue" in body
    assert "source-a.md" in body


def test_admin_repos_page_accessible(auth_client, app, sample_repo):
    with app.app_context():
        from config import Config

        Config.ADMIN_USERNAME = "alice"
    resp = auth_client.get("/admin/repos?q=test-kb")
    assert resp.status_code == 200
    assert "alice/test-kb" in resp.data.decode("utf-8")


# -- Public repo guest access --------------------------------------------------


def test_public_repo_query_no_login(app, sample_repo):
    """公开仓库未登录访客可以 POST query"""
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from models import Repo, db
        r = Repo.query.filter_by(slug=slug).first()
        r.is_public = True
        db.session.commit()
    fake = {
        "markdown": "answer", "confidence": {"level": "low", "score": 0.1, "reasons": []},
        "wiki_evidence": [], "chunk_evidence": [], "evidence_summary": "",
        "referenced_pages": [], "wiki_sources": [], "qdrant_sources": [],
    }
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=fake):
        resp = app.test_client().post(f"/alice/{slug}/query", json={"q": "test"})
    assert resp.status_code == 200


def test_private_repo_query_no_login_returns_401(app, sample_repo):
    """私有仓库未登录访客不能 POST query"""
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from models import Repo, db
        r = Repo.query.filter_by(slug=slug).first()
        r.is_public = False
        db.session.commit()
    resp = app.test_client().post(f"/alice/{slug}/query", json={"q": "test"})
    assert resp.status_code == 401


# -- Import ZIP ----------------------------------------------------------------


def test_import_zip_route(sample_repo, app):
    import io
    import zipfile
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("wiki/test-import-page.md", "---\ntitle: Test Import\ntype: concept\n---\n\n# Test Import\n")
    buf.seek(0)
    resp = client.post(
        f"/alice/{slug}/import.zip",
        data={"file": (buf, "export.zip"), "mode": "merge"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    import os
    from config import Config
    from utils import get_repo_path
    with app.app_context():
        base = get_repo_path(Config.DATA_DIR, "alice", slug)
        assert os.path.isfile(os.path.join(base, "wiki", "test-import-page.md"))


# -- Conversation sessions -----------------------------------------------------


def test_query_api_stores_session(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake = {
        "markdown": "answer", "confidence": {"level": "low", "score": 0.1, "reasons": []},
        "wiki_evidence": [], "chunk_evidence": [], "evidence_summary": "",
        "referenced_pages": [], "wiki_sources": [], "qdrant_sources": [],
    }
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=fake):
        resp = client.post(f"/alice/{slug}/query", json={"q": "hello", "session_key": "test-sess-1"})
    assert resp.status_code == 200
    with app.app_context():
        from models import ConversationSession
        import json as _j
        cs = ConversationSession.query.filter_by(session_key="test-sess-1").first()
        assert cs is not None
        msgs = _j.loads(cs.messages_json)
        assert any(m.get("content") == "hello" for m in msgs)


def test_clear_session(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from models import ConversationSession, User, db
        u = User.query.filter_by(username="alice").first()
        cs = ConversationSession(
            repo_id=repo_info["id"], user_id=u.id,
            session_key="clr-sess", messages_json='[{"role":"user","content":"hi"}]'
        )
        db.session.add(cs)
        db.session.commit()
    resp = client.post(f"/alice/{slug}/session/clear", json={"key": "clr-sess"})
    assert resp.status_code == 200
    with app.app_context():
        from models import ConversationSession
        cs2 = ConversationSession.query.filter_by(session_key="clr-sess").first()
        assert cs2.messages_json == "[]"


def test_save_query_page_creates_wiki_file(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/query/save",
        data={"query": "Revenue Summary", "content": "# Revenue\n\nSummary."},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/wiki/revenue-summary" in resp.headers["Location"]

    with app.app_context():
        from config import Config

        path = os.path.join(Config.DATA_DIR, "alice", slug, "wiki", "revenue-summary.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "title: Revenue Summary" in content
        assert "# Revenue" in content


def test_get_session_returns_messages(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    with app.app_context():
        from models import ConversationSession, User, db

        user = User.query.filter_by(username="alice").first()
        db.session.add(
            ConversationSession(
                repo_id=repo_info["id"],
                user_id=user.id,
                session_key="sess-view",
                messages_json=json.dumps([{"role": "user", "content": "hello"}], ensure_ascii=False),
            )
        )
        db.session.commit()

    resp = client.get(f"/alice/{slug}/session?key=sess-view")
    assert resp.status_code == 200
    assert resp.get_json() == {"messages": [{"role": "user", "content": "hello"}]}


def test_render_markdown_api_batch(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/render-markdown",
        json={"messages": ["# Title", "**bold**"]},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["html_list"]) == 2
    assert "<h1" in data["html_list"][0]
    assert "<strong>" in data["html_list"][1]


def test_session_lifecycle_endpoints(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]

    create_resp = client.post(f"/alice/{slug}/sessions/new")
    assert create_resp.status_code == 200
    create_data = create_resp.get_json()
    key = create_data["key"]

    rename_resp = client.post(
        f"/alice/{slug}/sessions/{key}/rename",
        json={"title": "Renamed Session"},
    )
    assert rename_resp.status_code == 200

    list_resp = client.get(f"/alice/{slug}/sessions")
    assert list_resp.status_code == 200
    sessions = list_resp.get_json()["sessions"]
    assert any(s["key"] == key and s["title"] == "Renamed Session" for s in sessions)

    delete_resp = client.post(f"/alice/{slug}/sessions/{key}/delete")
    assert delete_resp.status_code == 200

    list_resp2 = client.get(f"/alice/{slug}/sessions")
    keys = [s["key"] for s in list_resp2.get_json()["sessions"]]
    assert key not in keys


def test_feedback_submit_success_and_invalid(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]

    bad_resp = client.post(f"/alice/{slug}/feedback", json={"trace_id": "", "rating": "meh"})
    assert bad_resp.status_code == 400

    resp = client.post(
        f"/alice/{slug}/feedback",
        json={"trace_id": "trace-123", "rating": "good", "comment": "helpful"},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}

    with app.app_context():
        from models import QueryFeedback

        feedback = QueryFeedback.query.filter_by(trace_id="trace-123").first()
        assert feedback is not None
        assert feedback.rating == "good"
        assert feedback.comment == "helpful"


def test_insights_analyze_invokes_engine(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake_gaps = [{"gap": "missing timeline", "questions": ["When?"]}]
    with patch.object(app.wiki_engine, "find_gaps", return_value=fake_gaps) as mock_find_gaps:
        resp = client.get(f"/alice/{slug}/insights?analyze=1")
    assert resp.status_code == 200
    mock_find_gaps.assert_called_once()


def test_entity_check_analyze_invokes_engine(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake_result = [{"canonical": "Transformer", "aliases": ["transformers"]}]
    with patch.object(app.wiki_engine, "find_entity_duplicates", return_value=fake_result) as mock_find_dupes:
        resp = client.get(f"/alice/{slug}/entity-check?analyze=1")
    assert resp.status_code == 200
    mock_find_dupes.assert_called_once()


# -- README, insights, entity check, semantic search ---------------------------


def test_save_and_show_readme(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/settings",
        data={"action": "update_readme", "readme": "# 说明\n\n这是测试知识库。"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    resp2 = client.get(f"/alice/{slug}")
    assert "说明" in resp2.data.decode("utf-8")


def test_insights_page_accessible(sample_repo):
    client, repo_info = sample_repo
    resp = client.get(f"/alice/{repo_info['slug']}/insights")
    assert resp.status_code == 200
    assert "知识缺口" in resp.data.decode("utf-8")


def test_entity_check_page_accessible(sample_repo):
    client, repo_info = sample_repo
    resp = client.get(f"/alice/{repo_info['slug']}/entity-check")
    assert resp.status_code == 200
    assert "实体去重" in resp.data.decode("utf-8")


def test_semantic_search_accessible(sample_repo):
    client, repo_info = sample_repo
    resp = client.get(f"/alice/{repo_info['slug']}/search/semantic?q=test")
    assert resp.status_code == 200
    assert "语义检索" in resp.data.decode("utf-8")


# -- Duplicate source detection ------------------------------------------------


def test_duplicate_upload_warns(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    content = b"# Duplicate Content\n\nSame text here.\n"
    resp1 = client.post(
        f"/alice/{slug}/sources/upload",
        data={"file": (io.BytesIO(content), "dup-test.md")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp1.status_code == 200
    resp2 = client.post(
        f"/alice/{slug}/sources/upload",
        data={"file": (io.BytesIO(content), "dup-test2.md")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp2.status_code == 200
    assert "重复" in resp2.data.decode("utf-8")


def test_task_worker_recovers_running_tasks(app, sample_repo):
    from task_worker import TaskWorker

    with app.app_context():
        from models import Task, db

        task = Task(repo_id=sample_repo[1]["id"], type="ingest", status="running", progress=35, progress_msg="busy")
        db.session.add(task)
        db.session.commit()
        tid = task.id

    worker = TaskWorker(app)
    worker._recover_stale_tasks()

    with app.app_context():
        from models import Task, db

        task = db.session.get(Task, tid)
        assert task.status == "queued"
        assert task.progress == 0
        assert task.progress_msg == "Recovered after service restart"


def test_task_worker_runs_rebuild_index_task(app, sample_repo):
    from task_worker import TaskWorker

    with app.app_context():
        from config import Config
        from models import Task, db

        slug = sample_repo[1]["slug"]
        wiki_dir = os.path.join(Config.DATA_DIR, "alice", slug, "wiki")
        with open(os.path.join(wiki_dir, "index-test.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: Index Test\ntype: concept\n---\n\n# Index Test\n")

        task = Task(repo_id=sample_repo[1]["id"], type="rebuild_index", status="queued", input_data="import_zip")
        db.session.add(task)
        db.session.commit()
        tid = task.id

    with patch.object(app.qdrant, "upsert_page") as mock_upsert_page, \
         patch.object(app.qdrant, "upsert_page_chunks") as mock_upsert_chunks:
        TaskWorker(app)._poll_once()

    with app.app_context():
        from models import Task, db

        task = db.session.get(Task, tid)
        assert task.status == "done"
        assert "Rebuilt index" in (task.progress_msg or "")
    assert mock_upsert_page.called
    assert mock_upsert_chunks.called
