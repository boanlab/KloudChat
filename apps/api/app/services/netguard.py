"""Where a fetch on someone's behalf may go: the public internet, nothing behind it.

A page fetch runs from inside the deployment, next to the database, LiteLLM, the tool
gateway and whatever else the private network holds. A URL a person types or a model
picks must not turn that position into a probe of those services (SSRF). The rule is
by address, not by name: the host is resolved and every address it resolves to has to
be a public one.

The check happens before the request; a redirect the fetcher follows is the fetcher's
to check (the Crawl4AI shim does the same check on the address it ended up at).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

_IP = ipaddress.IPv4Address | ipaddress.IPv6Address

#: Names that always mean this machine or this deployment, whatever DNS says.
_INTERNAL_HOSTS = frozenset(
    {
        "localhost",
        "host.docker.internal",
        "gateway.docker.internal",
        "metadata.google.internal",
        "metadata",
    }
)
#: Suffixes reserved for local names (RFC 6761/6762) and reverse lookups.
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".arpa", ".home.arpa")

SCHEME = "http(s) 주소만 읽을 수 있습니다."
INTERNAL = "내부 네트워크 주소는 읽을 수 없습니다."
UNRESOLVED = "주소의 호스트를 찾을 수 없습니다."

Resolver = Callable[[str], Awaitable[list[str]]]


def is_public(address: _IP) -> bool:
    """Whether an address belongs to the public internet.

    `is_global` follows the IANA special-purpose registries: loopback, private, link-local,
    carrier-grade NAT (100.64/10), documentation and reserved ranges are all excluded.
    Multicast and an IPv4 address carried inside IPv6 are handled explicitly.
    """
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
    """Why `url` may not be fetched, in the reader's words; None when it may.

    `resolve` stands in for DNS in tests.
    """
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        return SCHEME
    try:
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        return SCHEME
    if not host or parts.username is not None or parts.password is not None:
        return SCHEME
    if host in _INTERNAL_HOSTS or host.endswith(_INTERNAL_SUFFIXES):
        return INTERNAL

    literal = _literal(host)
    if literal is not None:
        return None if is_public(literal) else INTERNAL
    # A dotless name is a service on this deployment's network (`litellm`, `kloudchat-db`).
    if "." not in host:
        return INTERNAL

    addresses = await (resolve or _system_resolver)(host)
    if not addresses:
        return UNRESOLVED
    for text in addresses:
        try:
            address = ipaddress.ip_address(text.split("%", 1)[0])
        except ValueError:
            return UNRESOLVED
        if not is_public(address):
            return INTERNAL
    return None


def _literal(host: str) -> _IP | None:
    """The host as an IP address literal, or None for a name.

    Only the dotted-quad and RFC 4291 forms count; `0x7f000001`, `2130706433` and
    `127.1` are names here and are refused as dotless or resolved and judged.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


__all__ = ["INTERNAL", "SCHEME", "UNRESOLVED", "is_public", "refusal"]
