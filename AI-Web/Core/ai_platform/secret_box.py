"""
Local authenticated encryption for provider / MCP credentials.

Uses the same installation signing key file the rest of QA AI Studio already
relies on (Database/web_secret.key) and the same construction already used by
the publishing integration secrets (HMAC-derived keystream + independent
HMAC tag), domain-separated with its own label so a value encrypted for the
AI platform can never be decrypted by another subsystem or vice versa.

Secrets are never returned to the frontend and never written to logs; callers
only ever see ``is_set`` booleans and masked previews (last 4 characters).
"""

import base64
import hashlib
import hmac
import secrets
from pathlib import Path


def _key_path():
    return Path(__file__).resolve().parents[2] / "Database" / "web_secret.key"


class SecretBox:

    LABEL = b"ai-platform-v1"

    def __init__(self, key_material=None):

        if key_material is None:
            path = _key_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(secrets.token_bytes(32))
            key_material = path.read_bytes()

        self.key = hashlib.sha256(bytes(key_material) + self.LABEL).digest()

    def encrypt(self, value):

        if not value:
            return ""

        nonce = secrets.token_bytes(16)
        raw = str(value).encode("utf-8")
        stream = self._stream(nonce, len(raw))
        cipher = bytes(a ^ b for a, b in zip(raw, stream))
        tag = hmac.new(self.key, nonce + cipher, hashlib.sha256).digest()

        return base64.urlsafe_b64encode(nonce + tag + cipher).decode("ascii")

    def decrypt(self, blob):

        if not blob:
            return ""

        payload = base64.urlsafe_b64decode(blob.encode("ascii"))
        nonce, tag, cipher = payload[:16], payload[16:48], payload[48:]
        expected = hmac.new(self.key, nonce + cipher, hashlib.sha256).digest()

        if not hmac.compare_digest(tag, expected):
            raise ValueError("Stored credential could not be authenticated.")

        stream = self._stream(nonce, len(cipher))

        return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")

    def _stream(self, nonce, length):

        stream = b""
        counter = 0

        while len(stream) < length:
            stream += hmac.new(
                self.key, nonce + counter.to_bytes(4, "big"), hashlib.sha256
            ).digest()
            counter += 1

        return stream[:length]


def mask_preview(value):
    """Return a safe preview of a secret ("****abcd") or "" when unset."""

    value = str(value or "")

    if not value:
        return ""

    return "•" * 4 + value[-4:] if len(value) > 8 else "•" * 8
