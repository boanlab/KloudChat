"""A `.pptx` KloudChat writes has real bullets: `a:buChar` in the accent, the text hanging
after it (a wrapped line starts under the text, not under the bullet), and the text in its own
colour, with no 「• 」 run before it. A deck written the old way, its bullet a run of its own,
heals when it is read or edited. A pattern slide's text too long for its box is written whole,
shrunk to fit, never cut with the `.pdf`'s ellipsis."""

from __future__ import annotations

import copy
import io
import json

from lxml import etree
from pptx import Presentation
from pptx.oxml.ns import qn

from app.services import deck_export, pptx_doc, pptx_edit, slide_patterns

LONG = (
    "업무 처리 시간이 부서별로 최대 3배까지 차이가 나서 결재가 늦어지고 고객 응대가 "
    "지연되는 일이 반복되고 있으며, 이를 줄이기 위한 절차 표준화가 필요하다"
)


def _prs(data: bytes):
    return Presentation(io.BytesIO(data))


def _paragraphs_with(data: bytes, words: str) -> list:
    return [
        paragraph
        for slide in _prs(data).slides
        for shape in slide.shapes
        if shape.has_text_frame
        for paragraph in shape.text_frame.paragraphs
        if words in paragraph.text
    ]


def _colour(run) -> str:
    return str(run.font.color.rgb)


def _assert_real_bullet(paragraph, accent: str, char: str = "•") -> None:
    props = paragraph._p.find(qn("a:pPr"))
    assert props.find(qn("a:buNone")) is None
    assert props.find(qn("a:buChar")).get("char") == char
    assert props.find(qn("a:buClr")).find(qn("a:srgbClr")).get("val") == accent
    margin, indent = int(props.get("marL")), int(props.get("indent"))
    assert margin > 0 and indent == -margin
    # one text, no bullet written as a run of its own, and not in the accent
    assert paragraph.runs and paragraph.runs[0].text.strip()[:1] not in "•·—-"
    assert all(_colour(run) != accent for run in paragraph.runs)


def old_style(data: bytes) -> bytes:
    """The deck as the house exporter wrote it before: each bullet a bold 「• 」 run in the
    bullet's colour, `a:buNone`, no hanging indent."""
    prs = _prs(data)
    for slide in prs.slides:
        for paragraph in slide._element.iter(qn("a:p")):
            props = paragraph.find(qn("a:pPr"))
            mark = props.find(qn("a:buChar")) if props is not None else None
            if mark is None:
                continue
            colour = props.find(qn("a:buClr"))
            first = paragraph.find(qn("a:r"))
            run = copy.deepcopy(first)
            run.find(qn("a:t")).text = f"{mark.get('char')} "
            rpr = run.find(qn("a:rPr"))
            rpr.set("b", "1")
            fill = rpr.find(qn("a:solidFill"))
            for child in list(fill):
                fill.remove(child)
            fill.append(copy.deepcopy(colour[0]))
            first.addprevious(run)
            for child in (colour, mark):
                props.remove(child)
            del props.attrib["marL"], props.attrib["indent"]
            none = etree.SubElement(props, qn("a:buNone"))
            props.remove(none)
            props.append(none)
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def test_a_written_deck_has_real_hanging_bullets_in_the_accent():
    data = pptx_doc.deck("사업 계획 보고", "[부서명]", [
        {"title": "추진 배경", "bullets": [LONG, "고객 [고객 수]명"]},
    ], closing="감사합니다")
    paragraphs = _paragraphs_with(data, "업무 처리") + _paragraphs_with(data, "고객 [고객")
    assert len(paragraphs) == 2
    accent = str(paragraphs[0]._p.find(qn("a:pPr")).find(qn("a:buClr"))[0].get("val"))
    for paragraph in paragraphs:
        _assert_real_bullet(paragraph, accent)
    assert paragraphs[0].text == LONG
    # the outline reads the words, the bullet is the paragraph's
    assert "    업무 처리" in pptx_edit.outline(data)


