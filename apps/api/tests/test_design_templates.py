"""The rendering catalogue: what it promises, and what it refuses to pass on.

Three properties carry the feature, and each has a test that fails without the
code it guards:

1. Every shipped template is complete enough to be offered — a card with no
   preview, or an outline prompt naming a layout the seed does not style, is a
   promise the product cannot keep.
2. The model writes content and never layout. What it sends is reduced to the
   seed's vocabulary before it reaches a file somebody downloads.
3. A session with no template produces exactly what it produced before.
"""

from __future__ import annotations

import re

import pytest
from fastapi import HTTPException

from app.models.chat import SessionKind
from app.routers import sessions as sessions_router
from app.services import design_templates as dt
from app.services import imagegen, page

# ── the shipped catalogue ──────────────────────────────────────────────


#: What an id begins with, per kind. A convention the loader does not enforce,
#: kept because a folder listing is where the catalogue is actually browsed
#: while it is being written.
_PREFIX = {
    "deck": "deck-",
    "document": "doc-",
    "image": "image-",
    "video": "video-",
    "audio": "audio-",
}


def test_every_surface_that_can_be_shaped_has_something_to_offer():
    """A property, not an inventory.

    Listing the ids here would mean editing this test every time a template is
    added, which teaches everybody to edit it without reading it. What has to
    hold is that no surface is left with an empty gallery behind its button.
    """
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
    # Instructions are what `page.write` composes into a writing turn. A media
    # template has no such turn — its whole expertise is the prompt and the
    # suffix — so requiring a file nothing reads would only invite drift.
    if template.kind in dt.HTML_KINDS:
        assert template.instructions.strip()
    else:
        assert not template.instructions
    # Both halves of the card, or an English UI shows a Korean one.
    assert template.name_en and template.description_en and template.category_en
    assert template.example_prompt_en.strip()
    # The gallery renders the seed around the sample; either missing is a card
    # advertising a shape nobody can see.
    assert template.seed and template.sample
    assert "{{TOKENS}}" in template.seed and "{{BODY}}" in template.seed


def test_the_categories_group_the_catalogue_the_same_way_in_both_languages():
    """The chip row is a filter, so a category is a grouping key, not a label.

    Two templates that share 발표 have to share whatever the English side calls
    it: otherwise the same gallery offers one chip in Korean and three in
    English, and picking one hides templates that the other language keeps.
    """
    seen: dict[str, str] = {}
    for template in dt.all_templates():
        assert seen.setdefault(template.category, template.category_en) == template.category_en, (
            f"{template.id}: {template.category} is {seen[template.category]} elsewhere"
        )


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_a_seed_carries_no_script(template):
    """`sandbox=""` blocks it anyway; the file is also downloaded and opened."""
    assert "<script" not in template.seed.lower()
    assert "onclick" not in template.seed.lower()


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_a_seed_breaks_korean_lines_between_words(template):
    """The catalogue writes Korean, and Korean breaks between syllables.

    Left alone, a browser puts the second half of a word on the next line —
    which on a slide reads as a typo. `keep-all` is one line in a seed and easy
    to leave out of the next one, so it is asserted rather than remembered.
    """
    assert "keep-all" in template.seed


#: Tags a block may contain that a seed has to have an opinion about. Left
#: unstyled they render as browser defaults in the middle of a designed page,
#: which is exactly what makes generated documents look generated.
_MUST_STYLE = ("h3", "table", "blockquote", "figure", "figcaption", "dl", "dd", "hr")


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
    """A portrait picture at full width is twice the height of a slide.

    Measured before this rule: a 600×1200 image took a `deck-editorial` slide
    from 100vh to 2154px, which stops scroll-snap landing on slide boundaries
    and puts one slide across two printed pages. Width is the obvious
    constraint and the wrong one; height is what has to be capped.
    """
    assert re.search(r"figure img \{[^}]*max-height", template.seed), template.id


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_no_seed_justifies_korean(template):
    """`keep-all` breaks Korean at spaces, and a Korean line has few of them.

    Justifying then stretches those few into rivers running down the page —
    the one alignment that looks tidy in English and ragged here.
    """
    assert "text-align: justify" not in template.seed


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_every_blank_is_one_the_prompt_actually_has(template):
    """A blank nobody substitutes is a `{name}` printed into the sentence."""
    for argument in template.arguments:
        assert f"{{{argument.name}}}" in template.example_prompt, argument.name
        assert f"{{{argument.name}}}" in template.example_prompt_en, argument.name
        assert argument.label and argument.label_en
        assert argument.default and argument.default_en
        # A picker's options are a closed list; both sides have to line up or
        # one language silently offers fewer choices.
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
    """A deck's brief is a sentence somebody writes, not a form they fill."""
    assert not template.arguments
    assert not template.defaults


