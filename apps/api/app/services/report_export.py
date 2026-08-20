"""Turning a stored report into a file someone can hand in.

Three formats: `.docx` for track-changes review, `.pdf` for submission, and
`.hwpx` for Korean submission systems that take nothing else.

All built from the artifact's own sections rather than rendered HTML — the
structure is already there, and a browser engine would only lose the headings.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile

import PIL.Image
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image as RLImage
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from app.services import design, fonts

log = logging.getLogger(__name__)


def _markdown_to_lines(text: str) -> list[tuple[str, str, str]]:
    """`(kind, text, marker)` per line: heading2, bullet, number, or body.

    Deliberately small — the model writes prose with the occasional sub-heading
    and list, so a full Markdown parser would be mostly dead code.

    `marker` is what the exporters render in the hanging position: `•` for a
    bullet, `3.` for an ordered item, empty otherwise. Ordered items keep their
    numbers, because that is where the number carries meaning.

    Numbering follows Markdown: the first item's own number starts the run and
    the rest count from there, so `1.` on every line renders 1, 2, 3. A heading,
    bullet or prose line ends the run; a blank line does not.
    """
    out: list[tuple[str, str, str]] = []
    number = 0
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if heading := re.match(r"^#{2,6}\s+(.*)$", line):
            number = 0
            out.append(("heading", heading.group(1).strip(), ""))
        elif bullet := re.match(r"^\s*[-*+]\s+(.*)$", line):
            number = 0
            out.append(("bullet", bullet.group(1).strip(), "•"))
        elif numbered := re.match(r"^\s*(\d{1,9})[.)]\s+(.*)$", line):
            number = number + 1 if number else int(numbered.group(1))
            out.append(("number", numbered.group(2).strip(), f"{number}."))
        else:
            number = 0
            out.append(("body", line.strip(), ""))
    return out


def _strip_inline(text: str) -> str:
    """Bold and code markers, removed rather than rendered.

    Half-applied emphasis reads worse than none, across three document models.
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)
    return text.replace("`", "")


