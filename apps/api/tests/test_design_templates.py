"""The rendering catalogue: what it promises, and what it refuses to pass on.

Three properties carry the feature, and each has a test that fails without the
code it guards:

1. Every shipped template is complete enough to be offered — a card with no
   preview, or an outline prompt naming a layout the seed does not style, is a
   promise the product cannot keep.
2. The model writes content and never layout. What it sends is reduced to the
   seed's vocabulary before it reaches a file somebody downloads.
3. A session with no template produces exactly what it produced before.
4. A format belongs to the project as much as to the conversation: work
   started inside a project begins in that project's format, and the composer
   still decides the conversation it is opened in.
"""

from __future__ import annotations

import re
import time

import pytest
from conftest import both_passes
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.models.chat import SessionKind
from app.models.workspace import Project
from app.routers import sessions as sessions_router
from app.routers import workspace as workspace_router
from app.schemas.workspace import DesignTemplateOut
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


@pytest.mark.parametrize("template", dt.all_templates(), ids=lambda t: t.id)
def test_a_writing_template_says_what_it_will_be_read_against(template):
    """A rubric is a writing template's third file, not an optional one.

    A media template has nothing to review — the picture or the clip comes back
    whole — so it ships none, and a card that showed an empty heading for it
    would be worse than one that shows nothing.
    """
    if template.kind in dt.HTML_KINDS:
        assert len(template.checks) >= 3, template.id
        # Read as sentences on the card, so nothing may arrive still bulleted.
        assert not any(line.startswith("-") for line in template.checks), template.id
    else:
        assert not template.checks


def test_the_card_carries_the_rules_the_result_will_be_read_against():
    """The one thing that separates two shapes of the same kind.

    회의록 and 안내문 are both documents with a title and a description; what
    makes them different choices is that one keeps decisions apart from
    discussion and the other wants grounds and an effective date. That lived
    only in `checklist.md` until it went on the wire.
    """
    card = DesignTemplateOut.of(dt.get("doc-minutes"))
    assert card.checks == list(dt.get("doc-minutes").checks)
    assert any("실행 항목" in line for line in card.checks)
    # camelCase out, like every other field on this card.
    assert card.model_dump(by_alias=True)["checks"] == card.checks
    # No English half exists, and the card must not invent one — an image
    # template has no checklist at all and says so with an empty list.
    assert DesignTemplateOut.of(dt.get("image-poster")).checks == []


@pytest.mark.parametrize(
    "template", [t for t in dt.all_templates() if t.kind in dt.HTML_KINDS], ids=lambda t: t.id
)
def test_a_template_that_can_hold_code_says_so(template):
    """The tag list in `instructions.md` is the only list the model reads.

    A seed that styles `<code>` and instructions that do not mention it is a
    face nothing ever reaches — 최도현 and 오지훈 could not put a command or an
    exception name into any 서식, and the survey called that the thing to
    decide before adding another shape.
    """
    assert "code" in template.instructions


