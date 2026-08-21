"""The design-system layer: what it normalises, what it says, and what it draws.

Three properties are worth more than the rest, and each has a test that fails
without the change it guards:

1. A project with no design system produces exactly what it produced before.
2. A design system's accent replaces the model's colour choice rather than
   competing with it — and the model is not asked for a colour it cannot pick.
3. The rules that reach the model are the ones that can act on the surface.
"""

from __future__ import annotations

import io
import re
import zipfile

import pytest
from conftest import both_passes
from fastapi import HTTPException

from app.models.chat import SessionKind
from app.models.user import User
from app.models.workspace import DesignSystem, Project
from app.routers import workspace as workspace_router
from app.services import deck as deck_service
from app.services import deck_export, design, imagegen, report_export
from app.services.workspace_context import _load_design_system

# ── tokens ─────────────────────────────────────────────────────────────


def test_absent_tokens_are_the_previous_defaults():
    assert design.normalise_tokens(None) == design.DEFAULT_TOKENS
    # The same constant the deck exporter draws with, so a project with no
    # design system comes out identically either way.
    assert design.DEFAULT_TOKENS["accent"] == deck_service._ACCENT


def test_a_bad_field_falls_back_without_taking_the_good_ones_with_it():
    tokens = design.normalise_tokens(
        {"accent": "#0F766E", "ink": "not-a-colour", "font": "comic"}
    )
    assert tokens["accent"] == "#0f766e"
    assert tokens["ink"] == design.DEFAULT_TOKENS["ink"]
    assert tokens["font"] == design.DEFAULT_TOKENS["font"]


def test_unknown_craft_keys_are_dropped_rather_than_stored():
    assert design.craft_keys(["typography", "wharrgarbl", "typography"]) == ["typography"]


# ── what reaches the model ─────────────────────────────────────────────


def _system(**kwargs) -> DesignSystem:
    return DesignSystem(owner_id="u1", name="문서용", **kwargs)


def test_a_tokens_only_design_says_nothing_to_the_model():
    """Colour is for the renderer. A header with no rules under it is a bill."""
    assert design.prompt_block(_system(tokens={"accent": "#0f766e"}), SessionKind.report) == ""


def test_craft_reaches_only_the_surfaces_it_can_act_on():
    row = _system(craft=["restraint", "typography"])
    report = design.prompt_block(row, SessionKind.report)
    chat = design.prompt_block(row, SessionKind.chat)

    assert "제목 단계는" in report  # typography
    assert "이모지를 쓰지 않는다" in report  # restraint
    # Typography is about headings in a document; a chat turn has none.
    assert "제목 단계는" not in chat
    assert "이모지를 쓰지 않는다" in chat
    # Image prompts are composed in English by `image_clause`; Korean prose
    # about heading depth would be noise in one.
    assert design.prompt_block(row, SessionKind.image) == ""


def test_the_body_is_carried_under_the_design_systems_name():
    block = design.prompt_block(_system(body="한 문장에 한 사실."), SessionKind.report)
    assert block.startswith("# 디자인 시스템 — 문서용")
    assert "한 문장에 한 사실." in block


def test_no_design_system_adds_no_block():
    assert design.prompt_block(None, SessionKind.report) == ""


# ── image prompts ──────────────────────────────────────────────────────


def test_the_image_prompt_carries_the_colour_and_the_house_style():
    clause = design.image_clause(
        _system(tokens={"accent": "#7a1f3d"}, image_style="bold graphic")
    )
    composed = imagegen.compose_prompt("표지 그림", aspect="16:9", style="사진", design=clause)

    assert composed.startswith("표지 그림")
    assert "#7a1f3d" in composed
    assert "bold graphic" in composed
    # The chip the person picked for this picture still leads the design's
    # standing instruction, and the aspect stays last.
    assert composed.index("photorealistic") < composed.index("bold graphic")
    assert composed.endswith("aspect ratio 16:9")


def test_an_image_prompt_without_a_design_system_is_unchanged():
    assert imagegen.compose_prompt("표지", aspect="1:1", style="사진") == imagegen.compose_prompt(
        "표지", aspect="1:1", style="사진", design=""
    )


