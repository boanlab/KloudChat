"""Sheet documents as artifacts: one `.xlsx` blob per version, saved by the editor (the versions
themselves: `office_doc`). The grid edits through `sheet_patch`, which changes only the cells it
was given and copies every other part of the package as it was."""

from __future__ import annotations

import io
from typing import Any

import openpyxl
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.workspace import Artifact, ArtifactKind
from app.services import office_doc, sheet_guard
from app.services.office_doc import Conflict, read, read_version

__all__ = ["Conflict", "read", "read_version"]

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#: body text kept on the row for search
MAX_TEXT = 20000


def file_format(data: bytes) -> str:
    """`xlsx` when the bytes are a workbook package; the bytes decide, not the name."""
    if data[:4] != b"PK\x03\x04":
        return ""
    try:
        with sheet_guard.open_zip(data) as package:
            names = set(package.namelist())
    except sheet_guard.WorkbookRefused:
        return ""
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        return ""  # a macro-enabled package is not taken in as a sheet
    return "xlsx" if {"xl/workbook.xml", "xl/styles.xml"} <= names else ""


def describe(data: bytes, fmt: str) -> dict[str, Any]:
    """`Artifact.data` for a file: format, size, sheet names, how many cells hold something and
    their text for search (words, numbers and formulas: a sheet of numbers is not blank)."""
    names: list[str] = []
    parts: list[str] = []
    cells = 0
    try:
        sheet_guard.check(data)
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        names = list(book.sheetnames)
        size = 0
        for sheet in book.worksheets:
            sheet.reset_dimensions()
            for row in sheet.iter_rows(values_only=True):
                for value in row:
                    if value is None or (isinstance(value, str) and not value.strip()):
                        continue
                    cells += 1
                    if size <= MAX_TEXT:
                        parts.append(str(value).strip())
                        size += len(parts[-1]) + 1
        book.close()
    except Exception:  # noqa: BLE001 - a file the reader cannot walk still opens in the grid
        pass
    out = {"format": fmt, "size": len(data), "sheets": names, "cells": cells,
           "text": "\n".join(parts)[:MAX_TEXT]}
    from app.services import sheet_form

    if (form := sheet_form.mark_of(data)) is not None:
        # the Excel form this sheet started from, for the 「양식을 따름」 line
        out["form"] = {k: v for k, v in form.items() if k != "cleared"}
    return out


KIND = office_doc.Kind(ArtifactKind.xlsx, "xlsx", "새 시트", file_format, describe)


def blank() -> bytes:
    """An empty workbook with one empty sheet."""
    book = openpyxl.Workbook()
    book.active.title = "시트1"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


async def create(db: AsyncSession, **fields: Any) -> Artifact:
    return await office_doc.create(db, KIND, **fields)


async def save(db: AsyncSession, artifact: Artifact, data: bytes, **fields: Any) -> Artifact:
    """New version from `expected`; `Conflict` if another save landed first."""
    return await office_doc.save(db, KIND, artifact, data, **fields)