def test_what_is_inside_a_code_element_arrives_as_characters():
    """Its contents are text. That is the whole of what makes it a `<code>`.

    Every other rule in `sanitise` reads the fragment as markup, so left alone
    a sample of `<div>` became a real division and one of `<b>` real bold —
    the quotation stopped being the thing it was quoting.
    """
    kept = dt.sanitise("<p>이렇게 <code><div class=\"x\"></code> 쓴다</p>")
    assert kept == '<p>이렇게 <code>&lt;div class="x"&gt;</code> 쓴다</p>'
    # A model writes a sample both ways, and neither may be escaped twice.
    assert dt.sanitise("<code>&lt;b&gt;</code>") == "<code>&lt;b&gt;</code>"
    assert dt.sanitise("<code>a &amp;&amp; b</code>") == "<code>a &amp;&amp; b</code>"
    assert dt.sanitise("<code>a & b</code>") == "<code>a &amp; b</code>"
    # And a script in there is a script somebody is reading about, not one
    # anything runs — it is shown rather than removed.
    assert dt.sanitise("<code><script>x()</script></code>") == (
        "<code>&lt;script&gt;x()&lt;/script&gt;</code>"
    )
    # The shape that was refused: a block of it. What is inside a `<pre>` is
    # whitespace-significant and arbitrarily long, and the file exporters read
    # markdown lines — a stack trace would arrive re-indented with half of it
    # read back as a bullet list, which is the loss this batch set out to fix.
    assert "pre" not in dt._ALLOWED_TAGS


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
_MUST_STYLE = (
    "h3", "table", "blockquote", "figure", "figcaption", "dl", "dd", "hr", "code",
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


def test_a_preview_wears_the_design_system_it_is_shown_under():
    """The card and the file it advertises are one look, or the card is a lie.

    The gallery and the design editor both ask for a template in the tokens the
    project actually wears, so what is on the card is what comes out of it.
    """
    html = dt.preview(dt.get("deck-editorial"), {"accent": "#0A7B57", "font": "serif"})
    assert "--accent: #0a7b57;" in html
    assert "Nanum Myeongjo" in html
    # Nothing was said about the ink, so the ink is what it always was.
    assert "--ink: #1a1a1a;" in html


def test_a_token_nobody_could_draw_never_reaches_the_preview():
    """The route is unauthenticated, so the query string arrives a stranger.

    Per field rather than wholesale, exactly as `normalise_tokens` falls back:
    the good accent below survives the two values beside it that are not.
    """
    html = dt.preview(
        dt.get("deck-editorial"),
        {"accent": "#0a7b57", "ink": "red; } body { display:none", "font": "comic"},
    )
    assert "--accent: #0a7b57;" in html
    assert "display:none" not in html
    assert "--ink: #1a1a1a;" in html
    assert "comic" not in html


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


def test_the_preview_route_takes_a_look_by_query_and_trusts_none_of_it():
    """The one door the tokens come through, and what it lets past.

    An iframe can ask only by address, so the look travels in the query string
    of a route with no session behind it. The document that comes back is still
    made of this image's own files and four values the renderers agree exist.
    """
    app = FastAPI()
    app.include_router(workspace_router.router)
    with TestClient(app) as client:
        worn = client.get(
            "/design-templates/deck-editorial/preview",
            params={"accent": "#0a7b57", "ink": "#111111", "muted": "#777777", "font": "serif"},
        )
        assert worn.status_code == 200
        assert "--accent: #0a7b57;" in worn.text
        assert "Nanum Myeongjo" in worn.text

        junk = client.get(
            "/design-templates/deck-editorial/preview",
            params={"accent": "</style><script>steal()</script>", "font": "wingdings"},
        )
        assert junk.status_code == 200
        assert "steal()" not in junk.text
        assert "--accent: #5b5bd6;" in junk.text

        # No look asked for is the look a project without a design system gets.
        bare = client.get("/design-templates/deck-editorial/preview")
        assert bare.status_code == 200
        assert "--accent: #5b5bd6;" in bare.text
        assert client.get("/design-templates/nothing-like-it/preview").status_code == 404


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


# ── the project's own formats ──────────────────────────────────────────


class _Rows:
    """`db.get` and nothing else — the fallback asks for nothing else."""

    def __init__(self, rows: dict):
        self.rows = rows

    async def get(self, _model, row_id):
        return self.rows.get(row_id)


def _project(**kwargs) -> Project:
    return Project(id="p1", user_id="u1", name="공문 프로젝트", **kwargs)


def test_a_project_starts_with_no_format_of_its_own():
    """The migration's safety argument in one line: nothing existing moves."""
    assert Project(user_id="u1", name="p").render_templates is None


def test_a_default_reaches_the_surface_it_was_set_for_and_no_other():
    """Why the column is a map: two surfaces, two answers, one lookup."""
    defaults = {"report": "doc-notice", "slides": "deck-proposal"}
    assert dt.default_for(defaults, SessionKind.report) == "doc-notice"
    assert dt.default_for(defaults, SessionKind.slides) == "deck-proposal"
    # A surface the project said nothing about keeps the built-in track.
    assert dt.default_for(defaults, SessionKind.image) is None
    assert dt.default_for({"report": "doc-notice"}, SessionKind.slides) is None


@pytest.mark.parametrize(
    "defaults",
    [
        None,
        {},
        # Written by a version of this image that shipped a template this one
        # no longer has — the one case the router's refusal cannot reach.
        {"report": "doc-gone"},
        # Or one that moved to another surface since it was chosen.
        {"report": "deck-editorial"},
    ],
)
def test_a_default_the_catalogue_cannot_place_is_the_built_in_track(defaults):
    assert dt.default_for(defaults, SessionKind.report) is None


async def test_a_new_session_begins_in_its_projects_format():
    """The finding itself: the shape is said once, in the project."""
    db = _Rows({"p1": _project(render_templates={"report": "doc-notice"})})

    started = await sessions_router._project_render_template(db, "p1", SessionKind.report)
    assert started == "doc-notice"
    # The same project's slides were never given one.
    assert await sessions_router._project_render_template(db, "p1", SessionKind.slides) is None


async def test_work_outside_a_project_is_not_a_lookup():
    none = await sessions_router._project_render_template(_Rows({}), None, SessionKind.report)
    assert none is None


async def test_the_composer_still_decides_this_conversation():
    """Precedence, at the two functions that hold it.

    The project seeds a session that has not chosen. Picking a format in the
    composer is a decision about that one conversation, which is why it is
    written onto the session and never asked of the project again.
    """
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
        # An image template shapes a prompt; there is no document to start.
        ({"image": "image-poster"}, 404),
        ({"report": "deck-editorial"}, 422),
        # Chat produces no document, so no surface of it can carry a format.
        ({"chat": "doc-brief"}, 422),
    ],
)
def test_a_project_format_the_surface_cannot_use_is_refused_like_the_composers(
    defaults, expected
):
    with pytest.raises(HTTPException) as raised:
        workspace_router._validated_render_templates(defaults)
    assert raised.value.status_code == expected


def test_an_attribute_stripper_cannot_be_stalled_by_whitespace():
    """A padded fragment is sanitised in the time a fragment takes.

    `\\s+` before an attribute name is quadratic — the engine consumes a whole
    run of whitespace at every position in it and then fails on the next
    literal. Model output reaching a regex like that is a request that costs
    the server seconds of CPU and the caller one space bar.

    The ceiling is generous on purpose: what it catches is the difference
    between linear and quadratic, not a slow machine.
    """
    padded = "<p" + " " * 60_000 + 'onclick="steal()" style="color:red" href="http://x">hi</p>'

    started = time.perf_counter()
    cleaned = dt.sanitise(padded)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, f"{elapsed:.1f}s"
    # And it is still doing the job it was slow at.
    assert "onclick" not in cleaned
    assert "style=" not in cleaned
    assert "href=" not in cleaned
