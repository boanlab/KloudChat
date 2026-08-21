from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from app.models.chat import ChatSession, Message, RoutingMode, SessionKind
from app.models.governance import Governance
from app.models.user import AuditEvent, User, UserRole
from app.routers import models as models_router
from app.routers import sessions as sessions_router
from app.routers import usage as usage_router
from app.schemas.admin import GovernanceIn
from app.schemas.chat import SessionCreate, SessionPatch
from app.services import adaptive_routing
from app.services.context import build_messages


def _model(
    model_id: str,
    *,
    boundary: str = "external",
    input_cost: int = 10,
    output_cost: int = 10,
    context: int = 32_000,
    strict: bool = False,
    privacy_only: bool = False,
    tools: bool = False,
) -> dict:
    return {
        "id": model_id,
        "label": model_id,
        "kinds": ["chat"],
        "dataBoundary": boundary,
        "strictLocal": strict,
        "privacyOnly": privacy_only,
        "inputCreditCost": input_cost,
        "creditCost": output_cost,
        "contextWindow": context,
        "supportsTools": tools,
    }


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "PUT",
            "path": "/api/admin/governance",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )


def _event(chunk: str) -> dict:
    return json.loads(chunk.removeprefix("data: ").strip())


class _SessionWriteDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _value: object) -> None:
        return None


