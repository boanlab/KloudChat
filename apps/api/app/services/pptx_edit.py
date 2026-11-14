"""Read and edit a `.pptx` in place: a numbered outline for the model, and edit ops that keep
each paragraph's level and bullets, each run's character format and each cell's own fill.

Ids are positions in the current version: slides `s1…`, and in each slide the shapes worth
naming (text, tables, charts, pictures) `s2.1, s2.2…` in drawing order, a group's shapes
where the group stands. Hidden shapes are not listed: decks exported from KloudChat carry the
slide title twice, in a visible box and in a 1 pt hidden title placeholder for the outline
view; an edit of the visible title takes the hidden copy along. Slide ops renumber, so one
`apply` call resolves every op against the version it was given, and the next call reads
again.

A shape's text is one line per paragraph. A rewrite touches only what changed: lines that
stay keep their runs, a changed line keeps the text it shares at the start and the end in its
own runs (a bold label stays bold), and a new line takes the format of its neighbour.
"""

from __future__ import annotations

import copy
import difflib
import io
import re
from dataclasses import dataclass, field

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from app.services import office_edit
from app.services.files import _open_zip
from app.services.office_edit import BLANK, Result, short

__all__ = [
    "BLANK", "OPS", "PptxError", "Result", "apply", "blanks", "blank_deck",
    "breaks_in_runs", "heal_written_bullets", "healed", "normalise", "outline", "plain_text",
    "set_bullet", "slide_number_field",
]

OPS = (
    "replace_text", "fill_blank", "set_cell", "set_notes", "insert_table_row",
    "delete_table_row", "add_slide", "duplicate_slide", "delete_slide", "move_slide",
)
#: ops after which the slide and shape numbers are not the ones read
RENUMBERS = frozenset({"add_slide", "duplicate_slide", "delete_slide", "move_slide"})

#: a line break inside a paragraph (`a:br`), as the outline writes it
BREAK = "↵"
MAX_SLIDES = 200

_A_P, _A_R, _A_T, _A_BR, _A_FLD = qn("a:p"), qn("a:r"), qn("a:t"), qn("a:br"), qn("a:fld")
_A_RPR, _A_END, _A_PPR = qn("a:rPr"), qn("a:endParaRPr"), qn("a:pPr")
_A_TR, _A_TC, _A_TXBODY = qn("a:tr"), qn("a:tc"), qn("a:txBody")
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_TITLES = (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE)
_BODIES = (PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT, PP_PLACEHOLDER.VERTICAL_BODY,
           PP_PLACEHOLDER.VERTICAL_OBJECT)
_MARKS = {
    PP_PLACEHOLDER.TITLE: "제목", PP_PLACEHOLDER.CENTER_TITLE: "제목",
    PP_PLACEHOLDER.VERTICAL_TITLE: "제목", PP_PLACEHOLDER.SUBTITLE: "부제목",
    PP_PLACEHOLDER.BODY: "본문", PP_PLACEHOLDER.OBJECT: "본문",
    PP_PLACEHOLDER.VERTICAL_BODY: "본문", PP_PLACEHOLDER.VERTICAL_OBJECT: "본문",
    PP_PLACEHOLDER.DATE: "날짜", PP_PLACEHOLDER.FOOTER: "바닥글",
    PP_PLACEHOLDER.SLIDE_NUMBER: "쪽 번호",
}


class PptxError(ValueError):
    """A file or an op this module refuses; the message is shown to the model."""


@dataclass
class _Item:
    sid: str
    shape: object
    #: `text`, `table`, `chart`, `picture`, `diagram` (SmartArt)
    kind: str
    mark: str = ""


@dataclass
class _Slide:
    slide: object
    items: list[_Item] = field(default_factory=list)
    #: hidden shapes with text (the outline's title copy)
    hidden: list[object] = field(default_factory=list)


@dataclass
class _Deck:
    prs: object
    slides: list[_Slide] = field(default_factory=list)


# ── loading ──


def _hidden(shape) -> bool:
    props = shape._element.find(".//" + qn("p:cNvPr"))
    return props is not None and props.get("hidden") in ("1", "true")


def _walk(shapes, hidden: bool = False, inside: bool = False):
    for shape in shapes:
        gone = hidden or _hidden(shape)
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk(shape.shapes, gone, True)
        else:
            yield shape, gone, inside


def _placeholder_type(shape):
    if not shape.is_placeholder:
        return None
    try:
        return shape.placeholder_format.type
    except (ValueError, AttributeError):
        return None


def _index(prs) -> _Deck:
    deck = _Deck(prs=prs)
    for number, slide in enumerate(prs.slides, 1):
        entry = _Slide(slide=slide)
        count = 0
        for shape, gone, inside in _walk(slide.shapes):
            kind = ""
            if getattr(shape, "has_table", False) and shape.has_table:
                kind = "table"
            elif getattr(shape, "has_chart", False) and shape.has_chart:
                kind = "chart"
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                kind = "picture"
            elif shape._element.find(".//" + qn("a:graphicData")) is not None and "diagram" in (
                shape._element.find(".//" + qn("a:graphicData")).get("uri") or ""
            ):
                kind = "diagram"
            elif shape.has_text_frame and (
                shape.text_frame.text.strip() or _placeholder_type(shape) is not None
            ):
                kind = "text"
            if not kind:
                continue
            if gone:
                if kind == "text":
                    entry.hidden.append(shape)
                continue
            count += 1
            mark = _MARKS.get(_placeholder_type(shape), "") if kind == "text" else ""
            if inside:
                mark = f"{mark}, 묶음 안" if mark else "묶음 안"
            entry.items.append(_Item(f"s{number}.{count}", shape, kind, mark))
        # a visible box holding what the hidden title holds is the slide's title
        titles = {
            _shape_text(h) for h in entry.hidden if _placeholder_type(h) in _TITLES
        }
        for item in entry.items:
            if item.kind == "text" and not item.mark and _shape_text(item.shape) in titles:
                item.mark = "제목"
        deck.slides.append(entry)
    return deck


