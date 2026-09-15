"""Safe one-line serialization for structured Playwright QA_STEP metadata."""

import re


def normalize_metadata_value(value, default="-"):
    """Return deterministic, readable text that cannot escape a comment line."""
    normalized = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return normalized or default


def metadata_comment(key, value, default="-"):
    return f"# {normalize_metadata_value(key, '')}: {normalize_metadata_value(value, default)}"