def to_docx(title: str, sections: list[dict], *, tokens: dict[str, str] | None = None) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor

    style = design.normalise_tokens(tokens) if tokens else None
    #: Colour only. The face stays Word's own: this document is written to be
    #: edited, and a run-level typeface would override whatever the reviewer's
    #: template sets, in every paragraph, unremovably.
    accent = RGBColor.from_string(style["accent"].lstrip("#").upper()) if style else None

    def recolour(heading) -> None:
        if accent is None:
            return
        for run in heading.runs:
            run.font.color.rgb = accent

    document = Document()
    recolour(document.add_heading(title, level=0))

    for section in sections:
        recolour(document.add_heading(section.get("heading") or "", level=1))
        for kind, text, marker in _markdown_to_lines(section.get("content") or ""):
            clean = _strip_inline(text)
            if kind == "heading":
                document.add_heading(clean, level=2)
            elif kind == "bullet":
                document.add_paragraph(clean, style="List Bullet")
            elif kind == "number":
                # Not Word's "List Number" style: its automatic numbering runs
                # on across separate lists, so the second section would start at
                # 4. The literal marker matches the source and the other formats.
                paragraph = document.add_paragraph(f"{marker} {clean}")
                paragraph.paragraph_format.left_indent = Inches(0.25)
                paragraph.paragraph_format.space_after = Pt(3)
            else:
                paragraph = document.add_paragraph(clean)
                paragraph.paragraph_format.space_after = Pt(6)
        for picture in section.get("images") or []:
            data = picture.get("data")
            if not data:
                continue
            width_pt, height_pt = _picture_size(data)
            try:
                # Both dimensions, not just the width: Word scales height from
                # the width and a portrait picture then fills the page on its
                # own — 120 mm wide made a 600×1200 screenshot 240 mm tall,
                # which is a sheet of paper with one figure on it. `_picture_
                # size` already caps the height; pass what it decided.
                document.add_picture(
                    io.BytesIO(data), width=Pt(width_pt), height=Pt(height_pt)
                )
            except Exception as exc:  # noqa: BLE001 — a bad picture is not a failed export
                log.warning("could not place a picture in the docx: %s", exc)
                continue
            # A figure is centred in all three formats, or the same document
            # reads differently depending on which one somebody opened.
            document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            caption = str(picture.get("caption") or "")
            if caption:
                paragraph = document.add_paragraph(caption)
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                paragraph.paragraph_format.space_after = Pt(8)
                for run in paragraph.runs:
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def to_pdf(title: str, sections: list[dict], *, tokens: dict[str, str] | None = None) -> bytes:
    # Serif for print, and embedded: reportlab's bundled CID font is not, and a
    # reader without the Adobe-Korea1 CMaps draws blank where Korean was.
    # See services/fonts.py.
    #
    # Serif stays the default when no design system names a face — this is the
    # submission format, and the deck's Gothic would be a change of document.
    style = design.normalise_tokens(tokens) if tokens else None
    korean = fonts.korean(style["font"] if style else "serif")
    # Absent a design system the headings stay black, exactly as before.
    heading_colour = {"textColor": HexColor(style["accent"])} if style else {}

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "t", parent=base["Title"], fontName=korean, fontSize=20, leading=26,
            **heading_colour,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontName=korean, fontSize=14, leading=20,
            spaceBefore=14, spaceAfter=6, **heading_colour,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName=korean, fontSize=12, leading=17,
            spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "b", parent=base["BodyText"], fontName=korean, fontSize=10.5, leading=17,
            alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "li", parent=base["BodyText"], fontName=korean, fontSize=10.5, leading=17,
            leftIndent=10 * mm, bulletIndent=4 * mm, spaceAfter=3,
        ),
        # Centred, under a centred picture: a caption hanging off the left
        # margin belongs to the paragraph above it, not to the figure.
        "caption": ParagraphStyle(
            "cap", parent=base["BodyText"], fontName=korean, fontSize=9, leading=13,
            alignment=TA_CENTER,
            textColor=HexColor(style["muted"]) if style else HexColor("#666666"),
            spaceBefore=2, spaceAfter=2,
        ),
    }

    story: list = [Paragraph(_escape(title), styles["title"]), Spacer(1, 8 * mm)]
    for index, section in enumerate(sections):
        if index:
            story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(_escape(section.get("heading") or ""), styles["h1"]))
        for kind, text, marker in _markdown_to_lines(section.get("content") or ""):
            clean = _escape(_strip_inline(text))
            if kind == "heading":
                story.append(Paragraph(clean, styles["h2"]))
            elif kind in ("bullet", "number"):
                # `bulletText` hangs the marker, keeping two-digit numbers
                # aligned with single-digit ones.
                story.append(Paragraph(clean, styles["bullet"], bulletText=marker))
            else:
                story.append(Paragraph(clean, styles["body"]))
        for picture in section.get("images") or []:
            data = picture.get("data")
            if not data:
                continue
            width, height = _picture_size(data)
            caption = str(picture.get("caption") or "")
            try:
                image = RLImage(io.BytesIO(data), width=width, height=height)
                image.hAlign = "CENTER"
                figure: list = [image]
            except Exception as exc:  # noqa: BLE001 — a bad picture is not a failed export
                log.warning("could not place a picture in the report pdf: %s", exc)
                continue
            if caption:
                figure.append(Paragraph(_escape(caption), styles["caption"]))
            story.append(Spacer(1, 3 * mm))
            # Kept together: a caption on the page after its picture is a
            # caption for whatever happens to be above it.
            story.append(KeepTogether(figure))
            story.append(Spacer(1, 3 * mm))

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=22 * mm,
        bottomMargin=20 * mm,
        title=title,
    ).build(story)
    return buffer.getvalue()


#: The text column of an A4 page with the margins these exporters use, and as
#: much height as one figure may take before it owns the page.
_PICTURE_MM = 150.0
_PICTURE_MAX_MM = 170.0

#: Pixels are read at 96 DPI, the same rate Hancom uses, so a picture prints at
#: the size it was made unless it does not fit.
_POINTS_PER_PIXEL = 72 / 96


def _picture_size(data: bytes) -> tuple[float, float]:
    """`(width, height)` in points: native size, shrunk only if it overflows.

    Every picture used to be placed at one fixed width, which enlarged the
    small ones — two figures of different sizes came out identical, and a
    360x240 diagram was blown up to the width of the page. Scaling down only
    is both the honest rule and the one the `.hwpx` path was verified with.
    """
    try:
        with PIL.Image.open(io.BytesIO(data)) as picture:
            pixels_wide, pixels_high = picture.size
    except Exception:  # noqa: BLE001 — an unreadable picture still gets a box
        pixels_wide, pixels_high = 480, 320
    width = max(1, pixels_wide) * _POINTS_PER_PIXEL
    height = max(1, pixels_high) * _POINTS_PER_PIXEL
    scale = min(1.0, _PICTURE_MM * mm / width, _PICTURE_MAX_MM * mm / height)
    return width * scale, height * scale