def test_classifier_context_keeps_complete_answer_envelope_and_tool_snapshot() -> None:
    history = [
        {"role": "user", "content": "old complex architecture constraint"},
        {"role": "assistant", "content": "old analysis"},
        {"role": "user", "content": "second-user"},
        {"role": "assistant", "content": "second-assistant"},
        {"role": "user", "content": "third-user"},
        {"role": "assistant", "content": "third-assistant"},
        {"role": "user", "content": "current"},
    ]
    messages = build_messages(
        SessionKind.chat,
        history,
        untrusted_context=["global memory requiring specialised analysis"],
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "large_result_tool",
                "description": "May return a large result",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    encoded = adaptive_routing.classifier_context(messages, tools)

    assert encoded is not None
    payload = json.loads(encoded)
    assert payload["messages"] == messages
    assert payload["qualityModelTools"] == tools
    assert "old complex architecture constraint" in encoded
    assert "global memory requiring specialised analysis" in encoded
    assert (
        adaptive_routing.classifier_context(
            [{"role": "user", "content": "x" * adaptive_routing.MAX_CLASSIFIER_CHARS}]
        )
        is None
    )
    huge_tool = [
        {
            "type": "function",
            "function": {
                "name": "huge",
                "description": "x" * adaptive_routing.MAX_CLASSIFIER_CHARS,
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert adaptive_routing.classifier_context(messages[:1], huge_tool) is None


def test_context_estimate_does_not_treat_korean_like_ascii() -> None:
    assert adaptive_routing.estimated_context_tokens(["a" * 24_000]) == 24_000
    assert adaptive_routing.estimated_context_tokens(["가" * 24_000]) == 72_000
    high_entropy = "".join(chr(33 + index % 94) for index in range(24_000))
    assert adaptive_routing.estimated_context_tokens([high_entropy]) == 24_000


@pytest.mark.asyncio
async def test_create_auto_requires_live_user_allowed_quality_model(monkeypatch) -> None:
    quality = _model("quality")
    blocked = _model("blocked")
    reserved = _model("classifier-only", privacy_only=True)
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        allowed_models=[quality["id"], reserved["id"]],
    )

    async def enabled_kinds():
        return {SessionKind.chat.value}

    async def validate_links(*_args, **_kwargs):
        return None

    async def catalogue():
        return {"models": [quality, blocked, reserved], "litellmAvailable": True}

    monkeypatch.setattr(sessions_router.settings_store, "enabled_kinds", enabled_kinds)
    monkeypatch.setattr(sessions_router, "_validate_session_links", validate_links)
    monkeypatch.setattr(
        sessions_router.model_service,
        "list_models_for_egress",
        catalogue,
    )

    db = _SessionWriteDb()
    for invalid_model in (None, "gone", blocked["id"], reserved["id"]):
        with pytest.raises(Exception) as caught:
            await sessions_router.create_session(
                SessionCreate(
                    kind=SessionKind.chat,
                    model=invalid_model,
                    routing_mode=RoutingMode.auto,
                ),
                user,
                db,
            )
        assert getattr(caught.value, "status_code", None) == 409
        assert getattr(caught.value, "detail", None) == "auto_quality_model_required"

    assert db.added == []
    assert db.commits == 0

    created = await sessions_router.create_session(
        SessionCreate(
            kind=SessionKind.chat,
            model=quality["id"],
            routing_mode=RoutingMode.auto,
        ),
        user,
        db,
    )

    assert created.model == quality["id"]
    assert created.routing_mode is RoutingMode.auto
    assert db.commits == 1


@pytest.mark.asyncio
async def test_patch_auto_validates_current_and_proposed_quality_model(monkeypatch) -> None:
    quality = _model("quality")
    blocked = _model("blocked")
    reserved = _model("classifier-only", privacy_only=True)
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        allowed_models=[quality["id"], reserved["id"]],
    )
    session = ChatSession(
        user_id=user.id,
        model=quality["id"],
        routing_mode=RoutingMode.manual,
    )

    async def owned(*_args, **_kwargs):
        return session

    async def catalogue():
        return {"models": [quality, blocked, reserved], "litellmAvailable": True}

    monkeypatch.setattr(sessions_router, "_owned", owned)
    monkeypatch.setattr(
        sessions_router.model_service,
        "list_models_for_egress",
        catalogue,
    )
    db = _SessionWriteDb()

    for invalid_current in ("", "gone"):
        session.model = invalid_current
        with pytest.raises(Exception) as caught:
            await sessions_router.patch_session(
                session.id,
                SessionPatch(routing_mode=RoutingMode.auto),
                user,
                db,
            )
        assert getattr(caught.value, "status_code", None) == 409
        assert getattr(caught.value, "detail", None) == "auto_quality_model_required"

    session.model = quality["id"]
    for invalid_proposed in (blocked["id"], reserved["id"]):
        with pytest.raises(Exception) as caught:
            await sessions_router.patch_session(
                session.id,
                SessionPatch(model=invalid_proposed, routing_mode=RoutingMode.auto),
                user,
                db,
            )
        assert getattr(caught.value, "status_code", None) == 409
        assert getattr(caught.value, "detail", None) == "auto_quality_model_required"
    assert session.model == quality["id"]
    assert session.routing_mode is RoutingMode.manual
    assert db.added == []
    assert db.commits == 0

    patched = await sessions_router.patch_session(
        session.id,
        SessionPatch(model=quality["id"], routing_mode=RoutingMode.auto),
        user,
        db,
    )

    assert patched.model == quality["id"]
    assert patched.routing_mode is RoutingMode.auto
    assert db.commits == 1

    # SQLAlchemy's String column may hydrate this field as the raw value. An
    # unrelated patch must still validate the existing Auto quality ceiling.
    session.routing_mode = RoutingMode.auto.value
    session.model = "gone"
    with pytest.raises(Exception) as caught:
        await sessions_router.patch_session(
            session.id,
            SessionPatch(title="rename only"),
            user,
            db,
        )
    assert getattr(caught.value, "status_code", None) == 409
    assert getattr(caught.value, "detail", None) == "auto_quality_model_required"
    assert session.title == ""
    assert db.commits == 1


def test_economy_candidates_fail_closed_and_keep_admin_order() -> None:
    quality = _model("quality", input_cost=10, output_cost=20, tools=True)
    allowed = {"strict", "hybrid", "private", "too-small", "costlier", "second", "first"}
    catalogue = [
        quality,
        _model("strict", boundary="self_hosted", input_cost=0, output_cost=0, tools=True),
        _model("hybrid", boundary="hybrid", input_cost=1, output_cost=1, tools=True),
        _model(
            "private",
            boundary="self_hosted",
            input_cost=0,
            output_cost=0,
            strict=True,
            privacy_only=True,
            tools=True,
        ),
        _model("too-small", input_cost=1, output_cost=1, context=2_000, tools=True),
        _model("costlier", input_cost=11, output_cost=1, tools=True),
        _model("second", input_cost=2, output_cost=3, tools=True),
        _model("first", input_cost=1, output_cost=2, tools=True),
    ]

    candidates = adaptive_routing.economy_candidates(
        catalogue,
        ["second", "hybrid", "private", "too-small", "costlier", "first", "strict"],
        quality_model=quality,
        allowed_model_ids=allowed,
        context_tokens=2_000,
        requires_tools=True,
    )

    assert [model["id"] for model in candidates] == ["second", "first", "strict"]


def test_missing_prices_are_not_treated_as_free() -> None:
    classifier = _model(
        "classifier",
        boundary="self_hosted",
        input_cost=0,
        output_cost=0,
        strict=True,
    )
    classifier.pop("inputCreditCost")
    assert not adaptive_routing.classifier_is_usable(
        classifier, allowed_model_ids=set()
    )

    quality = _model("quality", input_cost=10, output_cost=10)
    economy = _model("economy", input_cost=1, output_cost=1)
    economy.pop("creditCost")
    assert (
        adaptive_routing.economy_candidates(
            [quality, economy],
            ["economy"],
            quality_model=quality,
            allowed_model_ids=set(),
            context_tokens=100,
            requires_tools=False,
        )
        == []
    )


@pytest.mark.parametrize(
    ("quality_boundary", "expected"),
    [
        ("self_hosted", ["self"]),
        ("hybrid", ["self"]),
        ("unknown", ["self"]),
        ("external", ["self", "external"]),
    ],
)
def test_economy_boundary_never_worsens_quality(
    quality_boundary: str, expected: list[str]
) -> None:
    quality = _model("quality", boundary=quality_boundary, input_cost=10, output_cost=10)
    self_hosted = _model("self", boundary="self_hosted", input_cost=1, output_cost=1)
    external = _model("external", boundary="external", input_cost=1, output_cost=1)

    candidates = adaptive_routing.economy_candidates(
        [quality, self_hosted, external],
        ["self", "external"],
        quality_model=quality,
        allowed_model_ids=set(),
        context_tokens=100,
        requires_tools=False,
    )

    assert [model["id"] for model in candidates] == expected


@pytest.mark.asyncio
async def test_classifier_request_is_strict_redacted_and_rejects_non_object(monkeypatch) -> None:
    calls: list[dict] = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "[]"}}]}

    class Client:
        def __init__(self, **kwargs):
            calls.append({"client": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, json):
            calls.append({"path": path, "json": json})
            return Response()

    async def config():
        return "http://litellm.test", "master-must-not-be-used"

    monkeypatch.setattr(adaptive_routing.httpx, "AsyncClient", Client)
    monkeypatch.setattr(adaptive_routing.settings_store, "litellm_config", config)

    result = await adaptive_routing.classify(
        model_id="strict-local/classifier",
        context='{"conversation":[]}',
        user_id="user-1",
        api_key="virtual-user-key",
    )

    assert result is None
    assert calls[0]["client"]["headers"] == {
        "Authorization": "Bearer virtual-user-key",
        "x-litellm-enable-message-redaction": "true",
    }
    assert calls[1]["json"]["disable_fallbacks"] is True
    assert calls[1]["json"]["model"] == "strict-local/classifier"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"choices": "bad", "usage": {}},
        {"choices": ["bad"], "usage": {}},
        {"choices": [{"message": "bad"}], "usage": {}},
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "complexity": "low",
                                "confidence": 0.95,
                                "reasonCode": "simple_factual",
                            }
                        )
                    }
                }
            ],
            "usage": [],
        },
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "complexity": "low",
                                "confidence": 0.95,
                                "reasonCode": "simple_factual",
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": -1, "completion_tokens": 1},
        },
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "complexity": "low",
                                "confidence": 0.95,
                                "reasonCode": "simple_factual",
                            }
                        )
                    }
                }
            ],
            "usage": {"completion_tokens": 1},
        },
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "complexity": "low",
                                "confidence": 10**400,
                                "reasonCode": "simple_factual",
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    ],
)
async def test_classifier_malformed_nested_shapes_keep_quality(monkeypatch, payload) -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    async def config():
        return "http://litellm.test", "unused-master"

    monkeypatch.setattr(adaptive_routing.httpx, "AsyncClient", Client)
    monkeypatch.setattr(adaptive_routing.settings_store, "litellm_config", config)

    assert (
        await adaptive_routing.classify(
            model_id="strict-local/classifier",
            context='{"conversation":[]}',
            user_id="user-1",
            api_key="virtual-user-key",
        )
        is None
    )


