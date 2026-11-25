"""Checks every workbook goes through before a sheet service unpacks or parses it.

Sheet files come from uploads, so the package is measured first (declared sizes, entry count,
inflation ratio: `files.open_capped_zip`) and read through the capped archive. XML is parsed with
a parser that resolves no entities and fetches nothing."""

from __future__ import annotations

import zipfile

from lxml import etree

from app.services import files

#: packed size of a workbook the sheet surface takes (uploads may be larger for other uses)
MAX_BYTES = 50 * 1024 * 1024

PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


class PatchError(ValueError):
    """A change the workbook cannot take; the message is shown to the person."""


class WorkbookRefused(PatchError):
    """A package too large or too inflated to open."""


def open_zip(data: bytes) -> files.CappedZipFile:
    """The workbook's archive after the zip-bomb checks; reads through it stay capped."""
    if len(data) > MAX_BYTES:
        raise WorkbookRefused("시트 파일이 너무 큽니다.")
    try:
        return files.open_capped_zip(data)
    except zipfile.BadZipFile:
        raise WorkbookRefused("xlsx 파일이 아닙니다.") from None
    except RuntimeError as error:
        raise WorkbookRefused(str(error)) from None


def check(data: bytes) -> None:
    """Raises `WorkbookRefused` unless the package passes the zip-bomb checks."""
    open_zip(data).close()


def parse(raw: bytes):
    """One XML part as a tree, with no entity resolved and nothing fetched."""
    return etree.fromstring(raw, PARSER)
