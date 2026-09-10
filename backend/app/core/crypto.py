"""
core/crypto.py — symmetric encryption for secrets at rest.

Swiggy access tokens live in Redis for 5 days. They should not sit there in
plaintext — anyone with Redis access (a leaked URL, a shared instance, a
backup) would have every connected user's food-delivery account.

We encrypt them with Fernet (AES-128-CBC + HMAC), keyed off `SECRET_KEY`.
The key is derived, not used raw, so `SECRET_KEY` can be any string.

`decrypt()` is lenient: a value that isn't a Fernet token is returned
unchanged, so tokens written before this landed keep working until they
expire.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

# Fernet needs a 32-byte urlsafe-base64 key. Derive it deterministically
# from SECRET_KEY so restarts / multiple instances share it.
_key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
_fernet = Fernet(_key)

# Fernet tokens are urlsafe-base64 and always start with this (version 0x80).
_FERNET_PREFIX = "gAAAAA"


def encrypt(plaintext: str) -> str:
    """Encrypt a string for storage. Empty input passes through."""
    if not plaintext:
        return plaintext
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(value: str) -> str:
    """
    Decrypt a value written by `encrypt()`. If it doesn't look like / isn't
    a valid Fernet token (legacy plaintext), return it as-is.
    """
    if not value or not value.startswith(_FERNET_PREFIX):
        return value
    try:
        return _fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        return value
