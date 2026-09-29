"""Authentication: password hashing and signed session tokens.

Passwords are hashed with PBKDF2-SHA256 and a per-user salt. After a
successful login the user receives a token of the form
``<user_id>.<expiry>.<signature>`` where the signature is an HMAC of the
first two parts with the server secret.
"""

import hashlib
import hmac
import os
import time

from bookshelf.config import settings
from bookshelf.storage import UserRepository

PBKDF2_ITERATIONS = 200_000


class AuthError(Exception):
    """Raised when credentials or tokens are invalid."""


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Hash a password with PBKDF2 and return ``salt$hash`` in hex."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored ``salt$hash`` value in constant time."""
    salt_hex, _ = stored.split("$", 1)
    candidate = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(candidate, stored)


def _sign(payload: str) -> str:
    return hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(user_id: int) -> str:
    """Create a signed session token that expires after ``token_ttl_seconds``."""
    expires = int(time.time()) + settings.token_ttl_seconds
    payload = f"{user_id}.{expires}"
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> int:
    """Return the user id inside a valid token, or raise ``AuthError``."""
    try:
        user_id, expires, signature = token.split(".")
    except ValueError as exc:
        raise AuthError("Malformed token") from exc
    if not hmac.compare_digest(signature, _sign(f"{user_id}.{expires}")):
        raise AuthError("Bad signature")
    if int(expires) < time.time():
        raise AuthError("Token expired")
    return int(user_id)


def login(users: UserRepository, email: str, password: str) -> str:
    """Authenticate a user by email and password and return a session token."""
    user = users.find_by_email(email)
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("Invalid email or password")
    return issue_token(user.id)
