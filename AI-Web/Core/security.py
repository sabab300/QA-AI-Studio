# Create: AI/Core/security.py

"""
QA AI Studio
Password Hashing & Session Token Signing

Version: 1.0

Deliberately stdlib-only (hashlib/hmac/secrets) so the web
migration's very first module doesn't force a new third-party
security dependency before the team has agreed on one. Swapping
this for passlib/PyJWT/OAuth later is a self-contained change —
every caller goes through the four functions below, never through
hashlib directly.

Token format: base64url(payload_json) + "." + base64url(hmac_sha256),
where payload_json carries {user_id, username, role_id, role_name,
exp}. This is intentionally the same shape as a JWT so it's a small
change to switch to PyJWT later — it just isn't one today.
"""

import base64
import hashlib
import hmac
import json
import secrets
import string
import time
from pathlib import Path

_PBKDF2_ITERATIONS = 260_000
_TOKEN_TTL_SECONDS = 8 * 60 * 60  # 8 hour session


def hash_password(plain_password: str):

    salt = secrets.token_hex(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        plain_password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    ).hex()

    return digest, salt


def verify_password(plain_password: str, password_hash: str, salt: str) -> bool:

    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        plain_password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    ).hex()

    return hmac.compare_digest(candidate, password_hash)


def generate_temp_password(length: int = 14) -> str:

    alphabet = string.ascii_letters + string.digits

    return "".join(secrets.choice(alphabet) for _ in range(length))


def _get_or_create_secret() -> bytes:
    """
    A per-installation signing secret, generated once and reused —
    so tokens survive an app restart but a fresh install (or a
    deliberately deleted key file) invalidates every old session.
    """

    key_path = Path("Database") / "web_secret.key"

    key_path.parent.mkdir(exist_ok=True, parents=True)

    if key_path.exists():

        return key_path.read_bytes()

    secret = secrets.token_bytes(32)

    key_path.write_bytes(secret)

    return secret


def _b64encode(data: bytes) -> str:

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:

    padding = "=" * (-len(data) % 4)

    return base64.urlsafe_b64decode(data + padding)


def create_token(user_id, username, role_id, role_name) -> str:

    payload = {
        "user_id": user_id,
        "username": username,
        "role_id": role_id,
        "role_name": role_name,
        "exp": time.time() + _TOKEN_TTL_SECONDS,
    }

    payload_bytes = json.dumps(payload).encode("utf-8")

    secret = _get_or_create_secret()

    signature = hmac.new(secret, payload_bytes, hashlib.sha256).digest()

    return f"{_b64encode(payload_bytes)}.{_b64encode(signature)}"


def verify_token(token: str):
    """Returns the payload dict if valid and unexpired, else None."""

    try:

        payload_part, signature_part = token.split(".", 1)

        payload_bytes = _b64decode(payload_part)

        signature = _b64decode(signature_part)

    except Exception:

        return None

    secret = _get_or_create_secret()

    expected_signature = hmac.new(
        secret, payload_bytes, hashlib.sha256
    ).digest()

    if not hmac.compare_digest(signature, expected_signature):

        return None

    payload = json.loads(payload_bytes.decode("utf-8"))

    if payload.get("exp", 0) < time.time():

        return None

    return payload
