"""HWP 5.x record splitting and paragraph text decoding."""

from __future__ import annotations

import struct

from app.services.files import _HWPTAG_PARA_TEXT, _hwp_paragraph, _hwp_records


def record(tag: int, payload: bytes, level: int = 0) -> bytes:
    size = len(payload)
    if size < 0xFFF:
        return struct.pack("<I", (tag & 0x3FF) | (level << 10) | (size << 20)) + payload
    head = struct.pack("<I", (tag & 0x3FF) | (level << 10) | (0xFFF << 20))
    return head + struct.pack("<I", size) + payload


def test_splits_a_stream_into_tag_and_payload():
    stream = record(_HWPTAG_PARA_TEXT, b"ab") + record(16, b"cdef")
    assert list(_hwp_records(stream)) == [(_HWPTAG_PARA_TEXT, b"ab"), (16, b"cdef")]


def test_reads_the_extended_size_past_4095_bytes():
    payload = b"\x41" * 5000
    assert list(_hwp_records(record(_HWPTAG_PARA_TEXT, payload))) == [
        (_HWPTAG_PARA_TEXT, payload)
    ]


def test_a_truncated_stream_stops_where_it_ends():
    stream = record(_HWPTAG_PARA_TEXT, b"ab") + b"\x00\x00"
    assert list(_hwp_records(stream)) == [(_HWPTAG_PARA_TEXT, b"ab")]


def test_paragraphs_are_utf16le():
    assert _hwp_paragraph("한글 text".encode("utf-16-le")) == "한글 text"


def test_an_extended_control_occupies_eight_characters():
    # Table control (11) followed by body text.
    body = struct.pack("<H", 11) + b"\xff" * 14 + "본문".encode("utf-16-le")
    assert _hwp_paragraph(body) == "본문"


def test_an_inline_control_occupies_one_character():
    body = struct.pack("<H", 4) + "본문".encode("utf-16-le")
    assert _hwp_paragraph(body) == "본문"


def test_keeps_line_breaks_and_drops_other_controls():
    body = struct.pack("<HHH", 0x41, 13, 0x42)
    assert _hwp_paragraph(body) == "A\nB"
