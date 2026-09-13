"""Search-output masking through the real agent loop and turn persistence."""

from __future__ import annotations

import json
import socket
from copy import deepcopy

import httpx
import pytest

from app.models.chat import ChatSession, Message, Role
from app.models.user import AuditEvent, User
from app.routers import sessions
from app.services import agent, governance
from app.services.tools.base import Tool, ToolResult, openai_snapshot

PUBLIC_SEARCH = (
    "USD/KRW 1,350.50; rate_type=reference; date=2026-09-12; "
    "time=12:00:09 KST; Year 2023 2024 2025 2026; "
    "source=https://example.test/article/20260912120009"
)
CARD = "4111 1111 1111 1111"
KEY = "sk-syntheticsearchfixtureabcdefghijklmnop"


async def _search_turn(monkeypatch, *, sensitive=False, protected_reply=False):
    def no_network(*_args, **_kwargs):
        raise AssertionError("This integration test must not make network calls")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(httpx, "AsyncClient", no_network)
    user = User(
        id="search-masking-user", email="fixture@example.test", password_hash="unused",
        name="Search masking fixture", monthly_credits=10_000,
    )
    session = ChatSession(id="search-masking-session", user_id=user.id)
    added = []
    hops = []
    tool_calls = []
    artifact_calls = []
    private_email = "private.owner@gmail.com"
    public_email = "press@example.test"

    class TurnDb:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, _id):
            if model is ChatSession:
                return session
            if model is User:
                return user
            return None

        def add(self, value):
            added.append(value)

        async def commit(self):
            return None

    async def search(arguments):
        tool_calls.append(arguments)
        content = PUBLIC_SEARCH
        if sensitive:
            content += f"\nfixture_card={CARD}; fixture_key={KEY}"
        return ToolResult(content=content, detail=PUBLIC_SEARCH)

    async def model_stream(_model, messages, *_args, **kwargs):
        hops.append({"messages": deepcopy(messages), "redact": kwargs.get("redact_logging")})
        acc = agent._Accumulator()
        acc.actual_model = "fixture/external-model"
        acc.usage = {"inputTokens": 10, "outputTokens": 10}
        if len(hops) == 1:
            acc.finish_reason = "tool_calls"
            acc.calls[0] = {
                "id": "search-call", "name": "web_search",
                "arguments": '{"query":"synthetic exchange rate"}',
            }
        else:
            assert len(hops) == 2
            result = next(message["content"] for message in reversed(messages)
                          if message["role"] == "tool")
            if protected_reply:
                result += f"\nuser_contact={private_email}; public_contact={public_email}"
            acc.finish_reason = "stop"
            acc.content.append(result)
            yield "delta", result
        yield "done", acc

    async def store_artifacts(**kwargs):
        artifact_calls.append(kwargs)
        return None

    async def no_memory(**_kwargs):
        return None

    monkeypatch.setattr(sessions, "SessionLocal", TurnDb)
    monkeypatch.setattr(sessions, "_store_artifacts", store_artifacts)
    monkeypatch.setattr(sessions, "_enrich_memory", no_memory)
    monkeypatch.setattr(agent, "_stream_once", model_stream)
    tool = Tool(
        name="web_search", description="Read synthetic search facts", label="Web search",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        run=search, read_only=True,
    )
    model = {
        "id": "fixture/external-model", "label": "Fixture model",
        "dataBoundary": "external", "strictLocal": False, "kinds": ["chat"], "creditCost": 1,
    }
    routing = {
        "requestedModels": [model["id"]], "routedModels": [model["id"]],
        "effectiveModels": [model["id"]], "actualModels": [], "action": "none",
        "dataBoundary": "external", "findingCounts": [],
    }
    protected = (
        frozenset(governance.protected_values(private_email)) if protected_reply else frozenset()
    )
    if protected_reply:
        routing["action"] = "mask_external"
        routing["findingCounts"] = [{"category": "email", "source": "current_input", "count": 1}]
    wire = [
        event async for event in sessions._run_turn(
            user_id=user.id, api_key="unused-fixture-key", auto_memory=False,
            session_id=session.id, model=model,
            messages=[{"role": "user", "content": "Summarize the synthetic exchange-rate source."}],
            tools=[tool], tool_definitions=openai_snapshot([tool]),
            first_user_message="Summarize the synthetic exchange-rate source.", is_first_turn=False,
            routing=routing, mask_at_rest=True, sanitize_tool_output=True,
            protect_enrichment=True, protected_values=protected,
        )
    ]
    announced = [json.loads(line.removeprefix("data: ")) for line in "".join(wire).splitlines()
                 if line.startswith("data: ")]
    answers = [row for row in added if isinstance(row, Message) and row.role is Role.assistant]
    assert len(answers) == 1
    assert answers[0].failure is None
    assert len(hops) == 2
    assert tool_calls == [{"query": "synthetic exchange rate"}]
    assert len(artifact_calls) == 1 and not artifact_calls[0]["requested_artifacts"]
    assert not any(event["type"] in {"error", "artifact"} for event in announced)
    assert all(hop["redact"] is True for hop in hops)
    return hops, answers[0], announced, added, private_email, public_email


