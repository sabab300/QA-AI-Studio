"""Figma REST API client (official API). The access token is never logged, returned or stored in plain text.

The base URL is fixed to https://api.figma.com. Tests inject ``transport`` (a callable) instead of the network.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit, parse_qs, unquote

from .security import figma_image_url_ok

API_BASE = "https://api.figma.com"
TIMEOUT_SECONDS = 25
MAX_RETRY_WAIT = 8
MAX_IMAGE_DOWNLOAD = 40 * 1024 * 1024


class FigmaError(Exception):
    """A Figma problem with a user-safe message and a stable ``code``."""

    def __init__(self, message, code="figma_error", status=None, retry_after=None):
        super().__init__(message)
        self.code, self.status, self.retry_after = code, status, retry_after


def parse_figma_url(text: str) -> dict:
    """Extract file key and optional node id from a Figma URL or accept a bare file key."""
    raw = (text or "").strip()
    if not raw:
        raise FigmaError("Enter a Figma design URL or file key.", "invalid_url")
    if re.fullmatch(r"[A-Za-z0-9]{10,40}", raw):
        return {"file_key": raw, "node_id": "", "url": f"https://www.figma.com/design/{raw}"}
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise FigmaError("The Figma URL is not valid.", "invalid_url") from None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or not (host == "figma.com" or host.endswith(".figma.com")):
        raise FigmaError("Only figma.com design URLs are accepted.", "invalid_url")
    m = re.match(r"^/(?:design|file|proto|make)/([A-Za-z0-9]{10,40})(?:/|$)", parts.path)
    if not m:
        if re.match(r"^/board/", parts.path):
            raise FigmaError("FigJam boards are not designs. Provide a Figma Design file URL.", "invalid_url")
        raise FigmaError("The URL does not look like a Figma design file (expected /design/<key>/...).", "invalid_url")
    node = ""
    q = parse_qs(parts.query)
    if q.get("node-id"):
        node = unquote(q["node-id"][0]).replace("-", ":")
    return {"file_key": m.group(1), "node_id": node, "url": f"https://www.figma.com/design/{m.group(1)}"}


class FigmaClient:
    def __init__(self, token: str, transport=None, downloader=None, sleep=time.sleep, base_url=API_BASE):
        self._token = token or ""
        self._transport = transport
        self._downloader = downloader
        self._sleep = sleep
        self._base = base_url.rstrip("/")

    # ---------------------------------------------------------------- transport
    def _request(self, path: str, params=None):
        if not self._token:
            raise FigmaError("No Figma access token is configured. Add a token in Connect Figma.", "missing_credentials")
        attempts = 0
        while True:
            attempts += 1
            try:
                if self._transport:
                    status, headers, body = self._transport(self._base + path, params or {}, {"X-Figma-Token": self._token})
                else:
                    import requests
                    r = requests.get(self._base + path, params=params or {}, headers={"X-Figma-Token": self._token}, timeout=TIMEOUT_SECONDS)
                    status, headers = r.status_code, r.headers
                    try:
                        body = r.json()
                    except ValueError:
                        body = {}
            except FigmaError:
                raise
            except Exception as error:  # network failure; message must not echo request headers
                raise FigmaError(f"Figma could not be reached ({type(error).__name__}). Check the network and try again.", "network") from None
            if status == 429:
                wait = _retry_after(headers)
                if attempts <= 2 and wait <= MAX_RETRY_WAIT:
                    self._sleep(wait)
                    continue
                raise FigmaError(f"Figma rate limit reached. Retry after about {int(wait)} seconds.", "rate_limited", 429, wait)
            if status in (401, 403):
                raise FigmaError("Figma rejected the access token or you do not have access to this file (HTTP %d)." % status, "forbidden", status)
            if status == 404:
                raise FigmaError("The Figma file or node was not found. Check the URL and your access.", "not_found", 404)
            if status == 400:
                msg = (body or {}).get("err") or "bad request"
                raise FigmaError(f"Figma rejected the request: {str(msg)[:160]}", "bad_request", 400)
            if status >= 500:
                raise FigmaError(f"Figma is temporarily unavailable (HTTP {status}).", "unavailable", status)
            if status != 200:
                raise FigmaError(f"Unexpected Figma response (HTTP {status}).", "unexpected", status)
            return body if isinstance(body, dict) else {}

    # ---------------------------------------------------------------- API
    def whoami(self) -> dict:
        data = self._request("/v1/me")
        return {"handle": str(data.get("handle", ""))[:80], "email": str(data.get("email", ""))[:120]}

    def get_file(self, key: str, depth: int | None = None) -> dict:
        params = {"geometry": "paths"} if False else {}
        if depth:
            params["depth"] = depth
        return self._request(f"/v1/files/{key}", params)

    def get_nodes(self, key: str, ids: list) -> dict:
        return self._request(f"/v1/files/{key}/nodes", {"ids": ",".join(ids)})

    def get_image_urls(self, key: str, ids: list, scale: float = 1.0, fmt: str = "png") -> dict:
        data = self._request(f"/v1/images/{key}", {"ids": ",".join(ids), "format": fmt, "scale": scale})
        if data.get("err"):
            raise FigmaError(f"Figma could not render the frames: {str(data['err'])[:160]}", "render_failed")
        return data.get("images") or {}

    def get_variables(self, key: str) -> dict:
        """Local variables are an Enterprise-plan API. A refusal is reported as unavailable, not as an error."""
        try:
            return self._request(f"/v1/files/{key}/variables/local").get("meta") or {}
        except FigmaError as error:
            if error.code in ("forbidden", "not_found", "bad_request"):
                return {"unavailable": True, "reason": "Design variables are not available for this file or plan."}
            raise

    def download_image(self, url: str) -> bytes:
        if not figma_image_url_ok(url):
            raise FigmaError("Figma returned an image link that is not on an allowed Figma host; it was not downloaded.", "unsafe_image_url")
        try:
            if self._downloader:
                data = self._downloader(url)
            else:
                import requests
                with requests.get(url, timeout=TIMEOUT_SECONDS, stream=True, allow_redirects=False) as r:
                    if r.status_code != 200:
                        raise FigmaError(f"The rendered image could not be downloaded (HTTP {r.status_code}).", "download_failed")
                    chunks, total = [], 0
                    for chunk in r.iter_content(1 << 16):
                        total += len(chunk)
                        if total > MAX_IMAGE_DOWNLOAD:
                            raise FigmaError("The rendered image is larger than the allowed size.", "download_failed")
                        chunks.append(chunk)
                    data = b"".join(chunks)
        except FigmaError:
            raise
        except Exception as error:
            raise FigmaError(f"The rendered image could not be downloaded ({type(error).__name__}).", "network") from None
        return data


def _retry_after(headers) -> float:
    try:
        value = headers.get("Retry-After") if hasattr(headers, "get") else None
        return max(1.0, float(value)) if value is not None else 2.0
    except (TypeError, ValueError):
        return 2.0
