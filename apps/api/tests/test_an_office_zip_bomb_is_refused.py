"""Office/HWPX archives that inflate far beyond their size are refused before they unpack."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services import docx_edit, files, hwpx_edit, hwpx_import, pptx_edit

_SECTION = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
    "<hp:p><hp:run><hp:t>안녕하세요</hp:t></hp:run></hp:p></hs:sec>"
)


def _archive(members: dict[str, bytes], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, body in members.items():
            z.writestr(name, body)
    return buf.getvalue()


def _bomb(name: str, size: int = 24 * 1024 * 1024) -> bytes:
    """A few tens of KB on disk that inflate to `size` bytes of zeros."""
    return _archive({"mimetype": b"application/hwp+zip", name: b"\0" * size})


def test_a_small_archive_that_inflates_hugely_is_refused():
    data = _bomb("Contents/section0.xml")
    assert len(data) < 100 * 1024
    with pytest.raises(RuntimeError):
        files.open_capped_zip(data)


def test_too_many_members_are_refused():
    data = _archive({f"m{i}.xml": b"x" for i in range(files._MAX_ENTRIES + 1)})
    with pytest.raises(RuntimeError):
        files.open_capped_zip(data)


def test_a_bzip2_member_is_refused():
    data = _archive({"Contents/section0.xml": _SECTION.encode()}, zipfile.ZIP_BZIP2)
    with pytest.raises(RuntimeError):
        files.open_capped_zip(data)


def test_a_member_that_lies_about_its_size_stops_at_the_declared_size():
    """Central and local headers say 100 bytes; the stream holds megabytes."""
    real = _archive({"Contents/section0.xml": b"\0" * (4 * 1024 * 1024)})
    data = bytearray(real)
    local = data.find(b"PK\x03\x04")
    data[local + 22 : local + 26] = (100).to_bytes(4, "little")
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 24 : cd + 28] = (100).to_bytes(4, "little")
    archive = files.open_capped_zip(bytes(data))
    # zipfile stops inflating at the declared size; the CRC then fails instead of growing
    with pytest.raises(zipfile.BadZipFile):
        archive.read("Contents/section0.xml")
    with pytest.raises(RuntimeError):
        hwpx_import.read(bytes(data))
    with pytest.raises(hwpx_edit.HwpxError):
        hwpx_edit.outline(bytes(data))


def test_the_capped_reader_counts_what_it_unpacks(monkeypatch):
    monkeypatch.setattr(files, "_MAX_UNPACKED", 3000)
    data = _archive({"a.xml": b"a" * 1000, "b.xml": b"b" * 1000})
    archive = files.open_capped_zip(data)
    monkeypatch.setattr(files, "_MAX_UNPACKED", 1500)
    archive.read("a.xml")
    with pytest.raises(RuntimeError):
        archive.read("b.xml")


def test_a_normal_hwpx_still_opens():
    data = _archive(
        {"mimetype": b"application/hwp+zip", "Contents/section0.xml": _SECTION.encode()}
    )
    assert "안녕하세요" in hwpx_edit.plain_text(data)


def test_hwpx_import_refuses_a_bomb():
    with pytest.raises(RuntimeError):
        hwpx_import.read(_bomb("Contents/section0.xml"))


def test_hwpx_edit_refuses_a_bomb():
    with pytest.raises(hwpx_edit.HwpxError):
        hwpx_edit.outline(_bomb("Contents/section0.xml"))


def test_docx_and_pptx_edit_refuse_a_bomb():
    with pytest.raises(docx_edit.DocxError):
        docx_edit.outline(_bomb("word/document.xml"))
    with pytest.raises(pptx_edit.PptxError):
        pptx_edit.outline(_bomb("ppt/presentation.xml"))


def test_text_extraction_refuses_a_bomb():
    with pytest.raises(RuntimeError):
        files._from_hwpx(_bomb("Contents/section0.xml"))