def test_a_web_deck_exports_real_bullets_in_each_column_and_on_the_closing_slide():
    data = deck_export.to_pptx("덱", [
        {"layout": "two-column", "title": "둘", "accent": "#3B5BDB",
         "bullets": [LONG, "둘째", "셋째", "넷째", "다섯째"]},
        {"layout": "closing", "title": "마무리", "accent": "#3B5BDB",
         "bullets": ["다음 회의 [일정]", "질문은 [담당자]에게"]},
    ])
    for words in (LONG, "다섯째"):
        _assert_real_bullet(_paragraphs_with(data, words)[0], "3B5BDB")
    closing = _paragraphs_with(data, "다음 회의")[0]
    props = closing._p.find(qn("a:pPr"))
    assert props.find(qn("a:buChar")).get("char") == "—"
    assert int(props.get("indent")) < 0 and closing.runs[0].text == "다음 회의 [일정]"


def test_an_old_deck_with_written_bullets_heals_when_read_and_edited():
    new = pptx_doc.deck("보고", "", [{"title": "배경", "bullets": [LONG, "둘"]}])
    old = old_style(new)
    first = _paragraphs_with(old, "업무 처리")[0]
    assert first.runs[0].text == "• "
    assert first._p.find(qn("a:pPr")).find(qn("a:buNone")) is not None
    # read: the outline is of the healed deck
    assert "• 업무" not in pptx_edit.outline(old)
    # any edit saves it healed, the edited line too, written with its bullet or without
    edited = pptx_edit.apply(old, [
        {"op": "replace_text", "id": "s2.2", "text": f"• {LONG}\n둘째 줄\n• 셋"},
    ]).data
    accent = str(_paragraphs_with(new, "업무 처리")[0]._p
                 .find(qn("a:pPr")).find(qn("a:buClr"))[0].get("val"))
    texts = [p.text for p in _paragraphs_with(edited, "") if p.text in (LONG, "둘째 줄", "셋")]
    assert texts == [LONG, "둘째 줄", "셋"]
    for words in (LONG, "둘째 줄", "셋"):
        _assert_real_bullet(_paragraphs_with(edited, words)[0], accent)


def test_a_dash_that_is_a_sign_is_not_taken_for_a_bullet():
    prs = Presentation()
    box = prs.slides.add_slide(prs.slide_layouts[6]).shapes.add_textbox(0, 0, 3000000, 900000)
    paragraph = box.text_frame.paragraphs[0]
    for text in ("-", "5% 감소"):
        paragraph.add_run().text = text
    assert pptx_edit.heal_written_bullets(prs) == 0
    assert paragraph.text == "-5% 감소"
    # a lone bullet sign, or a dash with its space, is one
    paragraph.runs[0].text = "- "
    assert pptx_edit.heal_written_bullets(prs) == 1
    assert paragraph.text == "5% 감소"


def _long(value):
    if isinstance(value, str):
        return f"{value} — {LONG}" if value else value
    if isinstance(value, list):
        return [_long(v) for v in value]
    if isinstance(value, dict):
        return {k: (_long(v) if k in ("items", "title", "body") else v) for k, v in value.items()}
    return value


def test_a_pattern_slide_writes_its_overflowing_text_whole_and_shrunk():
    slides, wanted = [], []
    for pattern in slide_patterns.PATTERNS:
        if pattern.shape == "chart":
            continue
        example = json.loads(pattern.example.replace("{{", "{").replace("}}", "}"))
        slide = {"title": pattern.label, "layout": pattern.name, "accent": "#3b5bdb"}
        for key in ("items", "columns", "body"):
            if key in example:
                slide[key] = _long(example[key])
        for key, value in example.items():
            slide.setdefault(key, value)
        slides.append(slide)
        words = [w for pair in slide.get("items") or [] for w in pair if isinstance(w, str)]
        words += [w for c in slide.get("columns") or [] for w in c.get("items") or []]
        wanted.append((pattern.name, [w for w in words if LONG in w]))
    data = deck_export.to_pptx("패턴", slides)
    shrunk = 0
    for (name, words), slide in zip(wanted, _prs(data).slides, strict=True):
        text = "\n".join(
            p.text for s in slide.shapes if s.has_text_frame for p in s.text_frame.paragraphs
        )
        assert "…" not in text, f"{name}: a display ellipsis was written"
        for word in words:
            assert word in text, f"{name}: {word[:20]!r} is not written whole"
        shrunk += len(slide._element.findall(".//" + qn("a:normAutofit")))
    # the text that did not fit asks PowerPoint to fit it
    assert shrunk
    # the .pdf still draws what fits
    assert deck_export.to_pdf("패턴", slides).startswith(b"%PDF")


