from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


@lru_cache
def _dummy_hash() -> str:
    return _hasher.hash("eagle-timing-equalizer")


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check so unknown usernames are not detectable."""
    verify_password(_dummy_hash(), password)


class SecretBox:
    """Encrypts credentials at rest (inference keys, SMTP passwords, tokens)."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()


def mask_secret(value: str, visible: int = 4) -> str:
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * 8 + value[-visible:]
