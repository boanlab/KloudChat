"""Successful document action acknowledgements need no model-accuracy disclaimer.

Writers and persistence are synthetic; these are route-contract tests, not an
assertion that a live model followed the document-body grounding instruction.
"""

from __future__ import annotations

import copy
import json

import pytest

from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.user import User
from app.routers import sessions
from app.services import design_templates

MODEL = {
    "id": "synthetic/document-model",
    "creditCost": 0,
    "inputCreditCost": 0,
    "knowledgeCutoff": "2024-06",
}
SOURCES = [{"title": "Synthetic reference", "url": "https://example.test/source"}]
SECTIONS = [{"id": "s1", "heading": "Synthetic section", "content": "Source-based body [1]."}]
SLIDES = [{"id": "p1", "layout": "bullets", "title": "Synthetic slide", "bullets": ["Fact [1]"]}]
BLOCKS = [{"layout": "text", "title": "Synthetic block", "html": "<p>Source-based body [1].</p>"}]
HTML = "<html><body><p>Source-based body [1].</p></body></html>"
SURFACES = ["report", "slides", "page_report", "page_html"]


class _Store:
    def __init__(self, surface, *, session_exists=True):
        kind = SessionKind.report if surface in ("report", "page_report") else SessionKind.slides
        self.session = ChatSession(id="synthetic-session", user_id="synthetic-user", kind=kind)
        self.user = User(id="synthetic-user", email="synthetic@example.test", password_hash="hash")
        self.session_exists = session_exists
        self.rows = []
        self.artifacts = []
        self.plans = []
        self.settlements = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, _key):
        if model is ChatSession:
            return self.session if self.session_exists else None
        return self.user if model is User else None

    def add(self, row):
        self.rows.append(row)

    async def commit(self):
        self.commits += 1


async def _run(monkeypatch, surface, *, outcome="complete", question="근거에 따라 문서를 작성해줘"):
    store = _Store(surface, session_exists=outcome != "missing_session")
    service, driver = {
        "report": (sessions.report_service, sessions._run_report),
        "slides": (sessions.deck_service, sessions._run_deck),
        "page_report": (sessions.page_service, sessions._run_page),
        "page_html": (sessions.page_service, sessions._run_page),
    }[surface]
    payload = {
        "report": {"type": "report", "sections": copy.deepcopy(SECTIONS)},
        "slides": {"type": "deck", "slides": copy.deepcopy(SLIDES)},
        "page_report": {"type": "page", "html": HTML, "blocks": copy.deepcopy(BLOCKS)},
        "page_html": {"type": "page", "html": HTML, "blocks": copy.deepcopy(BLOCKS)},
    }[surface]
    original_payload = copy.deepcopy(payload)

    async def writer(**_kwargs):
        if outcome == "failure":
            raise RuntimeError("synthetic empty writer failure")
        if outcome == "proposal":
            yield {"type": "proposal", "plan": {"title": "Synthetic plan", "sections": []}}
            return
        if outcome == "needs":
            yield {"type": "needs", "questions": [{"id": "q1", "text": "Which scope?"}]}
            return
        if outcome == "empty":
            return
        yield {"type": "sources", "sources": copy.deepcopy(SOURCES)}
        yield payload
        yield {"type": "usage", "inputTokens": 2, "outputTokens": 3}

    async def artifact(_db, _session, **kwargs):
        store.artifacts.append(copy.deepcopy(kwargs))
        return "synthetic-artifact"

    async def plan(**kwargs):
        store.plans.append(copy.deepcopy(kwargs))

    monkeypatch.setattr(service, "write", writer)
    monkeypatch.setattr(sessions, "SessionLocal", lambda: store)
    monkeypatch.setattr(sessions, "_store_document", artifact)
    monkeypatch.setattr(sessions, "_settle_plan_turn", plan)
    monkeypatch.setattr(
        sessions, "settle", lambda *_args, **kwargs: store.settlements.append(kwargs)
    )
    monkeypatch.setattr(sessions, "record_searches", lambda *_args, **_kwargs: None)
    kwargs = {
        "user_id": store.user.id,
        "api_key": "synthetic-not-a-credential",
        "session_id": store.session.id,
        "model": MODEL,
        "request": question,
        "project_id": None,
        "web_search": False,
    }
    if surface.startswith("page_"):
        kwargs["template"] = design_templates.get("doc-report")
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in driver(**kwargs)
    ]
    assert payload == original_payload
    return store, events


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize(
    "question",
    [
        "근거에 따라 문서를 작성해줘",
        "Write a source-based document.",
        'Translate "안녕하세요" into English and create a document.',
    ],
)
async def test_completed_document_action_acknowledgement_has_no_accuracy_notice(
    monkeypatch, surface, question
):
    store, events = await _run(monkeypatch, surface, question=question)
    deltas = [event["text"] for event in events if event["type"] == "delta"]
    assert deltas == []
    assert [event["type"] for event in events][-3:] == ["artifact", "usage", "done"]
    completions = [
        row for row in store.rows if isinstance(row, Message) and row.role is Role.assistant
    ]
    assert len(completions) == 1
    assert completions[0].content.strip()
    assert "학습 기준" not in completions[0].content
    assert "training cutoff" not in completions[0].content
    assert completions[0].model == MODEL["id"]
    assert completions[0].usage == {"inputTokens": 2, "outputTokens": 3, "credits": 0}
    assert len(store.artifacts) == store.commits == 1

    artifact = store.artifacts[0]["data"]
    assert "학습 기준" not in json.dumps(artifact, ensure_ascii=False)
    if surface == "report":
        assert artifact["sources"] == SOURCES
        assert artifact["sections"] == [{**SECTIONS[0], "level": 1, "status": "done"}]
    elif surface == "slides":
        assert artifact["slides"] == SLIDES
    elif surface == "page_report":
        assert artifact["sources"] == SOURCES
        assert artifact["sections"][0]["content"] == BLOCKS[0]["html"]
        assert artifact["sections"][0]["format"] == "html"
    else:
        assert artifact["content"] == HTML
        assert artifact["blocks"] == BLOCKS
    # The report-template branch's pre-existing accounting behavior is out of scope.
    assert len(store.settlements) == (0 if surface == "page_report" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("outcome", ["proposal", "needs", "empty", "failure", "missing_session"])
async def test_uncompleted_document_does_not_emit_or_persist_a_success_notice(
    monkeypatch, surface, outcome
):
    store, events = await _run(monkeypatch, surface, outcome=outcome)
    assert store.artifacts == []
    assert not any(event["type"] in ("artifact", "delta") for event in events)
    assert all(
        not isinstance(row, Message) or (not row.content and row.failure is not None)
        for row in store.rows
    )
    assert len(store.plans) == int(outcome in ("proposal", "needs"))
    assert store.settlements == []
    assert events[-1]["type"] == "done"
