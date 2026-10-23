"""A restructure of the open report or deck is written as re-planned: no outline card, no questions."""

from __future__ import annotations

import copy
import json

import pytest
from test_grounded_document_completion import MODEL, SECTIONS, SLIDES, SOURCES, _Store

from app.routers import sessions

#: more parts than a plan written without a card may have
PLAN = {"title": "Synthetic plan",
        "sections": [{"heading": f"Part {i}", "points": []} for i in range(1, 13)],
        "slides": None}


async def _restructure(monkeypatch, surface, *, carry_from):
    store = _Store(surface)
    service, driver = {
        "report": (sessions.report_service, sessions._run_report),
        "slides": (sessions.deck_service, sessions._run_deck),
    }[surface]
    plan = {k: v for k, v in PLAN.items() if v is not None}
    if surface == "slides":
        plan = {"title": "Synthetic plan",
                "slides": [{"title": f"Slide {i}", "points": []} for i in range(1, 30)]}
    payload = ({"type": "report", "sections": copy.deepcopy(SECTIONS)} if surface == "report"
               else {"type": "deck", "slides": copy.deepcopy(SLIDES)})
    calls: list[dict] = []

    async def writer(**kwargs):
        calls.append(kwargs)
        if kwargs.get("approved_plan") is None:
            yield {"type": "proposal", "plan": copy.deepcopy(plan)}
            return
        yield {"type": "sources", "sources": copy.deepcopy(SOURCES)}
        yield copy.deepcopy(payload)
        yield {"type": "usage", "inputTokens": 2, "outputTokens": 3}

    async def artifact(_db, _session, **kwargs):
        store.artifacts.append(copy.deepcopy(kwargs))
        return "synthetic-artifact"

    async def settle_plan(**kwargs):
        store.plans.append(kwargs)

    monkeypatch.setattr(service, "write", writer)
    monkeypatch.setattr(sessions, "SessionLocal", lambda: store)
    monkeypatch.setattr(sessions, "_store_document", artifact)
    monkeypatch.setattr(sessions, "_settle_plan_turn", settle_plan)
    monkeypatch.setattr(sessions, "settle", lambda *_a, **kw: store.settlements.append(kw))
    monkeypatch.setattr(sessions, "record_searches", lambda *_a, **_kw: None)
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in driver(
            user_id=store.user.id, api_key="synthetic-not-a-credential",
            session_id=store.session.id, model=MODEL, request="자료를 바탕으로 문서를 만들어 줘",
            project_id=None, web_search=False, carry_from=carry_from,
            carry_note="전체를 세 부분으로 다시 짜 줘" if carry_from else "",
            untrusted_context=["[현재 문서]\n1. Part 1\n본문"])
    ]
    return store, events, calls


@pytest.mark.asyncio
async def test_a_report_restructure_asks_nothing_and_writes_the_new_outline_at_once(monkeypatch):
    store, events, calls = await _restructure(monkeypatch, "report", carry_from="old-artifact")
    assert calls[0]["may_ask"] is False
    assert len(calls) == 2 and calls[1]["approved_plan"]["sections"]
    assert "proposal" not in [e["type"] for e in events]
    assert store.plans == [] and len(store.artifacts) == 1


@pytest.mark.asyncio
async def test_a_new_long_report_still_offers_its_outline(monkeypatch):
    store, events, calls = await _restructure(monkeypatch, "report", carry_from=None)
    assert calls[0]["may_ask"] is True
    assert len(calls) == 1 and "proposal" in [e["type"] for e in events]
    assert len(store.plans) == 1 and store.artifacts == []


@pytest.mark.asyncio
async def test_a_deck_restructure_writes_its_new_outline_at_once(monkeypatch):
    store, events, calls = await _restructure(monkeypatch, "slides", carry_from="old-artifact")
    assert "proposal" not in [e["type"] for e in events]
    assert store.plans == []
    assert any(c.get("approved_plan") for c in calls)