@pytest.mark.asyncio
async def test_resolve_cost_routing_routes_only_high_confidence_low(monkeypatch) -> None:
    quality = _model("quality", input_cost=10, output_cost=20)
    classifier = _model(
        "classifier",
        boundary="self_hosted",
        input_cost=0,
        output_cost=0,
        strict=True,
        privacy_only=True,
    )
    economy = _model("economy", input_cost=1, output_cost=2)
    user = User(email="person@example.test", password_hash="hash", name="Person")
    policy = Governance(
        adaptive_routing_enabled=True,
        adaptive_classifier_model_id=classifier["id"],
        adaptive_economy_model_ids=[economy["id"]],
    )

    class Db:
        def is_modified(self, _value):
            return False

    classifier_messages = build_messages(
        SessionKind.chat,
        [
            {"role": "user", "content": "old complex constraint"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "hello"},
        ],
        untrusted_context=["global memory"],
    )

    async def classify(**kwargs):
        assert kwargs["api_key"] == "virtual-key"
        assert json.loads(kwargs["context"])["messages"] == classifier_messages
        return adaptive_routing.Classification("low", 0.9, "simple_factual", 12, 4)

    monkeypatch.setattr(sessions_router.litellm_service, "user_key", lambda _user: "virtual-key")
    monkeypatch.setattr(sessions_router.adaptive_routing, "classify", classify)

    selected, route = await sessions_router._resolve_cost_routing(
        db=Db(),
        user=user,
        policy=policy,
        catalogue=[quality, classifier, economy],
        quality_model=quality,
        classifier_messages=classifier_messages,
        classifier_tool_definitions=[],
        context_tokens=100,
        unsupported_reason=None,
    )

    assert selected["id"] == "economy"
    assert route == {
        "mode": "auto",
        "decision": "routed",
        "reasonCode": "low_complexity",
        "requestedModel": "quality",
        "selectedModel": "economy",
        "classifierVersion": adaptive_routing.CLASSIFIER_VERSION,
        "classifierModel": "classifier",
        "complexity": "low",
        "confidence": 0.9,
        "classifierInputTokens": 12,
        "classifierOutputTokens": 4,
    }


