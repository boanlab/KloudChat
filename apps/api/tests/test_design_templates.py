"""`design_templates`: catalogue completeness, `sanitise`, assembly, and project defaults."""

from __future__ import annotations

import re
import time

import pytest
from conftest import both_passes
from fastapi import HTTPException

from app.models.chat import SessionKind
from app.models.workspace import Project
from app.routers import sessions as sessions_router
from app.routers import workspace as workspace_router
from app.schemas.workspace import DesignTemplateOut
from app.services import design_templates as dt
from app.services import imagegen, page, research

# ── the shipped catalogue ──────────────────────────────────────────────


#: Id prefix per kind; a convention the loader does not enforce.
_PREFIX = {
    "deck": "deck-",
    "document": "doc-",
    "image": "image-",
    "video": "video-",
    "audio": "audio-",
}


def test_every_surface_that_can_be_shaped_has_something_to_offer():
    """No surface has an empty gallery."""
    for surface in set(dt.SURFACE.values()):
        assert dt.for_surface(surface), surface


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_a_template_lands_on_the_surface_its_kind_names(template):
    assert template.surface is dt.SURFACE[template.kind]
    assert template.id.startswith(_PREFIX[template.kind]), template.id


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_a_shipped_template_is_complete(template):
    assert template.name and template.description and template.category
    assert template.example_prompt.strip()
    # Only writing templates carry instructions and a seed.
    if template.kind in dt.HTML_KINDS:
        assert template.instructions.strip()
    else:
        assert not template.instructions
    assert template.name_en and template.description_en and template.category_en
    assert template.example_prompt_en.strip()
    if template.kind in dt.HTML_KINDS:
        assert template.seed
        assert "{{TOKENS}}" in template.seed and "{{BODY}}" in template.seed
    else:
        assert not template.seed


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_a_writing_template_says_what_it_will_be_read_against(template):
    """Writing templates ship at least three unbulleted checks; media templates ship none."""
    if template.kind in dt.HTML_KINDS:
        assert len(template.checks) >= 3, template.id
        assert not any(line.startswith("-") for line in template.checks), template.id
    else:
        assert not template.checks


def test_the_card_carries_the_rules_the_result_will_be_read_against():
    """`DesignTemplateOut.checks` carries the template's checks; an image template's is empty."""
    card = DesignTemplateOut.of(dt.get("doc-minutes"))
    assert card.checks == list(dt.get("doc-minutes").checks)
    assert any("실행 항목" in line for line in card.checks)
    assert card.model_dump(by_alias=True)["checks"] == card.checks
    assert DesignTemplateOut.of(dt.get("image-poster")).checks == []


@pytest.mark.parametrize(
    "template", [t for t in dt.all_templates() if t.kind in dt.HTML_KINDS], ids=lambda t: t.id
)
def test_a_template_that_can_hold_code_says_so(template):
    """Every writing template's instructions mention `code`."""
    assert "code" in template.instructions


def test_what_is_inside_a_code_element_arrives_as_characters():
    """`<code>` contents are escaped as text, never double-escaped; `<pre>` is not allowed."""
    kept = dt.sanitise('<p>이렇게 <code><div class="x"></code> 쓴다</p>')
    assert kept == '<p>이렇게 <code>&lt;div class="x"&gt;</code> 쓴다</p>'
    assert dt.sanitise("<code>&lt;b&gt;</code>") == "<code>&lt;b&gt;</code>"
    assert dt.sanitise("<code>a &amp;&amp; b</code>") == "<code>a &amp;&amp; b</code>"
    assert dt.sanitise("<code>a & b</code>") == "<code>a &amp; b</code>"
    assert dt.sanitise("<code><script>x()</script></code>") == (
        "<code>&lt;script&gt;x()&lt;/script&gt;</code>"
    )
    assert "pre" not in dt._ALLOWED_TAGS


def test_the_categories_group_the_catalogue_the_same_way_in_both_languages():
    """Templates sharing a Korean category share its English name."""
    seen: dict[str, str] = {}
    for template in dt.all_templates():
        assert seen.setdefault(template.category, template.category_en) == template.category_en, (
            f"{template.id}: {template.category} is {seen[template.category]} elsewhere"
        )


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_a_seed_carries_no_script(template):
    """No seed contains a script or an inline handler."""
    assert "<script" not in template.seed.lower()
    assert "onclick" not in template.seed.lower()


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_a_seed_breaks_korean_lines_between_words(template):
    """Every writing seed sets `word-break: keep-all`."""
    if template.kind not in dt.HTML_KINDS:
        return
    assert "keep-all" in template.seed


