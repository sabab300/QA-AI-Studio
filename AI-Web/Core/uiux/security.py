"""Authorized-target validation and SSRF protection for browser automation and Figma access.

Rules (all enforced server-side):
  * only explicitly authorized origins may be opened; there is no "test any website" mode;
  * http/https only, no embedded credentials, valid port;
  * cloud-metadata, link-local, unspecified, multicast and reserved addresses are always refused;
  * loopback/private addresses are refused unless the authorization record says ``internal_ok``;
  * the host is resolved once and the browser is pinned to the addresses that were checked;
  * every browser request and every redirect is re-checked against the allowed origins.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

ENVIRONMENTS = ("Development", "QA", "UAT", "Staging", "Production", "Other")


class TargetError(ValueError):
    """The target is not authorized or is unsafe. The message is safe to show to the user."""


_ALWAYS_BLOCKED_NETS = [ipaddress.ip_network(n) for n in (
    "169.254.0.0/16",        # link-local incl. 169.254.169.254 cloud metadata
    "100.100.100.200/32",    # Alibaba metadata
    "0.0.0.0/8", "224.0.0.0/4", "240.0.0.0/4", "192.0.0.0/24", "198.18.0.0/15",
    "fe80::/10", "fd00:ec2::/32", "ff00::/8", "::/128",
)]


def classify_ip(value: str) -> str:
    """'blocked' | 'loopback' | 'private' | 'public'."""
    try:
        ip = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return "blocked"
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in net for net in _ALWAYS_BLOCKED_NETS):
        return "blocked"
    if ip.is_loopback:
        return "loopback"
    if ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return "private" if ip.is_private and not (ip.is_reserved or ip.is_multicast or ip.is_unspecified or ip.is_link_local) else "blocked"
    return "public"


def normalize_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise TargetError("The application URL is empty.")
    if re.search(r"[\s\x00-\x1f\x7f]", text):
        raise TargetError("The application URL must not contain spaces or control characters.")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as error:
        raise TargetError(f"The application URL is not valid: {error}") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise TargetError("Only http:// and https:// application URLs are allowed.")
    if not parts.hostname:
        raise TargetError("The application URL has no host name.")
    if parts.username or parts.password:
        raise TargetError("The application URL must not contain a user name or password; use the authentication settings.")
    if port is not None and not (0 < port < 65536):
        raise TargetError("The application URL has an invalid port.")
    return parts.geturl()


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    default = 443 if parts.scheme.lower() == "https" else 80
    port = f":{parts.port}" if parts.port and parts.port != default else ""
    return f"{parts.scheme.lower()}://{host}{port}"


def resolve_host(host: str, resolver=None) -> list[str]:
    resolver = resolver or (lambda h: [i[4][0] for i in socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)])
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        return [str(literal)]
    except ValueError:
        pass
    try:
        addresses = list(dict.fromkeys(resolver(host)))
    except OSError:
        raise TargetError(f"The host '{host}' could not be resolved.") from None
    if not addresses:
        raise TargetError(f"The host '{host}' could not be resolved.")
    return addresses


@dataclass
class AuthorizedTarget:
    url: str
    origin: str
    host: str
    environment: str
    target_id: str
    pinned_ips: list = field(default_factory=list)
    pins: dict = field(default_factory=dict)      # host -> checked IP the browser is pinned to
    allowed_origins: list = field(default_factory=list)
    internal: bool = False

    def public(self):
        return {"url": self.url, "origin": self.origin, "environment": self.environment, "target_id": self.target_id,
                "internal_address": self.internal}


def authorize(url: str, environment: str, records: list, resolver=None) -> AuthorizedTarget:
    """Return the authorized target or raise TargetError.

    ``records`` are rows of uiux_targets (dict-like) that are active. A URL is permitted only when
    a record exists for its origin AND the selected environment.
    """
    if environment not in ENVIRONMENTS:
        raise TargetError("Select a valid environment: " + ", ".join(ENVIRONMENTS) + ".")
    clean = normalize_url(url)
    origin = origin_of(clean)
    host = urlsplit(clean).hostname.lower()
    record = next((r for r in records if r["origin"] == origin and r["environment"] == environment and r["active"]), None)
    if record is None:
        raise TargetError(
            f"{origin} is not an authorized target for the {environment} environment. "
            "Authorize it first (Application Under Test → Authorize target); arbitrary websites are never tested.")
    ips = resolve_host(host, resolver)
    internal = False
    for ip in ips:
        cls = classify_ip(ip)
        if cls == "blocked":
            raise TargetError(f"The address {ip} for '{host}' is in a blocked range (metadata, link-local or reserved) and can never be tested.")
        if cls in ("loopback", "private"):
            internal = True
            if not record["internal_ok"]:
                raise TargetError(
                    f"'{host}' resolves to an internal address ({ip}). Internal network targets must be explicitly "
                    "authorized with 'internal address allowed' so the tool is not used to probe your network.")
    import json
    extras, pins = [], {host: ips[0]}
    for item in json.loads(record["extra_origins_json"] or "[]"):
        try:
            extra = normalize_url(item)
            extra_host = urlsplit(extra).hostname.lower()
            extra_ips = resolve_host(extra_host, resolver)
            if any(classify_ip(ip) == "blocked" or (classify_ip(ip) in ("loopback", "private") and not record["internal_ok"]) for ip in extra_ips):
                continue  # an extra origin that resolves somewhere unsafe is silently excluded; it is never opened
            extras.append(origin_of(extra))
            pins[extra_host] = extra_ips[0]
        except TargetError:
            continue
    return AuthorizedTarget(url=clean, origin=origin, host=host, environment=environment, target_id=record["id"],
                            pinned_ips=ips, pins=pins, allowed_origins=[origin] + extras, internal=internal)


def request_allowed(url: str, allowed_origins: list) -> tuple[bool, str]:
    """Decision for one browser request. Returns (allowed, reason)."""
    low = url.lower()
    if low.startswith(("data:", "blob:", "about:")):
        return True, ""
    try:
        parts = urlsplit(url)
        origin = origin_of(url)
    except ValueError:
        return False, "malformed URL"
    if parts.scheme.lower() not in ("http", "https"):
        return False, f"scheme '{parts.scheme}' is not allowed"
    if origin in allowed_origins:
        return True, ""
    return False, f"origin {origin} is not authorized for this run"


# ---------------------------------------------------------------- Figma remote fetches

_FIGMA_API_HOST = "api.figma.com"
_FIGMA_IMAGE_SUFFIXES = (".amazonaws.com", ".figma.com", ".figstatic.com")


def figma_image_url_ok(url: str, resolver=None) -> bool:
    """Exported-image links returned by the Figma API must be https on a Figma/CDN host and public."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    if host != _FIGMA_API_HOST and not host.endswith(_FIGMA_IMAGE_SUFFIXES):
        return False
    try:
        return all(classify_ip(ip) == "public" for ip in resolve_host(host, resolver))
    except TargetError:
        return False