@pytest.mark.asyncio
async def test_complete_classifier_envelope_over_limit_keeps_quality_before_key(
    monkeypatch,
) -> None:
    quality = _model("quality", input_cost=10, output_cost=20)
    classifier = _model(
        "classifier",
        boundary="self_hosted",
        input_cost=0,
        output_cost=0,
        strict=True,
        privacy_only=True,
    )
    economy = _model("economy", input_cost=1, output_cost=2)
    user = User(email="person@example.test", password_hash="hash", name="Person")
    policy = Governance(
        adaptive_routing_enabled=True,
        adaptive_classifier_model_id=classifier["id"],
        adaptive_economy_model_ids=[economy["id"]],
    )

    class Db:
        def is_modified(self, _value):
            return False

    def forbidden_key(_user):
        pytest.fail("oversized complete envelope reached virtual-key resolution")

    monkeypatch.setattr(sessions_router.litellm_service, "user_key", forbidden_key)
    selected, route = await sessions_router._resolve_cost_routing(
        db=Db(),
        user=user,
        policy=policy,
        catalogue=[quality, classifier, economy],
        quality_model=quality,
        classifier_messages=[
            {
                "role": "user",
                "content": "x" * adaptive_routing.MAX_CLASSIFIER_CHARS,
            }
        ],
        classifier_tool_definitions=[],
        context_tokens=100,
        unsupported_reason=None,
    )

    assert selected["id"] == quality["id"]
    assert route["decision"] == "kept_quality"
    assert route["reasonCode"] == "input_too_long"


@pytest.mark.asyncio
async def test_models_catalogue_reports_user_scoped_auto_availability(monkeypatch) -> None:
    classifier = _model(
        "classifier",
        boundary="self_hosted",
        input_cost=0,
        output_cost=0,
        strict=True,
        privacy_only=True,
    )
    economy = _model("economy", input_cost=1, output_cost=2)
    blocked = _model("blocked", input_cost=1, output_cost=1)
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        allowed_models=["classifier", "economy"],
    )

    async def catalogue():
        return {
            "models": [classifier, economy, blocked],
            "litellmAvailable": True,
            "defaultChatModel": "economy",
        }

    async def policy():
        return Governance(
            adaptive_routing_enabled=True,
            adaptive_classifier_model_id="classifier",
            adaptive_economy_model_ids=["blocked", "economy"],
        )

    monkeypatch.setattr(models_router.model_service, "list_models", catalogue)
    monkeypatch.setattr(models_router.governance, "current", policy)

    result = await models_router.list_models(user)

    assert [model["id"] for model in result["models"]] == ["classifier", "economy"]
    assert result["autoRouting"] == {
        "enabled": True,
        "available": True,
        "reason": None,
        "classifierModelId": "classifier",
        "economyModelIds": ["economy"],
    }


