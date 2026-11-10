"""
QA AI Studio
Web Fetcher

Version: 1.0
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

from Core.logger import Logger


class UnsafeURL(ValueError):
    """Raised when a URL found on the open web points at a non-public destination."""


def check_public_url(url, resolver=None):
    """Task 4 SSRF guard: only http(s) URLs whose host resolves exclusively to public addresses.

    Search-result links are untrusted data. Without this a hostile result could make the server
    request loopback, private-network or cloud-metadata addresses. `resolver(host)` returns a list
    of IP strings (injectable for offline tests)."""

    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURL("Only http(s) URLs are fetched.")
    if parsed.username or parsed.password:
        raise UnsafeURL("URLs with embedded credentials are not fetched.")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeURL("Non-public host.")
    try:
        addrs = [host] if ipaddress.ip_address(host) else []
    except ValueError:
        try:
            if resolver:
                addrs = list(resolver(host))
            else:
                addrs = [i[4][0] for i in socket.getaddrinfo(host, parsed.port or 443,
                                                             proto=socket.IPPROTO_TCP)]
        except (OSError, UnicodeError):
            raise UnsafeURL("Host could not be resolved.")
    if not addrs:
        raise UnsafeURL("Host could not be resolved.")
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise UnsafeURL("Non-public address.")
    return url


class WebFetcher:

    TIMEOUT = 20

    USER_AGENT = (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/137.0 Safari/537.36"
    )

    def __init__(self):

        self.logger = Logger.get_logger()

    # --------------------------------------------------
    # GET
    # --------------------------------------------------

    def get(

        self,

        url,

        headers=None,

        params=None

    ):

        try:

            request_headers = {

                "User-Agent": self.USER_AGENT

            }

            if headers:

                request_headers.update(headers)

            # Redirects are followed manually so every hop is checked (a public page must not be
            # able to bounce the server to an internal address).
            target = url
            response = None
            for _hop in range(5):

                check_public_url(target)

                response = requests.get(

                    target,

                    headers=request_headers,

                    params=params if _hop == 0 else None,

                    timeout=self.TIMEOUT,

                    allow_redirects=False

                )

                if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                    target = urljoin(target, response.headers["Location"])
                    continue
                break
            else:
                raise UnsafeURL("Too many redirects.")

            response.raise_for_status()

            return {

                "success": True,

                "status_code": response.status_code,

                "text": response.text,

                "url": response.url

            }

        except UnsafeURL as error:

            self.logger.warning("Web Fetch blocked: %s" % error)

            return {

                "success": False,

                "error": "blocked: %s" % error

            }

        except Exception as error:

            self.logger.exception(

                f"Web Fetch failed: {error}"

            )

            return {

                "success": False,

                "error": str(error)

            }