def _escape(text: str) -> str:
    """reportlab's Paragraph reads its input as mini-HTML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── HWPX (OWPML, KS X 6101) ───────────────────────────────────────────
#
# An XML zip like .docx, written with the standard library alone. Hancom
# Office's reader is stricter than Word's: a missing part, or a mimetype entry
# in the wrong position, is rejected rather than repaired.
#
# The skeleton below is minimal on purpose. A larger style table would go
# unused while adding places to break.

_HWPX_MIMETYPE = "application/hwp+zip"

_HWPX_VERSION = """<?xml version="1.0" encoding="UTF-8"?>
<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" tagetApplication="WORDPROCESSOR"
 major="5" minor="1" micro="1" buildNumber="0" os="1" xmlVersion="1.4" application="KloudChat"/>"""

_HWPX_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container"
 xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf">
 <ocf:rootfiles>
  <ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>
 </ocf:rootfiles>
</ocf:container>"""

_HWPX_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest">
 <odf:file-entry odf:full-path="/" odf:media-type="application/hwp+zip"/>
 <odf:file-entry odf:full-path="Contents/header.xml" odf:media-type="application/xml"/>
 <odf:file-entry odf:full-path="Contents/section0.xml" odf:media-type="application/xml"/>
</odf:manifest>"""

_HWPX_CONTENT_HPF = """<?xml version="1.0" encoding="UTF-8"?>
<hpf:package xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf"
 xmlns:opf="http://www.idpf.org/2007/opf/" version="" unique-identifier="" id="">
 <opf:metadata><opf:title>{title}</opf:title></opf:metadata>
 <opf:manifest>
  <opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>
  <opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>
 </opf:manifest>
 <opf:spine><opf:itemref idref="header"/><opf:itemref idref="section0"/></opf:spine>
</hpf:package>"""

#: Five character shapes, five paragraph shapes. section0.xml refers to them by
#: index, so reordering corrupts the body.
#:
#: Mandatory:
#:
#: * `<hh:lineSpacing>` — without it Hancom Office reads 0% and draws every
#:   paragraph on one line.
#: * `<hh:align horizontal="..."/>` as a child element. The `paraPr@align`
#:   attribute is HWPML 2010 and OWPML ignores it silently.
#:
#: `<hh:heading>` and `<hh:breakSetting>` are spelled out for the same reason.
_HWPX_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"
 xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" version="1.4" secCnt="1">
 <hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>
 <hh:refList>
  <hh:fontfaces itemCnt="1">
   <hh:fontface lang="HANGUL" fontCnt="1">
    <hh:font id="0" face="함초롬바탕" type="TTF" isEmbedded="0"/>
   </hh:fontface>
  </hh:fontfaces>
{char_properties}
{para_properties}
  <hh:styles itemCnt="1">
   <hh:style id="0" type="PARA" name="바탕글" engName="Normal" paraPrIDRef="3" charPrIDRef="0" nextStyleIDRef="0" langID="1042"/>
  </hh:styles>
 </hh:refList>
</hh:head>"""

#: ``charPr@height`` is in 1/100 pt — 1000 is 10 pt. These sizes are what make
#: a heading read as one.
_HWPX_CHAR_SHAPES = (
    # (id, height, bold)
    (0, 1000, False),  # body
    (1, 1000, True),   # body, bold
    (2, 1600, True),   # document title
    (3, 1300, True),   # section heading  (h1)
    (4, 1100, True),   # sub-heading      (h2)
)

#: (id, horizontal align, left indent, space-before, space-after) in HWPUNIT
#: (1/7200 in, so 1000 == 10 pt).
_HWPX_PARA_SHAPES = (
    (0, "CENTER", 0, 0, 600),      # title
    (1, "LEFT", 0, 600, 300),      # h1
    (2, "LEFT", 0, 400, 200),      # h2
    (3, "JUSTIFY", 0, 0, 150),     # body
    (4, "JUSTIFY", 1000, 0, 100),  # bullet — indented from the body margin
    (5, "CENTER", 0, 300, 100),    # figure and its caption
)

#: 160% is the usual line spacing for a Korean report; single spacing sets Hangul
#: text solid, and 0% (the value Hancom infers when the element is absent) makes
#: the paragraphs overlap outright.
_HWPX_LINE_SPACING = 160