#: Block tags every seed must style.
_MUST_STYLE = (
    "h3",
    "table",
    "blockquote",
    "figure",
    "figcaption",
    "dl",
    "dd",
    "hr",
    "code",
)


@pytest.mark.parametrize(
    "template", [t for t in dt.all_templates() if t.kind in dt.HTML_KINDS], ids=lambda t: t.id
)
def test_a_seed_styles_every_tag_a_block_may_reach_for(template):
    for tag in _MUST_STYLE:
        assert tag in dt._ALLOWED_TAGS or tag == "hr", tag
        # A bare element selector — `.slide.figure` is a layout, not a `<figure>`.
        assert re.search(rf"(?:^|[\s,]){tag}\s*[{{,]", template.seed, re.M), tag


@pytest.mark.parametrize(
    "template", [t for t in dt.all_templates() if t.kind in dt.HTML_KINDS], ids=lambda t: t.id
)
def test_a_picture_cannot_grow_past_the_page_it_is_on(template):
    """Every writing seed caps `figure img` height."""
    assert re.search(r"figure img \{[^}]*max-height", template.seed), template.id


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_no_seed_justifies_korean(template):
    """No seed justifies text; with `keep-all` that leaves rivers in Korean."""
    assert "text-align: justify" not in template.seed


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_every_blank_is_one_the_prompt_actually_has(template):
    """Every argument appears in both example prompts with labels, defaults and matching options."""
    for argument in template.arguments:
        assert f"{{{argument.name}}}" in template.example_prompt, argument.name
        assert f"{{{argument.name}}}" in template.example_prompt_en, argument.name
        assert argument.label and argument.label_en
        assert argument.default and argument.default_en
        assert len(argument.options) == len(argument.options_en)
        assert not argument.options or argument.default in argument.options


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_a_prompt_has_no_blank_without_an_argument_behind_it(template):
    named = {a.name for a in template.arguments}
    for placeholder in re.findall(r"\{([a-z_]+)\}", template.example_prompt):
        assert placeholder in named, placeholder


@pytest.mark.parametrize(
    "template", [t for t in dt.all_templates() if t.kind in dt.HTML_KINDS], ids=lambda t: t.id
)
def test_a_writing_arguments_are_only_for_media(template):
    """Writing templates have no arguments or defaults."""
    assert not template.arguments
    assert not template.defaults


@pytest.mark.parametrize(
    "template", [t for t in dt.all_templates() if t.kind in dt.HTML_KINDS], ids=lambda t: t.id
)
def test_a_writing_template_can_print(template):
    """Every writing seed has print and `@page` rules."""
    assert "@media print" in template.seed
    assert "@page" in template.seed


def test_only_image_templates_hide_a_clause_from_the_composer():
    """Only picture (non-figure) image templates carry a hidden `prompt_suffix`."""
    for template in dt.all_templates():
        if template.kind == "image" and not template.figure:
            assert template.prompt_suffix.strip(), template.id
        elif template.kind == "image":
            # 도식은 그림 모델로 가지 않는다 — 감출 화풍 문구가 없다.
            assert not template.prompt_suffix, template.id
        else:
            assert not template.prompt_suffix, template.id


def test_media_templates_carry_the_settings_they_imply():
    """Media templates ship the settings their shape implies."""
    assert dt.get("video-product").defaults["aspect"] == "16:9"
    assert dt.get("video-opening").defaults["resolution"] == "1080p"
    assert dt.get("audio-narration").defaults["audioKind"] == "narration"
    assert dt.get("audio-bed").defaults["audioKind"] == "music"
    assert dt.get("image-poster").defaults["aspect"] == "9:16"


def test_a_script_is_removed_with_its_payload():
    assert dt.sanitise("<p>본문</p><script>steal()</script>") == "<p>본문</p>"
    assert "steal" not in dt.sanitise("<script>steal()</script>")


def test_handlers_and_remote_references_are_stripped():
    assert "onclick" not in dt.sanitise('<p onclick="x()">본문</p>')
    assert "evil.example" not in dt.sanitise('<img src="https://evil.example/p.png">')
    assert dt.sanitise("<style>body{display:none}</style><p>본문</p>") == "<p>본문</p>"


def test_tags_outside_the_vocabulary_lose_their_markup_and_keep_their_words():
    assert dt.sanitise("<marquee>흐르는 글</marquee>") == "흐르는 글"
    assert dt.sanitise("<ul><li>항목</li></ul>") == "<ul><li>항목</li></ul>"