@pytest.mark.parametrize(
    "template", [t for t in dt.all_templates() if t.kind in dt.HTML_KINDS], ids=lambda t: t.id
)
def test_a_writing_template_can_print(template):
    """No rendering engine on the server, so print *is* the PDF path."""
    assert "@media print" in template.seed
    assert "@page" in template.seed


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_the_preview_is_the_seed_rather_than_a_second_file(template):
    html = dt.preview(template)
    assert "{{" not in html  # every placeholder substituted
    assert "--accent: #5b5bd6;" in html
    # The sample is what makes the card show this template's own shape.
    assert template.sample.strip().split("\n")[0][:40] in html


def test_only_image_templates_hide_a_clause_from_the_composer():
    """Guardrails stay invisible; a brief stays visible.

    An image template's `no lettering, no logos` is true of every picture it
    makes and would be noise in the composer. A video or audio template has no
    such standing rule — its whole prompt is the brief, and the brief belongs
    where the person can read and edit it.
    """
    for template in dt.all_templates():
        if template.kind == "image":
            assert template.prompt_suffix.strip(), template.id
        else:
            assert not template.prompt_suffix, template.id


def test_media_templates_carry_the_settings_they_imply():
    """Picking a shape and then setting its aspect by hand is asking twice."""
    assert dt.get("video-product").defaults["aspect"] == "16:9"
    assert dt.get("video-opening").defaults["resolution"] == "1080p"
    assert dt.get("audio-narration").defaults["audioKind"] == "narration"
    assert dt.get("audio-bed").defaults["audioKind"] == "music"
    assert dt.get("image-poster").defaults["aspect"] == "9:16"


# ── what the model is allowed to contribute ────────────────────────────


def test_a_script_is_removed_with_its_payload():
    assert dt.sanitise("<p>본문</p><script>steal()</script>") == "<p>본문</p>"
    assert "steal" not in dt.sanitise("<script>steal()</script>")


def test_handlers_and_remote_references_are_stripped():
    assert "onclick" not in dt.sanitise('<p onclick="x()">본문</p>')
    assert "evil.example" not in dt.sanitise('<img src="https://evil.example/p.png">')
    # A style block would let the model rewrite the seed's own layout.
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
    # Numbered in order, which is what the seed prints in the corner.
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
    html = dt.render(dt.get("doc-report"), title='</title><script>x()</script>', tokens={}, body="")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


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
    """A body layout in the first position gives a document with no title page."""
    _, blocks = page._parse_outline(
        '{"title":"제목","blocks":[{"title":"본문","layout":"bullets"}]}', dt.get("deck-editorial")
    )
    assert blocks[0]["layout"] == "cover"


def test_a_count_stated_in_the_request_is_honoured_within_bounds():
    assert page.requested_blocks("8장짜리 발표") == 8
    assert page.requested_blocks("발표 자료") is None
    assert page.requested_blocks("200장") == 24  # clamped to the runtime ceiling


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
    assert composed.endswith("aspect ratio 16:9")


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
    """Enough of `httpx.AsyncClient` for an outline and its blocks."""

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
    events = [
        event
        async for event in page.write(
            request="연구실 장비 관리 발표 자료",
            model="mock-model",
            api_key="mock-key",
            template=template,
            **kwargs,
        )
    ]
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
    # A fenced answer is unwrapped rather than printed as literal backticks.
    assert "```" not in html and "<li>보유 42대</li>" in html
    # And the script the model appended never reaches the file.
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
    # The empty block is absent from the file rather than present and hollow.
    assert finished["html"].count('class="slide') == 2


