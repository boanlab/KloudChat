"""Pictures in a document reach the exported .pptx, .pdf, .docx and .hwpx."""

from __future__ import annotations

import base64
import io
import re
import struct
import zipfile
import zlib

import pytest

from app.services import deck_export, page_export, pictures, report_export
from app.services import design_templates as dt

TOKENS = {"accent": "#5b5bd6", "ink": "#111111", "muted": "#666666", "font": "gothic"}

def png(width: int = 8, height: int = 12) -> str:
    """A valid PNG of the given size, base64-encoded."""
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
    assert slides[1]["bullets"] == ["보유 42대"]


def test_a_remote_address_is_never_read_back():
    """Only a base64 `data:` raster URI decodes; remote and SVG sources are refused."""
    assert pictures.decode("https://example.test/p.png") is None
    assert pictures.decode("data:image/svg+xml;base64,PHN2Zz4=") is None
    assert pictures.decode("data:image/png;base64,!!!not base64!!!") is None
    assert pictures.decode(pictures.encode("image/png", b"\x89PNG")) == ("image/png", b"\x89PNG")


def test_the_pptx_carries_the_picture():
    slides = page_export.to_slides(document("deck-editorial", "bullets"))
    archive = zipfile.ZipFile(io.BytesIO(deck_export.to_pptx("t", slides, tokens=TOKENS)))
    media = [name for name in archive.namelist() if name.startswith("ppt/media/")]
    assert media, "그림이 .pptx 에 들어가지 않았습니다"
    assert archive.read(media[0])[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_deck_pdf_carries_the_picture():
    """The deck PDF with a picture has more `/Image` streams than without (fonts add some too)."""
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


def test_hwpx_carries_the_picture_too():
    """An OWPML picture is `BinData/` bytes, an `opf:item` in content.hpf and a `binaryItemIDRef`.

    `<hh:binDataList>` belongs to the older HML format and must not appear in header.xml.
    """
    sections = page_export.to_sections(document("doc-report", "section"))
    archive = zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("t", sections, tokens=TOKENS)))

    assert "BinData/image1.png" in archive.namelist()
    assert archive.read("BinData/image1.png")[:8] == b"\x89PNG\r\n\x1a\n"
    # Hancom stores pictures uncompressed.
    assert archive.getinfo("BinData/image1.png").compress_type == zipfile.ZIP_STORED

    hpf = archive.read("Contents/content.hpf").decode()
    assert '<opf:item id="image1" href="BinData/image1.png"' in hpf
    assert 'isEmbeded="1"' in hpf  # one `d`: OWPML's own spelling
    assert '<opf:itemref idref="image1"' not in hpf  # the spine holds header+section only

    section_xml = archive.read("Contents/section0.xml").decode()
    assert 'binaryItemIDRef="image1"' in section_xml
    assert 'treatAsChar="1"' in section_xml  # inline, and nothing else makes it so
    assert "그림 1. 시험" in section_xml  # the caption stays a caption
    assert "binDataList" not in archive.read("Contents/header.xml").decode()


def test_hwpx_gives_the_page_a_size():
    """The first paragraph carries `<hp:secPr>`; without it Hancom draws no pictures."""
    archive = zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("t", [], tokens=TOKENS)))
    section_xml = archive.read("Contents/section0.xml").decode()
    assert "<hp:secPr" in section_xml
    assert 'width="59528" height="84188"' in section_xml  # A4
    assert section_xml.index("<hp:secPr") < section_xml.index("</hp:p>")


def test_a_picture_too_big_for_the_page_is_scaled_to_it():
    """Placed size shrinks to the column while `imgDim` stays at native `pixels * 75`."""
    markup = report_export._hwpx_picture(1, base64.b64decode(png(width=1024, height=683)))
    placed = int(re.search(r'<hp:sz width="(\d+)"', markup).group(1))
    assert placed <= report_export._HWPX_MAX_WIDTH
    assert f'dimwidth="{1024 * 75}"' in markup  # the source stays native
    small = report_export._hwpx_picture(1, base64.b64decode(png(width=200, height=100)))
    assert f'<hp:sz width="{200 * 75}"' in small  # one that already fits is untouched


