"""A picture put into a document has to leave in the file somebody downloads.

`sanitise` allows exactly one kind of picture — a `data:` URI already inside
the artifact — so this is a closed problem: read it back out of the markup and
hand the bytes to each renderer. Three of the four formats can carry it.
"""

from __future__ import annotations

import base64
import io
import struct
import zipfile
import zlib

import pytest

from app.services import deck_export, page_export, report_export
from app.services import design_templates as dt

TOKENS = {"accent": "#5b5bd6", "ink": "#111111", "muted": "#666666", "font": "gothic"}

def png(width: int = 8, height: int = 12) -> str:
    """A real PNG, base64, built here rather than pasted.

    Pasted base64 is unreadable in a diff and easy to truncate — the first
    version of this file carried a broken one, `python-docx` refused it, and
    the test failed for a reason that had nothing to do with the exporters.
    """
    raw = b"".join(b"\x00" + bytes([200, 40, 90] * width) for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    body = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(body).decode()


_PNG = png()


def document(template_id: str, layout: str, *, caption: str = "그림 1. 시험") -> str:
    template = dt.get(template_id)
    assert template is not None
    blocks = [
        {"title": "표지", "layout": template.layouts[0], "html": '<p class="lead">한 줄</p>'},
        {
            "title": "그림 있는 장",
            "layout": layout,
            "html": "<ul><li>보유 42대</li></ul>"
            + dt.figure(mime="image/png", data_b64=_PNG, alt="시험", caption=caption),
        },
    ]
    return dt.render(
        template, title="그림 시험", tokens=TOKENS, body=dt.assemble(template, blocks)
    )


def test_the_reader_finds_the_picture_and_its_caption():
    slides = page_export.to_slides(document("deck-editorial", "bullets"))
    picture = slides[1]["image"]
    assert picture["mime"] == "image/png"
    assert picture["data"][:8] == b"\x89PNG\r\n\x1a\n"
    assert picture["caption"] == "그림 1. 시험"
    # The words are still there: a picture is added to the slide, not instead.
    assert slides[1]["bullets"] == ["보유 42대"]


def test_a_remote_address_is_never_read_back():
    """It cannot be stored, and if it somehow is, nothing fetches it."""
    assert page_export.decode_picture("https://example.test/p.png") is None
    assert page_export.decode_picture("data:image/svg+xml;base64,PHN2Zz4=") is None
    assert page_export.decode_picture("data:image/png;base64,!!!not base64!!!") is None


def test_the_pptx_carries_the_picture():
    slides = page_export.to_slides(document("deck-editorial", "bullets"))
    archive = zipfile.ZipFile(io.BytesIO(deck_export.to_pptx("t", slides, tokens=TOKENS)))
    media = [name for name in archive.namelist() if name.startswith("ppt/media/")]
    assert media, "그림이 .pptx 에 들어가지 않았습니다"
    assert archive.read(media[0])[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_deck_pdf_carries_the_picture():
    """Counted rather than searched for: an embedded font's own streams put
    `/Image` in a PDF that has no pictures in it at all."""
    slides = page_export.to_slides(document("deck-editorial", "bullets"))
    with_picture = deck_export.to_pdf("t", slides, tokens=TOKENS)
    for slide in slides:
        slide.pop("image", None)
    without = deck_export.to_pdf("t", slides, tokens=TOKENS)
    assert with_picture.count(b"/Image") > without.count(b"/Image")
    assert len(with_picture) > len(without)


def test_the_docx_carries_the_picture():
    sections = page_export.to_sections(document("doc-report", "section"))
    archive = zipfile.ZipFile(io.BytesIO(report_export.to_docx("t", sections, tokens=TOKENS)))
    media = [name for name in archive.namelist() if name.startswith("word/media/")]
    assert media, "그림이 .docx 에 들어가지 않았습니다"
    assert archive.read(media[0])[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_report_pdf_carries_the_picture():
    sections = page_export.to_sections(document("doc-report", "section"))
    with_picture = report_export.to_pdf("t", sections, tokens=TOKENS)
    for section in sections:
        section.pop("images", None)
    without = report_export.to_pdf("t", sections, tokens=TOKENS)
    assert with_picture.count(b"/Image") > without.count(b"/Image")
    assert len(with_picture) > len(without)


def test_hwpx_leaves_the_picture_out_and_still_opens():
    """The one format that does not carry it, deliberately.

    A picture in OWPML needs a `BinData` part, a manifest entry, a header
    `binDataList` and a `<hp:pic>` that references all three by id. Hancom
    refuses a file that gets any of it wrong, and there is no Hancom here to
    check against — so this ships without pictures rather than with a document
    nobody can open. The text is unaffected.
    """
    sections = page_export.to_sections(document("doc-report", "section"))
    archive = zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("t", sections, tokens=TOKENS)))
    assert "Contents/section0.xml" in archive.namelist()
    assert not [name for name in archive.namelist() if "BinData" in name]
    assert "그림 있는 장" in archive.read("Contents/section0.xml").decode()


@pytest.mark.parametrize("template_id", ["deck-editorial", "deck-signal"])
def test_a_slide_that_is_only_a_picture_still_exports(template_id):
    """No bullets, no body — the slide is the picture, and it used to vanish."""
    template = dt.get(template_id)
    html = dt.render(
        template,
        title="그림만",
        tokens=TOKENS,
        body=dt.assemble(
            template,
            [
                {"title": "표지", "layout": "cover", "html": '<p class="lead">한 줄</p>'},
                {
                    "title": "사진 한 장",
                    "layout": "bullets",
                    "html": dt.figure(mime="image/png", data_b64=_PNG, alt="시험"),
                },
            ],
        ),
    )
    slides = page_export.to_slides(html)
    assert len(slides) == 2
    assert slides[1]["image"]["data"][:8] == b"\x89PNG\r\n\x1a\n"
    assert deck_export.to_pptx("t", slides, tokens=TOKENS)[:2] == b"PK"


@pytest.mark.parametrize(
    "broken",
    [b"", b"not a picture at all", base64.b64decode(_PNG)[:20]],
    ids=["empty", "text", "truncated"],
)
def test_a_picture_that_is_not_one_does_not_take_the_export_down(broken):
    """Bytes can stop being a picture — truncated on the way in, or a format a
    library refuses. Losing the illustration is a document; losing the export
    is somebody's afternoon."""
    slides = page_export.to_slides(document("deck-editorial", "bullets"))
    slides[1]["image"] = {"mime": "image/png", "data": broken, "caption": "깨진 그림"}
    assert deck_export.to_pptx("t", slides, tokens=TOKENS)[:2] == b"PK"
    assert deck_export.to_pdf("t", slides, tokens=TOKENS)[:4] == b"%PDF"

    sections = page_export.to_sections(document("doc-report", "section"))
    sections[1]["images"] = [{"mime": "image/png", "data": broken, "caption": "깨진 그림"}]
    assert report_export.to_docx("t", sections, tokens=TOKENS)[:2] == b"PK"
    assert report_export.to_pdf("t", sections, tokens=TOKENS)[:4] == b"%PDF"
    assert report_export.to_hwpx("t", sections, tokens=TOKENS)[:2] == b"PK"
