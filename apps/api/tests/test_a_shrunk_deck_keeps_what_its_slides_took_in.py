"""A shrink of an open deck: merged slides keep what they took in, no questions, the count met."""

from __future__ import annotations

import json

import pytest
from test_grounded_document_completion import MODEL, _Store

from app.routers import sessions
from app.services import deck as deck_service
from app.services import revise

OLD = [
    {"title": "Q3 Results", "layout": "title"},
    {"title": "Q3 Performance", "layout": "table",
     "rows": [["Metric", "Q3"], ["Installs", "412"], ["Margin", "21%"]]},
    {"title": "Install Trend", "layout": "bullets", "bullets": ["355 in Q2, 412 in Q3"]},
    {"title": "Q4 Priorities", "layout": "cards",
     "cards": [["Grow", "Reach 480 installs"], ["Hire", "Two crews"]]},
    {"title": "Warehouse Approval Request", "layout": "bands",
     "bands": [["Ask", "Approve $1.2M for a second warehouse"]]},
]


def test_a_slide_that_took_in_a_dropped_slides_figures_is_not_overwritten_by_its_old_namesake():
    new = [
        {"title": "Q3 Results", "layout": "title"},
        {"title": "Q3 Performance", "layout": "table",
         "rows": [["Metric", "Q3"], ["Installs", "412"], ["Margin", "21%"]]},
        {"title": "Install Trend", "layout": "bullets", "bullets": ["355 in Q2, 412 in Q3"]},
        # old 4 and 5 merged under 4's title
        {"title": "Q4 Priorities", "layout": "bullets",
         "bullets": ["Reach 480 installs", "Approve $1.2M for a second warehouse"]},
    ]
    out, carried = revise.carry_parts(new, OLD, is_deck=True, mentioned="Shrink to 4 slides")
    assert out[3]["bullets"] == new[3]["bullets"]
    # untouched slides still come back as they were
    assert carried == 2 and out[1]["rows"] == OLD[1]["rows"]


def test_a_rewritten_slide_with_only_its_own_figures_still_takes_the_old_one_back():
    new = [dict(OLD[0]), {"title": "Q4 Priorities", "layout": "bullets",
                          "bullets": ["Reach 480 installs"]}, dict(OLD[4])]
    out, carried = revise.carry_parts(new, OLD, is_deck=True, mentioned="")
    assert out[1]["cards"] == OLD[3]["cards"] and carried == 2


@pytest.mark.asyncio
async def test_a_reshape_of_the_open_deck_asks_no_questions_and_may_merge_to_its_count(
        monkeypatch):
    store = _Store("slides")
    given: list[dict] = []

    async def writer(**kwargs):
        given.append(kwargs)
        yield {"type": "proposal", "plan": {"title": "Plan", "slides": []}}

    async def plan(**_kwargs):
        return None

    monkeypatch.setattr(sessions.deck_service, "write", writer)
    monkeypatch.setattr(sessions, "SessionLocal", lambda: store)
    monkeypatch.setattr(sessions, "_settle_plan_turn", plan)
    base = {"user_id": store.user.id, "api_key": "k", "session_id": store.session.id,
            "model": MODEL, "request": "3장으로 줄여줘", "project_id": None,
            "web_search": False}
    for carry_from in ("old-deck", None):
        [chunk async for chunk in sessions._run_deck(**base, carry_from=carry_from)]
    reshaped, fresh = given
    assert reshaped["may_ask"] is False and reshaped["reshape"] is True
    assert fresh["may_ask"] is True and fresh["reshape"] is False


class _Payload:
    status_code = 200

    def __init__(self, text: str):
        self._text = text

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._text}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class _SameOutline:
    """An outline model that keeps the five slides, size retry or not."""

    def __init__(self, posts: list[dict]):
        self.posts = posts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _path: str, *, json: dict):
        self.posts.append(json)
        return _Payload(FIVE)


FIVE = json.dumps({"title": "Q3 Results", "slides": [
    {"title": "Q3 Results", "layout": "title"},
    {"title": "Q3 Performance", "layout": "table"},
    {"title": "Install Trend", "layout": "chart"},
    {"title": "Q4 Priorities", "layout": "cards"},
    {"title": "Warehouse Approval Request", "layout": "statement"},
]})


async def _plan(monkeypatch, *, reshape: bool) -> list[dict]:
    posts: list[dict] = []

    async def litellm_config():
        return "http://mock-litellm", "unused"

    monkeypatch.setattr(deck_service.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(deck_service.httpx, "AsyncClient", lambda **_kw: _SameOutline(posts))
    return [event async for event in deck_service.write(
        request="Q3 실적 발표 자료를 슬라이드 3장으로 만들어줘. 설치 412건, 마진 21%.",
        model="m", api_key="k", may_ask=False, web_search=False, reshape=reshape)]


@pytest.mark.asyncio
async def test_a_shrink_whose_outline_misses_the_count_twice_merges_slides_to_it(monkeypatch):
    events = await _plan(monkeypatch, reshape=True)
    proposal = next(e["plan"] for e in events if e["type"] == "proposal")
    titles = [s["title"] for s in proposal["slides"]]
    assert len(titles) == 3 and titles[0] == "Q3 Results"
    # every old subject is still in the plan
    for subject in ("Q3 Performance", "Install Trend", "Q4 Priorities",
                    "Warehouse Approval Request"):
        assert any(subject in t for t in titles)


@pytest.mark.asyncio
async def test_a_new_deck_whose_outline_misses_the_count_still_says_so(monkeypatch):
    events = await _plan(monkeypatch, reshape=False)
    assert not any(e["type"] == "proposal" for e in events)
    assert any(e["type"] == "error" and "3장" in e["message"] for e in events)


def test_merging_to_a_count_keeps_the_cover_and_never_goes_below_it():
    plan = [{"title": "Cover", "layout": "title"}, {"title": "A", "layout": "bullets"},
            {"title": "Part 2", "layout": "section"}, {"title": "B", "layout": "bullets"}]
    out = deck_service.merge_to_count(plan, 2)
    assert out == [{"title": "Cover", "layout": "title"}, {"title": "A · B", "layout": "bullets"}]
    assert deck_service.merge_to_count(plan, 1) == plan