def test_blocks_land_inside_the_structure_the_seed_styles():
    body = dt.assemble(
        dt.get("deck-editorial"),
        [
            {"layout": "cover", "title": "표지", "html": '<p class="lead">부제</p>'},
            {"layout": "quote", "title": "한 줄", "html": "<blockquote>말</blockquote>"},
        ],
    )
    assert body.startswith('<section class="slide cover"><h2>표지</h2>')
    assert '<section class="slide quote">' in body
    assert '<span class="num">2</span>' in body


def test_a_one_pager_puts_its_sections_in_the_grid_and_its_cover_outside():
    body = dt.assemble(
        dt.get("doc-brief"),
        [
            {"layout": "cover", "title": "제목", "html": "<p>한 줄</p>"},
            {"layout": "section", "title": "배경", "html": "<p>본문</p>"},
        ],
    )
    assert body.index('<div class="cover">') < body.index('<div class="grid">')
    assert body.count('<div class="grid">') == 1


def test_an_empty_block_is_left_out_rather_than_rendered_hollow():
    body = dt.assemble(
        dt.get("doc-report"),
        [
            {"layout": "cover", "title": "제목", "html": "<p>한 줄</p>"},
            {"layout": "section", "title": "빈 절", "html": ""},
        ],
    )
    assert "빈 절" not in body


# ── the finished file ──────────────────────────────────────────────────


def test_the_design_system_reaches_the_document_as_css_variables():
    html = dt.render(
        dt.get("doc-report"),
        title="제목",
        tokens={"accent": "#7a1f3d", "ink": "#111827", "muted": "#6b7280", "font": "serif"},
        body="<section><h2>절</h2><p>본문</p></section>",
    )
    assert "--accent: #7a1f3d;" in html
    assert "--ink: #111827;" in html
    assert "Nanum Myeongjo" in html  # the serif token, not the sans fallback
    assert "<title>제목</title>" in html


def test_a_title_cannot_break_out_of_the_document():
    html = dt.render(dt.get("doc-report"), title="</title><script>x()</script>", tokens={}, body="")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_a_card_shows_the_shape_the_form_actually_has():
    """Every `<h2>` in a card's sample is a heading in the template's .docx form."""
    import zipfile
    from xml.etree import ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    checked = 0
    for template in dt.all_templates():
        if not template.sample or not template.form_file.endswith(".docx"):
            continue
        with zipfile.ZipFile(template.form_file) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        headings = {
            "".join(node.text or "" for node in para.iter(f"{ns}t")).strip()
            for para in document.iter(f"{ns}p")
            if (style := para.find(f"{ns}pPr/{ns}pStyle")) is not None
            and str(style.get(f"{ns}val")).startswith("Heading")
        }
        for shown in re.findall(r"<h2[^>]*>(.*?)</h2>", template.sample, re.S):
            assert shown.strip() in headings, (
                f"{template.id}: 카드는 '{shown.strip()}' 절을 보여 주는데 "
                f"양식에는 그런 절이 없다 — {sorted(headings)}"
            )
        checked += 1
    assert checked >= 10, "문서 서식이 이만큼은 있어야 이 시험이 무언가를 지킨다"


# ── the outline ────────────────────────────────────────────────────────


def test_the_outline_is_offered_only_layouts_the_seed_styles():
    for template in dt.all_templates():
        if template.kind not in dt.HTML_KINDS:
            continue
        styled = set(re.findall(r"\.slide\.([a-z-]+)", template.seed)) or {"section"}
        for layout in template.layouts:
            if layout in ("cover", "section", "bullets", "table"):
                continue  # plain blocks, styled by the base rules
            assert layout in styled, f"{template.id}: {layout} has no rule"


def test_an_unknown_layout_is_coerced_rather_than_dropped():
    template = dt.get("deck-editorial")
    title, blocks = page._parse_outline(
        '{"title":"제목","blocks":[{"title":"표지","layout":"cover"},'
        '{"title":"본문","layout":"carousel"}]}',
        template,
    )
    assert title == "제목"
    assert [b["layout"] for b in blocks] == ["cover", "bullets"]


def test_the_first_block_is_always_the_cover():
    """`_parse_outline` forces the first block to `cover`."""
    _, blocks = page._parse_outline(
        '{"title":"제목","blocks":[{"title":"본문","layout":"bullets"}]}', dt.get("deck-editorial")
    )
    assert blocks[0]["layout"] == "cover"


