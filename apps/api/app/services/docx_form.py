"""A report written on a Word form (양식): the form's styles, numbering, header and footer, page
and cover kept, its chapter headings kept as the report's chapters, and its guidance gone.

How: the form's body is set aside and the report's body is written into the emptied form by the
house writer in its form mode (`report_export.to_docx(form_mode=True)`): every paragraph takes
the form's own styles (a style the house writer names and the form lacks is added on the form's
look). The form's body comes back, and under each of its chapter headings (heading 1) the body
of the report's chapter of the same name replaces what the form had there (작성요령, sample
lines, empty paragraphs). A chapter the form lacks is added at the end in the form's heading
style. The cover before the first chapter is kept, its label cells filled where the report's
head gives a value. A form with no chapter headings gets the report after its own content.

`write` returns the file and what was followed, for the answer's 「양식을 따름」 line.
"""

from __future__ import annotations

import copy
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.services import form_profile

log = logging.getLogger(__name__)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
#: the house writer's paragraph styles: a form without one gets it, on the form's look
_HOUSE_PARAGRAPH = ("Body Text", "List Bullet", "List Bullet 2", "List Bullet 3", "Caption")
#: a chapter heading's own number (「1.」, 「Ⅰ.」, 「제1장」, 「가.」), set aside to compare names
_NUMBER = re.compile(r"^\s{0,4}(?:제\s{0,2}\d{1,3}\s{0,2}[장절]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,4}\.?|"
                     r"\d{1,3}(?:\.\d{1,3}){0,3}\.?|[가-하]\.|\(\d{1,3}\)|[①-⑳])\s{0,3}")
#: label cell → the report head field it takes
_LABELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:제\s{0,2}목|사\s{0,2}업\s{0,2}명|과\s{0,2}제\s{0,2}명|보고서\s{0,2}명|"
                r"건\s{0,2}명|문서\s{0,2}명)$"), "title"),
    (re.compile(r"^(?:작\s{0,2}성\s{0,2}일(?:자)?|일\s{0,2}자|날\s{0,2}짜|보고\s{0,2}일)$"),
     "date"),
)


class FormWriteError(ValueError):
    """The form could not be followed; the message is shown to the person."""


@dataclass
class Followed:
    #: form chapters the report's chapters went under
    chapters: list[str] = field(default_factory=list)
    #: report chapters the form has no place for, added at the end
    added: list[str] = field(default_factory=list)
    #: form chapters the report did not write (their heading kept, their guidance gone)
    unwritten: list[str] = field(default_factory=list)
    #: cover cells filled from the report's head
    filled: list[str] = field(default_factory=list)
    guidance_removed: int = 0
    header: bool = False
    footer: bool = False
    numbered: bool = False

    def wire(self) -> dict[str, Any]:
        return {
            "chapters": list(self.chapters), "added": list(self.added),
            "unwritten": list(self.unwritten), "filled": list(self.filled),
            "guidanceRemoved": self.guidance_removed, "header": self.header,
            "footer": self.footer, "numbered": self.numbered,
        }


def _q(tag: str) -> str:
    return f"{{{_W}}}{tag}"


def _text(element) -> str:
    return "".join(t.text or "" for t in element.iter(_q("t")))


def _style_of(paragraph) -> str:
    style = paragraph.find(f"{_q('pPr')}/{_q('pStyle')}")
    return style.get(_q("val")) if style is not None else ""


def name_key(text: str) -> str:
    """A chapter name to compare: its number, spaces and punctuation set aside."""
    bare = _NUMBER.sub("", text or "", count=1)
    return re.sub(r"[\s·・,./()\[\]「」『』:：-]+", "", bare).lower()


def _ensure_styles(document, profile: dict) -> tuple[dict[str, str], set[str]]:
    """Styles the house writer names, added on the form's look where the form lacks them;
    returns (house heading style id → the form's heading style id, the ids a chapter heading
    has: the form's level-1 style and the house one)."""
    from docx.enum.style import WD_STYLE_TYPE

    styles = document.styles
    names = {style.name for style in styles}
    headings = profile.get("headings") or {}
    by_id = {style.style_id: style for style in styles}
    remap: dict[str, str] = {}
    ones: set[str] = set()
    normal = styles["Normal"]
    for level in (1, 2, 3):
        own = (headings.get(str(level)) or {}).get("style") or ""
        name = f"Heading {level}"
        if name in names:
            house = styles[name]
        else:
            house = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
            house.base_style = by_id.get(own) or normal
        if own and own != house.style_id and own in by_id:
            remap[house.style_id] = own
        if level == 1:
            ones |= {house.style_id, *([own] if own else [])}
    for name in _HOUSE_PARAGRAPH:
        if name not in names:
            made = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
            made.base_style = normal
    if "Table Grid" not in names:
        grid = styles.add_style("Table Grid", WD_STYLE_TYPE.TABLE)
        found = next((s for s in styles if s.type == WD_STYLE_TYPE.TABLE
                      and s.style_id not in ("TableNormal", "a1")), None)
        if found is not None:
            grid.base_style = found
    return remap, ones


