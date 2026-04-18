"""API Token 鉴权 —— Bearer token 支持，用于 /api/v1/** 外部调用。

设计要点：
- Token 原文：``ollw_<base64url(32B)>``，仅在创建时返回一次，DB 只存 sha256。
- ``token_prefix`` 保存原文前 12 位（``ollw_<6>``），方便在列表里辨认是哪把。
- ``scopes`` 细粒度控制，默认 ``kb:search,kb:read``；后续写类 API 另开 scope。
- ``expires_at`` 为空表示永不过期；到期或被吊销（``is_active=False``）即失效。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import current_app, g, jsonify, request
from sqlalchemy import or_


TOKEN_PREFIX = "ollw_"
TOKEN_BYTES = 32
TOKEN_PREFIX_STORE_LEN = 12  # 存入 token_prefix 的长度（含 `ollw_`）

DEFAULT_SCOPES = "kb:search,kb:read"


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """生成 token 三元组：``(plaintext, token_hash, token_prefix)``。"""
    body = secrets.token_urlsafe(TOKEN_BYTES).rstrip("=")
    plaintext = f"{TOKEN_PREFIX}{body}"
    token_hash = hash_token(plaintext)
    token_prefix = plaintext[:TOKEN_PREFIX_STORE_LEN]
    return plaintext, token_hash, token_prefix


def _extract_bearer_token() -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _json_error(code: str, message: str, status: int):
    return jsonify(error=code, message=message), status


def _update_last_used(token) -> None:
    """以 1 分钟为间隔节流更新 last_used_at，避免每次请求都写库。"""
    from models import db

    now = datetime.now(timezone.utc)
    last = token.last_used_at
    if last is None or (now - (last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last)) > timedelta(minutes=1):
        token.last_used_at = now
        try:
            db.session.commit()
        except Exception:  # noqa: BLE001
            db.session.rollback()


def api_token_required(*required_scopes: str):
    """装饰器：校验 Bearer token；通过后 ``g.api_user`` / ``g.api_token`` 就绪。

    失败返回标准 JSON：``{"error": <code>, "message": <desc>}``，HTTP 401/403。
    """
    required = tuple(required_scopes) or ("kb:read",)

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            plaintext = _extract_bearer_token()
            if not plaintext:
                return _json_error(
                    "missing_bearer_token",
                    "缺少 Authorization: Bearer <token> 头",
                    401,
                )
            from models import ApiToken

            token = ApiToken.query.filter_by(token_hash=hash_token(plaintext)).first()
            if token is None:
                return _json_error("invalid_token", "token 无效", 401)
            if not token.is_active:
                return _json_error("token_revoked", "token 已被吊销", 401)
            if token.is_expired():
                return _json_error("token_expired", "token 已过期", 401)
            for scope in required:
                if not token.has_scope(scope):
                    return _json_error(
                        "insufficient_scope",
                        f"缺少所需权限：{scope}",
                        403,
                    )

            g.api_token = token
            g.api_user = token.user
            try:
                _update_last_used(token)
            except Exception as exc:  # noqa: BLE001
                current_app.logger.warning("update token last_used failed: %s", exc)
            return view(*args, **kwargs)

        return wrapper

    return decorator


__all__ = [
    "TOKEN_PREFIX",
    "DEFAULT_SCOPES",
    "hash_token",
    "generate_token",
    "api_token_required",
]