@pytest.mark.parametrize("template_id", ["deck-editorial", "deck-signal"])
def test_a_slide_that_is_only_a_picture_still_exports(template_id):
    """A slide holding only a picture still exports."""
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
    """Undecodable picture bytes are dropped without failing the export."""
    slides = page_export.to_slides(document("deck-editorial", "bullets"))
    slides[1]["image"] = {"mime": "image/png", "data": broken, "caption": "깨진 그림"}
    assert deck_export.to_pptx("t", slides, tokens=TOKENS)[:2] == b"PK"
    assert deck_export.to_pdf("t", slides, tokens=TOKENS)[:4] == b"%PDF"

    sections = page_export.to_sections(document("doc-report", "section"))
    sections[1]["images"] = [{"mime": "image/png", "data": broken, "caption": "깨진 그림"}]
    assert report_export.to_docx("t", sections, tokens=TOKENS)[:2] == b"PK"
    assert report_export.to_pdf("t", sections, tokens=TOKENS)[:4] == b"%PDF"
    assert report_export.to_hwpx("t", sections, tokens=TOKENS)[:2] == b"PK"


# ── the JSON deck track: a slide picture is the `data:` URI itself ──────


def test_a_json_deck_slide_carries_a_picture_as_its_own_address():
    slides = [
        {"id": "s0", "layout": "title", "title": "표지", "body": "한 줄"},
        {
            "id": "s1",
            "layout": "bullets",
            "title": "그림 있는 장",
            "bullets": ["보유 42대"],
            "image": {
                "src": pictures.encode("image/png", base64.b64decode(_PNG)),
                "caption": "그림 1. 시험",
            },
        },
    ]
    archive = zipfile.ZipFile(io.BytesIO(deck_export.to_pptx("t", slides, tokens=TOKENS)))
    media = [name for name in archive.namelist() if name.startswith("ppt/media/")]
    assert media
    assert archive.read(media[0])[:8] == b"\x89PNG\r\n\x1a\n"

    with_picture = deck_export.to_pdf("t", slides, tokens=TOKENS)
    slides[1].pop("image")
    assert with_picture.count(b"/Image") > deck_export.to_pdf(
        "t", slides, tokens=TOKENS
    ).count(b"/Image")


def test_cover_fit_crops_the_same_central_window_in_pptx_and_pdf():
    image = base64.b64decode(png(width=800, height=200))
    width, height, left, top, right, bottom = deck_export._fill(
        image, box=(300, 310)
    )
    assert height == pytest.approx(310)
    assert width > 300
    assert left == pytest.approx(right)
    assert left > 0
    assert top == bottom == 0

    slide = {
        "id": "s1",
        "layout": "bullets",
        "title": "채우기",
        "bullets": ["본문"],
        "image": {
            "src": pictures.encode("image/png", image),
            "caption": "가운데를 남긴다",
            "fit": "cover",
        },
    }
    archive = zipfile.ZipFile(io.BytesIO(deck_export.to_pptx("t", [slide], tokens=TOKENS)))
    xml = archive.read("ppt/slides/slide1.xml").decode()
    assert "a:srcRect" in xml
    assert re.search(r'<a:srcRect l="[1-9]\d*" r="[1-9]\d*"', xml)
    pdf = deck_export.to_pdf("t", [slide], tokens=TOKENS)
    assert pdf.startswith(b"%PDF")
    assert b"/Image" in pdf


