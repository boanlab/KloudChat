"""A model call LiteLLM's fallback answered is recorded where people look: the message's
routing (the 「외부 대체로 답함」 badge), and the audit log as `model.fallback`. A published bot
keeps its agent's output language in the frozen copy it answers with.
"""

from __future__ import annotations

import httpx
from sqlmodel import select
from test_publishing import _ask, _publish, world  # noqa: F401 — the fixture

from app.models.chat import Message, Role
from app.models.user import AuditEvent
from app.models.workspace import Agent
from app.routers import sessions
from app.services import agent as agent_service
from app.services import fallback_watch

_CLOUD_ID = "openai/gpt-cloud"


def test_the_proxy_headers_name_the_answering_model():
    asked = "local/m"
    assert fallback_watch.served_by({}, asked, "local/m") == ""
    assert fallback_watch.served_by({"x-litellm-attempted-fallbacks": "0"}, asked, "x") == ""
    hit = {"x-litellm-attempted-fallbacks": "1", "x-litellm-model-group": _CLOUD_ID}
    assert fallback_watch.served_by(hit, asked, "gpt-4.1-mini") == _CLOUD_ID
    # the group header naming the asked group: the body's model is the one that answered
    same = {"x-litellm-attempted-fallbacks": "2", "x-litellm-model-group": asked}
    assert fallback_watch.served_by(same, asked, "gpt-4.1-mini") == "gpt-4.1-mini"
    assert fallback_watch.attempts({"x-litellm-attempted-fallbacks": "n/a"}) == 0


def test_the_record_says_whether_the_answer_left_the_institution():
    asked = {"id": "local/m", "dataBoundary": "hybrid"}
    catalogue = [{"id": _CLOUD_ID, "dataBoundary": "external"},
                 {"id": "local/small", "dataBoundary": "self_hosted"}]
    out = fallback_watch.record(asked, _CLOUD_ID, catalogue)
    assert out["external"] is True and out["dataBoundary"] == "external"
    assert out["requestedModel"] == "local/m" and out["requestedBoundary"] == "hybrid"
    assert fallback_watch.record(asked, "local/small", catalogue)["external"] is False
    # not in the catalogue: cannot be shown to stay inside
    assert fallback_watch.record(asked, "mystery", catalogue)["external"] is True


async def test_the_stream_reader_reports_a_fallback_hop(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"model": "gpt-4.1-mini", "choices": [{"delta": {"content": "답"}}]}\n\n'
            'data: {"choices": [{"delta": {}, "finish_reason": "stop"}], '
            '"usage": {"prompt_tokens": 3, "completion_tokens": 1}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={
            "x-litellm-attempted-fallbacks": "1", "x-litellm-model-group": _CLOUD_ID})

    async def client(*_args, **_kwargs):
        return httpx.AsyncClient(base_url="http://litellm.test",
                                 transport=httpx.MockTransport(handler))

    async def roles():
        return agent_service.settings_store.model_roles_from({})

    monkeypatch.setattr(agent_service, "_client", client)
    monkeypatch.setattr(agent_service.settings_store, "model_roles", roles)
    async for kind, value in agent_service._stream_once("local/m", [], [], "u", "k"):
        if kind == "done":
            acc = value
    assert fallback_watch.served_by(acc.fallback_headers, "local/m", acc.actual_model) == _CLOUD_ID


async def test_a_fallback_answer_is_marked_and_audited(world, monkeypatch):  # noqa: F811
    world.catalogue.append({**world.catalogue[0], "id": _CLOUD_ID, "dataBoundary": "external",
                            "strictLocal": False})
    fixture_turn = agent_service.run_turn

    async def failed_over(model, messages, tools, ctx, **kwargs):
        yield {"type": "fallback_route", "routedModel": model, "servedBy": _CLOUD_ID,
               "attempts": 1}
        async for event in fixture_turn(model, messages, tools, ctx, **kwargs):
            yield event

    async def listed(*_args, **_kwargs):
        return {"models": list(world.catalogue)}

    monkeypatch.setattr(agent_service, "run_turn", failed_over)
    monkeypatch.setattr(sessions.model_service, "list_models", listed)
    pub = await _publish(world)
    sid, _stream = await _ask(world, pub["slug"], "휴학은 언제까지 신청해요?")
    async with world.local() as db:
        answer = (await db.exec(select(Message).where(
            Message.session_id == sid, Message.role == Role.assistant))).one()
        fallback = answer.routing["fallback"]
        assert fallback["servedBy"] == _CLOUD_ID and fallback["external"] is True
        audited = (await db.exec(select(AuditEvent).where(
            AuditEvent.action == "model.fallback"))).all()
        assert len(audited) == 1 and audited[0].severity == "warn"
        assert audited[0].target == sid and audited[0].actor_id == "owner"
        assert audited[0].event_metadata["publicationId"] == pub["id"]


async def test_a_bot_answers_in_its_agents_language(world):  # noqa: F811
    async with world.local() as db:
        agent = await db.get(Agent, "agent-1")
        agent.output_language = "en"
        db.add(agent)
        await db.commit()
    pub = await _publish(world)
    async with world.local() as db:
        snaps = (await db.exec(select(Agent).where(Agent.snapshot_of == "agent-1"))).all()
        assert [s.output_language for s in snaps] == ["en"]
    await _ask(world, pub["slug"], "휴학은 언제까지 신청해요?")
    (call,) = world.calls
    assert "This agent writes in English" in str(call["messages"])