def test_healing_is_the_deck_itself_when_there_is_nothing_to_heal():
    new = pptx_doc.deck("보고", "", [{"title": "배경", "bullets": [LONG, "둘"]}])
    assert pptx_edit.healed(new) is new
    healed = pptx_edit.healed(old_style(new))
    _assert_real_bullet(_paragraphs_with(healed, "업무 처리")[0],
                        str(_paragraphs_with(new, "업무 처리")[0]._p.find(qn("a:pPr"))
                            .find(qn("a:buClr"))[0].get("val")))
    assert pptx_edit.healed(healed) is healed


def test_a_column_of_more_points_than_fit_writes_every_point_with_its_bullet():
    example = json.loads(
        slide_patterns.BY_NAME["compare-2"].example.replace("{{", "{").replace("}}", "}")
    )
    points = [f"{n}번 {LONG}" for n in range(1, 8)]
    columns = [{**column, "items": points} for column in example["columns"]]
    slide = {**example, "title": "비교", "layout": "compare-2", "accent": "#3b5bdb",
             "columns": columns}
    data = deck_export.to_pptx("비교", [slide])
    for point in points:
        written = _paragraphs_with(data, point)
        # each column's list, every point whole on a real bullet (the .pdf drew what fit)
        assert len(written) == 2 and all(p.text == point for p in written)
        for paragraph in written:
            props = paragraph._p.find(qn("a:pPr"))
            assert props.find(qn("a:buChar")) is not None and int(props.get("indent")) < 0
    assert deck_export.to_pdf("비교", [slide]).startswith(b"%PDF")


def _raw_breaks(data: bytes) -> list[str]:
    return [
        t.text for slide in _prs(data).slides for t in slide._element.iter(qn("a:t"))
        if t.text and any(c in t.text for c in "\r\n\v")
    ]


def test_a_line_break_in_an_item_is_a_break_in_its_bulleted_paragraph():
    data = pptx_doc.deck("보고", "", [
        {"title": "배경", "bullets": ["첫 줄\n둘째 줄", "셋\r\n넷"]},
    ])
    assert _raw_breaks(data) == []
    first = _paragraphs_with(data, "첫 줄")[0]
    assert first._p.find(qn("a:br")) is not None and first.text == "첫 줄\v둘째 줄"
    assert first._p.find(qn("a:pPr")).find(qn("a:buChar")) is not None
    assert [r.text for r in first.runs] == ["첫 줄", "둘째 줄"]
    assert _paragraphs_with(data, "셋")[0].text == "셋\v넷"


def test_an_old_deck_with_a_raw_line_feed_in_a_run_heals_on_load():
    new = pptx_doc.deck("보고", "", [{"title": "배경", "bullets": ["첫 줄 둘째 줄", "다음"]}])
    old = old_style(new)
    prs = _prs(old)
    for leaf in prs.slides[1]._element.iter(qn("a:t")):
        if leaf.text == "첫 줄 둘째 줄":
            leaf.text = "첫 줄\n둘째 줄"
    out = io.BytesIO()
    prs.save(out)
    healed = pptx_edit.healed(out.getvalue())
    assert _raw_breaks(out.getvalue()) and _raw_breaks(healed) == []
    paragraph = _paragraphs_with(healed, "첫 줄")[0]
    assert paragraph.text == "첫 줄\v둘째 줄" and [r.text for r in paragraph.runs] == [
        "첫 줄", "둘째 줄"]
    assert paragraph._p.find(qn("a:pPr")).find(qn("a:buChar")) is not None


def _numbers(data: bytes) -> list[list[str]]:
    return [
        [f.find(qn("a:t")).text for f in slide._element.iter(qn("a:fld"))
         if f.get("type") == "slidenum"]
        for slide in _prs(data).slides
    ]


def test_a_page_number_is_a_field_that_follows_its_slide():
    data = pptx_doc.deck("보고", "", [
        {"title": "하나", "bullets": ["가"]}, {"title": "둘", "bullets": ["나"]},
    ])
    assert _numbers(data) == [[], ["2"], ["3"]]
    copied = pptx_edit.apply(data, [{"op": "duplicate_slide", "id": "s2", "after": "s3"}]).data
    assert _numbers(copied) == [[], ["2"], ["3"], ["4"]]
    moved = pptx_edit.apply(copied, [{"op": "move_slide", "id": "s4", "after": "s1"}]).data
    assert _numbers(moved) == [[], ["2"], ["3"], ["4"]]
    assert "s3.1 (제목): 하나" in pptx_edit.outline(moved)


