"""Sheet documents as artifacts: one `.xlsx` blob per version, saved by the editor (the versions
themselves: `office_doc`). The grid edits through `sheet_patch`, which changes only the cells it
was given and copies every other part of the package as it was."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import openpyxl

from app.models.workspace import ArtifactKind
from app.services import office_doc

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#: body text kept on the row for search
MAX_TEXT = 20000


def file_format(data: bytes) -> str:
    """`xlsx` when the bytes are a workbook package; the bytes decide, not the name."""
    if data[:4] != b"PK\x03\x04":
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            names = set(package.namelist())
    except zipfile.BadZipFile:
        return ""
    return "xlsx" if {"xl/workbook.xml", "xl/styles.xml"} <= names else ""


def describe(data: bytes, fmt: str) -> dict[str, Any]:
    """`Artifact.data` for a file: format, size, sheet names and cell text for search."""
    names: list[str] = []
    parts: list[str] = []
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        names = list(book.sheetnames)
        size = 0
        for sheet in book.worksheets:
            for row in sheet.iter_rows(values_only=True):
                for value in row:
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
                        size += len(parts[-1]) + 1
                if size > MAX_TEXT:
                    break
            if size > MAX_TEXT:
                break
        book.close()
    except Exception:  # noqa: BLE001 - a file the reader cannot walk still opens in the grid
        pass
    return {"format": fmt, "size": len(data), "sheets": names, "text": "\n".join(parts)[:MAX_TEXT]}


KIND = office_doc.Kind(ArtifactKind.xlsx, "xlsx", "새 시트", file_format, describe)


def blank() -> bytes:
    """An empty workbook with one empty sheet."""
    book = openpyxl.Workbook()
    book.active.title = "시트1"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