@pytest.mark.asyncio
@pytest.mark.parametrize("sensitive", [False, True], ids=["public-source", "mixed-secrets"])
async def test_search_numbers_and_source_survive_followup_and_persistence(monkeypatch, sensitive):
    hops, answer, announced, added, _, _ = await _search_turn(monkeypatch, sensitive=sensitive)
    followup = json.dumps(hops[1]["messages"], ensure_ascii=False)
    stored_steps = json.dumps(answer.steps, ensure_ascii=False)
    assert PUBLIC_SEARCH in followup
    assert PUBLIC_SEARCH in answer.content
    assert PUBLIC_SEARCH in stored_steps
    assert answer.model == "fixture/external-model"
    assert all(step["status"] == "done" for step in answer.steps)

    if sensitive:
        persisted_and_wire = (
            followup, answer.content, stored_steps, json.dumps(announced, ensure_ascii=False),
        )
        for value in persisted_and_wire:
            assert CARD not in value
            assert KEY not in value
        assert "[카드번호]" in followup and "[API키]" in followup
        assert "[카드번호]" in answer.content and "[API키]" in answer.content
        assert answer.routing["initialAction"] == "none"
        assert answer.routing["action"] == "mask_external"
        assert answer.routing["toolOutputMasked"] == 2
        expected = [
            {"category": "api_key", "source": "tool_output", "count": 1},
            {"category": "payment_card", "source": "tool_output", "count": 1},
        ]
        assert answer.routing["toolOutputFindings"] == expected
        audit = next(row for row in added if isinstance(row, AuditEvent)
                     and row.action == "privacy.mask_tool_output")
        assert audit.event_metadata["findings"] == expected
        assert CARD not in json.dumps(audit.event_metadata)
        assert KEY not in json.dumps(audit.event_metadata)
    else:
        assert answer.routing["action"] == "none"
        assert answer.routing.get("toolOutputMasked", 0) == 0
        assert answer.routing.get("toolOutputFindings", []) == []
        assert not any(isinstance(row, AuditEvent) for row in added)


@pytest.mark.asyncio
async def test_user_protected_contact_still_masks_at_rest_beside_public_source(monkeypatch):
    _, answer, _, _, private_email, public_email = await _search_turn(
        monkeypatch, protected_reply=True,
    )
    assert PUBLIC_SEARCH in answer.content
    assert private_email not in answer.content
    assert "[이메일]" in answer.content
    assert public_email in answer.content
    assert {"category": "email", "source": "current_input", "count": 1} in (
        answer.routing["findingCounts"]
    )
    assert {"category": "email", "source": "assistant_output", "count": 1} in (
        answer.routing["findingCounts"]
    )


def test_inline_source_next_to_secret_fields_stays_conservative():
    # Only an unambiguous source path is exempt, not adjoining credential fields.
    text = f"{PUBLIC_SEARCH}; fixture_card={CARD}; fixture_key={KEY}"
    for scope in ("tool", "answer"):
        masked, count = governance.mask(text, scope=scope)
        assert "/20260912120009" not in masked
        assert CARD not in masked and KEY not in masked
        assert count == 3