@pytest.mark.asyncio
async def test_disabling_stale_auto_policy_does_not_require_live_models(monkeypatch) -> None:
    policy = Governance(
        adaptive_routing_enabled=True,
        adaptive_classifier_model_id="gone-classifier",
        adaptive_economy_model_ids=["gone-economy"],
    )
    admin = User(
        email="admin@example.test",
        password_hash="hash",
        name="Admin",
        role=UserRole.admin,
    )

    class Db:
        def __init__(self):
            self.added: list[object] = []

        async def get(self, model, key):
            assert model is Governance and key == "default"
            return policy

        def add(self, value):
            self.added.append(value)

        async def commit(self):
            return None

    async def catalogue():
        return {"models": [], "litellmAvailable": False, "defaultChatModel": ""}

    async def sweep(_db):
        return 0

    monkeypatch.setattr(usage_router.model_service, "list_models_for_egress", catalogue)
    monkeypatch.setattr(usage_router.governance, "sweep_expired", sweep)
    monkeypatch.setattr(usage_router.governance, "invalidate", lambda: None)

    result = await usage_router.put_governance(
        GovernanceIn(
            adaptive_routing_enabled=False,
            adaptive_classifier_model_id="gone-classifier",
            adaptive_economy_model_ids=["gone-economy"],
        ),
        _request(),
        admin,
        Db(),
    )

    assert result["ok"] is True
    assert policy.adaptive_routing_enabled is False


@pytest.mark.asyncio
async def test_run_turn_emits_only_full_auto_routes_and_persists_savings(monkeypatch) -> None:
    economy = _model("economy", input_cost=1, output_cost=1)
    quality = _model("quality", input_cost=10, output_cost=10)
    session = ChatSession(
        id="session-1",
        user_id="user-1",
        model="quality",
        routing_mode=RoutingMode.auto,
    )
    user = User(
        id="user-1",
        email="person@example.test",
        password_hash="hash",
        name="Person",
    )
    added: list[object] = []
    seen: dict = {}

    class Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            if model is ChatSession and key == session.id:
                return session
            if model is User and key == user.id:
                return user
            if model is AuditEvent:
                return None
            return None

        def add(self, value):
            added.append(value)

        async def commit(self):
            return None

    async def run_turn(*_args, **kwargs):
        seen.update(kwargs)
        yield {"type": "model_route", "routedModel": "economy", "actualModel": "provider/economy"}
        yield {"type": "delta", "text": "answer"}
        yield {"type": "usage", "inputTokens": 1_000, "outputTokens": 1_000}

    async def title(model_id, *_args, **kwargs):
        seen["title"] = (model_id, kwargs)
        return None, {"inputTokens": 0, "outputTokens": 0}

    async def enrich(**kwargs):
        seen["enrich"] = kwargs
        return None, None

    monkeypatch.setattr(sessions_router, "SessionLocal", Db)
    monkeypatch.setattr(sessions_router.agent_service, "run_turn", run_turn)
    monkeypatch.setattr(sessions_router.chat_service, "generate_title", title)
    monkeypatch.setattr(sessions_router, "_enrich", enrich)

    routing = {
        "requestedModels": ["quality"],
        "routedModels": ["economy"],
        "effectiveModels": ["economy"],
        "actualModels": [],
        "action": "none",
        "dataBoundary": "external",
        "modelRoutes": [],
        "costRouting": {
            "mode": "auto",
            "decision": "routed",
            "reasonCode": "low_complexity",
            "requestedModel": "quality",
            "selectedModel": "economy",
            "classifierVersion": adaptive_routing.CLASSIFIER_VERSION,
        },
    }
    chunks = [
        chunk
        async for chunk in sessions_router._run_turn(
            user_id=user.id,
            api_key="virtual-key",
            auto_memory=False,
            session_id=session.id,
            model=economy,
            quality_model=quality,
            disable_fallbacks=True,
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            first_user_message="hello",
            is_first_turn=True,
            routing=routing,
        )
    ]

    events = [_event(chunk) for chunk in chunks]
    public_routes = [event for event in events if event["type"] == "model_route"]
    assert public_routes
    assert all(
        {"mode", "decision", "reasonCode", "requestedModel", "selectedModel"} <= route.keys()
        for route in public_routes
    )
    assert seen["disable_fallbacks"] is True
    assert seen["title"][0] == "economy"
    assert seen["title"][1]["disable_fallbacks"] is True
    assert seen["enrich"]["disable_fallbacks"] is True
    assistant = next(row for row in added if isinstance(row, Message))
    assert assistant.model == "provider/economy"
    assert assistant.usage["credits"] == 2
    assert assistant.routing["costRouting"]["executedModel"] == "provider/economy"
    assert assistant.routing["costRouting"]["estimatedCreditsSaved"] == 18