def _guidance(paragraph) -> bool:
    text = _text(paragraph).strip()
    return not text or bool(form_profile._GUIDANCE.search(text)) or bool(
        form_profile._FILL.fullmatch(text))


def _fill_cover(cover: list, fields: dict[str, str], followed: Followed) -> None:
    """Label cells of the cover tables take the report head's value beside them."""
    for element in cover:
        if element.tag != _q("tbl"):
            continue
        for row in element.iter(_q("tr")):
            cells = list(row.iter(_q("tc")))
            for label, value in zip(cells, cells[1:], strict=False):
                key = re.sub(r"\s+", "", _text(label))
                wanted = next((field_key for pattern, field_key in _LABELS
                               if pattern.match(_text(label).strip())), key)
                fill = fields.get(wanted) or fields.get(key)
                if not fill or _text(value).strip() and not form_profile._FILL.fullmatch(
                        _text(value).strip()):
                    continue
                paragraph = value.find(_q("p"))
                if paragraph is None:
                    continue
                for run in list(paragraph.findall(_q("r"))):
                    paragraph.remove(run)
                run = copy.deepcopy(label.find(f".//{_q('r')}")) if label.find(
                    f".//{_q('r')}") is not None else None
                if run is None:
                    from lxml import etree

                    run = etree.SubElement(paragraph, _q("r"))
                else:
                    for child in list(run):
                        if child.tag != _q("rPr"):
                            run.remove(child)
                    paragraph.append(run)
                text = run.makeelement(_q("t"), {})
                text.text = fill
                run.append(text)
                followed.filled.append(_text(label).strip())


def head_fields(title: str, title_block: dict | None) -> dict[str, str]:
    """The report head's values by label (and `title`, `date`)."""
    out = {"title": title}
    block = title_block or {}
    fields = block.get("fields") or []
    # `[[label, value], …]` as `doc_formats.title_block` writes them, or a mapping
    pairs = fields.items() if isinstance(fields, dict) else (
        tuple(pair) for pair in fields if isinstance(pair, (list, tuple)) and len(pair) == 2)
    for key, value in pairs:
        if isinstance(value, str) and value.strip() and not form_profile._FILL.fullmatch(
                value.strip()):
            key = re.sub(r"\s+", "", str(key))
            out[key] = value.strip()
            for pattern, name in _LABELS:
                if pattern.match(key):
                    out.setdefault(name, value.strip())
    for key in ("date", "작성일", "일자"):
        if isinstance(block.get(key), str) and block[key].strip():
            out["date"] = block[key].strip()
    return out


