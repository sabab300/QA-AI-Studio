"""Bounded, metadata-only HAR ingestion. Never persist raw traffic bodies.

All header/query/body values are discarded, rather than guessing which values
are credentials. Only field names, types and HTTP evidence leave this module.
"""
import hashlib
import json
import re
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

MAX_UPLOAD = 10 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_BODY = 65536
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
SENSITIVE = re.compile(r"password|passwd|secret|token|authorization|cookie|api.?key|credential|session", re.I)


class DiscoveryError(ValueError):
    """Safe application-level validation message (never raw traffic)."""


def field_name(value):
    value = str(value)
    # Keys can themselves contain injected data; never allow arbitrary long keys.
    return value if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_.$\[\]-]{0,79}", value) else "[unrecognized-field]"


def shape(value, depth=0):
    from .samples import PRIVATE
    if depth >= 5:
        return {"type": "unknown", "truncated": True}
    if isinstance(value, dict):
        return {"type": "object", "properties": {
            field_name(k): {"type": "redacted"} if SENSITIVE.search(str(k)) or PRIVATE.search(str(k)) else shape(v, depth + 1)
            for k, v in list(value.items())[:60]
        }}
    if isinstance(value, list):
        return {"type": "array", "items": shape(value[0], depth + 1) if value else {"type": "unknown"}}
    return {"type": "null" if value is None else "boolean" if isinstance(value, bool) else
            "number" if isinstance(value, (int, float)) else "string"}


def safe_url(raw):
    try:
        p = urlsplit(str(raw))
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            raise ValueError()
        host = p.hostname.encode("idna").decode("ascii").lower()
        port = p.port
        netloc = ("[" + host + "]") if ":" in host else host
        if port and port != (443 if p.scheme == "https" else 80):
            netloc += ":" + str(port)
        segments = []
        hide_next = False
        for segment in p.path.split("/")[:40]:
            decoded = unquote(segment)
            hidden = hide_next or len(decoded) > 32 or bool(re.fullmatch(r"[0-9a-fA-F-]{16,}", decoded))
            segments.append("[redacted]" if hidden else quote(decoded, safe="-._~"))
            hide_next = bool(SENSITIVE.search(decoded))
        query = "&".join(quote(field_name(k)) + "=[redacted]" for k, _ in parse_qsl(p.query, max_num_fields=100))
        return urlunsplit((p.scheme, netloc, "/".join(segments) or "/", query, ""))
    except (ValueError, UnicodeError):
        raise DiscoveryError("Traffic contains an invalid or unsupported HTTP URL.") from None


def normalize_url(url):
    p = urlsplit(safe_url(url))
    counts = {"id": 0, "opaque": 0}
    segments = []
    for s in p.path.split("/"):
        kind = "id" if re.fullmatch(r"\d+", s) else "opaque" if unquote(s) == "[redacted]" else None
        if kind:
            counts[kind] += 1
            s = "{" + kind + (str(counts[kind]) if counts[kind] > 1 else "") + "}"
        segments.append(s)
    return urlunsplit((p.scheme, p.netloc, "/".join(segments), "", ""))


def json_shape(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BODY:
        return {"type": "unknown", "reason": "Body omitted or exceeds schema sampling limit"}
    try:
        return shape(json.loads(text))
    except (ValueError, RecursionError):
        return {"type": "unknown", "reason": "Not JSON; raw content intentionally not retained"}


def normalize_entry(entry, source="HAR", action_id=None):
    if not isinstance(entry, dict) or not isinstance(entry.get("request"), dict):
        raise DiscoveryError("HAR entry has no valid request object.")
    request = entry["request"]
    response = entry.get("response") or {}
    if not isinstance(response, dict):
        raise DiscoveryError("HAR response must be an object.")
    method = str(request.get("method", "")).upper()
    if method not in METHODS:
        raise DiscoveryError("Unsupported HTTP method; WebSocket traffic is not a REST endpoint.")
    url = safe_url(request.get("url", ""))
    normalized = normalize_url(url)
    headers = request.get("headers") or []
    if not isinstance(headers, list) or len(headers) > 200:
        raise DiscoveryError("Invalid or excessive HTTP headers.")
    header_names = [field_name(h.get("name", "")) for h in headers if isinstance(h, dict)]
    auth = []
    for header in headers:
        if not isinstance(header, dict):
            continue
        name = str(header.get("name", "")).lower()
        if name == "authorization":
            scheme = str(header.get("value", "")).split(" ", 1)[0].lower()
            auth.append(scheme if scheme in {"basic", "bearer", "digest", "negotiate"} else "authorization-present")
        elif name == "cookie":
            auth.append("cookie-present")
        elif SENSITIVE.search(name):
            auth.append("credential-header-present")
    post = request.get("postData") or {}
    if not isinstance(post, dict):
        raise DiscoveryError("Invalid request payload metadata.")
    content = response.get("content") or {}
    if not isinstance(content, dict):
        raise DiscoveryError("Invalid response content metadata.")
    status = response.get("status", 0)
    if not isinstance(status, int) or not 0 <= status <= 599:
        raise DiscoveryError("Invalid HTTP response status.")
    mime = str(content.get("mimeType", "")).split(";", 1)[0].lower()
    mime = mime if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mime) else "unknown"
    request_schema = json_shape(post.get("text"))
    if isinstance(post.get("params"), list):
        request_schema = {"type": "object", "properties": {
            field_name(p.get("name", "")): {"type": "string"} for p in post["params"][:60] if isinstance(p, dict)}}
    duration = entry.get("time", 0)
    duration = max(0, min(3600000, duration)) if isinstance(duration, (float, int)) else 0
    return {
        "key": hashlib.sha256((method + " " + normalized).encode()).hexdigest()[:24],
        "method": method, "observed_url": url, "endpoint": normalized,
        "source": source, "classification": "Observed",
        "normalization_classification": "Inferred" if normalized != url.split("?", 1)[0] else "Observed",
        "query_parameters": sorted({field_name(k) for k, _ in parse_qsl(urlsplit(url).query)}),
        "header_names": sorted(set(header_names)), "authentication": sorted(set(auth)),
        "request_schema": request_schema, "response_schema": json_shape(content.get("text")),
        "status": status, "content_type": mime, "duration_ms": duration,
        "action_id": action_id,
        "limitations": ["Field values omitted for privacy; required fields and full API contract are unknown."],
    }


def parse_har(data):
    if len(data) > MAX_UPLOAD:
        raise DiscoveryError("HAR exceeds the 10 MiB upload limit.")
    try:
        document = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise DiscoveryError("Upload is not valid HAR JSON.") from None
    log = document.get("log") if isinstance(document, dict) else None
    entries = log.get("entries") if isinstance(log, dict) else None
    if not isinstance(entries, list) or not entries:
        raise DiscoveryError("HAR must contain a nonempty log.entries array.")
    if len(entries) > MAX_ENTRIES:
        raise DiscoveryError("HAR contains more than 2,000 requests; split the capture.")
    # Validate the entire upload before any database creation.
    normalized = []
    for entry in entries:
        # Still validate every entry: a malformed entry must not partially import.
        item = normalize_entry(entry)
        resource = str(entry.get("_resourceType", "")).lower()
        if resource in {"xhr", "fetch"} or "json" in item["content_type"] or item["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            normalized.append(item)
    return normalized
