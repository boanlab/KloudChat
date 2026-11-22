"""`kloudchat/super-agent` and `-strict-local` on the /llm proxy: key owner, listing, switch,
one-turn routing and route note (mock gateway)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_super_agent_turns import CATALOGUE, DEEPSEEK, FLASH, STRICT_FLASH, _model

from app.models.governance import Governance
from app.models.user import User, UserStatus
from app.routers import llm
from app.services import governance, model_health, super_api
from app.services import models as model_service

KEY = "sk-owner-key"
SUPER, STRICT = super_api.SUPER_ID, super_api.STRICT_ID
LOCAL_27B = _model("local/qwen3.8-27b", boundary="hybrid")
NO_TOOLS_FLASH = {**FLASH, "supportsTools": False}


class _World:
    def __init__(self) -> None:
        self.enabled = True
        self.models = list(CATALOGUE)
        self.policy = Governance(external_data_guard=True)
        self.user = User(id="u1", email="a@example.com", name="A", status=UserStatus.active)
        self.calls: list[dict] = []
        self.key_lookups = 0
        self.fail: set[str] = set()
        self.stream_usage = True


@pytest.fixture
def world(monkeypatch):
    w = _World()
    model_health.reset()
    super_api.forget_callers()
    real = httpx.AsyncClient

    def reply(status: int, payload, ctype: str = "application/json") -> httpx.Response:
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        # a stream, as LiteLLM's: the proxy relays raw chunks
        return httpx.Response(
            status, headers={"content-type": ctype}, stream=httpx.ByteStream(data)
        )

    def gateway(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/key/info":
            w.key_lookups += 1
            if request.url.params.get("key") != KEY:
                return reply(401, {"error": "bad key"})
            return reply(200, {"info": {"user_id": "u1", "metadata": {"kchat_user_id": "u1"}}})
        if path.endswith("/models"):
            return reply(
                200, {"object": "list", "data": [{"id": "local/qwen3.8-flash", "object": "model"}]}
            )
        body = json.loads(request.content)
        w.calls.append({"auth": request.headers.get("authorization"), "body": body})
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return reply(401, {"error": {"message": "invalid key"}})
        if body["model"].startswith("kloudchat/"):
            # the gateway serves no such model: only the app answers to these ids
            return reply(400, {"error": {"message": "invalid model", "code": "400"}})
        if body["model"] in w.fail:
            return reply(503, {"error": {"message": "busy"}})
        if body.get("stream"):
            chunks = [
                {
                    "id": "c1",
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {"content": "안녕"}, "finish_reason": None}],
                },
                {
                    "id": "c1",
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            if w.stream_usage:
                chunks.append(
                    {
                        "id": "c1",
                        "model": body["model"],
                        "choices": [],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                    }
                )
            sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return reply(200, sse.encode(), "text/event-stream")
        return reply(
            200,
            {
                "id": "c1",
                "object": "chat.completion",
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "안녕"},
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    async def config():
        return "http://litellm.invalid", "master"

    async def catalogue(force: bool = False):
        return {
            "models": w.models,
            "litellmAvailable": True,
            "defaultChatModel": FLASH["id"],
            "defaultModelByKind": {"chat": FLASH["id"]},
        }

    async def policy(force: bool = False):
        return w.policy

    async def enabled():
        return w.enabled

    async def owner(_user_id):
        return w.user

    async def no_classifier(*_a, **_k):
        return None

    for module in (llm.settings_store, super_api.settings_store):
        monkeypatch.setattr(module, "litellm_config", config)
    monkeypatch.setattr(llm.settings_store, "api_super_agent_enabled", enabled)
    monkeypatch.setattr(super_api.settings_store, "api_super_agent_enabled", enabled)
    monkeypatch.setattr(model_service, "list_models", catalogue)
    monkeypatch.setattr(model_service, "list_models_for_egress", catalogue)
    monkeypatch.setattr(governance, "current", policy)
    monkeypatch.setattr(governance, "current_for_egress", policy)
    monkeypatch.setattr(super_api, "_user_of", owner)
    monkeypatch.setattr(super_api.super_agent, "classify_with_model", no_classifier)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(gateway), **kw)
    )
    app = FastAPI()
    app.include_router(llm.router)
    w.client = TestClient(app)
    return w


def _ask(w: _World, text: str, model: str = SUPER, key: str = KEY, **extra):
    return w.client.post(
        "/llm/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": text}], **extra},
    )


def test_a_missing_or_unknown_key_gets_the_gateways_own_refusal(world) -> None:
    assert _ask(world, "안녕하세요!", key="sk-nobody").status_code == 401
    assert _ask(world, "안녕하세요!", key="").status_code == 401
    # the gateway was the one asked, and it saw the caller's key as it came
    assert [c["auth"] for c in world.calls] == ["Bearer sk-nobody", "Bearer "]


def test_a_suspended_owner_is_not_answered_by_the_app(world) -> None:
    world.user.status = UserStatus.suspended
    assert _ask(world, "안녕하세요!").status_code == 400
    assert [c["body"]["model"] for c in world.calls] == [SUPER]


def test_switched_off_the_ids_do_not_exist(world) -> None:
    world.enabled = False
    for model in (SUPER, STRICT):
        out = _ask(world, "안녕하세요!", model=model)
        assert out.status_code == 404 and out.json()["error"]["code"] == "model_not_found"
    assert world.calls == []
    ids = [
        m["id"]
        for m in world.client.get(
            "/llm/v1/models", headers={"Authorization": f"Bearer {KEY}"}
        ).json()["data"]
    ]
    assert SUPER not in ids and STRICT not in ids


def test_the_listing_carries_both_ids_after_the_gateways_models(world) -> None:
    data = world.client.get("/llm/v1/models", headers={"Authorization": f"Bearer {KEY}"}).json()[
        "data"
    ]
    assert [m["id"] for m in data] == ["local/qwen3.8-flash", SUPER, STRICT]
    assert data[1]["owned_by"] == "kloudchat" and data[2]["owned_by"] == "kloudchat"


def test_an_id_the_account_cannot_use_is_not_listed(world) -> None:
    # only an external model is allowed: the strict-local entry has nothing to pick
    world.user.allowed_models = [DEEPSEEK["id"]]
    ids = [
        m["id"]
        for m in world.client.get(
            "/llm/v1/models", headers={"Authorization": f"Bearer {KEY}"}
        ).json()["data"]
    ]
    assert SUPER in ids and STRICT not in ids


def test_an_easy_chat_runs_on_the_flash_row(world) -> None:
    out = _ask(world, "안녕하세요!")
    body = out.json()
    assert out.status_code == 200 and body["model"] == SUPER
    assert body["kloudchat"]["route"] == {
        "model": FLASH["id"],
        "row": "simple",
        "effort": "none",
        "strictLocal": False,
        "fallback": False,
    }
    assert world.calls[-1]["body"]["model"] == FLASH["id"]
    assert json.loads(out.headers["X-KloudChat-Route"]) == body["kloudchat"]["route"]
    # the caller's own key reaches the gateway, never the master key
    assert world.calls[-1]["auth"] == f"Bearer {KEY}"


def test_a_hard_chat_goes_to_the_escalation_model_with_reasoning(world) -> None:
    out = _ask(world, "우리 DB를 MySQL에서 PostgreSQL로 옮길지 판단 기준과 위험을 정리해 줘")
    route = out.json()["kloudchat"]["route"]
    assert route["model"] == DEEPSEEK["id"] and route["row"] == "reasoning"
    assert route["effort"] == "high"
    assert world.calls[-1]["body"]["reasoning"] == {"effort": "high"}


def test_a_request_with_tools_moves_to_a_model_that_takes_them(world) -> None:
    world.models = [NO_TOOLS_FLASH, LOCAL_27B, STRICT_FLASH, DEEPSEEK]
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    out = _ask(world, "안녕하세요!", tools=tools)
    route = out.json()["kloudchat"]["route"]
    assert route["model"] == LOCAL_27B["id"] and route["fallback"] is True
    assert world.calls[-1]["body"]["tools"] == tools


def test_the_system_message_and_the_whole_conversation_are_kept(world) -> None:
    messages = [
        {"role": "system", "content": "짧게 답해."},
        {"role": "user", "content": "내 이름은 철수야"},
        {"role": "assistant", "content": "안녕하세요 철수님"},
        {"role": "user", "content": "고마워요"},
    ]
    out = world.client.post(
        "/llm/v1/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
        json={"model": SUPER, "messages": messages},
    )
    assert out.status_code == 200
    assert world.calls[-1]["body"]["messages"] == messages


def test_the_strict_variant_never_leaves(world) -> None:
    out = _ask(
        world, "우리 DB를 MySQL에서 PostgreSQL로 옮길지 판단 기준과 위험을 정리해 줘", model=STRICT
    )
    route = out.json()["kloudchat"]["route"]
    assert route["strictLocal"] is True and route["model"].startswith("strict-local/")
    assert world.calls[-1]["body"]["disable_fallbacks"] is True


def test_the_strict_variant_without_an_internal_model_is_unavailable(world) -> None:
    world.models = [FLASH, DEEPSEEK]
    out = _ask(world, "안녕하세요!", model=STRICT)
    assert out.status_code == 503 and out.json()["error"]["code"] == "strict_local_unavailable"
    assert world.calls == []


def test_personal_data_moves_the_ordinary_variant_inside(world) -> None:
    out = _ask(world, "고객 연락처 010-2345-6789 로 사과 문자 초안 하나")
    route = out.json()["kloudchat"]["route"]
    assert route["strictLocal"] is True and route["model"].startswith("strict-local/")


def test_personal_data_without_an_internal_model_is_masked_on_the_way_out(world) -> None:
    world.models = [FLASH, DEEPSEEK]
    out = _ask(world, "고객 연락처 010-2345-6789 로 사과 문자 초안 하나")
    assert out.status_code == 200
    sent = world.calls[-1]["body"]["messages"][0]["content"]
    assert "010-2345-6789" not in sent


def test_the_strict_variant_reads_personal_data_unmasked_under_org_wide_masking(world) -> None:
    world.policy = Governance(pii_masking=True, external_data_guard=True)
    out = _ask(world, "고객 연락처 010-2345-6789 로 사과 문자 초안 하나", model=STRICT)
    assert out.status_code == 200
    assert "010-2345-6789" in world.calls[-1]["body"]["messages"][0]["content"]


def test_org_wide_masking_still_masks_a_pick_that_may_leave(world) -> None:
    world.models = [FLASH, DEEPSEEK]
    world.policy = Governance(pii_masking=True, external_data_guard=True)
    out = _ask(world, "고객 연락처 010-2345-6789 로 사과 문자 초안 하나")
    assert out.status_code == 200
    assert "010-2345-6789" not in world.calls[-1]["body"]["messages"][0]["content"]


def test_a_model_that_failed_is_passed_over_once_then_remembered(world) -> None:
    world.fail = {FLASH["id"]}
    out = _ask(world, "안녕하세요!")
    route = out.json()["kloudchat"]["route"]
    assert out.status_code == 200 and route["fallback"] is True and route["model"] != FLASH["id"]
    # one failed call to the gateway, one answered: nothing billed twice
    assert [c["body"]["model"] for c in world.calls][:1] == [FLASH["id"]]
    assert len(world.calls) == 2
    before = len(world.calls)
    _ask(world, "안녕하세요!")
    assert len(world.calls) == before + 1


def test_one_request_is_one_gateway_call_on_the_callers_key(world) -> None:
    _ask(world, "안녕하세요!")
    assert len(world.calls) == 1 and world.calls[0]["auth"] == f"Bearer {KEY}"


def test_the_owner_of_a_key_is_looked_up_once_a_minute(world) -> None:
    for _ in range(3):
        _ask(world, "안녕하세요!")
    assert world.key_lookups == 1


def _events(text: str) -> list[str]:
    return [e[5:].strip() for e in text.split("\n\n") if e.startswith("data:")]


def test_a_stream_names_the_requested_model_and_carries_the_route_once(world) -> None:
    out = _ask(world, "안녕하세요!", stream=True, stream_options={"include_usage": True})
    assert out.status_code == 200 and "X-KloudChat-Route" in out.headers
    events = _events(out.text)
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    assert all(c["model"] == SUPER for c in chunks)
    routed = [c for c in chunks if "kloudchat" in c]
    assert len(routed) == 1 and routed[0].get("usage")
    assert routed[0]["kloudchat"]["route"]["model"] == FLASH["id"]


def test_a_stream_without_usage_carries_it_on_the_finishing_chunk(world) -> None:
    world.stream_usage = False
    chunks = [json.loads(e) for e in _events(_ask(world, "안녕하세요!", stream=True).text)[:-1]]
    routed = [c for c in chunks if "kloudchat" in c]
    assert len(routed) == 1 and routed[0]["choices"][0]["finish_reason"] == "stop"


def test_an_ordinary_model_goes_through_unchanged(world) -> None:
    raw = json.dumps(
        {"model": "local/qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}]},
        separators=(",", ":"),
    ).encode()
    out = world.client.post(
        "/llm/v1/chat/completions", content=raw, headers={"Authorization": f"Bearer {KEY}"}
    )
    assert out.status_code == 200
    assert "kloudchat" not in out.json() and "X-KloudChat-Route" not in out.headers
    assert world.key_lookups == 0