def write(form: bytes, title: str, sections: list[dict], *, title_block: dict | None = None,
          profile: dict | None = None, name: str = "form.docx",
          charts_as_pictures: bool = True) -> tuple[bytes, Followed]:
    """The report's `sections` written on the Word `form`."""
    from docx import Document

    from app.services import report_export

    found = profile or form_profile.profile(name, form)
    if not found or found.get("kind") != "docx" or found.get("error"):
        raise FormWriteError((found or {}).get("error") or "Word 양식이 아닙니다.")
    try:
        document = Document(io.BytesIO(form_profile.as_document(form)))
    except Exception as exc:  # noqa: BLE001 — any reader failure is an unreadable form
        raise FormWriteError("Word 양식을 열지 못했습니다.") from exc
    body = document.element.body
    original = [element for element in body if element.tag != _q("sectPr")]
    for element in original:
        body.remove(element)
    remap, ones = _ensure_styles(document, found)
    emptied = io.BytesIO()
    document.save(emptied)
    written = report_export.to_docx(
        title, sections, template=emptied.getvalue(), title_block=title_block,
        charts_as_pictures=charts_as_pictures, static_toc=True, form_mode=True)
    result = Document(io.BytesIO(written))
    body = result.element.body
    end = body.find(_q("sectPr"))
    made = [element for element in body if element.tag != _q("sectPr")]
    for element in made:
        body.remove(element)
    # the house writer's headings in the form's heading styles
    for paragraph in (e for e in made if e.tag == _q("p")):
        style = paragraph.find(f"{_q('pPr')}/{_q('pStyle')}")
        if style is not None and style.get(_q("val")) in remap:
            style.set(_q("val"), remap[style.get(_q("val"))])
    form_one = (found.get("headings") or {}).get("1", {}).get("style") or ""

    def chapters(elements: list, ones: set[str]) -> tuple[list, list[tuple[Any, list]]]:
        """(before the first chapter, [(chapter heading, its elements)])."""
        before: list = []
        out: list[tuple[Any, list]] = []
        for element in elements:
            if element.tag == _q("p") and _style_of(element) in ones and _text(element).strip():
                out.append((element, []))
            elif out:
                out[-1][1].append(element)
            else:
                before.append(element)
        return before, out

    followed = Followed(header=bool(found.get("header")), footer=bool(found.get("footer")),
                        numbered=bool(found.get("numbered_headings")))
    cover, slots = chapters(original, ones)
    lead, written_chapters = chapters(made, ones)
    by_name: dict[str, tuple[Any, list]] = {}
    for heading, content in written_chapters:
        by_name.setdefault(name_key(_text(heading)), (heading, content))
    used: set[str] = set()

    def put(element) -> None:
        if end is not None:
            end.addprevious(element)
        else:
            body.append(element)

    _fill_cover(cover, head_fields(title, title_block), followed)
    for element in cover:
        put(element)
    if not slots:
        # a form with no chapters: the report after the form's own content
        for element in lead:
            put(element)
        for heading, content in written_chapters:
            put(heading)
            for element in content:
                put(element)
            followed.added.append(_text(heading).strip())
    else:
        for element in lead:
            put(element)
        for order, (heading, content) in enumerate(slots):
            key = name_key(_text(heading))
            match = by_name.get(key)
            if match is None and order < len(written_chapters) and \
                    name_key(_text(written_chapters[order][0])) not in {
                        name_key(_text(h)) for h, _ in slots}:
                match = written_chapters[order]
            put(heading)
            if match is None:
                followed.unwritten.append(_text(heading).strip())
                followed.guidance_removed += sum(1 for e in content if e.tag == _q("p")
                                                 and _guidance(e) and _text(e).strip())
                for element in content:
                    if not (element.tag == _q("p") and _guidance(element)):
                        put(element)
                continue
            used.add(name_key(_text(match[0])))
            followed.chapters.append(_text(heading).strip())
            followed.guidance_removed += sum(1 for e in content if e.tag == _q("p")
                                             and _text(e).strip())
            for element in match[1]:
                put(element)
        for heading, content in written_chapters:
            if name_key(_text(heading)) in used:
                continue
            style = heading.find(f"{_q('pPr')}/{_q('pStyle')}")
            if style is not None and form_one:
                style.set(_q("val"), form_one)
            # the form numbers its own chapters: the house number goes; typed numbers
            # (「1. 사업 개요」) run on
            _drop_number(heading)
            if typed := _typed_next(slots, len(followed.added)):
                _prefix(heading, typed)
            put(heading)
            for element in content:
                put(element)
            followed.added.append(_text(heading).strip())
    out = io.BytesIO()
    result.save(out)
    return out.getvalue(), followed


_TYPED = re.compile(r"^\s{0,4}(\d{1,3})\.\s")


def _typed_next(slots: list, added: int) -> str:
    """「4. 」 when every form chapter heading types its number (「1. 」…「3. 」), else ``."""
    numbers = [_TYPED.match(_text(heading)) for heading, _ in slots]
    if not numbers or not all(numbers):
        return ""
    return f"{int(numbers[-1].group(1)) + 1 + added}. "


def _prefix(paragraph, text: str) -> None:
    for node in paragraph.iter(_q("t")):
        node.text = text + (node.text or "")
        return


def _drop_number(paragraph) -> None:
    """A chapter heading's typed number taken off (the form's numbering gives one)."""
    for text in paragraph.iter(_q("t")):
        if text.text:
            text.text = _NUMBER.sub("", text.text, count=1)
            break
