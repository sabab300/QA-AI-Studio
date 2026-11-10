# Create: AI-Web/Core/recording_targets.py

"""
QA AI Studio
Recording Targets

Version: 1.0

Where the Web recorder opens its browser used to be fixed: either an
explicit ?start_url= query value (unchecked) or the single Base URL
saved in Test Environment Settings.  This module adds an explicit,
validated "recording target" concept WITHOUT touching or reshaping the
existing environment configuration:

  * "configured"  - the Base URL already saved in Test Environment
                    Settings (read through TestEnvironmentConfig.load(),
                    never written here).  This stays the default.
  * saved targets - extra named URLs an operator explicitly authorised
                    earlier (Config/recording_targets.json, a separate
                    file so Test Environment Settings is untouched).
  * custom URL    - any other http(s) URL, accepted only when the caller
                    states that they are authorised to test that target.

Validation is generic: scheme must be http/https, a host is required,
embedded credentials are refused (they would be recorded into scripts
and logs), and control characters / whitespace are refused.
"""

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from Core.test_environment_config import TestEnvironmentConfig

TARGETS_PATH = Path(__file__).resolve().parent.parent / "Config" / "recording_targets.json"

CONFIGURED_TARGET_ID = "configured"

_LOCK = threading.Lock()


class RecordingTargetError(ValueError):
    """The requested recording target is invalid or not authorised."""


def normalize_target_url(raw):
    """Return a normalised http(s) URL or raise RecordingTargetError."""

    text = (raw or "").strip()

    if not text:
        raise RecordingTargetError("The recording target URL is empty.")

    if re.search(r"[\s\x00-\x1f\x7f]", text):
        raise RecordingTargetError("The recording target URL must not contain spaces or control characters.")

    if "://" not in text:
        # "javascript:...", "mailto:...", "data:..." etc. are schemes, not hosts; "host:8080/x" is a host.
        if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:(?!//)(?!\d+(?:[/?#]|$))", text):
            raise RecordingTargetError("Only http:// and https:// recording targets are allowed.")
        text = "https://" + text

    try:
        parts = urlsplit(text)
        port = parts.port  # raises ValueError for an invalid port
    except ValueError as error:
        raise RecordingTargetError(f"The recording target URL is not valid: {error}") from error

    if parts.scheme.lower() not in ("http", "https"):
        raise RecordingTargetError("Only http:// and https:// recording targets are allowed.")

    if not parts.hostname:
        raise RecordingTargetError("The recording target URL has no host name.")

    if parts.username or parts.password:
        raise RecordingTargetError(
            "The recording target URL must not contain a user name or password; "
            "use the Test Environment credentials instead."
        )

    del port
    return parts.geturl()


def origin_of(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme.lower()}://{host}{port}"


def _read_saved():
    try:
        if TARGETS_PATH.exists():
            data = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict) and item.get("url")]
    except Exception:
        pass
    return []


def _write_saved(items):
    TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGETS_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _configured_base_url():
    base = (TestEnvironmentConfig().load().get("base_url") or "").strip()
    if not base:
        return ""
    try:
        return normalize_target_url(base)
    except RecordingTargetError:
        return ""


def list_targets():
    """The configured environment first, then every saved target."""

    configured = _configured_base_url()
    items = [{
        "id": CONFIGURED_TARGET_ID,
        "name": "Configured test environment",
        "url": configured,
        "kind": "configured",
        "available": bool(configured),
    }]
    for saved in _read_saved():
        items.append({
            "id": saved.get("id"),
            "name": saved.get("name") or saved.get("url"),
            "url": saved.get("url"),
            "kind": "saved",
            "available": True,
            "created_at": saved.get("created_at"),
        })
    return items


def save_target(name, url, authorized):
    """Remember an authorised target for later selection."""

    if not authorized:
        raise RecordingTargetError(
            "Confirm that you are authorised to test this target before saving it."
        )
    normalized = normalize_target_url(url)
    label = (name or "").strip()[:80] or urlsplit(normalized).hostname
    with _LOCK:
        items = _read_saved()
        for item in items:
            if origin_of(item["url"]) == origin_of(normalized) and item["url"] == normalized:
                item["name"] = label
                _write_saved(items)
                return item
        item = {
            "id": "t" + uuid.uuid4().hex[:10],
            "name": label,
            "url": normalized,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        items.append(item)
        _write_saved(items)
        return item


def delete_target(target_id):
    with _LOCK:
        items = _read_saved()
        remaining = [item for item in items if item.get("id") != target_id]
        if len(remaining) == len(items):
            return False
        _write_saved(remaining)
        return True


def resolve_target(target_id=None, url=None, authorized=False):
    """
    Decide the URL the recorder should open.

    Returns {"url": str, "source": "configured"|"saved"|"custom", "label": str}.
    Raises RecordingTargetError when nothing is configured, the URL is
    invalid, or a custom target was not acknowledged as authorised.
    """

    target_id = (target_id or "").strip()
    custom = (url or "").strip()

    if target_id and target_id != CONFIGURED_TARGET_ID:
        for saved in _read_saved():
            if saved.get("id") == target_id:
                return {"url": normalize_target_url(saved["url"]), "source": "saved",
                        "label": saved.get("name") or saved["url"]}
        raise RecordingTargetError("The selected recording target no longer exists.")

    if custom and not target_id:
        normalized = normalize_target_url(custom)
        origin = origin_of(normalized)
        known_origins = {origin_of(item["url"]) for item in _read_saved()}
        configured = _configured_base_url()
        if configured:
            known_origins.add(origin_of(configured))
        if origin in known_origins:
            return {"url": normalized, "source": "saved" if configured and origin != origin_of(configured) else "configured",
                    "label": origin}
        if not authorized:
            raise RecordingTargetError(
                f"{origin} is not a configured or saved recording target. "
                "Confirm that you are authorised to test it to record against it."
            )
        return {"url": normalized, "source": "custom", "label": origin}

    configured = _configured_base_url()
    if not configured:
        raise RecordingTargetError(
            "No recording target was chosen and no Base URL is set in Test "
            "Environment Settings. Choose a target or set a Base URL first."
        )
    return {"url": configured, "source": "configured", "label": "Configured test environment"}