def test_an_old_static_page_number_becomes_a_field():
    data = pptx_doc.deck("보고", "", [{"title": "하나", "bullets": ["가"]}])
    prs = _prs(data)
    for found in list(prs.slides[1]._element.iter(qn("a:fld"))):
        run = etree.Element(qn("a:r"))
        for child in found:
            run.append(copy.deepcopy(child))
        found.addprevious(run)
        found.getparent().remove(found)
    out = io.BytesIO()
    prs.save(out)
    assert _numbers(out.getvalue()) == [[], []]
    assert _numbers(pptx_edit.healed(out.getvalue())) == [[], ["2"]]


async def test_a_deck_written_in_a_project_takes_its_design_system_and_slides_template():
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

    from app.models.chat import ChatSession, SessionKind
    from app.models.user import User
    from app.models.workspace import DesignSystem, Project
    from app.services import design_templates

    template_id = "deck-editorial"
    assert design_templates.default_for({"slides": template_id}, SessionKind.slides) == template_id
    rows = {
        Project: Project(id="p1", user_id="u1", name="사업", design_system_id="d1",
                         render_templates={"slides": template_id}),
        DesignSystem: DesignSystem(id="d1", owner_id="u1", name="회사",
                                   tokens={"accent": "#C0392B", "font": "serif"}),
    }

    class Db:
        async def get(self, model, _row_id):
            return rows.get(model)

    user = User(id="u1", email="u@example.test", name="u")
    session = ChatSession(id="s1", user_id="u1", title="t", project_id="p1")
    tokens, template = await pptx_doc.project_look(Db(), user, session)
    assert tokens["accent"].upper() == "#C0392B" and tokens["font"] == "serif"
    assert template == design_templates.rendering(template_id).pptx_template and template

    data = pptx_doc.deck("보고", "", [{"title": "배경", "bullets": ["하나"]}],
                         tokens=tokens, template=template)
    _assert_real_bullet(_paragraphs_with(data, "하나")[0], "C0392B")
    run = _paragraphs_with(data, "하나")[0].runs[0]
    assert run.font.name == "Georgia"
    # the template's own theme and masters, not python-pptx's
    prs = _prs(data)
    theme = etree.fromstring(prs.slide_masters[0].part.part_related_by(RT.THEME).blob)
    major = theme.find(qn("a:themeElements")).find(qn("a:fontScheme")).find(
        qn("a:majorFont")).find(qn("a:latin")).get("typeface")
    assert major != "Calibri"
    names = {layout.name for layout in Presentation(template).slide_layouts}
    assert {layout.name for layout in prs.slide_layouts} == names

    # no project: the house look
    alone = ChatSession(id="s2", user_id="u1", title="t")
    assert await pptx_doc.project_look(Db(), user, alone) == (None, "")


async def test_a_blank_deck_made_in_a_project_wears_its_theme_and_the_picked_template():
    from app.models.user import User
    from app.models.workspace import DesignSystem, Project
    from app.services import design_templates

    rows = {
        Project: Project(id="p1", user_id="u1", name="사업", design_system_id="d1",
                         render_templates={"slides": "deck-editorial"}),
        DesignSystem: DesignSystem(id="d1", owner_id="u1", name="회사",
                                   tokens={"accent": "#1E8449", "font": "gothic"}),
    }

    class Db:
        async def get(self, model, _row_id):
            return rows.get(model)

        async def exec(self, _query):  # an agent's design system is never looked up here
            raise AssertionError("no query expected")

    user = User(id="u1", email="u@example.test", name="u")
    # no conversation yet: the project's theme and default template
    tokens, template = await pptx_doc.project_look(Db(), user, project_id="p1")
    assert tokens["accent"].upper() == "#1E8449"
    assert template == design_templates.rendering("deck-editorial").pptx_template
    # a template the person picked wins over the project's default
    picked = next(row.id for row in design_templates.all_templates()
                  if row.pptx_template and row.id != "deck-editorial")
    _, chosen = await pptx_doc.project_look(Db(), user, project_id="p1", template_id=picked)
    assert chosen == design_templates.rendering(picked).pptx_template

    data = pptx_doc.blank(tokens=tokens, template=template)
    prs = _prs(data)
    assert len(prs.slides) == 1
    names = {layout.name for layout in Presentation(template).slide_layouts}
    assert {layout.name for layout in prs.slide_layouts} == names
    # outside a project the blank deck is the plain one
    assert len(_prs(pptx_doc.blank()).slides) == 1