# ── the deck outline ───────────────────────────────────────────────────


class _Response:
    def __init__(self, text: str):
        self.status_code = 200

    def raise_for_status(self):
        return None


class _OutlineClient:
    """Enough of `httpx.AsyncClient` for one outline and one slide."""

    def __init__(self, responses: list[str], posts: list[dict]):
        self.responses = responses
        self.posts = posts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _path: str, *, json: dict):
        self.posts.append(json)
        return _Payload(self.responses.pop(0))


class _Payload:
    def __init__(self, text: str):
        self._text = text
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": self._text}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


async def _run_deck(monkeypatch, tokens):
    responses = [
        (
            '{"title":"검증 발표","subtitle":"부제","theme":"청록",'
            '"slides":[{"title":"검증 발표","layout":"title"},'
            '{"title":"핵심","layout":"bullets"}]}'
        ),
        '{"bullets":["하나","둘"],"notes":"설명"}',
    ]
    posts: list[dict] = []

    async def litellm_config():
        return "http://mock-litellm", "unused"

    monkeypatch.setattr(deck_service.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(
        deck_service.httpx, "AsyncClient", lambda **_kwargs: _OutlineClient(responses, posts)
    )
    events = await both_passes(
        deck_service, request="발표 자료를 만들어줘", model="m", api_key="k", tokens=tokens
    )
    return events, posts


@pytest.mark.asyncio
async def test_the_design_systems_accent_replaces_the_models_choice(monkeypatch):
    # Deliberately not one of `deck._THEMES`: with a palette colour here the
    # assertion would also pass if the model's answer were still being read.
    events, posts = await _run_deck(monkeypatch, {"accent": "#7a1f3d", "font": "gothic"})

    deck = next(event for event in events if event["type"] == "deck")
    assert {slide["accent"] for slide in deck["slides"]} == {"#7a1f3d"}
    # The model still answered "청록"; it was not consulted.
    outline_prompt = posts[0]["messages"][-1]["content"]
    assert "theme 은 주제에 맞는" not in outline_prompt
    assert "청록" not in outline_prompt


@pytest.mark.asyncio
async def test_without_a_design_system_the_model_still_picks_the_colour(monkeypatch):
    events, posts = await _run_deck(monkeypatch, None)

    deck = next(event for event in events if event["type"] == "deck")
    assert {slide["accent"] for slide in deck["slides"]} == {deck_service._THEMES["청록"]}
    assert "theme 은 주제에 맞는" in posts[0]["messages"][-1]["content"]


# ── exports ────────────────────────────────────────────────────────────

_SLIDES = [
    {"layout": "title", "title": "표지", "body": "부제", "accent": "#7a1f3d"},
    {"layout": "bullets", "title": "본문", "bullets": ["하나", "둘"], "accent": "#7a1f3d"},
]
_SECTIONS = [{"heading": "요약", "content": "본문 한 줄.\n\n- 항목"}]
_TOKENS = {"accent": "#7a1f3d", "ink": "#111827", "muted": "#6b7280", "font": "serif"}


def _pptx_xml(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return "".join(
            archive.read(name).decode("utf-8", "replace")
            for name in archive.namelist()
            if name.startswith("ppt/slides/slide")
        )


#: reportlab stamps a creation date and a document id into every file, so two
#: runs of the same export differ in those bytes and nowhere else. Stripped so
#: the comparison is about what was drawn.
_PDF_STAMPS = re.compile(rb"/(?:CreationDate|ModDate)\s*\([^)]*\)|/ID\s*\[[^]]*\]")


def _drawn(blob: bytes) -> bytes:
    return _PDF_STAMPS.sub(b"", blob)


def test_a_deck_without_a_design_system_exports_exactly_what_it_did_before():
    """The regression this whole change had to avoid."""
    assert deck_export.to_pptx("제목", _SLIDES) == deck_export.to_pptx("제목", _SLIDES, tokens=None)
    assert _drawn(deck_export.to_pdf("제목", _SLIDES)) == _drawn(
        deck_export.to_pdf("제목", _SLIDES, tokens=None)
    )


def test_the_design_systems_face_reaches_powerpoint():
    plain = _pptx_xml(deck_export.to_pptx("제목", _SLIDES))
    styled = _pptx_xml(deck_export.to_pptx("제목", _SLIDES, tokens=_TOKENS))

    assert "맑은 고딕" in plain and "바탕" not in plain
    assert "바탕" in styled and "Georgia" in styled
    # Ink follows the design too, not only the typeface.
    assert "111827" in styled


def test_the_deck_pdf_changes_only_when_a_design_system_is_given():
    assert _drawn(deck_export.to_pdf("제목", _SLIDES)) != _drawn(
        deck_export.to_pdf("제목", _SLIDES, tokens=_TOKENS)
    )


def test_report_headings_take_the_accent_and_the_body_stays_black():
    with zipfile.ZipFile(
        io.BytesIO(report_export.to_hwpx("제목", _SECTIONS, tokens=_TOKENS))
    ) as archive:
        header = archive.read("Contents/header.xml").decode()

    # Title (id 2) and section heading (id 3) carry it; body (id 0) does not.
    assert 'id="2" height="1600" textColor="#7a1f3d"' in header
    assert 'id="3" height="1300" textColor="#7a1f3d"' in header
    assert 'id="0" height="1000" textColor="#000000"' in header


def test_a_report_without_a_design_system_stays_black():
    with zipfile.ZipFile(
        io.BytesIO(report_export.to_hwpx("제목", _SECTIONS))
    ) as archive:
        header = archive.read("Contents/header.xml").decode()
    assert "#7a1f3d" not in header
    assert header.count('textColor="#000000"') == len(report_export._HWPX_CHAR_SHAPES)


def test_every_report_format_still_produces_a_file_with_a_design_system():
    assert report_export.to_pdf("제목", _SECTIONS, tokens=_TOKENS)[:4] == b"%PDF"
    assert report_export.to_docx("제목", _SECTIONS, tokens=_TOKENS)[:2] == b"PK"
    assert report_export.to_hwpx("제목", _SECTIONS, tokens=_TOKENS)[:2] == b"PK"


# ── who may wear what ──────────────────────────────────────────────────


class _Rows:
    """`db.get` and nothing else — these two functions ask for nothing else."""

    def __init__(self, rows: dict):
        self.rows = rows

    async def get(self, _model, row_id):
        return self.rows.get(row_id)


def _user(user_id: str) -> User:
    return User(id=user_id, email=f"{user_id}@x.io", password_hash="x", name=user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "shared", "expected"),
    [("u1", False, None), ("u1", True, None), ("u2", True, None), ("u2", False, 404)],
)
async def test_a_design_system_must_be_one_the_caller_can_see(owner, shared, expected):
    """An id from another account would attach that account's look to this project."""
    row = DesignSystem(id="d1", owner_id=owner, name="x", shared=shared)
    db = _Rows({"d1": row})

    if expected is None:
        assert await workspace_router._validate_design_system_id(db, _user("u1"), "d1") is None
    else:
        with pytest.raises(HTTPException) as raised:
            await workspace_router._validate_design_system_id(db, _user("u1"), "d1")
        assert raised.value.status_code == expected


@pytest.mark.asyncio
async def test_no_design_system_asked_for_is_not_a_lookup():
    assert await workspace_router._validate_design_system_id(_Rows({}), _user("u1"), None) is None


@pytest.mark.asyncio
async def test_a_look_that_stopped_being_shared_drops_out_rather_than_failing_the_turn():
    """Somebody else's edit must not break this person's work."""
    project = Project(id="p1", user_id="u1", name="p", design_system_id="d1")
    unshared = DesignSystem(id="d1", owner_id="u2", name="x", shared=False)

    assert await _load_design_system(_Rows({"d1": unshared}), _user("u1"), project) is None
    # And a deleted one is the same story.
    assert await _load_design_system(_Rows({}), _user("u1"), project) is None


# ── the project column ─────────────────────────────────────────────────


def test_a_project_wears_nothing_by_default():
    """The migration's whole safety argument in one line."""
    assert Project(user_id="u1", name="p").design_system_id is None
