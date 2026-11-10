"""Encrypted credential storage using the application's existing installation key (Database/web_secret.key).

Uses the same authenticated-encryption construction as the AI platform's SecretBox with its own domain label,
so a value stored here cannot be decrypted by another subsystem. Secrets are write-only through the API:
callers only ever receive ``is_set`` and a masked preview.
"""
from __future__ import annotations

import os

from .schema import UiuxDatabase
from .util import now_iso


def _box(key_material=None):
    from Core.ai_platform.secret_box import SecretBox

    class UiuxBox(SecretBox):
        LABEL = b"uiux-v1"

    return UiuxBox(key_material)


class Vault:
    def __init__(self, db: UiuxDatabase, key_material=None):
        self.db = db
        self._key = key_material

    def put(self, name: str, value: str, user: str = ""):
        if not value:
            raise ValueError("A secret value is required.")
        blob = _box(self._key).encrypt(value)
        with self.db.connect() as c:
            c.execute("INSERT INTO uiux_secrets(name,blob,set_by,set_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(name) DO UPDATE SET blob=excluded.blob,set_by=excluded.set_by,set_at=excluded.set_at",
                      (name, blob, user, now_iso()))

    def get(self, name: str) -> str:
        with self.db.connect() as c:
            row = c.execute("SELECT blob FROM uiux_secrets WHERE name=?", (name,)).fetchone()
        if not row:
            return ""
        try:
            return _box(self._key).decrypt(row["blob"])
        except ValueError:
            return ""

    def delete(self, name: str):
        with self.db.connect() as c:
            c.execute("DELETE FROM uiux_secrets WHERE name=?", (name,))

    def status(self, name: str) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT set_by,set_at FROM uiux_secrets WHERE name=?", (name,)).fetchone()
        if not row:
            return {"is_set": False}
        from Core.ai_platform.secret_box import mask_preview
        return {"is_set": True, "preview": mask_preview(self.get(name)), "set_by": row["set_by"], "set_at": row["set_at"]}

    # ----- Figma token: stored secret first, FIGMA_ACCESS_TOKEN environment variable as fallback
    FIGMA = "figma.token"

    def figma_token(self) -> tuple[str, str]:
        stored = self.get(self.FIGMA)
        if stored:
            return stored, "stored"
        env = os.environ.get("FIGMA_ACCESS_TOKEN", "").strip()
        return (env, "environment") if env else ("", "none")


def redact(text, secrets_list=()):
    """Remove known secret values and token-looking strings from text that may be logged or returned."""
    import re
    out = str(text or "")
    for secret in secrets_list:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "[redacted]")
    out = re.sub(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}", r"\1 [redacted]", out)
    out = re.sub(r"(?i)(x-figma-token|authorization|cookie|password|passwd|token|secret)(\"?\s*[:=]\s*\"?)(?!\[redacted\]|(?:bearer|basic)\s+\[redacted\])([^\s\"',;]{4,})", r"\1\2[redacted]", out)
    out = re.sub(r"figd_[A-Za-z0-9_\-]{10,}", "[redacted]", out)
    return out
