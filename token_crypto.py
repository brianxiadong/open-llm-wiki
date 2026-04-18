"""API Token 明文的对称加密存取。

设计背景：
- DB 里的 ``api_tokens.token_hash`` 是 SHA256 摘要，用于接入鉴权，不可逆。
- 但运营/脚本使用场景下用户需要随时在 UI 里复制**完整明文**再贴到外部系统
  （OpenClaw / ci-bot 等）。为此引入一个"对称加密密文"列 ``token_cipher``，
  密钥只放在应用运行环境的 ``API_TOKEN_ENC_KEY`` 里，DB 本身不知道。
- 选型 Fernet（AES-128-CBC + HMAC-SHA256），``cryptography`` 包内置，适合
  几十字节明文，防篡改 + 含时间戳。密钥轮换超出当前范围。

接口：
- ``encrypt_token(plaintext) -> str | None``: 返回密文文本；密钥缺失时返回 None，
  调用方据此决定是否把 ``token_cipher`` 写成空（老 token / 未配置密钥场景）。
- ``decrypt_token(cipher_text) -> str``: 返回明文；密钥缺失或密文损坏会抛异常。
- ``encryption_enabled() -> bool``: 给上层/UI 用，判断是否能提供"复制完整 token"。
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

_ENV_KEY_NAME = "API_TOKEN_ENC_KEY"
_cached_fernet: Fernet | None = None
_cached_key_value: str | None = None


class TokenCryptoError(RuntimeError):
    """令牌加解密层的统一异常。"""


def _load_fernet() -> Fernet | None:
    """按需加载 Fernet；密钥缺失时返回 None（非致命，但上层应报错/降级）。"""
    global _cached_fernet, _cached_key_value

    key = os.environ.get(_ENV_KEY_NAME, "").strip()
    if not key:
        # 关闭缓存，便于单测在 monkeypatch env 后立刻生效。
        _cached_fernet = None
        _cached_key_value = None
        return None

    if _cached_fernet is not None and _cached_key_value == key:
        return _cached_fernet

    try:
        f = Fernet(key.encode("ascii"))
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        raise TokenCryptoError(
            f"{_ENV_KEY_NAME} 不是合法 Fernet 密钥（应是 32 字节 URL-safe base64，"
            f"可用 python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' 生成）"
        ) from exc

    _cached_fernet = f
    _cached_key_value = key
    return f


def encryption_enabled() -> bool:
    """UI 层用：当前进程是否配置了可用的令牌加密密钥。"""
    try:
        return _load_fernet() is not None
    except TokenCryptoError:
        return False


def encrypt_token(plaintext: str) -> Optional[str]:
    """加密明文 token。

    密钥未配置时返回 None（调用方写 cipher=None，表示"不可恢复"）。
    """
    if not plaintext:
        raise ValueError("plaintext 不能为空")
    f = _load_fernet()
    if f is None:
        log.warning(
            "未配置 %s，token 明文将无法保存到 api_tokens.token_cipher，"
            "列表页的一键复制能力会降级。",
            _ENV_KEY_NAME,
        )
        return None
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(cipher_text: str) -> str:
    """解密密文 token。

    - 密钥缺失：抛 TokenCryptoError，UI 应该显示"未配置密钥，无法恢复"。
    - 密文损坏/密钥不匹配：抛 TokenCryptoError。
    """
    if not cipher_text:
        raise TokenCryptoError("cipher_text 为空，无法解密")
    f = _load_fernet()
    if f is None:
        raise TokenCryptoError(
            f"{_ENV_KEY_NAME} 未配置，当前进程无法解密已有 token 密文。"
        )
    try:
        return f.decrypt(cipher_text.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise TokenCryptoError(
            "token 密文解密失败：密钥不匹配或密文已损坏。"
        ) from exc


def generate_key() -> str:
    """生成一个新的 Fernet 密钥（供 manage.py 命令/文档使用）。"""
    return Fernet.generate_key().decode("ascii")
