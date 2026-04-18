"""/api/v1 Bearer-token 鉴权与三端点 (me / repos / search) 的集成测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 工具：创建用户 + 给定 scope/过期时间 的 token；返回 (token_plaintext, token_db_id)
# ---------------------------------------------------------------------------

def _create_user_with_token(
    app,
    *,
    username: str = "alice",
    email: str | None = None,
    scopes: str = "kb:search,kb:read",
    expires_delta: timedelta | None = None,
    is_active: bool = True,
) -> tuple[str, int, int]:
    """在 DB 里创建 user + api_token，返回 (plaintext, token_id, user_id)。"""
    from api_auth import generate_token
    from models import ApiToken, User, db

    with app.app_context():
        existing = User.query.filter_by(username=username).first()
        if existing is None:
            user = User(
                username=username,
                email=email or f"{username}@example.com",
                display_name=username.title(),
            )
            user.set_password("password123")
            user.email_verified = True
            db.session.add(user)
            db.session.commit()
        else:
            user = existing
        plaintext, token_hash, token_prefix = generate_token()
        expires_at = None
        if expires_delta is not None:
            expires_at = datetime.now(timezone.utc) + expires_delta
        t = ApiToken(
            user_id=user.id,
            name=f"{username}-test",
            token_hash=token_hash,
            token_prefix=token_prefix,
            scopes=scopes,
            expires_at=expires_at,
            is_active=is_active,
        )
        db.session.add(t)
        db.session.commit()
        return plaintext, t.id, user.id


def _create_repo(
    app,
    *,
    owner_id: int,
    name: str,
    slug: str,
    description: str = "",
    is_public: bool = False,
):
    from models import Repo, db

    with app.app_context():
        repo = Repo(
            user_id=owner_id,
            name=name,
            slug=slug,
            description=description,
            is_public=is_public,
        )
        db.session.add(repo)
        db.session.commit()
        return repo.id


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Bearer 鉴权边界
# ---------------------------------------------------------------------------


def test_api_me_missing_auth_header_returns_401(app):
    resp = app.test_client().get("/api/v1/me")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "missing_bearer_token"


def test_api_me_malformed_auth_returns_401(app):
    resp = app.test_client().get("/api/v1/me", headers={"Authorization": "NotBearer xyz"})
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "missing_bearer_token"


def test_api_me_unknown_token_returns_401(app):
    resp = app.test_client().get("/api/v1/me", headers=_h("ollw_fake_unknown"))
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_token"


def test_api_me_revoked_token_returns_401(app):
    plaintext, _, _ = _create_user_with_token(app, username="u_revoked", is_active=False)
    resp = app.test_client().get("/api/v1/me", headers=_h(plaintext))
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "token_revoked"


def test_api_me_expired_token_returns_401(app):
    plaintext, _, _ = _create_user_with_token(
        app, username="u_expired", expires_delta=timedelta(seconds=-60)
    )
    resp = app.test_client().get("/api/v1/me", headers=_h(plaintext))
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "token_expired"


def test_api_search_requires_kb_search_scope(app):
    """只有 kb:read 而无 kb:search 的 token 调用 /search 应被 403."""
    plaintext, _, user_id = _create_user_with_token(
        app, username="u_readonly", scopes="kb:read"
    )
    _create_repo(app, owner_id=user_id, name="KB", slug="kb", is_public=True)
    resp = app.test_client().post(
        "/api/v1/search",
        json={"query": "hello", "repo": "u_readonly/kb"},
        headers=_h(plaintext),
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "insufficient_scope"


# ---------------------------------------------------------------------------
# /api/v1/me
# ---------------------------------------------------------------------------


def test_api_me_success_returns_user_and_token_metadata(app):
    plaintext, token_id, _ = _create_user_with_token(
        app, username="alice_me", expires_delta=timedelta(days=7)
    )
    resp = app.test_client().get("/api/v1/me", headers=_h(plaintext))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["username"] == "alice_me"
    assert data["email"] == "alice_me@example.com"
    assert data["token"]["prefix"].startswith("ollw_")
    assert "kb:search" in data["token"]["scopes"]
    assert "kb:read" in data["token"]["scopes"]
    assert data["token"]["expires_at"] is not None


def test_api_me_updates_last_used_at(app):
    from models import ApiToken

    from models import db

    plaintext, token_id, _ = _create_user_with_token(app, username="u_last_used")
    with app.app_context():
        assert db.session.get(ApiToken, token_id).last_used_at is None
    resp = app.test_client().get("/api/v1/me", headers=_h(plaintext))
    assert resp.status_code == 200
    with app.app_context():
        assert db.session.get(ApiToken, token_id).last_used_at is not None


# ---------------------------------------------------------------------------
# /api/v1/repos
# ---------------------------------------------------------------------------


def test_api_repos_returns_owned_member_and_public(app):
    """alice 的可见 KB = 自己的 + 共享加入的 + 其他用户的公开仓库。"""
    from models import RepoMember, db

    alice_token, _, alice_id = _create_user_with_token(app, username="alice_r")
    _, _, bob_id = _create_user_with_token(app, username="bob_r")
    _, _, carol_id = _create_user_with_token(app, username="carol_r")

    owned = _create_repo(app, owner_id=alice_id, name="Alice Own", slug="alice-own")
    bob_shared = _create_repo(
        app, owner_id=bob_id, name="Bob Shared", slug="bob-shared"
    )
    carol_public = _create_repo(
        app,
        owner_id=carol_id,
        name="Carol Public",
        slug="carol-public",
        is_public=True,
    )
    carol_private = _create_repo(
        app, owner_id=carol_id, name="Carol Private", slug="carol-private"
    )

    with app.app_context():
        db.session.add(RepoMember(repo_id=bob_shared, user_id=alice_id, role="viewer"))
        db.session.commit()

    resp = app.test_client().get("/api/v1/repos", headers=_h(alice_token))
    assert resp.status_code == 200
    data = resp.get_json()
    slugs = {r["slug"] for r in data["repos"]}
    assert "alice-own" in slugs  # owner
    assert "bob-shared" in slugs  # member
    assert "carol-public" in slugs  # public
    assert "carol-private" not in slugs  # 其他人的私有
    # full_name 格式 owner/slug
    assert any(r["full_name"] == "carol_r/carol-public" for r in data["repos"])


def test_api_repos_include_public_false_excludes_public(app):
    alice_token, _, alice_id = _create_user_with_token(app, username="alice_r2")
    _, _, bob_id = _create_user_with_token(app, username="bob_r2")
    _create_repo(app, owner_id=alice_id, name="My", slug="my")
    _create_repo(
        app, owner_id=bob_id, name="Bob Public", slug="bob-public", is_public=True
    )
    resp = app.test_client().get(
        "/api/v1/repos?include_public=false", headers=_h(alice_token)
    )
    assert resp.status_code == 200
    slugs = {r["slug"] for r in resp.get_json()["repos"]}
    assert "my" in slugs
    assert "bob-public" not in slugs


# ---------------------------------------------------------------------------
# /api/v1/search
# ---------------------------------------------------------------------------


_FAKE_QUERY_RESULT = {
    "markdown": "## 答案\nAE350 支持 RJ45 (PoE)。",
    "confidence": {"level": "high", "score": 0.82, "reasons": ["命中 3 条证据"]},
    "wiki_evidence": [
        {"filename": "ae350-overview.md", "title": "AE350 概览", "reason": "主题匹配"}
    ],
    "chunk_evidence": [
        {"filename": "ae350-specifications.md", "score": 0.78, "snippet": "RJ45 PoE ..."}
    ],
    "fact_evidence": [],
    "evidence_summary": "3 条证据",
    "query_mode": "hybrid",
    "intent": "generic",
    "citation_validation": {"cited": [], "unknown": [], "ok": True},
    "referenced_pages": [],
    "wiki_sources": [],
    "qdrant_sources": [],
}


def test_api_search_missing_query_returns_400(app):
    plaintext, _, user_id = _create_user_with_token(app, username="alice_s1")
    _create_repo(app, owner_id=user_id, name="KB", slug="kb")
    resp = app.test_client().post(
        "/api/v1/search", json={"repo": "alice_s1/kb"}, headers=_h(plaintext)
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "missing_query"


def test_api_search_explicit_repo_returns_answer_with_evidence(app):
    plaintext, _, user_id = _create_user_with_token(app, username="alice_s2")
    _create_repo(app, owner_id=user_id, name="AE350 KB", slug="ae350-kb")
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=_FAKE_QUERY_RESULT):
        resp = app.test_client().post(
            "/api/v1/search",
            json={"query": "AE350 的接口", "repo": "alice_s2/ae350-kb"},
            headers=_h(plaintext),
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["routing"]["mode"] == "explicit"
    assert data["routing"]["selected_repo"]["slug"] == "ae350-kb"
    assert data["answer"].startswith("## 答案")
    assert data["confidence"]["level"] == "high"
    assert len(data["evidence"]["wiki_pages"]) == 1
    assert len(data["evidence"]["chunks"]) == 1
    assert data["trace_id"]
    assert "latency_ms" in data


def test_api_search_persists_query_log(app):
    from models import QueryLog

    plaintext, _, user_id = _create_user_with_token(app, username="alice_s3")
    _create_repo(app, owner_id=user_id, name="KB", slug="kb3")
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=_FAKE_QUERY_RESULT):
        resp = app.test_client().post(
            "/api/v1/search",
            json={"query": "q", "repo": "alice_s3/kb3"},
            headers=_h(plaintext),
        )
    assert resp.status_code == 200
    trace_id = resp.get_json()["trace_id"]
    with app.app_context():
        log = QueryLog.query.filter_by(trace_id=trace_id).first()
        assert log is not None
        assert log.question == "q"
        assert log.user_id == user_id
        assert log.confidence == "high"


def test_api_search_inaccessible_private_repo_returns_404(app):
    """alice 用她的 token 尝试检索 bob 的私有仓库 → 404。"""
    alice_token, _, _ = _create_user_with_token(app, username="alice_s4")
    _, _, bob_id = _create_user_with_token(app, username="bob_s4")
    _create_repo(app, owner_id=bob_id, name="Bob Secret", slug="bob-secret")
    resp = app.test_client().post(
        "/api/v1/search",
        json={"query": "q", "repo": "bob_s4/bob-secret"},
        headers=_h(alice_token),
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "repo_not_found"


def test_api_search_bad_repo_ref_returns_400(app):
    plaintext, _, _ = _create_user_with_token(app, username="alice_s5")
    resp = app.test_client().post(
        "/api/v1/search",
        json={"query": "q", "repo": "no-slash-here"},
        headers=_h(plaintext),
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_repo_ref"


def test_api_search_auto_route_hits_high_confidence_repo(app):
    """自动路由：LLM 以高置信度选中某 KB → 正常返回检索结果 + routing.mode=auto。"""
    plaintext, _, user_id = _create_user_with_token(app, username="alice_s6")
    _create_repo(
        app,
        owner_id=user_id,
        name="AE350 产品资料",
        slug="ae350-kb",
        description="小鱼 AE350 一体机规格与参数手册",
    )
    _create_repo(app, owner_id=user_id, name="个人笔记", slug="notes", description="")

    fake_llm_route = {
        "selected": "alice_s6/ae350-kb",
        "confidence": 0.92,
        "reason": "name/description 都命中 AE350",
    }
    with patch.object(app.llm, "chat_json", return_value=fake_llm_route), \
         patch.object(app.wiki_engine, "query_with_evidence", return_value=_FAKE_QUERY_RESULT):
        resp = app.test_client().post(
            "/api/v1/search",
            json={"query": "帮我查 AE350 的产品参数"},
            headers=_h(plaintext),
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["routing"]["mode"] == "auto"
    assert data["routing"]["selected_repo"]["slug"] == "ae350-kb"
    assert data["routing"]["confidence"] == pytest.approx(0.92)
    assert "AE350" in data["routing"]["reason"]
    assert data["answer"].startswith("## 答案")


def test_api_search_auto_route_low_confidence_returns_422(app):
    """自动路由：LLM 给出低置信度 → 返回 422 + candidates + 不触发真正检索。"""
    plaintext, _, user_id = _create_user_with_token(app, username="alice_s6b")
    _create_repo(app, owner_id=user_id, name="KB A", slug="kb-a", description="")
    _create_repo(app, owner_id=user_id, name="KB B", slug="kb-b", description="")

    fake_llm_route = {"selected": None, "confidence": 0.1, "reason": "无明显匹配"}
    with patch.object(app.llm, "chat_json", return_value=fake_llm_route), \
         patch.object(app.wiki_engine, "query_with_evidence") as mock_query:
        resp = app.test_client().post(
            "/api/v1/search",
            json={"query": "不相关的问题"},
            headers=_h(plaintext),
        )
        assert mock_query.call_count == 0  # 低置信度不应触发检索
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["error"] == "no_matching_repo"
    assert data["routing"]["mode"] == "auto"
    assert data["routing"]["error"] in ("low_confidence", "selected_not_in_candidates")
    assert len(data["candidates"]) == 2


def test_api_search_auto_route_no_visible_repo_returns_422(app):
    """自动路由：token 持有人没有任何可见 KB → 422 empty_candidates，不调用 LLM。"""
    plaintext, _, _ = _create_user_with_token(app, username="alice_s6c")
    with patch.object(app.llm, "chat_json") as mock_chat:
        resp = app.test_client().post(
            "/api/v1/search",
            json={"query": "任何问题"},
            headers=_h(plaintext),
        )
        assert mock_chat.call_count == 0
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "no_matching_repo"
    assert resp.get_json()["candidates"] == []


def test_api_search_auto_route_llm_hallucinated_repo_rejected(app):
    """自动路由：LLM 返回候选之外的 owner/slug → 当作未命中，422。"""
    plaintext, _, user_id = _create_user_with_token(app, username="alice_s6d")
    _create_repo(app, owner_id=user_id, name="Real KB", slug="real-kb")

    fake_llm_route = {
        "selected": "alice_s6d/ghost-kb",  # 不在候选里
        "confidence": 0.95,
        "reason": "瞎猜的",
    }
    with patch.object(app.llm, "chat_json", return_value=fake_llm_route):
        resp = app.test_client().post(
            "/api/v1/search",
            json={"query": "q"},
            headers=_h(plaintext),
        )
    assert resp.status_code == 422
    data = resp.get_json()
    assert data["error"] == "no_matching_repo"
    assert data["routing"]["error"] == "selected_not_in_candidates"


def test_api_search_explicit_repo_public_accessible_by_other_user(app):
    """其他用户的 public repo，alice 的 token 也能检索。"""
    alice_token, _, _ = _create_user_with_token(app, username="alice_s7")
    _, _, bob_id = _create_user_with_token(app, username="bob_s7")
    _create_repo(
        app, owner_id=bob_id, name="Public KB", slug="public-kb", is_public=True
    )
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=_FAKE_QUERY_RESULT):
        resp = app.test_client().post(
            "/api/v1/search",
            json={"query": "anything", "repo": "bob_s7/public-kb"},
            headers=_h(alice_token),
        )
    assert resp.status_code == 200
    assert resp.get_json()["routing"]["selected_repo"]["owner"] == "bob_s7"


# ---------------------------------------------------------------------------
# Web 端：创建 token 后明文一次性展示 + 选填过期
# ---------------------------------------------------------------------------


def test_web_create_token_shows_plaintext_once_and_persists_prefix(auth_client, app):
    from models import ApiToken

    resp = auth_client.post(
        "/user/settings/tokens/create",
        data={"name": "OpenClaw 集成", "expires_days": "30"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "新 Token 已创建" in body
    assert "ollw_" in body  # 明文 token 一次性展示
    # DB 记录：prefix 入库，hash 非空，expires_at ≈ 30 天后
    with app.app_context():
        t = ApiToken.query.filter_by(name="OpenClaw 集成").first()
        assert t is not None
        assert t.token_prefix.startswith("ollw_")
        assert len(t.token_hash) == 64
        assert t.expires_at is not None
        delta = t.expires_at - datetime.now(timezone.utc).replace(tzinfo=None)
        # allow ±1 day drift
        assert abs(delta.days - 30) <= 1

    # 第二次打开列表页：明文不应再出现（session 已消费掉）
    resp2 = auth_client.get("/user/settings/tokens")
    body2 = resp2.get_data(as_text=True)
    assert "新 Token 已创建" not in body2
    assert "ollw_" not in body2 or "ollw_xxxx" in body2  # 示例文字里的占位可保留


def test_web_create_token_rejects_invalid_expires_days(auth_client):
    resp = auth_client.post(
        "/user/settings/tokens/create",
        data={"name": "bad", "expires_days": "abc"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "有效期" in resp.get_data(as_text=True)
