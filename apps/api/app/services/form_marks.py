"""What form a written Office file followed, kept inside the file: one custom document property
(`docProps/custom.xml`, `KloudChatForm`, a JSON string) of a `.pptx` or `.docx`; in an `.hwpx`,
which has no custom properties, the entry `KloudChat/form.json` (an editor keeps an entry it
does not know, `hwpx_edit._write`).

The artifact's description reads it back (`pptx_doc.describe`), so every version says whether
and how it followed a form, and an edit that keeps the file's parts keeps the mark.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any
from xml.sax.saxutils import escape

from lxml import etree

from app.services.files import open_capped_zip

NAME = "KloudChatForm"
_PART = "docProps/custom.xml"
_CT = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
_REL = ("http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
        "custom-properties")
_PROPS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def _properties(info: dict[str, Any]) -> bytes:
    value = escape(json.dumps(info, ensure_ascii=False))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Properties xmlns="{_PROPS}" xmlns:vt="{_VT}">'
        f'<property fmtid="{_FMTID}" pid="2" name="{NAME}"><vt:lpwstr>{value}</vt:lpwstr>'
        "</property></Properties>"
    ).encode()


_HWPX_ENTRY = "KloudChat/form.json"


def _hwpx(data: bytes) -> bool:
    try:
        with open_capped_zip(data) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, RuntimeError):
        return False
    return any(n.startswith("Contents/section") for n in names) and "word/document.xml" \
        not in names


def _mark_hwpx(data: bytes, info: dict[str, Any]) -> bytes:
    source = open_capped_zip(data)
    out = io.BytesIO()
    with source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as copy:
        names = source.namelist()
        if "mimetype" in names:
            # OWPML: `mimetype` first and stored
            copy.writestr(zipfile.ZipInfo("mimetype"), source.read("mimetype"),
                          zipfile.ZIP_STORED)
        for item in source.infolist():
            if item.filename in ("mimetype", _HWPX_ENTRY):
                continue
            copy.writestr(item, source.read(item.filename))
        copy.writestr(_HWPX_ENTRY, json.dumps(info, ensure_ascii=False).encode("utf-8"))
    return out.getvalue()


def mark(data: bytes, info: dict[str, Any]) -> bytes:
    """`data` with the form mark set (replacing any custom properties it had)."""
    if _hwpx(data):
        return _mark_hwpx(data, info)
    source = open_capped_zip(data)
    out = io.BytesIO()
    with source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as copy:
        for item in source.infolist():
            if item.filename == _PART:
                continue
            body = source.read(item.filename)
            if item.filename == "[Content_Types].xml" and f"/{_PART}".encode() not in body:
                body = body.replace(
                    b"</Types>",
                    f'<Override PartName="/{_PART}" ContentType="{_CT}"/></Types>'.encode())
            elif item.filename == "_rels/.rels" and _REL.encode() not in body:
                body = body.replace(
                    b"</Relationships>",
                    f'<Relationship Id="rIdKcForm" Type="{_REL}" Target="{_PART}"/>'
                    "</Relationships>".encode())
            copy.writestr(item, body)
        copy.writestr(_PART, _properties(info))
    return out.getvalue()


def read(data: bytes) -> dict[str, Any] | None:
    """The form mark of an Office file, or None."""
    try:
        with open_capped_zip(data) as archive:
            if _HWPX_ENTRY in archive.namelist():
                found = json.loads(archive.read(_HWPX_ENTRY).decode("utf-8"))
                return found if isinstance(found, dict) else None
            root = etree.fromstring(archive.read(_PART), _PARSER)
    except (KeyError, zipfile.BadZipFile, RuntimeError, etree.XMLSyntaxError, ValueError):
        return None
    for prop in root.iter(f"{{{_PROPS}}}property"):
        if prop.get("name") == NAME:
            text = "".join(prop.itertext())
            try:
                found = json.loads(text)
            except ValueError:
                return None
            return found if isinstance(found, dict) else None
    return None
