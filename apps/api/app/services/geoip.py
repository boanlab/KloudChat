"""Where an address is, when that can be answered without telling anyone.

Three surfaces ask the same question — the visits on a shared link, a person's
own sign-in history, and the admin's audit trail — so they ask it here.

**Offline only.** The obvious way to resolve an address is to POST it to a
lookup service, and this deliberately does not: every visitor's address would
leave the instance, which is the one thing a product that defaults to a local
model and masks its own transcripts should not do quietly in the background.

So the answer comes from a MaxMind DB file on disk or it does not come. With no
file configured, `lookup` returns an empty string and every screen that shows a
region simply shows an address instead — which is what they all showed before.
To turn it on, download GeoLite2-City.mmdb (free, a MaxMind account) and set
`GEOIP_DATABASE` to its path.

Private ranges never reach the database. An address inside RFC 1918 is a
machine on the same network as the server, and "미국 애슈번" for 10.0.0.4 would
be a confident lie.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from functools import lru_cache

from app.core.config import settings

log = logging.getLogger(__name__)

#: The reader is opened once and kept: it mmaps the database, and reopening it
#: per request would be a page-cache miss on every audit row.
_reader = None
_reader_lock = threading.Lock()
_tried = False

#: Said in place of a location for an address that has none to look up.
_LOCAL = "내부망"


def _open():
    """The reader, or None. Failure is logged once and never retried."""
    global _reader, _tried
    if _tried:
        return _reader
    with _reader_lock:
        if _tried:
            return _reader
        _tried = True
        path = (settings.geoip_database or "").strip()
        if not path:
            return None
        try:
            import geoip2.database

            _reader = geoip2.database.Reader(path)
            log.info("geoip: reading %s", path)
        except Exception as exc:  # missing file, wrong format, library absent
            # Warned rather than raised: a missing city database must not be
            # able to take down sign-in, which is what an exception on this
            # path would do.
            log.warning("geoip: disabled (%s)", exc)
            _reader = None
    return _reader


def _is_local(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


@lru_cache(maxsize=4096)
def _resolve(ip: str) -> str:
    reader = _open()
    if reader is None:
        return ""
    try:
        found = reader.city(ip)
    except Exception:
        # Not in the database, or not a routable address. Nothing to say.
        return ""
    # Korean names where MaxMind has them, English otherwise — a row reading
    # "Republic of Korea" beside "서울" is worse than either alone.
    country = found.country.names.get("ko") or found.country.names.get("en") or ""
    city = found.city.names.get("ko") or found.city.names.get("en") or ""
    if country and city:
        return f"{country} {city}"
    return country or city


def lookup(ip: str) -> str:
    """A place name for an address, or an empty string.

    Empty is a real answer and the callers show it as one: no database
    configured, an address the database does not cover, and a proxy that
    stripped the address all end here, and none of them should invent a region.
    """
    ip = (ip or "").strip()
    if not ip:
        return ""
    if _is_local(ip):
        return _LOCAL
    return _resolve(ip)