def test_a_left_picture_moves_the_text_column_right_in_powerpoint():
    image = base64.b64decode(png(width=320, height=240))
    slide = {
        "id": "s1",
        "layout": "bullets",
        "title": "왼쪽 그림",
        "bullets": ["오른쪽 본문"],
        "image": {
            "src": pictures.encode("image/png", image),
            "position": "left",
        },
    }
    presentation = deck_export.Presentation(
        io.BytesIO(deck_export.to_pptx("t", [slide], tokens=TOKENS))
    )
    shapes = presentation.slides[0].shapes
    picture = next(shape for shape in shapes if shape.shape_type == 13)
    title = next(shape for shape in shapes if getattr(shape, "text", "") == "왼쪽 그림")
    assert picture.left < title.left


def test_a_slide_picture_that_is_not_an_address_is_ignored():
    """A slide image without a `data:` src is ignored."""
    slides = [
        {"id": "s0", "layout": "bullets", "title": "가", "bullets": ["나"], "image": {}},
        {
            "id": "s1",
            "layout": "bullets",
            "title": "다",
            "bullets": ["라"],
            "image": {"src": "https://example.test/p.png"},
        },
    ]
    assert deck_export.to_pptx("t", slides, tokens=TOKENS)[:2] == b"PK"
    archive = zipfile.ZipFile(io.BytesIO(deck_export.to_pptx("t", slides, tokens=TOKENS)))
    assert not [name for name in archive.namelist() if name.startswith("ppt/media/")]


def test_a_portrait_picture_does_not_take_a_whole_page():
    """Picture height is capped at 170 mm and the aspect ratio kept."""
    tall = png(width=600, height=1200)
    width, height = report_export._picture_size(base64.b64decode(tall))
    assert height <= 170 * 72 / 25.4 + 1
    assert width < height  # portrait stays portrait
    assert width == pytest.approx(height * 600 / 1200, rel=0.02)


def test_two_pictures_of_different_sizes_stay_different_sizes():
    """A picture keeps its native size and is shrunk only when it overflows the column."""
    small = report_export._picture_size(base64.b64decode(png(width=360, height=240)))
    large = report_export._picture_size(base64.b64decode(png(width=1024, height=683)))
    assert small[0] < large[0]
    # 360 px at 96 DPI is 270 pt.
    assert small[0] == pytest.approx(270, rel=0.001)
    assert large[0] == pytest.approx(150 * 72 / 25.4, rel=0.01)


def test_every_format_sizes_a_picture_the_same_way():
    """.hwpx and .docx/PDF size a picture by the same rule in their own units."""
    data = base64.b64decode(png(width=1024, height=683))
    width_pt, _ = report_export._picture_size(data)
    markup = report_export._hwpx_picture(1, data)
    hwpx_pt = int(re.search(r'<hp:sz width="(\d+)"', markup).group(1)) / 7200 * 72
    assert width_pt == pytest.approx(hwpx_pt, rel=0.01)


def test_a_figure_is_centred_in_every_format():
    """Picture and caption are centred in .docx and .hwpx."""
    sections = page_export.to_sections(document("doc-report", "section"))

    docx = zipfile.ZipFile(io.BytesIO(report_export.to_docx("t", sections, tokens=TOKENS)))
    body = docx.read("word/document.xml").decode()
    picture_at = body.index("<w:drawing>")
    paragraph_at = body.rindex("<w:p>", 0, picture_at)
    assert 'w:jc w:val="center"' in body[paragraph_at:picture_at]

    hwpx = zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("t", sections, tokens=TOKENS)))
    section_xml = hwpx.read("Contents/section0.xml").decode()
    # Paragraph shape 5 is the centred one.
    assert '<hp:p paraPrIDRef="5" styleIDRef="0"><hp:run charPrIDRef="0"><hp:pic' in section_xml
    assert '<hp:p paraPrIDRef="5" styleIDRef="0"><hp:run charPrIDRef="4">' in section_xml
    header = hwpx.read("Contents/header.xml").decode()
    assert '<hh:paraPr id="5"' in header and 'horizontal="CENTER"' in header
