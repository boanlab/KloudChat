"""Offline IP-to-region lookup from a MaxMind DB file (`GEOIP_DATABASE`).

Addresses never leave the instance: no database means an empty answer, and
private ranges are never looked up.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from functools import lru_cache

from app.core.config import settings

log = logging.getLogger(__name__)

#: Opened once; the reader mmaps the database.
_reader = None
_reader_lock = threading.Lock()
_tried = False

#: Shown for private, loopback and link-local addresses.
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
            # Warned, not raised: this runs on the sign-in path.
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
    except Exception:  # not in the database, or not a routable address
        return ""
    country = found.country.names.get("ko") or found.country.names.get("en") or ""
    city = found.city.names.get("ko") or found.city.names.get("en") or ""
    if country and city:
        return f"{country} {city}"
    return country or city


def lookup(ip: str) -> str:
    """Place name for an address; empty when unknown or no database is configured."""
    ip = (ip or "").strip()
    if not ip:
        return ""
    if _is_local(ip):
        return _LOCAL
    return _resolve(ip)