#: Character-shape ids the accent may colour: the document title and the
#: section headings. Body text stays black — a report is read, and coloured
#: paragraphs are what makes a submission look like a brochure.
_HWPX_ACCENT_SHAPES = (2, 3)


def _hwpx_char_properties(accent: str | None = None) -> str:
    items = []
    for cid, height, bold in _HWPX_CHAR_SHAPES:
        colour = accent if (accent and cid in _HWPX_ACCENT_SHAPES) else "#000000"
        items.append(
            f'   <hh:charPr id="{cid}" height="{height}" textColor="{colour}"'
            ' shadeColor="none" useFontSpace="0" useKerning="0">\n'
            '    <hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>\n'
            '    <hh:ratio hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>\n'
            '    <hh:spacing hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>\n'
            '    <hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>\n'
            '    <hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>\n'
            + ("    <hh:bold/>\n" if bold else "")
            + "   </hh:charPr>"
        )
    return (
        f'  <hh:charProperties itemCnt="{len(items)}">\n'
        + "\n".join(items)
        + "\n  </hh:charProperties>"
    )


def _hwpx_para_properties() -> str:
    items = []
    for pid, align, left, prev, nxt in _HWPX_PARA_SHAPES:
        items.append(
            f'   <hh:paraPr id="{pid}" tabPrIDRef="0" condense="0" fontLineHeight="0"'
            ' snapToGrid="1" suppressLineNumbers="0" checked="0">\n'
            f'    <hh:align horizontal="{align}" vertical="BASELINE"/>\n'
            '    <hh:heading type="NONE" idRef="0" level="0"/>\n'
            '    <hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="KEEP_WORD"'
            ' widowOrphan="0" keepWithNext="0" keepLines="0" pageBreakBefore="0"'
            ' lineWrap="BREAK"/>\n'
            '    <hh:autoSpacing eAsianEng="0" eAsianNum="0"/>\n'
            "    <hh:margin>\n"
            '     <hc:intent value="0" unit="HWPUNIT"/>\n'
            f'     <hc:left value="{left}" unit="HWPUNIT"/>\n'
            '     <hc:right value="0" unit="HWPUNIT"/>\n'
            f'     <hc:prev value="{prev}" unit="HWPUNIT"/>\n'
            f'     <hc:next value="{nxt}" unit="HWPUNIT"/>\n'
            "    </hh:margin>\n"
            f'    <hh:lineSpacing type="PERCENT" value="{_HWPX_LINE_SPACING}" unit="HWPUNIT"/>\n'
            "   </hh:paraPr>"
        )
    return (
        f'  <hh:paraProperties itemCnt="{len(items)}">\n'
        + "\n".join(items)
        + "\n  </hh:paraProperties>"
    )


def _hwpx_escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


#: Page geometry, carried in the first paragraph's run exactly as Hancom
#: writes it. A4 is 59528 x 84188 HWPUNIT; the margins are 30/30/20/15 mm.
#:
#: Text laid out without this — every .hwpx this wrote until now — because
#: Hancom falls back to its own defaults. **A picture does not.** With no page
#: box to sit in, an object sized in absolute units was read and then not
#: drawn: the file opened, the text was right, and the picture was simply
#: absent. Adding this is what made it appear, confirmed in Hancom Office.
_HWPX_SECPR = (
    '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000"'
    ' tabStopVal="4000" tabStopUnit="HWPUNIT" outlineShapeIDRef="0" memoShapeIDRef="0"'
    ' textVerticalWidthHead="0" masterPageCnt="0">'
    '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
    '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
    '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0"'
    ' border="SHOW_ALL" fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0"'
    ' showLineNumber="0"/>'
    '<hp:pagePr landscape="WIDELY" width="59528" height="84188" gutterType="LEFT_ONLY">'
    '<hp:margin header="4252" footer="4252" gutter="0" left="8504" right="8504"'
    ' top="5668" bottom="4252"/></hp:pagePr>'
    "</hp:secPr>"
)

#: HWPUNIT is 1/7200 inch, and Hancom reads a picture's pixels at 96 DPI
#: whatever the file's own metadata says: one pixel is 75 HWPUNIT.
_HWPUNIT_PER_PIXEL = 75

#: The text column of the page above, and as much height as a figure may take
#: before it owns the page.
_HWPX_MAX_WIDTH = 59528 - 8504 * 2
_HWPX_MAX_HEIGHT = int(170 / 25.4 * 7200)