@pytest.mark.asyncio
async def test_an_outline_that_cannot_be_parsed_ends_the_turn_without_billing(monkeypatch):
    events, posts = await _write(monkeypatch, dt.get("doc-report"), ["설명만 하고 JSON 은 없음"])

    assert len(posts) == 1  # no block calls were made
    assert not any(e["type"] == "page" for e in events)
    assert any(e["type"] == "error" for e in events)


def test_a_heading_the_model_repeats_is_dropped_rather_than_printed_twice():
    """The wrapper writes the block's title; a body `<h2>` renders it again.

    Dropped with its words, not unwrapped: leaving the text behind keeps the
    duplication, which is the part a reader actually sees.
    """
    assert dt.sanitise("<h2>판단 결과</h2><p>본문</p>") == "<p>본문</p>"
    assert dt.sanitise("<h1>표지 제목</h1><p>부제</p>") == "<p>부제</p>"
    # Sub-headings inside a block are still the model's to write.
    assert dt.sanitise("<h3>세부</h3>") == "<h3>세부</h3>"


# ── choosing and unchoosing ────────────────────────────────────────────


def test_clearing_and_omitting_are_both_no_template():
    """`""` is somebody clearing the choice; `None` is a payload that was silent."""
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
        # An image template shapes a prompt; it never becomes a session's shape.
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
    """Guards the shape of this module, not its logic.

    `_resolved_template_id` was first written between `@router.patch` and the
    handler it decorates, which unregisters the route without failing anything
    that only imports the module.
    """
    patchable = {
        route.path
        for route in sessions_router.router.routes
        if "PATCH" in getattr(route, "methods", set())
    }
    assert "/sessions/{session_id}" in patchable


def test_a_plan_is_salvaged_out_of_malformed_json():
    """One dropped quote should not cost the whole call.

    Observed: a small model answered `{"title: "…"` — legible to anybody
    reading it, and `json.loads` refuses it. The outline is already paid for.
    """
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
    """Salvage must not invent a plan out of an answer that refused to make one."""
    title, blocks = page._parse_outline("구성을 만들 수 없습니다.", dt.get("doc-brief"))
    assert not blocks and not title


# ── rewriting one block ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_rewrite_sees_the_document_around_it(monkeypatch):
    """Otherwise the new block repeats what the one before it already said."""
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
    # The plan, the neighbours as written, and the note — labelled, so it does
    # not read as part of the original request.
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
#
# The writing model cannot make a picture and is not allowed to point at one.
# What a person can do is put a picture this workspace already made *into* a
# page, which the server does by inlining the bytes — so the one `src` that
# survives sanitising is one that fetches nothing.


#: One transparent pixel, PNG. Small enough to read in a diff.
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
    """The seed owns the look; a `style=` is the one thing that could beat it.

    `class` stays, because that is how a block reaches the names its own seed
    styles — `lead` on a cover, `cols` on a split slide.
    """
    kept = dt.sanitise('<p class="lead" style="color:#f0f">표지 한 줄</p>')
    assert kept == '<p class="lead">표지 한 줄</p>'


def test_a_rewrite_keeps_the_picture_and_not_the_described_figure():
    """A rewrite is about the words, and the model cannot write a picture.

    Without this, asking for better wording on a block would silently delete
    the illustration somebody put in it. A figure written *in words* is a
    different thing: the model can write that again, and keeping it would
    leave the block saying it twice.
    """
    block = "<ul><li>보유 42대</li></ul>" + dt.figure(
        mime="image/png", data_b64=_PIXEL, alt="자물쇠", caption="그림 1"
    )
    kept = dt.pictures_in(block)
    assert kept.startswith("<figure>") and f"base64,{_PIXEL}" in kept
    assert "<figcaption>그림 1</figcaption>" in kept
    assert dt.pictures_in("<figure><p>표 1 설명</p><figcaption>설명</figcaption></figure>") == ""


def test_a_cover_that_came_back_empty_is_still_a_cover():
    """The title page is structural; the block that fills it is not.

    A cover call that returns nothing used to drop the whole `<section>`, and
    the document opened on a body slide — no title page, and the exports key
    off slide one being the cover.
    """
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
    # A body block that failed is still left out.
    gapped = dt.assemble(
        template,
        [
            {"title": "표지", "layout": "cover", "html": "<p class='lead'>한 줄</p>"},
            {"title": "빈 장", "layout": "bullets", "html": ""},
        ],
    )
    assert "빈 장" not in gapped