def test_a_count_stated_in_the_request_is_honoured_within_bounds():
    assert page.requested_blocks("8장짜리 발표") == 8
    assert page.requested_blocks("발표 자료") is None
    assert page.requested_blocks("200장") == 24  # clamped to the runtime ceiling
    # A deck 서식 has the slide track's ceiling of 50.
    deck = dt.get("deck-lecture")
    assert page.requested_blocks("30장", deck) == 30
    assert page.requested_blocks("200장", deck) == 50
    assert page.requested_blocks("30쪽", dt.get("doc-report")) == 24


# ── image templates ────────────────────────────────────────────────────


def test_an_image_template_shapes_the_prompt_after_the_chip_and_before_the_design():
    composed = imagegen.compose_prompt(
        "행사 그림",
        aspect="16:9",
        style="사진",
        template=dt.get("image-poster").prompt_suffix,
        design="primary colour #7a1f3d",
    )
    assert composed.index("photorealistic") < composed.index("poster composition")
    assert composed.index("poster composition") < composed.index("#7a1f3d")
    # Orientation is spelled out as well as the ratio.
    assert composed.endswith("aspect ratio 16:9, landscape orientation, wider than it is tall")


def test_without_a_template_the_image_prompt_is_unchanged():
    assert imagegen.compose_prompt("표지", aspect="1:1", style="사진") == imagegen.compose_prompt(
        "표지", aspect="1:1", style="사진", template="", design=""
    )


# ── one whole turn ─────────────────────────────────────────────────────


class _Payload:
    def __init__(self, text: str):
        self._text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": self._text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }


class _Client:
    """`httpx.AsyncClient` double that answers each post with the next reply."""

    def __init__(self, replies: list[str], posts: list[dict], **_kwargs):
        self.replies = replies
        self.posts = posts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _path: str, *, json: dict):
        self.posts.append(json)
        return _Payload(self.replies.pop(0))


