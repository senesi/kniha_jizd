from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()

# Minimum length shared by every path that sets a password (admin reset,
# self-service change). Kept here, not duplicated per call site, so "same
# rules everywhere" stays literally true.
MIN_PASSWORD_LENGTH = 8


class WeakPassword(Exception):
    pass


def validate_password_policy(plain_password: str) -> None:
    if len(plain_password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"Heslo musí mít alespoň {MIN_PASSWORD_LENGTH} znaků.")


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False