#: One picture, inline. `binaryItemIDRef` resolves against the `<opf:item>` id
#: in `Contents/content.hpf` — that single line is the whole link between this
#: element and the bytes in `BinData/`. Nothing is declared in `header.xml`:
#: `<hh:binDataList>` belongs to the older HML format and no HWPX carries one.
_HWPX_PIC = (
    '<hp:pic id="{n}" zOrder="0" numberingType="PICTURE" textWrap="SQUARE"'
    ' textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="0"'
    ' instid="{n}" reverse="0">'
    '<hp:offset x="0" y="0"/>'
    '<hp:orgSz width="{w}" height="{h}"/>'
    '<hp:curSz width="{w}" height="{h}"/>'
    '<hp:flip horizontal="0" vertical="0"/>'
    '<hp:rotationInfo angle="0" centerX="{cx}" centerY="{cy}" rotateimage="1"/>'
    "<hp:renderingInfo>"
    '<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
    '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
    '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
    "</hp:renderingInfo>"
    '<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{w}" y="0"/>'
    '<hc:pt2 x="{w}" y="{h}"/><hc:pt3 x="0" y="{h}"/></hp:imgRect>'
    '<hp:imgClip left="0" right="{dw}" top="0" bottom="{dh}"/>'
    '<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
    '<hp:imgDim dimwidth="{dw}" dimheight="{dh}"/>'
    '<hc:img binaryItemIDRef="{ref}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
    "<hp:effects/>"
    '<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" protect="0"/>'
    '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0"'
    ' holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP"'
    ' horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
    '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
    "</hp:pic>"
)


def _hwpx_picture(index: int, data: bytes) -> str:
    """One picture paragraph, sized to the page.

    `imgDim` and `imgClip` stay in the picture's own pixels — they are the
    source rectangle — while `orgSz`, `curSz`, `sz` and `imgRect` carry the
    size on the page. They are equal for a picture that already fits.
    """
    try:
        with PIL.Image.open(io.BytesIO(data)) as picture:
            pixels_wide, pixels_high = picture.size
    except Exception:  # noqa: BLE001 — an unreadable picture gets a plain box
        pixels_wide, pixels_high = 480, 320
    native_w = max(1, pixels_wide) * _HWPUNIT_PER_PIXEL
    native_h = max(1, pixels_high) * _HWPUNIT_PER_PIXEL
    scale = min(1.0, _HWPX_MAX_WIDTH / native_w, _HWPX_MAX_HEIGHT / native_h)
    width, height = int(native_w * scale), int(native_h * scale)
    return (
        '<hp:p paraPrIDRef="5" styleIDRef="0"><hp:run charPrIDRef="0">'
        + _HWPX_PIC.format(
            n=index,
            ref=f"image{index}",
            w=width,
            h=height,
            dw=native_w,
            dh=native_h,
            cx=width // 2,
            cy=height // 2,
        )
        + "<hp:t/></hp:run></hp:p>"
    )


def _hwpx_para(text: str, para_pr: int, char_pr: int = 0) -> str:
    """One `<hp:p>`. An empty run still needs the `<hp:t/>` — Hancom renders a
    paragraph with no run as a missing line rather than a blank one."""
    return (
        f'<hp:p paraPrIDRef="{para_pr}" styleIDRef="0">'
        f'<hp:run charPrIDRef="{char_pr}">'
        f"<hp:t>{_hwpx_escape(text)}</hp:t>"
        f"</hp:run></hp:p>"
    )


