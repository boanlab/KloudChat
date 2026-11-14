"""CSV downloads for admin tables: spreadsheet-safe cells, a UTF-8 BOM, streamed in chunks."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi.responses import StreamingResponse

from app.core.config import settings

#: Rows sent in one download; a larger list is narrowed with the screen's filters first.
MAX_ROWS = 50_000

# A cell a spreadsheet would run as a formula.
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def cell(value: Any) -> str | int | float:
    """One value as written: times in the service zone, formula-looking text kept as text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return value
    if isinstance(value, datetime):
        zone = ZoneInfo(settings.timezone)
        at = value if value.tzinfo else value.replace(tzinfo=ZoneInfo("UTC"))
        return at.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")
    text = str(getattr(value, "value", value))
    return f"'{text}" if text.startswith(_FORMULA) else text


def _chunks(header: list[str], rows: Iterable[Iterable[Any]]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    buffer.write("﻿")
    writer.writerow(header)
    for n, row in enumerate(rows, 1):
        writer.writerow([cell(v) for v in row])
        if n % 500 == 0:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
    yield buffer.getvalue()


def response(filename: str, header: list[str], rows: Iterable[Iterable[Any]]) -> StreamingResponse:
    """A streamed `text/csv` attachment; the rows are read as the body is sent."""
    return StreamingResponse(
        _chunks(header, rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )


def stamp() -> str:
    """Today in the service zone, for file names."""
    return datetime.now(ZoneInfo(settings.timezone)).strftime("%Y%m%d")
