"""token_crypto 的单元测试：加解密往返、密钥缺失/非法/不匹配的错误路径。"""

from __future__ import annotations

import importlib
import os

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def crypto_with_key(monkeypatch):
    """设置一个有效的 Fernet 密钥，并 reload 模块以刷新缓存。"""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("API_TOKEN_ENC_KEY", key)
    import token_crypto
    importlib.reload(token_crypto)
    yield token_crypto, key
    importlib.reload(token_crypto)


@pytest.fixture
def crypto_without_key(monkeypatch):
    monkeypatch.delenv("API_TOKEN_ENC_KEY", raising=False)
    import token_crypto
    importlib.reload(token_crypto)
    yield token_crypto
    importlib.reload(token_crypto)


def test_roundtrip_returns_original_plaintext(crypto_with_key):
    tc, _ = crypto_with_key
    plaintext = "ollw_" + "x" * 43
    cipher = tc.encrypt_token(plaintext)
    assert cipher is not None
    # 密文应该是 ASCII 安全的 URL-safe base64
    assert cipher.encode("ascii").decode("ascii") == cipher
    assert cipher != plaintext
    assert tc.decrypt_token(cipher) == plaintext


def test_encryption_enabled_true_when_key_present(crypto_with_key):
    tc, _ = crypto_with_key
    assert tc.encryption_enabled() is True


def test_encryption_disabled_when_key_absent(crypto_without_key):
    tc = crypto_without_key
    assert tc.encryption_enabled() is False


def test_encrypt_returns_none_without_key(crypto_without_key):
    tc = crypto_without_key
    assert tc.encrypt_token("ollw_anything") is None


def test_decrypt_raises_without_key(crypto_without_key):
    tc = crypto_without_key
    with pytest.raises(tc.TokenCryptoError):
        tc.decrypt_token("gAAAAABanythingcipher")


def test_invalid_key_format_raises(monkeypatch):
    monkeypatch.setenv("API_TOKEN_ENC_KEY", "this-is-not-a-valid-fernet-key")
    import token_crypto
    importlib.reload(token_crypto)
    with pytest.raises(token_crypto.TokenCryptoError):
        token_crypto.encrypt_token("ollw_test")
    importlib.reload(token_crypto)


def test_decrypt_with_wrong_key_raises(monkeypatch):
    """用一个密钥加密，换另一个密钥尝试解密应该抛 TokenCryptoError。"""
    import token_crypto

    k1 = Fernet.generate_key().decode("ascii")
    k2 = Fernet.generate_key().decode("ascii")
    assert k1 != k2

    monkeypatch.setenv("API_TOKEN_ENC_KEY", k1)
    importlib.reload(token_crypto)
    cipher = token_crypto.encrypt_token("ollw_abc123")
    assert cipher is not None

    monkeypatch.setenv("API_TOKEN_ENC_KEY", k2)
    importlib.reload(token_crypto)
    with pytest.raises(token_crypto.TokenCryptoError):
        token_crypto.decrypt_token(cipher)
    importlib.reload(token_crypto)


def test_encrypt_rejects_empty_plaintext(crypto_with_key):
    tc, _ = crypto_with_key
    with pytest.raises(ValueError):
        tc.encrypt_token("")


def test_generate_key_is_valid_fernet_key(crypto_with_key):
    tc, _ = crypto_with_key
    key = tc.generate_key()
    # 能被 Fernet 直接用（不抛异常）
    Fernet(key.encode("ascii"))