def to_hwpx(title: str, sections: list[dict], *, tokens: dict[str, str] | None = None) -> bytes:
    """The same document `to_docx` writes, as OWPML.

    Structure only — headings, paragraphs and bullets. Bullets are emitted as
    text prefixed with `•` rather than as a numbering definition: HWPX list
    numbering lives in the header's `numberings` table and referencing one
    incorrectly makes Hancom refuse the file, which is a bad trade for a dot.

    **A picture becomes a `[그림]` line, not a picture.** Embedding one needs a
    `BinData` part, a manifest entry, a header `binDataList` and a `<hp:pic>`
    that references all three by id — and the failure mode of getting any of it
    wrong is not a missing picture but a document Hancom refuses to open. There
    is no reader here to check against: LibreOffice's Hancom filter is the v5
    binary format and does not read HWPX, and no independent implementation of
    OWPML is available. So the picture is announced rather than embedded, which
    is a document somebody can still open and a fact they can act on — the
    `.docx` and the PDF beside it carry the real thing.
    """
    style = design.normalise_tokens(tokens) if tokens else None
    # (paraPr, charPr) pairs from the tables above: title / h1 / h2 / body / bullet.
    # The section properties ride in the first paragraph's run, which is where
    # Hancom puts them and the only place they are read from.
    body: list[str] = [
        f'<hp:p paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="2">'
        f"{_HWPX_SECPR}<hp:t>{_hwpx_escape(title)}</hp:t></hp:run></hp:p>"
    ]
    #: `BinData/imageN.png` and the `<opf:item id="imageN">` that resolves it.
    embedded: list[tuple[str, bytes, str]] = []
    for section in sections:
        heading = (section.get("heading") or "").strip()
        if heading:
            body.append(_hwpx_para(heading, 1, 3))
        for kind, text, marker in _markdown_to_lines(section.get("content") or ""):
            clean = _strip_inline(text)
            if kind == "heading":
                body.append(_hwpx_para(clean, 2, 4))
            elif kind in ("bullet", "number"):
                body.append(_hwpx_para(f"{marker} {clean}", 4))
            else:
                body.append(_hwpx_para(clean, 3))
        for picture in section.get("images") or []:
            data = picture.get("data")
            if not data:
                continue
            mime = str(picture.get("mime") or "image/png").lower()
            index = len(embedded) + 1
            # `image/jpg` rather than `image/jpeg`: Hancom's own spelling, and
            # the extension follows the same name so the three ids match.
            extension = {"image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}.get(
                mime, "png"
            )
            embedded.append((f"image{index}", data, extension))
            body.append(_hwpx_picture(index, data))
            caption = str(picture.get("caption") or "").strip()
            if caption:
                body.append(_hwpx_para(caption, 5, 4))

    section_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
        ' xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
        ' xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">'
        + "".join(body)
        + "</hs:sec>"
    )

    # PrvText is what a file manager previews. Cheap to fill and its absence is
    # what makes a generated .hwpx look empty before it is opened.
    preview = "\n".join(
        [title] + [(s.get("heading") or "") for s in sections]
    )[:1000]

    # One `<opf:item>` per picture, between the header and the section: that id
    # is what `<hc:img binaryItemIDRef>` resolves against, and `isEmbeded` —
    # one `d`, OWPML's own spelling — is what stops Hancom dropping it. The
    # spine is left alone; it holds only the header and the section.
    items = "".join(
        f'  <opf:item id="{name}" href="BinData/{name}.{extension}"'
        f' media-type="image/{"jpg" if extension == "jpg" else extension}" isEmbeded="1"/>\n'
        for name, _, extension in embedded
    )
    content_hpf = _HWPX_CONTENT_HPF.format(title=_hwpx_escape(title)).replace(
        '  <opf:item id="section0"', items + '  <opf:item id="section0"', 1
    )
    manifest = _HWPX_MANIFEST.replace(
        "</odf:manifest>",
        "".join(
            f' <odf:file-entry odf:full-path="BinData/{name}.{extension}"'
            f' odf:media-type="image/{"jpg" if extension == "jpg" else extension}"/>\n'
            for name, _, extension in embedded
        )
        + "</odf:manifest>",
        1,
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # `mimetype` must be first and STORED, exactly as in ODF/EPUB. A reader
        # that sniffs the container by byte offset fails on a deflated one.
        archive.writestr(
            zipfile.ZipInfo("mimetype"), _HWPX_MIMETYPE, compress_type=zipfile.ZIP_STORED
        )
        archive.writestr("version.xml", _HWPX_VERSION)
        archive.writestr("META-INF/container.xml", _HWPX_CONTAINER)
        archive.writestr("META-INF/manifest.xml", manifest)
        archive.writestr("Contents/content.hpf", content_hpf)
        archive.writestr(
            "Contents/header.xml",
            _HWPX_HEADER.format(
                char_properties=_hwpx_char_properties(style["accent"] if style else None),
                para_properties=_hwpx_para_properties(),
            ),
        )
        archive.writestr("Contents/section0.xml", section_xml)
        for name, data, extension in embedded:
            # Stored, like `mimetype` and like every picture in a file Hancom
            # wrote itself: the XML parts deflate, the binaries do not.
            archive.writestr(
                zipfile.ZipInfo(f"BinData/{name}.{extension}"),
                data,
                compress_type=zipfile.ZIP_STORED,
            )
        archive.writestr("Preview/PrvText.txt", preview)
    return buffer.getvalue()


__all__ = ["to_docx", "to_pdf", "to_hwpx"]
