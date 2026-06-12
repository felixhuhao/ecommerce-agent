from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return an argon2 hash of `password` with the salt embedded."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify `password` against an argon2 hash; return False for invalid hashes."""
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, ValueError):
        return False
