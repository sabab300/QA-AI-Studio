"""Small shared helpers: ids, time, colours, hashing, JSON."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def new_run_id() -> str:
    return "UIUX-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(text, default=None):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


_WINDOWS_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def preserve_filename(name: str, fallback: str = "design", max_len: int = 150) -> str:
    """The name the user uploaded, as Knowledge Hub stores it: only the characters Windows cannot store are replaced
    (so 'CRF_Portal_Figma_Design.fig' and 'Login (v2).fig' stay exactly as typed). Path parts are dropped."""
    base = str(name or "").replace("\\", "/").split("/")[-1]
    base = _WINDOWS_BAD.sub("_", base).strip(" .")
    if not base:
        base = fallback
    if len(base) > max_len:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: max_len - len(ext) - 1] + "." + ext) if dot and len(ext) < 12 else base[:max_len]
    if base.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        base = "_" + base
    return base


def safe_filename(name: str, fallback: str = "file") -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", str(name or "").replace("\\", "/").split("/")[-1]).strip(" .")
    return (base or fallback)[:120]


# ----------------------------------------------------------------------------- colours

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB = re.compile(r"rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:\s*[,/]\s*([\d.%]+))?\s*\)")


def parse_color(value):
    """Return (r, g, b, a) with r,g,b in 0..255 and a in 0..1, or None when unparseable."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in ("transparent",):
        return (0, 0, 0, 0.0) if text == "transparent" else None
    m = _HEX.match(text)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (r, g, b, round(a, 4))
    m = _RGB.match(text)
    if m:
        a = m.group(4)
        if a is None:
            alpha = 1.0
        elif a.endswith("%"):
            alpha = float(a[:-1]) / 100
        else:
            alpha = float(a)
        return (round(float(m.group(1))), round(float(m.group(2))), round(float(m.group(3))), alpha)
    return None


def to_hex(color) -> str:
    c = parse_color(color) if not isinstance(color, tuple) else color
    if c is None:
        return ""
    r, g, b, a = c
    base = "#%02X%02X%02X" % (int(r), int(g), int(b))
    return base if a >= 0.999 else base + "%02X" % round(a * 255)


def color_distance(a, b) -> float:
    """Euclidean distance in RGB (0..441). Alpha differences are added as extra weight."""
    ca, cb = parse_color(a), parse_color(b)
    if ca is None or cb is None:
        return float("inf")
    d = ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2 + (ca[2] - cb[2]) ** 2) ** 0.5
    return d + abs(ca[3] - cb[3]) * 255


def composite(fg, bg):
    """Alpha-composite an (r,g,b,a) foreground over an opaque (r,g,b,a) background."""
    a = fg[3]
    return (fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1.0)


def _lin(c):
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color) -> float:
    r, g, b = color[0], color[1], color[2]
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(fg, bg) -> float:
    l1, l2 = luminance(fg), luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


class Stopwatch:
    def __init__(self):
        self.t0 = time.monotonic()

    def seconds(self) -> float:
        return round(time.monotonic() - self.t0, 2)