async def _write(monkeypatch, template, replies, **kwargs):
    posts: list[dict] = []

    async def litellm_config():
        return "http://mock-litellm", "unused"

    monkeypatch.setattr(page.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(page.httpx, "AsyncClient", lambda **kw: _Client(replies, posts, **kw))
    events = await both_passes(
        page,
        request="연구실 장비 관리 발표 자료",
        model="mock-model",
        api_key="mock-key",
        template=template,
        **kwargs,
    )
    return events, posts


_OUTLINE = (
    '{"title":"연구실 장비 관리","blocks":['
    '{"title":"연구실 장비 관리","layout":"cover"},'
    '{"title":"현황","layout":"bullets"},'
    '{"title":"남길 한 줄","layout":"quote"}]}'
)


@pytest.mark.asyncio
async def test_a_turn_writes_one_file_out_of_one_call_per_block(monkeypatch):
    events, posts = await _write(
        monkeypatch,
        dt.get("deck-editorial"),
        [
            _OUTLINE,
            '<p class="lead">2026년 상반기 점검</p>',
            "```html\n<ul><li>보유 42대</li></ul>\n```",
            "<blockquote>점검하지 않은 장비는 없는 장비다</blockquote><script>x()</script>",
        ],
        tokens={"accent": "#7a1f3d", "ink": "#111827", "muted": "#6b7280", "font": "serif"},
    )

    assert len(posts) == 4  # one outline, three blocks
    finished = next(e for e in events if e["type"] == "page")
    html = finished["html"]

    assert len(page.filled(finished["blocks"])) == 3
    assert "--accent: #7a1f3d;" in html
    assert '<section class="slide cover"><h2>연구실 장비 관리</h2>' in html
    assert "```" not in html and "<li>보유 42대</li>" in html
    assert "<script>" not in html and "x()" not in html


@pytest.mark.asyncio
async def test_the_template_instructions_reach_the_model_and_the_layouts_are_named(monkeypatch):
    replies = [_OUTLINE, "<p>가</p>", "<p>나</p>", "<p>다</p>"]
    _, posts = await _write(monkeypatch, dt.get("deck-editorial"), replies)
    outline_call = posts[0]["messages"]
    assert "편집형 덱" in outline_call[0]["content"]  # instructions, system-side
    assert "cover / bullets / quote / split / table" in outline_call[-1]["content"]


@pytest.mark.asyncio
async def test_a_failed_block_leaves_a_gap_and_the_rest_still_lands(monkeypatch):
    replies = [_OUTLINE, "<p>표지</p>", "", "<blockquote>한 줄</blockquote>"]
    events, _ = await _write(monkeypatch, dt.get("deck-editorial"), replies)
    finished = next(e for e in events if e["type"] == "page")

    assert len(page.filled(finished["blocks"])) == 2
    assert "한 줄" in finished["html"]
    assert finished["html"].count('class="slide') == 2


@pytest.mark.asyncio
async def test_an_outline_that_cannot_be_parsed_ends_the_turn_without_billing(monkeypatch):
    events, posts = await _write(monkeypatch, dt.get("doc-report"), ["설명만 하고 JSON 은 없음"])

    assert len(posts) == 1  # no block calls were made
    assert not any(e["type"] == "page" for e in events)
    assert any(e["type"] == "error" for e in events)


def test_a_heading_the_model_repeats_is_dropped_rather_than_printed_twice():
    """`<h1>`/`<h2>` in a block are dropped with their text; `<h3>` stays."""
    assert dt.sanitise("<h2>판단 결과</h2><p>본문</p>") == "<p>본문</p>"
    assert dt.sanitise("<h1>표지 제목</h1><p>부제</p>") == "<p>부제</p>"
    assert dt.sanitise("<h3>세부</h3>") == "<h3>세부</h3>"


def test_the_layout_name_the_model_was_given_is_not_printed_back():
    """Leading lines naming any layout in the template's vocabulary are dropped."""
    assert dt.sanitise("bullets\n<ul><li>항목</li></ul>", layouts=("bullets",)) == (
        "<ul><li>항목</li></ul>"
    )
    assert dt.sanitise('layout: "bullets"\n<p>본문</p>', layouts=("table",)) == "<p>본문</p>"
    assert dt.sanitise("layout = quote\n<p>본문</p>", layouts=("quote",)) == "<p>본문</p>"
    assert (
        dt.sanitise('cover\nlayout: "cover"\n<p class="lead">부제</p>', layouts=("cover",))
        == '<p class="lead">부제</p>'
    )


def test_a_word_that_happens_to_be_a_layout_name_is_left_alone():
    """Only a leading line that is solely a layout name is dropped."""
    assert dt.sanitise("<p>bullets 는 항목을 뜻한다</p>", layouts=("bullets",)) == (
        "<p>bullets 는 항목을 뜻한다</p>"
    )
    assert dt.sanitise("<ul><li>bullets</li></ul>", layouts=("bullets",)) == (
        "<ul><li>bullets</li></ul>"
    )
    assert dt.sanitise("<p>본문</p>\nbullets", layouts=("bullets",)) == "<p>본문</p>\nbullets"
    assert dt.sanitise("bullets\n<p>본문</p>") == "bullets\n<p>본문</p>"


def test_a_block_that_came_back_as_its_own_envelope_is_unwrapped():
    """A JSON envelope with one string payload is unwrapped, whatever the key is called."""
    assert (
        dt.sanitise('{"layout": "bullets", "body": "<ul><li>항목</li></ul>"}', layouts=("bullets",))
        == "<ul><li>항목</li></ul>"
    )
    assert dt.sanitise('{"body": "<p>본문</p>", "layout": "quote"}') == "<p>본문</p>"
    assert dt.sanitise('{"layout": "cover", "content": "<p>본문</p>"}') == "<p>본문</p>"


def test_an_envelope_with_nothing_to_unwrap_is_left_alone():
    """An envelope with no payload, or two, is left as is."""
    only_meta = '{"layout": "table", "title": "비교"}'
    assert dt.sanitise(only_meta) == only_meta
    ambiguous = '{"layout": "quote", "body": "<p>a</p>", "notes": "b"}'
    assert dt.sanitise(ambiguous) == ambiguous


def test_an_envelope_that_will_not_parse_is_left_for_the_checker():
    """Truncated JSON is left intact for `lint` to flag."""
    cut = '{"layout": "bullets", "body": "<ul><li>항목'
    assert dt.sanitise(cut, layouts=("bullets",)).startswith("{")


def test_a_brace_in_somebody_s_writing_is_not_an_envelope():
    assert dt.sanitise('<p>{"a": 1} 은 JSON 이다</p>') == '<p>{"a": 1} 은 JSON 이다</p>'


# ── choosing and unchoosing ────────────────────────────────────────────


def test_clearing_and_omitting_are_both_no_template():
    """Both `""` and `None` resolve to no template."""
    assert sessions_router._resolved_template_id("", SessionKind.slides) is None
    assert sessions_router._resolved_template_id(None, SessionKind.slides) is None


def test_a_template_is_resolved_for_the_surface_that_can_use_it():
    assert (
        sessions_router._resolved_template_id("deck-editorial", SessionKind.slides)
        == "deck-editorial"
    )


@pytest.mark.parametrize(
    ("template_id", "kind", "expected"),
    [
        ("nope", SessionKind.slides, 404),
        ("image-poster", SessionKind.image, 404),
        ("deck-editorial", SessionKind.report, 422),
        ("doc-brief", SessionKind.slides, 422),
    ],
)
def test_a_template_the_surface_cannot_use_is_refused(template_id, kind, expected):
    with pytest.raises(HTTPException) as raised:
        sessions_router._resolved_template_id(template_id, kind)
    assert raised.value.status_code == expected


def test_the_session_routes_are_still_mounted():
    """The PATCH /sessions/{session_id} route is registered."""
    patchable = {
        route.path
        for route in sessions_router.router.routes
        if "PATCH" in getattr(route, "methods", set())
    }
    assert "/sessions/{session_id}" in patchable


def test_a_plan_is_salvaged_out_of_malformed_json():
    """`_parse_outline` salvages an outline with a dropped quote."""
    mangled = (
        '{"title": "학과 서버 교체", "blocks": ['
        '{"title": "학과 서버 교체", "layout": "cover"}, '
        '{"title: "이행 리스크", "layout": "section"}, '
        '{"title": "다음 행동", "layout": "section"}]'
    )
    title, blocks = page._parse_outline(mangled, dt.get("doc-brief"))

    assert title == "학과 서버 교체"
    assert [b["title"] for b in blocks] == ["학과 서버 교체", "이행 리스크", "다음 행동"]
    assert [b["layout"] for b in blocks] == ["cover", "section", "section"]


def test_nothing_is_salvaged_from_prose():
    """Prose salvages to no plan."""
    title, blocks = page._parse_outline("구성을 만들 수 없습니다.", dt.get("doc-brief"))
    assert not blocks and not title


def test_researched_document_blocks_are_told_which_citation_numbers_exist():
    findings = research.Findings(
        sources=[
            {"ordinal": 1, "title": "첫 자료"},
            {"ordinal": 4, "title": "넷째 자료"},
        ]
    )

    rule = page._citation_rule(findings, "document")
    assert "[1], [4]" in rule
    assert "목록에 없는 번호를 만들지" in rule
    assert page._citation_rule(findings, "deck") == ""
    assert page._citation_rule(research.Findings(), "document") == ""


# ── rewriting one block ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_rewrite_sees_the_document_around_it(monkeypatch):
    """A rewrite prompt carries the plan, the neighbours, and the labelled note, not the target."""
    blocks = [
        {"layout": "cover", "title": "표지", "html": "<p class='lead'>부제</p>"},
        {"layout": "bullets", "title": "현황", "html": "<ul><li>보유 42대</li></ul>"},
        {"layout": "quote", "title": "한 줄", "html": "<blockquote>말</blockquote>"},
    ]
    posts: list[dict] = []

    async def litellm_config():
        return "http://mock-litellm", "unused"

    monkeypatch.setattr(page.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(
        page.httpx, "AsyncClient", lambda **kw: _Client(["<ul><li>새 항목</li></ul>"], posts, **kw)
    )
    fragment, usage = await page.rewrite_block(
        request="장비 점검 발표",
        blocks=blocks,
        index=1,
        template=dt.get("deck-editorial"),
        model="m",
        api_key="k",
        note="숫자는 빼 주세요.",
    )

    assert fragment == "<ul><li>새 항목</li></ul>"
    assert usage["outputTokens"] == 20
    prompt = posts[0]["messages"][-1]["content"]
    assert "1. 표지" in prompt and "3. 한 줄" in prompt
    assert "부제" in prompt and "말" in prompt
    assert "현황: 보유 42대" not in prompt  # the target itself is not fed back
    assert "이번에 다시 쓰는 이유" in prompt and "숫자는 빼 주세요." in prompt


@pytest.mark.asyncio
async def test_a_rewrite_is_reduced_to_the_seed_vocabulary_like_any_other_block(monkeypatch):
    posts: list[dict] = []

    async def litellm_config():
        return "http://mock-litellm", "unused"

    monkeypatch.setattr(page.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(
        page.httpx,
        "AsyncClient",
        lambda **kw: _Client(["```html\n<p>본문</p><script>x()</script>\n```"], posts, **kw),
    )
    fragment, _ = await page.rewrite_block(
        request="r",
        blocks=[{"layout": "bullets", "title": "가", "html": "<p>이전</p>"}],
        index=0,
        template=dt.get("deck-editorial"),
        model="m",
        api_key="k",
    )
    assert fragment == "<p>본문</p>"


# ── pictures ───────────────────────────────────────────────────────────
# The only `src` that survives sanitising is an inlined raster data URI.


#: One transparent PNG pixel.
_PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_a_picture_already_inside_the_file_survives():
    markup = dt.figure(mime="image/png", data_b64=_PIXEL, alt="회로도", caption="그림 1. 회로도")
    kept = dt.sanitise(markup)
    assert f"data:image/png;base64,{_PIXEL}" in kept
    assert "<figcaption>그림 1. 회로도</figcaption>" in kept


@pytest.mark.parametrize(
    "src",
    [
        "https://example.test/p.png",  # fetched when the reader opens the file
        "/api/files/abc/content",  # same, and dead outside this server
        "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",  # a document, not a picture
        "data:text/html;base64,PGgxPngNCg==",
    ],
)
def test_every_other_address_is_dropped(src):
    assert "src" not in dt.sanitise(f'<img src="{src}" />')


def test_a_caption_cannot_smuggle_markup():
    kept = dt.sanitise(dt.figure(mime="image/png", data_b64=_PIXEL, caption="<script>x()</script>"))
    assert "<script" not in kept and "&lt;script&gt;" in kept


def test_inline_presentation_is_dropped_but_class_survives():
    """`style=` is stripped; `class` survives."""
    kept = dt.sanitise('<p class="lead" style="color:#f0f">표지 한 줄</p>')
    assert kept == '<p class="lead">표지 한 줄</p>'


def test_a_rewrite_keeps_the_picture_and_not_the_described_figure():
    """`pictures_in` keeps figures with a data URI image and drops text-only figures."""
    block = "<ul><li>보유 42대</li></ul>" + dt.figure(
        mime="image/png", data_b64=_PIXEL, alt="자물쇠", caption="그림 1"
    )
    kept = dt.pictures_in(block)
    assert kept.startswith("<figure>") and f"base64,{_PIXEL}" in kept
    assert "<figcaption>그림 1</figcaption>" in kept
    assert dt.pictures_in("<figure><p>표 1 설명</p><figcaption>설명</figcaption></figure>") == ""


def test_a_cover_that_came_back_empty_is_still_a_cover():
    """An empty cover block is still assembled; an empty body block is left out."""
    template = dt.get("deck-editorial")
    html = dt.assemble(
        template,
        [
            {"title": "연구실 장비 점검", "layout": "cover", "html": ""},
            {"title": "현황", "layout": "bullets", "html": "<ul><li>보유 42대</li></ul>"},
        ],
    )
    assert html.startswith('<section class="slide cover">')
    assert "연구실 장비 점검" in html
    gapped = dt.assemble(
        template,
        [
            {"title": "표지", "layout": "cover", "html": "<p class='lead'>한 줄</p>"},
            {"title": "빈 장", "layout": "bullets", "html": ""},
        ],
    )
    assert "빈 장" not in gapped


# ── the project's own formats ──────────────────────────────────────────


class _Rows:
    """A db double serving `get` only."""

    def __init__(self, rows: dict):
        self.rows = rows

    async def get(self, _model, row_id):
        return self.rows.get(row_id)


def _project(**kwargs) -> Project:
    return Project(id="p1", user_id="u1", name="공문 프로젝트", **kwargs)


def test_a_project_starts_with_no_format_of_its_own():
    """`render_templates` defaults to None."""
    assert Project(user_id="u1", name="p").render_templates is None


def test_a_default_reaches_the_surface_it_was_set_for_and_no_other():
    """`default_for` answers per surface and None for a surface not set."""
    defaults = {"report": "doc-notice", "slides": "deck-proposal"}
    assert dt.default_for(defaults, SessionKind.report) == "doc-notice"
    assert dt.default_for(defaults, SessionKind.slides) == "deck-proposal"
    assert dt.default_for(defaults, SessionKind.image) is None
    assert dt.default_for({"report": "doc-notice"}, SessionKind.slides) is None


@pytest.mark.parametrize(
    "defaults",
    [
        None,
        {},
        # Stored ids the catalogue no longer has, or has on another surface.
        {"report": "doc-gone"},
        {"report": "deck-editorial"},
    ],
)
def test_a_default_the_catalogue_cannot_place_is_the_built_in_track(defaults):
    assert dt.default_for(defaults, SessionKind.report) is None


async def test_a_new_session_begins_in_its_projects_format():
    """A new session takes its project's format for that surface only."""
    db = _Rows({"p1": _project(render_templates={"report": "doc-notice"})})

    started = await sessions_router._project_render_template(db, "p1", SessionKind.report)
    assert started == "doc-notice"
    assert await sessions_router._project_render_template(db, "p1", SessionKind.slides) is None


async def test_work_outside_a_project_is_not_a_lookup():
    none = await sessions_router._project_render_template(_Rows({}), None, SessionKind.report)
    assert none is None


async def test_the_composer_still_decides_this_conversation():
    """The project seeds the session; the composer's choice still resolves on its own."""
    db = _Rows({"p1": _project(render_templates={"report": "doc-notice"})})

    seeded = await sessions_router._project_render_template(db, "p1", SessionKind.report)
    assert seeded == "doc-notice"
    assert sessions_router._resolved_template_id("doc-brief", SessionKind.report) == "doc-brief"


def test_clearing_one_surface_leaves_the_other_alone():
    assert workspace_router._validated_render_templates(None) is None
    kept = workspace_router._validated_render_templates({"report": "doc-notice", "slides": ""})
    assert kept == {"report": "doc-notice"}


@pytest.mark.parametrize(
    ("defaults", "expected"),
    [
        ({"report": "nope"}, 404),
        ({"image": "image-poster"}, 404),
        ({"report": "deck-editorial"}, 422),
        ({"chat": "doc-brief"}, 422),
    ],
)
def test_a_project_format_the_surface_cannot_use_is_refused_like_the_composers(defaults, expected):
    with pytest.raises(HTTPException) as raised:
        workspace_router._validated_render_templates(defaults)
    assert raised.value.status_code == expected


def test_an_attribute_stripper_cannot_be_stalled_by_whitespace():
    """Attribute stripping stays linear in the whitespace before an attribute."""
    padded = "<p" + " " * 60_000 + 'onclick="steal()" style="color:red" href="http://x">hi</p>'

    started = time.perf_counter()
    cleaned = dt.sanitise(padded)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, f"{elapsed:.1f}s"
    assert "onclick" not in cleaned
    assert "style=" not in cleaned
    assert "href=" not in cleaned


def test_the_research_figures_ask_for_a_paper_figure_not_a_picture() -> None:
    """Research diagram templates declare `figure` and take a description; the teaser does not."""
    from app.services import design_templates

    family = {
        template.id: template
        for template in design_templates.all_templates()
        if template.kind == "image" and template.category == "연구"
    }
    assert {"image-diagram", "image-method", "image-pipeline", "image-teaser"} <= set(family)

    figures = {"image-diagram": "concept", "image-pipeline": "flow", "image-method": "method"}
    drawn = {t.id: t for t in design_templates.all_templates() if t.kind == "image"}
    assert drawn["image-infographic"].figure == "flow"
    assert drawn["image-architecture"].figure == "method"
    for template_id, figure in figures.items():
        template = family[template_id]
        assert template.figure == figure, template_id
        assert not template.prompt_suffix, template_id
        assert template.arguments[0].name == "description", template_id
        assert template.arguments[0].long, template_id
        names = [argument.name for argument in template.arguments]
        assert names == ["description", "highlight", "language"], template_id
        for argument in template.arguments[1:]:
            assert len(argument.options) >= 2, f"{template_id}.{argument.name}"
        for argument in template.arguments:
            assert f"{{{argument.name}}}" in template.example_prompt, argument.name

    teaser = family["image-teaser"]
    assert not teaser.figure
    suffix = teaser.prompt_suffix.lower()
    for banned in ("no text", "no lettering", "no logos"):
        assert banned in suffix, f"image-teaser: {banned}"
    assert "flat even lighting" in suffix
    assert "no drop shadows" in suffix


def test_a_research_figure_prompt_carries_both_halves() -> None:
    """Diagram rules go in the system message and the description in the user message."""
    from app.services import diagram

    messages = diagram._messages("인코더가 입력을 임베딩으로 바꾼다.", "method", "ko")
    system, user = messages[0]["content"], messages[1]["content"]
    assert "flowchart" in system and "subgraph" in system
    assert "인코더가 입력을 임베딩으로 바꾼다." in user
    assert "subgraph" not in user
