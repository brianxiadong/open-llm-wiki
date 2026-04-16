"""Local encryption helpers for confidential repositories."""

from __future__ import annotations

import hashlib
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_SIZE = 12
_KEY_LEN = 32
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def generate_salt(length: int = 16) -> str:
    return urlsafe_b64encode(secrets.token_bytes(length)).decode("ascii")


def derive_key(passphrase: str, salt_b64: str) -> bytes:
    salt = urlsafe_b64decode(salt_b64.encode("ascii"))
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
        maxmem=0,
    )


def encrypt_bytes(plaintext: bytes, *, key: bytes, aad: bytes = b"") -> bytes:
    nonce = secrets.token_bytes(_NONCE_SIZE)
    cipher = AESGCM(key)
    return nonce + cipher.encrypt(nonce, plaintext, aad)


def decrypt_bytes(ciphertext: bytes, *, key: bytes, aad: bytes = b"") -> bytes:
    nonce = ciphertext[:_NONCE_SIZE]
    payload = ciphertext[_NONCE_SIZE:]
    cipher = AESGCM(key)
    return cipher.decrypt(nonce, payload, aad)
