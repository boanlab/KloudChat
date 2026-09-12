"""Tool-authored answers have no answer-model charge, not a fictitious empty model run."""

import json
from dataclasses import replace

import pytest
from test_privacy import _external_model

from app.models.chat import ChatSession, Message, RoutingMode, TurnFailure
from app.models.user import AuditEvent, User
from app.routers import sessions
from app.services import agent
from app.services.tools.arithmetic import CALCULATE
from app.services.tools.base import Tool, ToolContext, ToolResult

ORIGIN = {
    "answerOrigin": "tool_result", "toolName": "calculate",
    "reasonCode": "division_by_zero", "actualModel": None,
}


def test_prior_model_attempt_without_model_name_or_usage_cannot_get_null_origin():
    result = ToolResult(content='{"reason":"division_by_zero"}', failed=True)
    assert agent._literal_zero_division_answer(
        CALCULATE, result, literal_preset=True, model_attempted=True,
    ) is None


@pytest.mark.parametrize("change", [
    "non_literal", "not_failed", "untrusted_runner", "reason", "invalid_json",
    "source", "write", "name", "missing_tool",
])
def test_null_origin_requires_the_real_literal_failure(change):
    result = ToolResult(content='{"reason":"division_by_zero"}', failed=change != "not_failed")
    tool = (
        replace(CALCULATE, run=lambda *_args: None) if change == "untrusted_runner" else CALCULATE
    )
    if change == "reason":
        result.content = '{"reason":"synthetic-secret"}'
    elif change == "invalid_json":
        result.content = "synthetic-secret"
    elif change == "source":
        tool = replace(CALCULATE, source="connector")
    elif change == "write":
        tool = replace(CALCULATE, read_only=False)
    elif change == "name":
        tool = replace(CALCULATE, name="other")
    elif change == "missing_tool":
        tool = None
    assert agent._literal_zero_division_answer(
        tool, result, literal_preset=change != "non_literal", model_attempted=False,
    ) is None


