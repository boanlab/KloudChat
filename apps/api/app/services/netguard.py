"""SSRF guard: fetches only to public addresses, judged by resolved address, not name."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

_IP = ipaddress.IPv4Address | ipaddress.IPv6Address

#: Names always meaning this machine/deployment, regardless of DNS.
_INTERNAL_HOSTS = frozenset(
    {
        "localhost",
        "host.docker.internal",
        "gateway.docker.internal",
        "metadata.google.internal",
        "metadata",
    }
)
#: Reserved local suffixes (RFC 6761/6762) and reverse lookups.
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".arpa", ".home.arpa")

SCHEME = "http(s) 주소만 읽을 수 있습니다."
INTERNAL = "내부 네트워크 주소는 읽을 수 없습니다."
UNRESOLVED = "주소의 호스트를 찾을 수 없습니다."

Resolver = Callable[[str], Awaitable[list[str]]]


def is_public(address: _IP) -> bool:
    """Whether an address is public internet (is_global, plus multicast and IPv4-mapped IPv6)."""
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return is_public(mapped)
    return address.is_global and not address.is_multicast


async def _system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        found = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return [entry[4][0] for entry in found]


async def refusal(url: str, *, resolve: Resolver | None = None) -> str | None:
    """Why `url` may not be fetched (reader-facing), or None; `resolve` replaces DNS in tests."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        return SCHEME
    try:
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        return SCHEME
    if not host or parts.username is not None or parts.password is not None:
        return SCHEME
    return (await _judge(host, resolve))[0]


async def _judge(host: str, resolve: Resolver | None) -> tuple[str | None, list[_IP]]:
    """Refusal for a bare host, or None with the addresses it stands for."""
    if host in _INTERNAL_HOSTS or host.endswith(_INTERNAL_SUFFIXES):
        return INTERNAL, []

    literal = _literal(host)
    if literal is not None:
        return (None, [literal]) if is_public(literal) else (INTERNAL, [])
    # dotless name = service on the deployment network
    if "." not in host:
        return INTERNAL, []

    found = await (resolve or _system_resolver)(host)
    if not found:
        return UNRESOLVED, []
    addresses: list[_IP] = []
    for text in found:
        try:
            address = ipaddress.ip_address(text.split("%", 1)[0])
        except ValueError:
            return UNRESOLVED, []
        if not is_public(address):
            return INTERNAL, []
        addresses.append(address)
    return None, addresses


class PublicTransport(httpx.AsyncHTTPTransport):
    """Dials only public addresses: each request's host is resolved and checked here, and the
    checked address is the one connected (no second lookup, so a rebinding name or a redirect
    to an internal host is refused too); Host header and TLS name stay the original host."""

    def __init__(self, *, resolve: Resolver | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._resolve = resolve

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme not in ("http", "https"):
            raise httpx.ConnectError(SCHEME, request=request)
        host = request.url.host.rstrip(".").lower()
        reason, addresses = await _judge(host, self._resolve)
        if reason:
            raise httpx.ConnectError(reason, request=request)
        if _literal(host) is None:
            request.url = request.url.copy_with(host=str(addresses[0]))
            if request.url.scheme == "https":
                request.extensions = {**request.extensions, "sni_hostname": host}
        return await super().handle_async_request(request)


def _literal(host: str) -> _IP | None:
    """Host as an IP literal, or None; only dotted-quad and RFC 4291 forms count."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


__all__ = ["INTERNAL", "SCHEME", "UNRESOLVED", "PublicTransport", "is_public", "refusal"]
