"""Turning a stored report into a file someone can hand in.

Three formats: `.docx` for track-changes review, `.pdf` for submission, and
`.hwpx` for Korean submission systems that take nothing else.

All built from the artifact's own sections rather than rendered HTML — the
structure is already there, and a browser engine would only lose the headings.
"""

from __future__ import annotations

import io
import re
import zipfile

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.services import fonts


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


def to_docx(title: str, sections: list[dict]) -> bytes:
    from docx import Document
    from docx.shared import Inches, Pt

    document = Document()
    document.add_heading(title, level=0)

    for section in sections:
        document.add_heading(section.get("heading") or "", level=1)
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

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def to_pdf(title: str, sections: list[dict]) -> bytes:
    # Serif for print, and embedded: reportlab's bundled CID font is not, and a
    # reader without the Adobe-Korea1 CMaps draws blank where Korean was.
    # See services/fonts.py.
    korean = fonts.korean("serif")

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "t", parent=base["Title"], fontName=korean, fontSize=20, leading=26
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontName=korean, fontSize=14, leading=20,
            spaceBefore=14, spaceAfter=6,
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
 major="5" minor="1" micro="1" buildNumber="0" os="1" xmlVersion="1.4" application="kchat"/>"""

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
)

#: 160% is the usual line spacing for a Korean report; single spacing sets Hangul
#: text solid, and 0% (the value Hancom infers when the element is absent) makes
#: the paragraphs overlap outright.
_HWPX_LINE_SPACING = 160


def _hwpx_char_properties() -> str:
    items = []
    for cid, height, bold in _HWPX_CHAR_SHAPES:
        items.append(
            f'   <hh:charPr id="{cid}" height="{height}" textColor="#000000"'
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


def _hwpx_para(text: str, para_pr: int, char_pr: int = 0) -> str:
    """One `<hp:p>`. An empty run still needs the `<hp:t/>` — Hancom renders a
    paragraph with no run as a missing line rather than a blank one."""
    return (
        f'<hp:p paraPrIDRef="{para_pr}" styleIDRef="0">'
        f'<hp:run charPrIDRef="{char_pr}">'
        f"<hp:t>{_hwpx_escape(text)}</hp:t>"
        f"</hp:run></hp:p>"
    )


def to_hwpx(title: str, sections: list[dict]) -> bytes:
    """The same document `to_docx` writes, as OWPML.

    Structure only — headings, paragraphs and bullets. Bullets are emitted as
    text prefixed with `•` rather than as a numbering definition: HWPX list
    numbering lives in the header's `numberings` table and referencing one
    incorrectly makes Hancom refuse the file, which is a bad trade for a dot.
    """
    # (paraPr, charPr) pairs from the tables above: title / h1 / h2 / body / bullet.
    body: list[str] = [_hwpx_para(title, 0, 2)]
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

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # `mimetype` must be first and STORED, exactly as in ODF/EPUB. A reader
        # that sniffs the container by byte offset fails on a deflated one.
        archive.writestr(
            zipfile.ZipInfo("mimetype"), _HWPX_MIMETYPE, compress_type=zipfile.ZIP_STORED
        )
        archive.writestr("version.xml", _HWPX_VERSION)
        archive.writestr("META-INF/container.xml", _HWPX_CONTAINER)
        archive.writestr("META-INF/manifest.xml", _HWPX_MANIFEST)
        archive.writestr(
            "Contents/content.hpf", _HWPX_CONTENT_HPF.format(title=_hwpx_escape(title))
        )
        archive.writestr(
            "Contents/header.xml",
            _HWPX_HEADER.format(
                char_properties=_hwpx_char_properties(),
                para_properties=_hwpx_para_properties(),
            ),
        )
        archive.writestr("Contents/section0.xml", section_xml)
        archive.writestr("Preview/PrvText.txt", preview)
    return buffer.getvalue()


__all__ = ["to_docx", "to_pdf", "to_hwpx"]
