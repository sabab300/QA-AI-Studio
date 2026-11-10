"""Build fingerprint: proves which code is actually running.

``loaded`` is hashed when the server imports the module; ``disk`` is hashed on demand. If they differ, the files on disk
changed after the server started and the running process is NOT the latest code (restart required).
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

from . import ENGINE_VERSION

APP_ROOT = Path(__file__).resolve().parents[2]


def _files() -> list:
    pkg = sorted((APP_ROOT / "Core" / "uiux").glob("*.py"))
    extra = [APP_ROOT / "Web" / "routers" / "uiux_router.py", APP_ROOT / "Frontend" / "uiux.js", APP_ROOT / "Frontend" / "uiux.css"]
    return pkg + [p for p in extra if p.is_file()]


def _hash(files) -> tuple[str, dict]:
    digest, parts = hashlib.sha256(), {}
    for path in files:
        data = path.read_bytes().replace(b"\r\n", b"\n")      # line endings do not change behaviour
        h = hashlib.sha256(data).hexdigest()
        parts[str(path.relative_to(APP_ROOT)).replace("\\", "/")] = h[:12]
        digest.update(path.name.encode() + b"\0" + h.encode())
    return digest.hexdigest(), parts


LOADED_HASH, LOADED_PARTS = _hash(_files())
STARTED_AT = time.time()


def fingerprint() -> dict:
    disk_hash, disk_parts = _hash(_files())
    try:
        from importlib.metadata import version
        pw = version("playwright")
    except Exception:
        pw = ""
    changed = sorted(k for k in set(LOADED_PARTS) | set(disk_parts) if LOADED_PARTS.get(k) != disk_parts.get(k))
    return {"engine_version": ENGINE_VERSION, "loaded_hash": LOADED_HASH[:16], "disk_hash": disk_hash[:16], "running_latest_code": LOADED_HASH == disk_hash,
            "changed_since_start": changed, "files": len(disk_parts), "pid": os.getpid(), "started_at": int(STARTED_AT), "python": sys.version.split()[0], "playwright": pw}
