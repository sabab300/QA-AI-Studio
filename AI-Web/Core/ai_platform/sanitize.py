"""
Error / prompt sanitising for the AI platform.

* ``scrub``           - remove credentials and token-like strings from any text
                        that may be shown in the UI, stored, or logged.
* ``redact_for_cloud`` - redact credential-like and e-mail strings from a prompt
                        before it is sent to a cloud provider (Security tab).
"""

import re

_PATTERNS = (
    # Authorization / bearer / api-key style header values
    re.compile(r"(?i)(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(x-api-key|api[-_ ]?key|apikey|authorization|x-goog-api-key)\s*[:=]\s*\S+"),
    # well-known key shapes
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bor-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    # JSON-ish "password": "x" / "token": "x"
    re.compile(r'(?i)"(password|passwd|secret|token|access_token|client_secret|api_key)"\s*:\s*"[^"]{1,}"'),
)

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_LONG_TOKEN = re.compile(r"\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9_-]{40,}\b")

MASK = "[REDACTED]"


def scrub(text, secrets=(), limit=300):
    """Return ``text`` with known secrets and credential-like strings masked."""

    text = "" if text is None else str(text)

    for secret in secrets or ():
        secret = str(secret or "")
        if len(secret) >= 4:
            text = text.replace(secret, MASK)

    for pattern in _PATTERNS:
        text = pattern.sub(MASK, text)

    text = " ".join(text.split())

    if limit and len(text) > limit:
        text = text[:limit] + "..."

    return text


def redact_for_cloud(text):
    """Mask credential-like strings, e-mail addresses and long opaque tokens."""

    text = "" if text is None else str(text)

    for pattern in _PATTERNS:
        text = pattern.sub(MASK, text)

    text = _EMAIL.sub("[EMAIL]", text)
    text = _LONG_TOKEN.sub(MASK, text)

    return text