async def test_literal_error_emits_typed_origin_before_answer(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("a copied literal failure called a model")
        yield

    monkeypatch.setattr(agent, "_stream_once", forbidden)
    events = [event async for event in agent.run_turn(
        "synthetic/model", [], [CALCULATE], ToolContext(user_id="qa", session_id="qa"),
        preflight_tool="calculate", calculation_required=True, calculation_expression="12/0",
    )]
    expected = {"type": "tool_result_answer", **ORIGIN}
    assert expected in events
    assert events.index(expected) < next(
        index for index, event in enumerate(events) if event["type"] == "delta"
    )
    assert not any(event["type"] in {"model_route", "freshness_abstention"} for event in events)


def _database(monkeypatch, *, mode=RoutingMode.manual):
    user = User(id="synthetic-user", email="origin@example.test", password_hash="hash")
    session = ChatSession(id="synthetic-session", user_id=user.id, title="Provisional title")
    cost_route = {
        "mode": mode.value, "decision": "kept_quality", "reasonCode": "low_complexity",
        "requestedModel": "synthetic/paid", "selectedModel": "synthetic/paid",
        "classifierVersion": "synthetic-version", "classifierModel": "synthetic/classifier",
        "classifierInputTokens": 44, "classifierOutputTokens": 9,
    }
    audit = AuditEvent(id="synthetic-audit", actor_id=user.id, action="routing.auto",
                       target=session.id, event_metadata=dict(cost_route))
    added = []

    class Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            if model is AuditEvent and key == audit.id:
                return audit
            return session if model is ChatSession else user if model is User else None

        def add(self, row):
            added.append(row)

        async def commit(self):
            pass

    monkeypatch.setattr(sessions, "SessionLocal", Db)
    return user, session, cost_route, audit, added


@pytest.mark.parametrize("mode", list(RoutingMode))
@pytest.mark.parametrize("search", [None, "ok", "failed"])
@pytest.mark.parametrize("stop_after_answer", [False, True])
@pytest.mark.parametrize("mask_at_rest", [False, True])
async def test_literal_answer_persists_without_charging_or_enrichment_and_preserves_prior_work(
    monkeypatch, mode, search, stop_after_answer, mask_at_rest,
):
    user, session, cost_route, audit, added = _database(monkeypatch, mode=mode)
    recorded_searches = []

    def forbidden(*_args, **_kwargs):
        pytest.fail("a tool answer invoked a model, generation settlement or enrichment")

    async def lookup(_arguments, ctx):
        # Even pending work from an earlier tool must not be committed for this answer.
        ctx.pending_notes.append({"content": "synthetic pending note"})
        ctx.pending_artifacts.append({"content": "synthetic pending artifact"})
        return ToolResult(content="Synthetic lookup", failed=search == "failed")

    async def stop_after_delta(events, stopping):
        try:
            async for event in events:
                yield event
                if event["type"] == "delta":
                    stopping.set()
                    return
        finally:
            await events.aclose()

    if stop_after_answer:
        monkeypatch.setattr(sessions, "_until_stopped", stop_after_delta)
    for name in ["_store_artifacts", "_enrich_memory", "_enrichment_model", "_store_notes",
                 "settle", "charge_for_tokens"]:
        monkeypatch.setattr(sessions, name, forbidden)
    monkeypatch.setattr(sessions.chat_service, "generate_title", forbidden)
    monkeypatch.setattr(agent, "_stream_once", forbidden)
    monkeypatch.setattr(sessions, "record_searches", lambda _db, _user, count, **_kw:
                        recorded_searches.append(count))
    tool = Tool(name="web_search", description="synthetic", parameters={"type": "object"},
                run=lookup, label="search", read_only=True, wants_context=True)
    chunks = [chunk async for chunk in sessions._run_turn(
        user_id=user.id, api_key="synthetic-noncredential", auto_memory=True, session_id=session.id,
        model={**_external_model("synthetic/paid"), "inputCreditCost": 10, "creditCost": 20},
        messages=[{"role": "user", "content": "12/0"}], tools=[CALCULATE, tool],
        first_user_message="12/0", is_first_turn=True,
        preflight_tool="calculate", calculation_required=True, calculation_expression="12/0",
        preset_call=("web_search", {"query": "12/0"}) if search else None,
        routing={
            "action": "allow", "actualModel": "synthetic/paid",
            **({"costRouting": cost_route} if mode != RoutingMode.manual else {}),
        },
        routing_audit_id=audit.id if mode != RoutingMode.manual else None,
        mask_at_rest=mask_at_rest,
    )]
    events = [json.loads(chunk.removeprefix("data: ").strip()) for chunk in chunks]
    answer = next(row for row in added if isinstance(row, Message))
    assert answer.model is None and answer.routing == {"action": "allow", **ORIGIN}
    assert answer.usage == {"inputTokens": 0, "outputTokens": 0, "credits": 0}
    assert answer.artifact_ids is None
    assert answer.failure == (TurnFailure.stopped if stop_after_answer else None)
    assert recorded_searches == [1 if search else 0]
    assert session.title == "Provisional title"
    event_at = next(
        index for index, event in enumerate(events) if event["type"] == "tool_result_answer"
    )
    assert not any(event["type"] == "model_route" for event in events[event_at:])
    assert next(event for event in events if event["type"] == "usage")["credits"] == 0
    if mode != RoutingMode.manual:
        assert audit.event_metadata == {
            **cost_route, "action": "allow", **ORIGIN, "executedModel": None,
        }


@pytest.mark.parametrize("reports_model", [False, True])
async def test_model_composed_failure_keeps_attempt_and_charge_even_with_zero_reported_tokens(
    monkeypatch, reports_model,
):
    user, session, _, _, added = _database(monkeypatch)
    settlements = []
    calls = []

    async def stream(*_args, **_kwargs):
        calls.append(True)
        acc = agent._Accumulator()
        if reports_model:
            acc.actual_model = "synthetic/paid"
        if len(calls) == 1:
            acc.calls[0] = {"id": "model-call", "name": "calculate",
                            "arguments": '{"expression":"12/0"}'}
        else:
            acc.content = ["UNVERIFIED"]
            yield "delta", "UNVERIFIED"
        yield "done", acc

    async def no_artifact(**_kwargs):
        return None

    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(sessions, "_store_artifacts", no_artifact)
    monkeypatch.setattr(sessions, "_enrich_memory", no_artifact)
    monkeypatch.setattr(sessions, "record_searches", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sessions, "settle", lambda _db, _user, credits, **kwargs:
                        settlements.append((kwargs["reason"], credits)))
    chunks = [chunk async for chunk in sessions._run_turn(
        user_id=user.id, api_key="synthetic-noncredential", auto_memory=False,
        session_id=session.id,
        model={**_external_model("synthetic/paid"), "inputCreditCost": 10, "creditCost": 20},
        messages=[], tools=[CALCULATE], first_user_message="a supplied word problem",
        is_first_turn=False,
        preflight_tool="calculate", calculation_required=True,
    )]
    answer = next(row for row in added if isinstance(row, Message))
    assert calls and answer.model == "synthetic/paid"
    assert answer.usage["credits"] == 1
    assert ("chat.completion", 1) in settlements
    assert not any("tool_result_answer" in chunk for chunk in chunks)
    assert "UNVERIFIED" not in answer.content
