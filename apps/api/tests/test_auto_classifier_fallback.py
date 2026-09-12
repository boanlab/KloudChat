from __future__ import annotations

import json
import socket

import httpx
import pytest
from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession, Message, Role
from app.models.governance import Governance
from app.models.user import AuditEvent, User
from app.routers import sessions as sessions_router
from app.schemas.chat import SendMessage
from app.services import adaptive_routing
from app.services.tools.base import Tool, openai_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "auto_quality"], ids=["economy", "quality"])
@pytest.mark.parametrize(
    "failure",
    ["http_error", "timeout", "invalid_json", "no_classifier", "no_candidate", "no_key"],
)
async def test_classifier_fallback_preserves_the_turn_and_model_identity(
    monkeypatch, mode: str, failure: str
) -> None:
    """Real classifier/parser and send/SSE paths; HTTP, answer generation and DB are synthetic."""
    network_attempts: list[str] = []

    def no_network(*_args, **_kwargs):
        network_attempts.append("blocked")
        pytest.fail("unexpected network access in the classifier fallback regression")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)

    user = User(
        email="fixture@example.test",
        password_hash="hash",
        name="Fixture",
        monthly_credits=1_000,
    )
    requested = {
        **_external_model("fixture/requested"),
        "inputCreditCost": 10,
        "creditCost": 20,
        "contextWindow": 64_000,
        "supportsTools": True,
    }
    classifier = {
        **_external_model("fixture/classifier"),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "privacyOnly": True,
        "inputCreditCost": 0,
        "creditCost": 0,
        "contextWindow": 64_000,
    }
    candidate = {
        **requested,
        "id": "fixture/candidate",
        "inputCreditCost": 1,
        "creditCost": 2,
    }
    catalogue = [requested]
    if failure != "no_classifier":
        catalogue.append(classifier)
    if failure != "no_candidate":
        catalogue.append(candidate)
    session = ChatSession(user_id=user.id, model=requested["id"], routing_mode=mode)
    await _patch_guard_dependencies(monkeypatch, session=session, models=catalogue, blocks=[])

    class Db(_NoWriteDb):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def is_modified(self, _value):
            return False

        async def get(self, kind, key):
            if kind is ChatSession and key == session.id:
                return session
            if kind is User and key == user.id:
                return user
            return next(
                (row for row in self.added if isinstance(row, kind) and row.id == key), None
            )

    db = Db()
    classifier_calls: list[dict] = []
    key_reads: list[str] = []
    answer_calls: list[dict] = []

    class ClassifierClient:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "http://classifier.invalid"
            assert kwargs["headers"] == {
                "Authorization": "Bearer fixture-virtual-key",
                "x-litellm-enable-message-redaction": "true",
            }
            assert (
                kwargs["timeout"].read
                == adaptive_routing.settings.auto_routing_classifier_timeout_sec
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, json):
            assert path == "/v1/chat/completions"
            assert json["model"] == classifier["id"]
            assert json["disable_fallbacks"] is True
            classifier_calls.append(json)
            request = httpx.Request("POST", "http://classifier.invalid/v1/chat/completions")
            if failure == "timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            if failure == "http_error":
                return httpx.Response(503, request=request)
            assert failure == "invalid_json"
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": "{not-json"}}]},
            )

    async def policy(*_args, **_kwargs):
        return Governance(
            external_data_guard=True,
            adaptive_routing_enabled=True,
            adaptive_quality_enabled=True,
            adaptive_classifier_model_id=classifier["id"],
            adaptive_economy_model_ids=[candidate["id"]],
            adaptive_quality_model_ids=[candidate["id"]],
        )

    def user_key(_user):
        key_reads.append("classifier")
        return None if failure == "no_key" else "fixture-virtual-key"

    async def ensure_key(*_args, **_kwargs):
        return None if failure == "no_key" else "fixture-virtual-key"

    async def credentials(*_args, **_kwargs):
        return "http://answer.invalid", "fixture-answer-key"

    async def classifier_config():
        return "http://classifier.invalid", "unused-fixture-master"

    async def forbidden_tool(_arguments):
        pytest.fail("the synthetic answer must not execute tools")

    exposed = Tool(
        name="fixture_tool",
        label="Fixture tool",
        description="A synthetic optional tool retained on the requested model.",
        parameters={"type": "object", "properties": {}},
        run=forbidden_tool,
    )

    async def tools(*_args, **_kwargs):
        return [exposed]

    async def answer(model_id, messages, tools, _context, **kwargs):
        answer_calls.append({"model": model_id, "messages": messages, "tools": tools, **kwargs})
        yield {"type": "model_route", "routedModel": model_id, "actualModel": model_id}
        yield {"type": "delta", "text": "Synthetic fallback answer."}
        yield {"type": "usage", "inputTokens": 1, "outputTokens": 1}

    async def enrichment_model(writer, **_kwargs):
        return writer

    async def title(*_args, **_kwargs):
        return None, {"inputTokens": 0, "outputTokens": 0}

    async def no_enrichment(**_kwargs):
        return None

    monkeypatch.setattr(adaptive_routing.httpx, "AsyncClient", ClassifierClient)
    monkeypatch.setattr(adaptive_routing.settings_store, "litellm_config", classifier_config)
    monkeypatch.setattr(sessions_router, "SessionLocal", lambda: db)
    monkeypatch.setattr(sessions_router.governance, "current_for_egress", policy)
    monkeypatch.setattr(sessions_router.litellm_service, "user_key", user_key)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions_router, "build_tools", tools)
    monkeypatch.setattr(sessions_router.agent_service, "run_turn", answer)
    monkeypatch.setattr(sessions_router, "_enrichment_model", enrichment_model)
    monkeypatch.setattr(sessions_router.chat_service, "generate_title", title)
    monkeypatch.setattr(sessions_router, "_store_artifacts", no_enrichment)
    monkeypatch.setattr(sessions_router, "_enrich_memory", no_enrichment)

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="Write the word fixture.", web_search=False),
        _request(),
        user,
        db,
    )
    events = [json.loads(chunk.removeprefix("data: ")) async for chunk in response.body_iterator]

    assert not network_attempts
    assert not any(event["type"] == "error" for event in events)
    assert events[-1]["type"] == "done"
    assert len(classifier_calls) == int(failure in {"http_error", "timeout", "invalid_json"})
    assert len(key_reads) == int(failure not in {"no_classifier", "no_candidate"})
    assert len(answer_calls) == 1
    assert answer_calls[0]["model"] == requested["id"]
    assert answer_calls[0]["tools"] == [exposed]
    assert answer_calls[0]["tool_definitions"] == openai_snapshot([exposed])
    assert answer_calls[0]["messages"][-1]["content"] == "Write the word fixture."
    assert answer_calls[0]["disable_fallbacks"] is False
    assert session.model == requested["id"]
    assert session.routing_mode == mode

    messages = [row for row in db.added if isinstance(row, Message)]
    assert len({row.id for row in messages}) == 2
    assert {row.role for row in messages} == {Role.user, Role.assistant}
    answer_row = next(row for row in messages if row.role == Role.assistant)
    assert answer_row.model == requested["id"]
    assert answer_row.content == "Synthetic fallback answer."
    assert answer_row.failure is None
    audits = [
        row for row in db.added if isinstance(row, AuditEvent) and row.action == "routing.auto"
    ]
    assert audits
    model_events = [event for event in events if event["type"] == "model_route"]
    assert model_events
    routes = [row.routing["costRouting"] for row in messages]
    routes.extend(row.event_metadata for row in audits)
    routes.extend(model_events)
    routes.extend(event["costRouting"] for event in events if event["type"] == "privacy_route")
    expected_reason = {
        "no_candidate": "no_quality_model" if mode == "auto_quality" else "no_economy_model",
        "no_key": "classifier_key_unavailable",
    }.get(failure, "classifier_unavailable")
    expected_decision = "kept_quality" if failure == "no_candidate" else "classifier_unavailable"
    assert all(route["mode"] == mode for route in routes)
    assert all(route["decision"] == expected_decision for route in routes)
    assert all(route["reasonCode"] == expected_reason for route in routes)
    assert all(route["requestedModel"] == requested["id"] for route in routes)
    assert all(route["selectedModel"] == requested["id"] for route in routes)
    assert all("complexity" not in route for route in routes)
    assert all("estimatedCreditsSaved" not in route for route in routes)
    assert answer_row.routing["costRouting"]["executedModel"] == requested["id"]
    assert model_events[-1]["executedModel"] == requested["id"]
    assert all(row.event_metadata["executedModel"] == requested["id"] for row in audits)
