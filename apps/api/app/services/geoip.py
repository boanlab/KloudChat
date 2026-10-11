"""Offline IP-to-region lookup (MaxMind DB); addresses never leave the instance."""

from __future__ import annotations

import ipaddress
import logging
import threading
from functools import lru_cache

from app.core.config import settings

log = logging.getLogger(__name__)

#: opened once (mmap)
_reader = None
_reader_lock = threading.Lock()
_tried = False

#: label for private/loopback/link-local
_LOCAL = "내부망"


def _open():
    """Reader or None; failure logged once, never retried."""
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
            # warn only: sign-in path
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
    """Place name for an address; empty if unknown."""
    ip = (ip or "").strip()
    if not ip:
        return ""
    if _is_local(ip):
        return _LOCAL
    return _resolve(ip)
