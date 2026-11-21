"""Accept only public links to known video sites (blocks requests aimed at our own network)."""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit


class UrlRejected(ValueError):
    """The message is safe to show the user."""


def _resolve(host: str):
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def validate_url(
    url: str,
    allowed_hosts: Iterable[str],
    resolver: Optional[Callable[[str], Iterable[str]]] = None,
) -> str:
    url = (url or "").strip()
    if len(url) > 500 or any(c.isspace() for c in url):
        raise UrlRejected("That does not look like a link.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UrlRejected("That does not look like a link.")
    if parts.username or parts.password:
        raise UrlRejected("Links with a username or password are not accepted.")
    host = parts.hostname.lower().rstrip(".")
    allowed = [h.lower() for h in allowed_hosts]
    if not any(host == h or host.endswith("." + h) for h in allowed):
        raise UrlRejected("I can read links from: " + ", ".join(sorted(allowed)) + ". For anything else, upload the video or paste the text.")
    try:
        addrs = list((resolver or _resolve)(host))
    except OSError:
        raise UrlRejected("I couldn't find that website.")
    if not addrs or not all(ipaddress.ip_address(a).is_global for a in addrs):
        raise UrlRejected("That link points to a private address.")
    return url