def _load(data: bytes, *, heal: bool = True):
    if data[:4] != b"PK\x03\x04":
        raise PptxError("PowerPoint 문서(.pptx)가 아닙니다.")
    try:
        # declared sizes first: a zip bomb is refused before anything unpacks it
        _open_zip(data).close()
        prs = Presentation(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — any unreadable package is the same answer
        raise PptxError("PowerPoint 문서(.pptx)가 아니거나 손상된 파일입니다.") from exc
    if heal:
        # read and edited normalised, so an edit saves the deck healed
        normalise(prs)
    return prs


def _save(prs) -> bytes:
    _renumber(prs)
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


# ── paragraph text ──


def _pieces(paragraph: etree._Element) -> list[tuple[etree._Element, str]]:
    """The paragraph's text in order: a run's `a:t`, a field (`a:fld`, its shown text) and a
    line break (`a:br`, 「↵」). Only a run's `a:t` takes new text."""
    out: list[tuple[etree._Element, str]] = []
    for child in paragraph:
        if child.tag == _A_R:
            leaf = child.find(_A_T)
            if leaf is not None:
                out.append((leaf, leaf.text or ""))
        elif child.tag == _A_FLD:
            leaf = child.find(_A_T)
            out.append((child, leaf.text or "" if leaf is not None else ""))
        elif child.tag == _A_BR:
            out.append((child, BREAK))
    return out


def _para_text(paragraph: etree._Element) -> str:
    return "".join(text for _, text in _pieces(paragraph))


def _paragraphs(body: etree._Element) -> list[etree._Element]:
    return list(body.iterchildren(_A_P))


def _body_text(body: etree._Element | None) -> str:
    if body is None:
        return ""
    return "\n".join(_para_text(p) for p in _paragraphs(body))


def _shape_text(shape) -> str:
    return _body_text(shape.text_frame._txBody) if shape.has_text_frame else ""


#: a run that is only a written bullet: 「• 」 before the text, often in the accent colour
_MARKER = re.compile(r"\s{0,3}[•▪◦‣●○■□◆◇➢➤✓✔–\-·]\s{0,3}")


def _runs(paragraph: etree._Element) -> list[etree._Element]:
    return [r for r in paragraph.iterchildren(_A_R) if r.find(_A_T) is not None]


def _marker(paragraph: etree._Element) -> etree._Element | None:
    """The paragraph's leading bullet run, when its text is written apart from the bullet."""
    runs = _runs(paragraph)
    if len(runs) > 1 and _MARKER.fullmatch(runs[0].find(_A_T).text or ""):
        return runs[0]
    return None


#: marks that are never text where they stand: a run of one of them alone is a bullet; a
#: dash or a middle dot is one only with the space after it (「-5%」 split in runs is a sign)
_SIGNS = "•▪◦‣●○■□◆◇➢➤✓✔"
#: `a:pPr` children in schema order (CT_TextParagraphProperties)
_PPR_ORDER = tuple(qn(f"a:{tag}") for tag in (
    "lnSpc", "spcBef", "spcAft", "buClrTx", "buClr", "buSzTx", "buSzPct", "buSzPts",
    "buFontTx", "buFont", "buNone", "buAutoNum", "buChar", "buBlip", "tabLst", "defRPr",
    "extLst",
))
_BU_KINDS = tuple(qn(f"a:{tag}") for tag in ("buNone", "buAutoNum", "buChar", "buBlip"))
_BU_COLOURS = (qn("a:buClrTx"), qn("a:buClr"))
_BU_FONTS = (qn("a:buFontTx"), qn("a:buFont"))
_COLOUR_TAGS = tuple(qn(f"a:{tag}") for tag in (
    "srgbClr", "schemeClr", "scrgbClr", "hslClr", "sysClr", "prstClr",
))
#: hanging indent of a bullet, in ems of its text
HANG_EM = 0.9


def _ppr(paragraph: etree._Element) -> etree._Element:
    props = paragraph.find(_A_PPR)
    if props is None:
        props = etree.Element(_A_PPR)
        paragraph.insert(0, props)
    return props


def _put(props: etree._Element, element: etree._Element) -> None:
    """`element` into `a:pPr` at its schema place."""
    rank = _PPR_ORDER.index(element.tag)
    later = next((c for c in props if c.tag in _PPR_ORDER and _PPR_ORDER.index(c.tag) > rank),
                 None)
    if later is not None:
        later.addprevious(element)
    else:
        props.append(element)


def set_bullet(
    paragraph: etree._Element,
    *,
    char: str = "•",
    colour: str | etree._Element | None = None,
    hang: int,
    numbered: str | None = None,
) -> None:
    """A real bullet on the paragraph: `char` (or `numbered`, an `a:buAutoNum` scheme such as
    `arabicPeriod`) in `colour` (an `RRGGBB` or a colour element), drawn in the text's own font
    and size, the text hanging `hang` EMU right of it so a wrapped line starts under the text
    rather than under the bullet."""
    props = _ppr(paragraph)
    for child in list(props):
        if child.tag in _BU_KINDS or child.tag in _BU_COLOURS or child.tag in _BU_FONTS:
            props.remove(child)
    if colour is not None:
        holder = etree.Element(qn("a:buClr"))
        if isinstance(colour, str):
            etree.SubElement(holder, qn("a:srgbClr")).set("val", colour.upper())
        else:
            holder.append(copy.deepcopy(colour))
        _put(props, holder)
    if numbered:
        mark = etree.Element(qn("a:buAutoNum"))
        mark.set("type", numbered)
    else:
        mark = etree.Element(qn("a:buChar"))
        mark.set("char", char)
    _put(props, mark)
    base = int(props.get("marL") or 0)
    indent = int(props.get("indent") or 0)
    if indent < 0:
        # already hanging: the text stays where it was
        base += indent
    props.set("marL", str(max(0, base) + hang))
    props.set("indent", str(-hang))


def _sign_of(text: str) -> str:
    """The bullet a lone run `text` writes, or ``."""
    found = _MARKER.fullmatch(text)
    if not found:
        return ""
    sign = text.strip()
    if sign in _SIGNS or (text != text.rstrip() and sign):
        return sign
    return ""


def _run_size(run: etree._Element | None) -> int:
    """A run's size in hundredths of a point, 0 when it inherits one."""
    props = run.find(_A_RPR) if run is not None else None
    try:
        return int(props.get("sz")) if props is not None and props.get("sz") else 0
    except ValueError:
        return 0


def _heal_paragraph(paragraph: etree._Element) -> bool:
    """A bullet written as text in a run of its own (「• 」, often bold in the accent colour,
    with `a:buNone`) made a real one in that colour: the run goes, and a wrapped line hangs
    under the text instead of starting under the bullet. Old house decks wrote theirs so."""
    runs = _runs(paragraph)
    if len(runs) < 2:
        return False
    sign = _sign_of(runs[0].find(_A_T).text or "")
    if not sign or not "".join(r.find(_A_T).text or "" for r in runs[1:]).strip():
        return False
    props = paragraph.find(_A_PPR)
    if props is not None and any(props.find(qn(f"a:{tag}")) is not None
                                 for tag in ("buAutoNum", "buChar", "buBlip")):
        return False
    marker = runs[0]
    fill = marker.find(_A_RPR).find(qn("a:solidFill")) if marker.find(_A_RPR) is not None else None
    colour = next((c for c in fill if c.tag in _COLOUR_TAGS), None) if fill is not None else None
    size = _run_size(runs[1]) or _run_size(marker) or 1800
    paragraph.remove(marker)
    # the text's first run starts where the bullet was: no space left over before it
    first = runs[1].find(_A_T)
    first.text = (first.text or "").lstrip()
    set_bullet(paragraph, char=sign, colour=colour, hang=int(size / 100 * HANG_EM * 12700))
    return True


def heal_written_bullets(prs) -> int:
    """Every written bullet in the deck's text (slides, groups, table cells) made a real one
    (`_heal_paragraph`); how many."""
    healed = 0
    for slide in prs.slides:
        for paragraph in slide._element.iter(_A_P):
            healed += _heal_paragraph(paragraph)
    return healed


#: a line break written into a run's text: LF, CR (python-pptx escapes it `_x000D_`), CRLF, VT
_RAW_BREAK = re.compile(r"_x000D_\n|_x000D_|_x000B_|\r\n|[\r\n\v]")


def breaks_in_runs(root: etree._Element) -> int:
    """Every line break written as a character inside a run's text made an `a:br` between
    two runs of that format; how many runs were split. PowerPoint draws a raw LF in `a:t` as
    nothing or a box, and the editor's text model reads it as a paragraph end that borrows
    the paragraph before's runs by character offset (a wrapped line unindented, its first
    letters in the bullet's colour)."""
    split = 0
    for run in list(root.iter(_A_R)):
        leaf = run.find(_A_T)
        text = leaf.text if leaf is not None else None
        if not text or not _RAW_BREAK.search(text):
            continue
        props = run.find(_A_RPR)
        parts = _RAW_BREAK.split(text)
        made: list[etree._Element] = []
        for index, part in enumerate(parts):
            if index:
                br = etree.Element(_A_BR)
                if props is not None:
                    br.append(copy.deepcopy(props))
                made.append(br)
            if part:
                piece = copy.deepcopy(run)
                piece.find(_A_T).text = part
                made.append(piece)
        for element in made:
            run.addprevious(element)
        run.getparent().remove(run)
        split += 1
    return split


def slide_number_field(run: etree._Element, number: int) -> etree._Element:
    """The run made a slide-number field (`a:fld type="slidenum"`) showing `number` now, so a
    copied or moved slide shows its own number."""
    field_ = etree.Element(qn("a:fld"))
    field_.set("id", "{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}")
    field_.set("type", "slidenum")
    props = run.find(_A_RPR)
    if props is not None:
        field_.append(copy.deepcopy(props))
    etree.SubElement(field_, _A_T).text = str(number)
    run.addprevious(field_)
    run.getparent().remove(run)
    return field_


def _static_numbers(prs) -> int:
    """A house deck's page number written as static text (its corner chip: one run that is
    the slide's own number, in the bottom-right corner) made a slide-number field."""
    made = 0
    width, height = prs.slide_width or 0, prs.slide_height or 0
    for number, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            if shape.is_placeholder or not shape.has_text_frame or shape.top is None:
                continue
            if shape.top < height * 0.85 or shape.left < width * 0.8:
                continue
            paragraphs = _paragraphs(shape.text_frame._txBody)
            runs = _runs(paragraphs[0]) if len(paragraphs) == 1 else []
            if len(runs) == 1 and (runs[0].find(_A_T).text or "").strip() == str(number):
                slide_number_field(runs[0], number)
                made += 1
    return made


def _renumber(prs) -> None:
    """Each slide-number field's shown text set to its slide's place, as PowerPoint does when
    it lays the deck out (a copied slide carries its source's number until then)."""
    for number, slide in enumerate(prs.slides, 1):
        for found in slide._element.iter(qn("a:fld")):
            leaf = found.find(_A_T)
            if found.get("type") == "slidenum" and leaf is not None:
                leaf.text = str(number)


def normalise(prs) -> int:
    """The deck's text as the editor and PowerPoint read it right: line breaks written into
    run text made `a:br`, written bullets made real ones, a static page number a field; how
    many places changed."""
    changed = 0
    for slide in prs.slides:
        changed += breaks_in_runs(slide._element)
    changed += heal_written_bullets(prs)
    changed += _static_numbers(prs)
    return changed


def healed(data: bytes) -> bytes:
    """The deck normalised (`normalise`): `data` itself when nothing needed it, so a deck
    opened in the editor heals before it is edited."""
    prs = _load(data, heal=False)
    return _save(prs) if normalise(prs) else data


def _bullet_sign(paragraph: etree._Element) -> str:
    """The character of the paragraph's own `a:buChar`, or ``."""
    props = paragraph.find(_A_PPR)
    mark = props.find(qn("a:buChar")) if props is not None else None
    return (mark.get("char") or "").strip() if mark is not None else ""


def _without_sign(paragraph: etree._Element, text: str) -> str:
    """`text` without a leading copy of the paragraph's own bullet (「• 하나」 for a bulleted
    line), so a line written with its bullet is not bulleted twice."""
    sign = _bullet_sign(paragraph)
    stripped = text.lstrip()
    if sign and stripped.startswith(sign) and stripped[len(sign):len(sign) + 1].isspace():
        return stripped[len(sign):].lstrip()
    return text


def _run_props(paragraph: etree._Element) -> etree._Element | None:
    """The format the paragraph's text is mostly in: its longest run's (a short bold label
    or a coloured bullet before it does not decide), else the paragraph mark's."""
    runs = [r for r in _runs(paragraph) if r.find(_A_RPR) is not None]
    if runs:
        return max(runs, key=lambda r: len(r.find(_A_T).text or "")).find(_A_RPR)
    return paragraph.find(_A_END)


def _set_text(paragraph: etree._Element, text: str) -> None:
    """Paragraph text replaced in one run in the format the text was mostly in; fields and
    breaks go, a 「↵」 is a break. A written bullet before the text stays, unless the new
    text brings its own."""
    text = _without_sign(paragraph, text)
    marker = _marker(paragraph)
    if marker is not None:
        sign = (marker.find(_A_T).text or "").strip()
        if text.lstrip().startswith(sign):
            # the outline showed the bullet with the text: it stays in its own run
            text = text.lstrip()[len(sign):].lstrip()
        if not text.strip():
            marker = None
    source = _run_props(paragraph)
    props = copy.deepcopy(source) if source is not None else None
    if props is not None:
        props.tag = _A_RPR
    for child in list(paragraph):
        if child.tag in (_A_R, _A_BR, _A_FLD) and child is not marker:
            paragraph.remove(child)
    end = paragraph.find(_A_END)
    made: list[etree._Element] = []
    for index, part in enumerate(text.split(BREAK)):
        if index:
            br = etree.Element(_A_BR)
            if props is not None:
                br.append(copy.deepcopy(props))
            made.append(br)
        if part:
            run = etree.Element(_A_R)
            if props is not None:
                run.append(copy.deepcopy(props))
            leaf = etree.SubElement(run, _A_T)
            leaf.text = part
            made.append(run)
    if not made and end is None and props is not None:
        # an emptied paragraph keeps the format typing in it takes
        end = copy.deepcopy(props)
        end.tag = _A_END
        paragraph.append(end)
    for element in made:
        if end is not None:
            end.addprevious(element)
        else:
            paragraph.append(element)


def _starts(pieces: list[tuple[etree._Element, str]]) -> list[int]:
    starts, at = [], 0
    for _, piece in pieces:
        starts.append(at)
        at += len(piece)
    return starts


def _splice(pieces: list[tuple[etree._Element, str]], starts: list[int],
            start: int, end: int, value: str) -> None:
    """Characters `start:end` replaced by `value`, written into the run text the first of
    them is in; the rest of the span is cut from what follows (a field or break in it goes)."""
    placed = False
    for (leaf, original), at in zip(pieces, starts, strict=True):
        size = len(original)
        if at + size <= start or at >= end:
            continue
        if leaf.tag != _A_T:
            parent = leaf.getparent()
            if parent is not None:
                parent.remove(leaf)
            continue
        text = leaf.text or ""
        head = text[: max(0, start - at)]
        tail = text[end - at:] if end - at < size else ""
        leaf.text = head + (value if not placed else "") + tail
        placed = True


def _rewrite(paragraph: etree._Element, text: str) -> None:
    """The paragraph's text made `text`, touching only what changed (docx_edit._rewrite)."""
    text = _without_sign(paragraph, text)
    pieces = _pieces(paragraph)
    old = "".join(t for _, t in pieces)
    if old == text:
        return
    pre = 0
    while pre < min(len(old), len(text)) and old[pre] == text[pre]:
        pre += 1
    suf = 0
    while (suf < min(len(old), len(text)) - pre
           and old[len(old) - 1 - suf] == text[len(text) - 1 - suf]):
        suf += 1
    start, end, value = pre, len(old) - suf, text[pre:len(text) - suf]
    starts = _starts(pieces)
    if start == end:
        holder = next((
            (leaf, at) for (leaf, piece), at in zip(pieces, starts, strict=True)
            if leaf.tag == _A_T and at < start <= at + len(piece)
        ), None)
    else:
        holder = next((
            (leaf, at) for (leaf, piece), at in zip(pieces, starts, strict=True)
            if leaf.tag == _A_T and at < end and start < at + len(piece)
        ), None)
    if (not pre and not suf) or holder is None or BREAK in value:
        _set_text(paragraph, text)
        return
    if start == end:
        leaf, at = holder
        body = leaf.text or ""
        leaf.text = body[: start - at] + value + body[start - at:]
        return
    _splice(pieces, starts, start, end, value)


def _replace_in(paragraph: etree._Element, old: str, new: str) -> int:
    """Every `old` replaced by `new` and an agreeing particle, in the run it starts in."""
    pieces = _pieces(paragraph)
    text = "".join(t for _, t in pieces)
    found = list(office_edit.blank_with_particle(old).finditer(text))
    if not found:
        return 0
    starts = _starts(pieces)
    for match in reversed(found):
        _splice(pieces, starts, match.start(), match.end(), office_edit.put(new, match.group(1)))
    return len(found)


def _new_paragraph(like: etree._Element, text: str) -> etree._Element:
    """A paragraph with `like`'s level, bullets and first run's format."""
    paragraph = copy.deepcopy(like)
    _set_text(paragraph, text)
    return paragraph


def _set_lines(body: etree._Element, text: str) -> None:
    """The text body made `text`, one paragraph per line; lines that stay keep their
    paragraphs, changed ones are rewritten in place, new ones copy their neighbour."""
    lines = [line.strip() for line in text.split("\n")]
    paragraphs = _paragraphs(body)
    if not paragraphs:
        paragraphs = [etree.SubElement(body, _A_P)]
    old = [_para_text(p) for p in paragraphs]
    if old == lines:
        return
    result: list[etree._Element] = []
    matcher = difflib.SequenceMatcher(None, old, lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            result += paragraphs[i1:i2]
            continue
        olds, news = paragraphs[i1:i2], lines[j1:j2]
        for k in range(max(len(olds), len(news))):
            if k < len(olds) and k < len(news):
                _rewrite(olds[k], news[k])
                result.append(olds[k])
            elif k < len(news):
                like = result[-1] if result else paragraphs[min(i1, len(paragraphs) - 1)]
                result.append(_new_paragraph(like, news[k]))
    if not result:
        first = paragraphs[0]
        _set_text(first, "")
        result = [first]
    for paragraph in paragraphs:
        body.remove(paragraph)
    for paragraph in result:
        body.append(paragraph)


# ── tables ──


def _tbl(shape) -> etree._Element:
    return shape.table._tbl


def _rows(tbl: etree._Element) -> list[etree._Element]:
    return list(tbl.iterchildren(_A_TR))


def _continued(cell: etree._Element) -> bool:
    return cell.get("hMerge") in ("1", "true") or cell.get("vMerge") in ("1", "true")


def _cells(tbl: etree._Element) -> list[tuple[int, int, etree._Element]]:
    """Each cell at its row and grid column; a merge is one cell, where it starts."""
    return [
        (r, c, cell)
        for r, row in enumerate(_rows(tbl))
        for c, cell in enumerate(row.iterchildren(_A_TC))
        if not _continued(cell)
    ]


def _shape_of(tbl: etree._Element) -> tuple[int, int]:
    rows = _rows(tbl)
    return len(rows), max((len(list(r.iterchildren(_A_TC))) for r in rows), default=0)


def _merged(tbl: etree._Element) -> bool:
    return any(
        _continued(cell) or cell.get("gridSpan") or cell.get("rowSpan")
        for cell in tbl.iter(_A_TC)
    )


def _cell_body(cell: etree._Element) -> etree._Element:
    body = cell.find(_A_TXBODY)
    if body is None:
        body = etree.Element(_A_TXBODY)
        etree.SubElement(body, qn("a:bodyPr"))
        etree.SubElement(body, qn("a:lstStyle"))
        etree.SubElement(body, _A_P)
        cell.insert(0, body)
    return body


def _cell_text(cell: etree._Element) -> str:
    return _body_text(cell.find(_A_TXBODY)).strip("\n")


def _shown(tbl: etree._Element) -> list[tuple[int, int, str]]:
    """A merged table's cells named by grid column, 「c2~3: 값」, and the covered ones said."""
    out: list[tuple[int, int, str]] = []
    for r, row in enumerate(_rows(tbl)):
        for c, cell in enumerate(row.iterchildren(_A_TC)):
            span = int(cell.get("gridSpan") or 1)
            name = f"c{c}" + (f"~{c + span - 1}" if span > 1 else "")
            if cell.get("hMerge") in ("1", "true"):
                continue
            text = "(위 칸과 병합)" if cell.get("vMerge") in ("1", "true") else _cell_text(cell)
            out.append((r, c, f"{name}: {text}"))
    return out


# ── reading ──


def _notes_body(slide) -> etree._Element | None:
    if not slide.has_notes_slide:
        return None
    frame = slide.notes_slide.notes_text_frame
    return frame._txBody if frame is not None else None


def _chart_line(shape) -> str:
    try:
        chart = shape.chart
        title = chart.chart_title.text_frame.text if chart.has_title else ""
        series = len(list(chart.plots[0].series)) if len(chart.plots) else 0
    except Exception:  # noqa: BLE001 — a chart we cannot read is still a chart
        title, series = "", 0
    said = ", ".join(x for x in (f"「{title}」" if title.strip() else "",
                                 f"계열 {series}개" if series else "") if x)
    return f"(차트{', ' + said if said else ''}; 고칠 수 없음)"


def outline(data: bytes) -> str:
    """Numbered outline: each slide, its shapes `s2.1 (제목): …` with a multi-line text one
    line per paragraph, tables `s3.2 (표 4×3)` with `r0: a | b` rows, the notes, then the
    blanks."""
    deck = _index(_load(data))
    out = office_edit.Outline()
    out.line(f"슬라이드 {len(deck.slides)}장 (sN은 N번째 슬라이드: 「5장」은 s5, 슬라이드에 찍힌 "
             "쪽 번호가 아니라 차례로 셉니다)")
    layouts = [lay.name for master in deck.prs.slide_masters for lay in master.slide_layouts
               if lay.name]
    if len(layouts) > 1:
        out.line("쓸 수 있는 레이아웃: " + ", ".join(f"「{name}」" for name in layouts))
    for number, entry in enumerate(deck.slides, 1):
        said = [f"레이아웃: {entry.slide.slide_layout.name or '-'}"]
        if entry.slide._element.get("show") in ("0", "false"):
            said.insert(0, "숨긴 슬라이드")
        out.line(f"── s{number} ({', '.join(said)}) ──")
        for item in entry.items:
            mark = f" ({item.mark})" if item.mark else ""
            if item.kind == "table":
                tbl = _tbl(item.shape)
                merged = _merged(tbl)
                out.table(item.sid, _shape_of(tbl), merged, _shown(tbl) if merged else [
                    (r, c, _cell_text(cell)) for r, c, cell in _cells(tbl)
                ])
                continue
            if item.kind == "chart":
                out.line(f"{item.sid}{mark}: {_chart_line(item.shape)}")
                continue
            if item.kind == "picture":
                out.line(f"{item.sid}{mark}: (그림)")
                continue
            if item.kind == "diagram":
                out.line(f"{item.sid}{mark}: (SmartArt 도해; 고칠 수 없음)")
                continue
            lines = _shape_text(item.shape).split("\n")
            if not "".join(lines).strip():
                out.line(f"{item.sid}{mark}: (비어 있음)")
            elif len(lines) == 1:
                out.line(f"{item.sid}{mark}: {lines[0]}")
            else:
                out.line(f"{item.sid}{mark}, {len(lines)}줄:")
                for line in lines:
                    out.line(f"    {line}" if line.strip() else "    (빈 줄)")
            out.note(_shape_text(item.shape), item.sid)
        notes = _body_text(_notes_body(entry.slide)).strip()
        if notes:
            out.line(f"s{number} 메모: {notes.replace(chr(10), ' / ')}")
            out.note(notes, f"s{number} 메모")
    return out.render()


def _texts(deck: _Deck) -> list[str]:
    out: list[str] = []
    for entry in deck.slides:
        for item in entry.items:
            if item.kind == "text":
                out.append(_shape_text(item.shape))
            elif item.kind == "table":
                out += [_cell_text(cell) for _, _, cell in _cells(_tbl(item.shape))]
        out.append(_body_text(_notes_body(entry.slide)))
    return out


def blanks(data: bytes) -> list[str]:
    """Bracket blanks still in the deck, first-seen order."""
    seen: dict[str, None] = {}
    for text in _texts(_index(_load(data))):
        for found in BLANK.finditer(text):
            seen.setdefault(found.group(0))
    return list(seen)


def plain_text(data: bytes) -> str:
    """Visible text and notes, one line per paragraph (preview and search)."""
    return "\n".join(
        line for text in _texts(_index(_load(data))) for line in text.split("\n")
        if line.strip()
    )


def slide_count(data: bytes) -> int:
    return len(_load(data).slides._sldIdLst)


# ── slides ──


def _slide_ref(deck: _Deck, sid: object, *, zero: bool = False) -> int:
    """`s3` → 2; `s0` (with `zero`) → -1, before the first slide."""
    text = str(sid if sid is not None else "").strip()
    if isinstance(sid, int) or text.isdigit():
        text = f"s{text}"
    found = re.fullmatch(r"s(\d{1,4})", text)
    low = 0 if zero else 1
    if not found or not low <= int(found.group(1)) <= len(deck.slides):
        raise PptxError(
            f"슬라이드 {sid!r}가 없습니다 ({'s0~' if zero else 's1~'}s{len(deck.slides)})."
        )
    return int(found.group(1)) - 1


def _item(deck: _Deck, sid: object, kinds: tuple[str, ...] = ("text",)) -> _Item:
    text = str(sid or "").strip()
    found = re.fullmatch(r"s(\d{1,4})\.(\d{1,4})", text)
    if found:
        number = int(found.group(1))
        if 1 <= number <= len(deck.slides):
            for item in deck.slides[number - 1].items:
                if item.sid == text:
                    if item.kind not in kinds:
                        what = {"table": "표", "chart": "차트", "picture": "그림",
                                "diagram": "SmartArt 도해", "text": "글 상자"}[item.kind]
                        raise PptxError(f"{text}는 {what}입니다.")
                    return item
    raise PptxError(f"도형 {sid!r}가 없습니다 (s2.1 꼴; pptx_read가 준 번호).")


def _clone_part(slide_part, target, reltype):
    """A chart part copied with its own relations (its workbook too), for a copied slide."""
    from pptx.parts.chart import ChartPart

    package = slide_part.package
    partname = package.next_partname(ChartPart.partname_template)
    made = ChartPart.load(partname, target.content_type, package, target.blob)
    remap: dict[str, str] = {}
    for rid, rel in list(target.rels.items()):
        if rel.is_external:
            remap[rid] = made.relate_to(rel.target_ref, rel.reltype, is_external=True)
            continue
        inner = rel.target_part
        if rel.reltype == RT.PACKAGE:
            from pptx.parts.embeddedpackage import EmbeddedXlsxPart

            inner = EmbeddedXlsxPart.new(inner.blob, package)
        remap[rid] = made.relate_to(inner, rel.reltype)
    _remap(made._element, remap)
    return slide_part.relate_to(made, reltype)


def _remap(root: etree._Element, remap: dict[str, str]) -> None:
    for element in root.iter():
        for name, value in list(element.attrib.items()):
            if name.startswith("{" + _R_NS + "}") and value in remap:
                element.set(name, remap[value])


def _duplicate(deck: _Deck, index: int):
    """A copy of slide `index`, appended (its place is set by the caller)."""
    prs = deck.prs
    source = deck.slides[index].slide
    made = prs.slides.add_slide(source.slide_layout)
    tree = made.shapes._spTree
    for shape in list(made.shapes):
        tree.remove(shape._element)
    remap: dict[str, str] = {}
    for rid, rel in source.part.rels.items():
        if rel.reltype in (RT.SLIDE_LAYOUT, RT.NOTES_SLIDE):
            continue
        if rel.is_external:
            remap[rid] = made.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
        elif rel.reltype == RT.CHART:
            remap[rid] = _clone_part(made.part, rel.target_part, rel.reltype)
        else:
            remap[rid] = made.part.relate_to(rel.target_part, rel.reltype)
    source_tree = source.shapes._spTree
    for child in source_tree:
        if child.tag in (qn("p:nvGrpSpPr"), qn("p:grpSpPr")):
            continue
        tree.append(copy.deepcopy(child))
    background = source._element.cSld.find(qn("p:bg"))
    if background is not None:
        made._element.cSld.insert(0, copy.deepcopy(background))
    for name in ("p:transition", "p:timing"):
        if (extra := source._element.find(qn(name))) is not None:
            made._element.append(copy.deepcopy(extra))
    if source._element.get("show"):
        made._element.set("show", source._element.get("show"))
    _remap(made._element, remap)
    notes = _body_text(_notes_body(source))
    if notes.strip():
        _set_lines(made.notes_slide.notes_text_frame._txBody, notes)
    return made


def _place(deck: _Deck, slide, after: etree._Element | None) -> etree._Element:
    """The new slide's entry moved to right after `after` (first when None)."""
    ids = deck.prs.slides._sldIdLst
    entry = next(e for e in ids if deck.prs.part.related_part(e.rId) is slide.part)
    ids.remove(entry)
    if after is None:
        ids.insert(0, entry)
    else:
        after.addnext(entry)
    return entry


def _title_and_body(
    deck: _Deck, slide, own
) -> tuple[object | None, object | None, list[object]]:
    """The slide's visible title shape, its main text shape, and its hidden text shapes. The
    main text is the body placeholder, else the largest text box of its own (not a page
    number, not text that stands on other slides too, such as a footer, not a sliver)."""
    shown, hidden = [], []
    for shape, gone, _ in _walk(slide.shapes):
        if shape.has_text_frame:
            (hidden if gone else shown).append(shape)
    title = next((s for s in shown if _placeholder_type(s) in _TITLES), None)
    if title is None:
        names = {_shape_text(h) for h in hidden if _placeholder_type(h) in _TITLES}
        title = next((s for s in shown if _shape_text(s) in names and _shape_text(s)), None)
    if title is None:
        texts = [s for s in shown if _shape_text(s).strip()]
        title = min(texts, key=lambda s: (s.top or 0, s.left or 0), default=None)
    body = next((s for s in shown if s != title and _placeholder_type(s) in _BODIES), None)
    if body is None:
        tall = (deck.prs.slide_height or 0) * 0.12
        rest = [
            s for s in shown
            if s != title and _shape_text(s).strip() and not _shape_text(s).strip().isdigit()
            and not _repeated(deck, _shape_text(s), own) and (s.height or 0) >= tall
        ]
        body = max(rest, key=lambda s: (s.width or 0) * (s.height or 0), default=None)
    return title, body, hidden


def _has_body(deck: _Deck, index: int) -> bool:
    """Slide `index` has a place for a new slide's lines: a layout with title and body
    places, or a body of its own."""
    slide = deck.slides[index].slide
    kinds = {_placeholder_type(p) for p in slide.slide_layout.placeholders}
    if kinds & set(_TITLES) and kinds & set(_BODIES):
        return True
    return _title_and_body(deck, slide, slide)[1] is not None


def _like(deck: _Deck, after: int) -> int:
    """The slide a new one after `after` is shaped like: the nearest body slide before it,
    else after it (a cover or a table slide gives no place for lines); else the one before."""
    count = len(deck.slides)
    order = [*range(after, 0, -1), *range(after + 1, count), 0]
    return next((i for i in order if 0 <= i < count and _has_body(deck, i)),
                max(0, min(after, count - 1)))


def _set_shape(shape, text: str, hidden: list[object]) -> None:
    """A shape's text set; a hidden copy of what it said (the outline title) follows."""
    before = _shape_text(shape)
    _set_lines(shape.text_frame._txBody, text)
    for twin in hidden:
        if twin != shape and _shape_text(twin) == before and before.strip():
            _set_lines(twin.text_frame._txBody, text)


def _repeated(deck: _Deck, text: str, own: object) -> bool:
    """Text that stands on another slide as well: a footer, a deck title in the corner."""
    return any(
        entry.slide != own and any(
            item.kind == "text" and _shape_text(item.shape) == text for item in entry.items
        )
        for entry in deck.slides
    )


def _add_slide(deck: _Deck, like: int, title: str, body: str):
    """A new slide shaped like slide `like`: from its layout when that layout has a title and
    a body place, else a copy of the slide whose title and main text are replaced and whose
    other own text, tables and charts are cleared."""
    source = deck.slides[like].slide
    layout = source.slide_layout
    kinds = {_placeholder_type(p) for p in layout.placeholders}
    if kinds & set(_TITLES) and kinds & set(_BODIES):
        made = deck.prs.slides.add_slide(layout)
        title_shape = next(
            (p for p in made.placeholders if _placeholder_type(p) in _TITLES), None
        )
        body_shape = next(
            (p for p in made.placeholders if _placeholder_type(p) in _BODIES), None
        )
        for shape in list(made.placeholders):
            if shape != title_shape and shape != body_shape:
                shape._element.getparent().remove(shape._element)
        if title_shape is not None:
            title_shape.text_frame.text = title
        if body_shape is not None:
            if body.strip():
                _set_lines(body_shape.text_frame._txBody, body)
            else:
                body_shape._element.getparent().remove(body_shape._element)
        return made
    made = _duplicate(deck, like)
    title_shape, body_shape, hidden = _title_and_body(deck, made, source)
    #: where a dropped table or chart stood: a body with nowhere else to go goes there
    room: tuple[int, int, int, int] | None = None
    for shape, gone, _ in list(_walk(made.shapes)):
        if gone:
            continue
        if (getattr(shape, "has_table", False) and shape.has_table) or (
            getattr(shape, "has_chart", False) and shape.has_chart
        ):
            room = room or (shape.left, shape.top, shape.width, shape.height)
            shape._element.getparent().remove(shape._element)
        elif shape.has_text_frame and shape != title_shape and shape != body_shape:
            text = _shape_text(shape).strip()
            if text and not text.isdigit() and not _repeated(deck, _shape_text(shape), source):
                _set_lines(shape.text_frame._txBody, "")
    if title_shape is not None:
        _set_shape(title_shape, title, hidden)
    if body_shape is not None:
        _set_shape(body_shape, body, hidden)
    elif body.strip():
        if room is None:
            width, height = deck.prs.slide_width or 0, deck.prs.slide_height or 0
            top = (title_shape.top + title_shape.height) if title_shape is not None else 0
            room = (int(width * 0.08), int(top + height * 0.05), int(width * 0.84),
                    int(height * 0.55))
        box = made.shapes.add_textbox(*(Emu(v) for v in room))
        box.text_frame.word_wrap = True
        box.text_frame.text = body.replace("\r", "")
        for paragraph in box.text_frame.paragraphs:
            for run in paragraph.runs:
                run.font.size = Pt(18)
    notes = _notes_body(made)
    if notes is not None:
        _set_lines(notes, "")
    return made


# ── edits ──


def _places(deck: _Deck, at: object) -> list[etree._Element]:
    """`s2` (a slide, its notes too), `s2.3`, `s3.2 r1c2` or `s2 메모`: the paragraphs one
    fill is kept to."""
    text = str(at or "").strip()
    if found := re.fullmatch(r"(s\d{1,4}\.\d{1,4})\s{0,2}r(\d{1,4})c(\d{1,4})", text):
        tbl = _tbl(_item(deck, found.group(1), ("table",)).shape)
        return list(_cell(tbl, found.group(2), found.group(3)).iter(_A_P))
    if found := re.fullmatch(r"(s\d{1,4})\s{0,2}\.?\s{0,2}(?:메모|notes?)", text, re.I):
        body = _notes_body(deck.slides[_slide_ref(deck, found.group(1))].slide)
        return _paragraphs(body) if body is not None else []
    if re.fullmatch(r"s\d{1,4}\.\d{1,4}", text):
        item = _item(deck, text, ("text", "table"))
        if item.kind == "table":
            return list(_tbl(item.shape).iter(_A_P))
        entry = next(e for e in deck.slides if item in e.items)
        out = list(item.shape.text_frame._txBody.iter(_A_P))
        before = _shape_text(item.shape)
        for twin in entry.hidden:
            if _shape_text(twin) == before:
                out += list(twin.text_frame._txBody.iter(_A_P))
        return out
    return _slide_paragraphs(deck.slides[_slide_ref(deck, text)])


def _slide_paragraphs(entry: _Slide) -> list[etree._Element]:
    out = list(entry.slide.shapes._spTree.iter(_A_P))
    notes = _notes_body(entry.slide)
    return out + (_paragraphs(notes) if notes is not None else [])


def _everywhere(deck: _Deck) -> list[etree._Element]:
    return [p for entry in deck.slides for p in _slide_paragraphs(entry)]


def _cell(tbl: etree._Element, row: object, col: object) -> etree._Element:
    try:
        r, c = int(row), int(col)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PptxError("row·col은 0부터 세는 숫자입니다.") from exc
    for at_r, at_c, cell in _cells(tbl):
        if (at_r, at_c) == (r, c):
            return cell
    rows, cols = _shape_of(tbl)
    raise PptxError(f"r{r}c{c} 칸이 없습니다 (표 {rows}×{cols}, 0부터; 병합된 칸은 시작 칸으로).")


def _row(tbl: etree._Element, at: object) -> tuple[int, list[etree._Element]]:
    if any(cell.get("rowSpan") or cell.get("vMerge") for cell in tbl.iter(_A_TC)):
        raise PptxError("세로로 병합된 칸이 있는 표는 줄을 넣거나 뺄 수 없습니다.")
    rows = _rows(tbl)
    try:
        index = int(at)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PptxError("row는 0부터 세는 숫자입니다.") from exc
    if not 0 <= index < len(rows):
        raise PptxError(f"r{index} 줄이 없습니다 (0~{len(rows) - 1}).")
    return index, rows


def _grow(shape, by: int) -> None:
    """The table's frame taller or shorter by a row's height."""
    try:
        shape.height = Emu(max(0, int(shape.height or 0) + by))
    except (AttributeError, ValueError):
        pass


def _text_arg(op: dict, name: str = "text") -> str:
    return office_edit.text_arg(op, name, PptxError)


def _lines_arg(op: dict, name: str = "text") -> str:
    """Text as lines: a list of lines is taken as given."""
    value = op.get(name)
    if value is None and name == "text":
        # a model that fills cells as it fills blanks: 「value」 for 「text」
        value = op.get("value")
    if value is None:
        given = ", ".join(str(k) for k in op if k != "op") or "없음"
        raise PptxError(
            f"{op.get('op')}: {name}이 없습니다 (넣을 글; 받은 키: {given}). "
            f"{op.get('op')} 객체 안에 \"{name}\": \"…\"로 주세요."
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if isinstance(value, list) and all(isinstance(v, (str, int, float)) for v in value):
        value = "\n".join(str(v) for v in value)
    return office_edit.text_arg({**op, name: value}, name, PptxError)


def apply(data: bytes, ops: list[dict],
          pictures: dict[str, tuple[str, bytes]] | None = None) -> Result:
    """Every op or none: targets resolve against `data` before anything changes. Tables,
    pictures and layouts are `pptx_ops`'s; `pictures` the image files they name."""
    from app.services import pptx_ops

    office_edit.check_ops(ops, PptxError)
    deck = _index(_load(data))
    ids = deck.prs.slides._sldIdLst
    entries = list(ids)
    plan: list[tuple[dict, object]] = []
    for op in ops:
        if not isinstance(op, dict):
            raise PptxError("op마다 {\"op\": ...} 객체여야 합니다.")
        kind = op.get("op")
        if kind == "replace_text":
            item = _item(deck, op.get("id"), ("text", "table"))
            if item.kind == "table":
                raise PptxError(f"{op.get('id')}는 표입니다. 칸은 set_cell로 고치세요.")
            entry = next(e for e in deck.slides if item in e.items)
            plan.append((op, (item, entry)))
            _lines_arg(op)
        elif kind == "fill_blank":
            blank = str(op.get("blank") or "").strip()
            if not BLANK.fullmatch(blank):
                raise PptxError(f"fill_blank: {blank!r}은 「[이름]」 꼴 빈칸이 아닙니다.")
            _text_arg(op, "value")
            places = _places(deck, op.get("at")) if op.get("at") else _everywhere(deck)
            if not any(blank in _para_text(p) for p in places):
                where = f" {op.get('at')}에" if op.get("at") else ""
                raise PptxError(f"fill_blank:{where} {blank}이 없습니다.")
            plan.append((op, places))
        elif kind == "set_cell":
            tbl = _tbl(_item(deck, op.get("table"), ("table",)).shape)
            plan.append((op, _cell(tbl, op.get("row"), op.get("col"))))
            _lines_arg(op)
        elif kind == "set_notes":
            plan.append((op, deck.slides[_slide_ref(deck, op.get("slide", op.get("id")))]))
            _lines_arg(op)
        elif kind in ("insert_table_row", "delete_table_row"):
            item = _item(deck, op.get("table"), ("table",))
            tbl = _tbl(item.shape)
            index, rows = _row(tbl, op.get("after" if kind == "insert_table_row" else "row"))
            if kind == "delete_table_row" and len(rows) < 2:
                raise PptxError("줄이 하나뿐인 표의 줄은 지울 수 없습니다.")
            plan.append((op, (item.shape, rows[index])))
        elif kind == "add_slide":
            after = _slide_ref(deck, op.get("after", f"s{len(deck.slides)}"), zero=True)
            if op.get("like") not in (None, ""):
                like = _slide_ref(deck, op.get("like"))
                # a table or cover slide named as the model: the nearest body slide to it
                like = like if _has_body(deck, like) else _like(deck, like)
            else:
                like = _like(deck, after)
            title = _text_arg(op, "title") if op.get("title") is not None else ""
            body = _lines_arg(op, "body") if op.get("body") is not None else ""
            plan.append((op, (entries[after] if after >= 0 else None, like, title, body)))
        elif kind == "duplicate_slide":
            index = _slide_ref(deck, op.get("id"))
            after = (_slide_ref(deck, op.get("after"), zero=True)
                     if op.get("after") not in (None, "") else index)
            plan.append((op, (index, entries[after] if after >= 0 else None)))
        elif kind == "delete_slide":
            index = _slide_ref(deck, op.get("id"))
            plan.append((op, entries[index]))
        elif kind == "move_slide":
            index = _slide_ref(deck, op.get("id"))
            after = _slide_ref(deck, op.get("after"), zero=True)
            plan.append((op, (entries[index], entries[after] if after >= 0 else None)))
        elif kind in pptx_ops.OPS:
            plan.append((op, pptx_ops.resolve(deck, op, pictures or {})))
        else:
            raise PptxError(f"모르는 op {kind!r}: {', '.join(OPS + pptx_ops.OPS)} 중 하나.")
    deleting = sum(1 for op, _ in plan if op["op"] == "delete_slide")
    adding = sum(1 for op, _ in plan if op["op"] in ("add_slide", "duplicate_slide"))
    if len(deck.slides) + adding - deleting < 1:
        raise PptxError("슬라이드가 하나도 남지 않습니다.")
    if len(deck.slides) + adding > MAX_SLIDES:
        raise PptxError(f"슬라이드는 {MAX_SLIDES}장까지입니다.")

    changes: list[str] = []
    #: per anchor, the last slide put after it, so several land in the order given
    placed: dict[int, etree._Element] = {}

    def put_after(slide, anchor: etree._Element | None) -> None:
        key = id(anchor)
        placed[key] = _place(deck, slide, placed.get(key, anchor))

    for op, target in plan:
        kind = op["op"]
        if kind == "replace_text":
            item, entry = target
            before = _shape_text(item.shape)
            _set_shape(item.shape, _lines_arg(op), entry.hidden)
            changes.append(f"{op['id']}: 「{short(before)}」 → 「{short(_lines_arg(op))}」")
        elif kind == "fill_blank":
            blank, value = op["blank"].strip(), " ".join(_text_arg(op, "value").split("\n"))
            count = sum(_replace_in(p, blank, value) for p in target)
            where = f"{op['at']}: " if op.get("at") else ""
            changes.append(f"{where}{blank} → 「{short(value)}」 ({count}곳)")
        elif kind == "set_cell":
            _set_lines(_cell_body(target), _lines_arg(op))
            changes.append(f"{op['table']} r{op['row']}c{op['col']} → 「{short(_lines_arg(op))}」")
        elif kind == "set_notes":
            _set_lines(target.slide.notes_slide.notes_text_frame._txBody, _lines_arg(op))
            number = deck.slides.index(target) + 1
            changes.append(f"s{number} 메모 → 「{short(_lines_arg(op))}」")
        elif kind == "insert_table_row":
            shape, row = target
            fresh = copy.deepcopy(row)
            for cell in fresh.iterchildren(_A_TC):
                _set_lines(_cell_body(cell), "")
            row.addnext(fresh)
            _grow(shape, int(row.get("h") or 0))
            changes.append(f"{op['table']} r{op['after']} 아래에 줄 추가")
        elif kind == "delete_table_row":
            shape, row = target
            row.getparent().remove(row)
            _grow(shape, -int(row.get("h") or 0))
            changes.append(f"{op['table']} r{op['row']} 줄 삭제")
        elif kind == "add_slide":
            anchor, like, title, body = target
            made = _add_slide(deck, like, title, body)
            put_after(made, anchor)
            changes.append(f"슬라이드 추가: 「{short(title or '(제목 없음)')}」")
        elif kind == "duplicate_slide":
            index, anchor = target
            made = _duplicate(deck, index)
            put_after(made, anchor)
            changes.append(f"{op['id']} 복제")
        elif kind == "delete_slide":
            entry = target
            rid = entry.rId
            deck.prs.part.drop_rel(rid)
            entry.getparent().remove(entry)
            changes.append(f"{op['id']} 삭제")
        elif kind == "move_slide":
            entry, anchor = target
            if entry is not anchor:
                entry.getparent().remove(entry)
                if anchor is None:
                    ids.insert(0, entry)
                else:
                    anchor.addnext(entry)
            where = "맨 앞으로" if anchor is None else f"{op['after']} 뒤로"
            changes.append(f"{op['id']}를 {where} 옮김")
        elif kind in pptx_ops.OPS:
            changes.append(pptx_ops.run(deck, op, target))
    return Result(_save(deck.prs), changes)


def written_bullets_only(data: bytes) -> bytes:
    """No automatic bullet the deck did not ask for. The house exporter marks a slide's body
    box as the body placeholder (for the outline view) and sets each bullet itself (`a:buChar`);
    on its blank layout a placeholder paragraph that sets none falls back to the master's
    bulleted body style, so PowerPoint and the editor would draw a bullet before a plain line.
    Each paragraph of a body placeholder that sets no bullet of its own gets `a:buNone`."""
    prs = _load(data)
    changed = False
    for slide in prs.slides:
        for shape, _, _ in _walk(slide.shapes):
            if _placeholder_type(shape) not in _BODIES or not shape.has_text_frame:
                continue
            for paragraph in _paragraphs(shape.text_frame._txBody):
                props = paragraph.find(_A_PPR)
                if props is None:
                    props = etree.Element(_A_PPR)
                    paragraph.insert(0, props)
                if any(props.find(qn(f"a:{tag}")) is not None
                       for tag in ("buNone", "buChar", "buAutoNum", "buBlip")):
                    continue
                none = etree.Element(qn("a:buNone"))
                # schema order: spacing, then bullet colour/size/font, then the bullet
                anchor = next((c for c in props if c.tag in (
                    qn("a:tabLst"), qn("a:defRPr"), qn("a:extLst"))), None)
                if anchor is not None:
                    anchor.addprevious(none)
                else:
                    props.append(none)
                changed = True
    return _save(prs) if changed else data


# ── a blank deck ──


def _no_shrink(root) -> None:
    """Every `a:normAutofit` under `root` becomes `a:noAutofit` (same place in `a:bodyPr`)."""
    for fit in list(root.iter(qn("a:normAutofit"))):
        fit.addprevious(fit.makeelement(qn("a:noAutofit"), {}))
        fit.getparent().remove(fit)


def blank_deck() -> bytes:
    """One empty title slide, 16:9, on python-pptx's default template stretched to 16:9 (its
    masters and layouts are drawn for 4:3), Korean text in 맑은 고딕."""
    prs = Presentation()
    factor = 16 / 9 / (prs.slide_width / prs.slide_height)
    prs.slide_width = Emu(int(prs.slide_width * factor))
    for root in [prs.slide_master._element, *(lay._element for lay in prs.slide_layouts)]:
        for off in root.iter(qn("a:off")):
            off.set("x", str(int(int(off.get("x", "0")) * factor)))
        for ext in root.iter(qn("a:ext")):
            if ext.get("cx") is not None:
                ext.set("cx", str(int(int(ext.get("cx", "0")) * factor)))
    # The default template's text places shrink their text to fit (`a:normAutofit`): the
    # editor would then shrink a whole box as soon as a line is added. Fixed boxes here; the
    # house writer's own overflow fallback (`deck_export._autofit`) is the only shrink.
    for root in [prs.slide_master._element, *(lay._element for lay in prs.slide_layouts)]:
        _no_shrink(root)
    theme = prs.slide_master.part.part_related_by(RT.THEME)
    root = etree.fromstring(theme.blob)
    for ea in root.iter(qn("a:ea")):
        ea.set("typeface", "맑은 고딕")
    theme._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    prs.slides.add_slide(prs.slide_layouts[0])
    return _save(prs)
