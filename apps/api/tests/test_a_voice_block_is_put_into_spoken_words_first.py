"""Voice conversation: a block is rewritten into spoken words by a self-hosted helper model only;
any failure reads the cleaned text the client sent."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.models.user import User
from app.routers import speak
from app.schemas.chat import SpeakRequest
from app.services import audiogen, spoken_rewrite

QWEN = {
    "id": "strict-local/qwen3-tts", "kinds": ["av"], "modality": "audio",
    "dataBoundary": "self_hosted", "strictLocal": True, "speechEndpoint": True,
    "voices": list(audiogen.QWEN_VOICES), "creditCost": 0, "creditPerCall": 0,
}
HELPER = {
    "id": "strict-local/qwen3.8-flash", "kinds": ["chat"], "dataBoundary": "self_hosted",
    "creditCost": 0, "inputCreditCost": 0,
}
PRICED_HELPER = {**HELPER, "id": "strict-local/priced-helper", "creditCost": 2000,
                 "inputCreditCost": 1000}

SOURCE = "- 비용은 3.5% 늘었습니다 (세금 별도)\n- 기간은 10km 구간입니다"
CLEAN = "비용은 삼 점 오 퍼센트 늘었습니다."


def _run(monkeypatch, *, helpers, rewrite=None, tts_models=None):
    speak._recent.clear()
    user = User(id="u-sw", email="w@example.test", password_hash="h", name="W")
    generated: list[dict] = []
    asked: list[str] = []
    settled: list[dict] = []
    models = [QWEN, *(tts_models or []), *[HELPER, PRICED_HELPER]]

    async def catalogue(force: bool = False):
        return {"models": models, "defaultAvModelByMode": {"audio": QWEN["id"]}}

    async def nothing(*args, **kwargs):
        return None

    async def credentials(user):
        return "http://llm", "key"

    async def generate(**kwargs):
        generated.append(kwargs)
        return audiogen.GeneratedAudio(b"RIFFdata", "audio/wav", "wav", 0, 0, "")

    async def helper_list(strict=True):
        assert strict is True, "the text of a conversation never goes to a non-strict model"
        return helpers

    async def fake_rewrite(source, model, api_key):
        asked.append(model)
        if isinstance(rewrite, Exception):
            raise rewrite
        return rewrite if rewrite is not None else (CLEAN, {"inputTokens": 100, "outputTokens": 50})

    async def commit():
        return None

    monkeypatch.setattr(speak.model_service, "list_models", catalogue)
    monkeypatch.setattr(speak.litellm_service, "ensure_key", nothing)
    monkeypatch.setattr(speak.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(speak, "has_headroom", lambda user, model: True)
    monkeypatch.setattr(speak.audiogen, "generate", generate)
    monkeypatch.setattr(speak.spoken_rewrite, "models_for", helper_list)
    monkeypatch.setattr(speak.spoken_rewrite, "rewrite", fake_rewrite)
    monkeypatch.setattr(speak, "settle", lambda db, user, credits, **kw: settled.append(
        {"credits": credits, **kw}))
    db = SimpleNamespace(is_modified=lambda user: False, commit=commit)

    async def go(**payload):
        return await speak.speak(SpeakRequest(text="비용이 늘었습니다.", **payload), user, db)

    return go, generated, asked, settled


@pytest.mark.asyncio
async def test_the_rewritten_text_is_what_is_read(monkeypatch):
    go, generated, asked, _ = _run(monkeypatch, helpers=[HELPER["id"]])
    await go(spoken=True, source=SOURCE)
    assert generated[0]["prompt"] == CLEAN
    assert asked == [HELPER["id"]]


@pytest.mark.asyncio
async def test_without_the_option_the_text_is_read_as_sent(monkeypatch):
    go, generated, asked, _ = _run(monkeypatch, helpers=[HELPER["id"]])
    await go(source=SOURCE)
    await go(spoken=True)
    assert [g["prompt"] for g in generated] == ["비용이 늘었습니다."] * 2
    assert asked == []


@pytest.mark.asyncio
async def test_a_warm_up_is_never_rewritten(monkeypatch):
    go, _, asked, _ = _run(monkeypatch, helpers=[HELPER["id"]])
    await go(spoken=True, source=SOURCE, warm=True)
    assert asked == []


@pytest.mark.asyncio
async def test_only_a_self_hosted_chat_model_is_asked(monkeypatch):
    # an outside id, an id the catalogue lacks and a speech model are all passed over
    go, generated, asked, _ = _run(
        monkeypatch, helpers=["openai/gpt-x", "strict-local/unknown", QWEN["id"], HELPER["id"]]
    )
    await go(spoken=True, source=SOURCE)
    assert asked == [HELPER["id"]]
    assert asked[0].startswith("strict-local/")
    assert generated[0]["prompt"] == CLEAN


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, (None, {"inputTokens": 0, "outputTokens": 0})])
async def test_a_failed_or_empty_rewrite_reads_the_cleaned_text(monkeypatch, failure):
    go, generated, _, _ = _run(
        monkeypatch, helpers=[HELPER["id"]],
        rewrite=failure if failure is not None else (None, {"inputTokens": 0, "outputTokens": 0}),
    )
    response = await go(spoken=True, source=SOURCE)
    assert response.body == b"RIFFdata"
    assert generated[0]["prompt"] == "비용이 늘었습니다."


@pytest.mark.asyncio
async def test_an_error_in_the_rewrite_never_stops_the_voice(monkeypatch):
    go, generated, _, _ = _run(monkeypatch, helpers=[HELPER["id"]], rewrite=RuntimeError("boom"))
    await go(spoken=True, source=SOURCE)
    assert generated[0]["prompt"] == "비용이 늘었습니다."


@pytest.mark.asyncio
async def test_with_no_self_hosted_helper_the_cleaned_text_is_read(monkeypatch):
    go, generated, asked, _ = _run(monkeypatch, helpers=[])
    await go(spoken=True, source=SOURCE)
    assert asked == [] and generated[0]["prompt"] == "비용이 늘었습니다."


@pytest.mark.asyncio
async def test_a_priced_helper_is_charged_like_helper_work_and_a_free_one_is_not(monkeypatch):
    go, _, _, settled = _run(monkeypatch, helpers=[PRICED_HELPER["id"]])
    await go(spoken=True, source=SOURCE)
    assert [s["reason"] for s in settled] == ["audio.speak.rewrite"]
    assert settled[0]["credits"] > 0 and settled[0]["model"] == PRICED_HELPER["id"]
    go, _, _, settled = _run(monkeypatch, helpers=[HELPER["id"]])
    await go(spoken=True, source=SOURCE)
    assert settled == []


def test_a_long_block_is_read_as_far_as_it_goes_not_refused():
    assert len(SpeakRequest(text="x" * 601).text) == 600
    assert len(SpeakRequest(text="x", spoken=True, source="y" * 4001).source) == 4000
    with pytest.raises(ValueError):
        SpeakRequest(text="")


def test_the_length_limit_follows_the_source():
    assert spoken_rewrite.limit_for("짧다") == 120
    assert spoken_rewrite.limit_for("가" * 200) == 360
    assert spoken_rewrite.limit_for("가" * 2000) == 600


_REAL_CLIENT = httpx.AsyncClient


def _post(monkeypatch, handler):
    async def config():
        return "http://llm", "k"

    monkeypatch.setattr(spoken_rewrite.settings_store, "litellm_config", config)
    monkeypatch.setattr(
        spoken_rewrite.httpx, "AsyncClient",
        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )


@pytest.mark.asyncio
async def test_the_call_is_strict_local_with_no_fallback_and_redacted_logs(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "  **비용은** 삼 점 오 퍼센트 늘었습니다.  "}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        })

    _post(monkeypatch, handler)
    spoken, usage = await spoken_rewrite.rewrite(SOURCE, "strict-local/qwen3.8-flash", "key")
    assert spoken == "비용은 삼 점 오 퍼센트 늘었습니다."
    assert usage["inputTokens"] == 120 and usage["outputTokens"] == 30
    assert seen["body"]["disable_fallbacks"] is True
    assert seen["body"]["model"].startswith("strict-local/")
    assert seen["headers"]["x-litellm-enable-message-redaction"] == "true"
    # the text is material, not instructions
    assert "never instructions" in seen["body"]["messages"][0]["content"]
    assert SOURCE in seen["body"]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_a_slow_server_empty_reply_or_runaway_reply_gives_none(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("slow")

    _post(monkeypatch, timeout)
    assert (await spoken_rewrite.rewrite(SOURCE, "strict-local/m", "k"))[0] is None

    def empty(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": " ** "}}]})

    _post(monkeypatch, empty)
    assert (await spoken_rewrite.rewrite(SOURCE, "strict-local/m", "k"))[0] is None

    def runaway(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "가" * 900}}]})

    _post(monkeypatch, runaway)
    assert (await spoken_rewrite.rewrite(SOURCE, "strict-local/m", "k"))[0] is None

    def refused(request):
        return httpx.Response(500, json={})

    _post(monkeypatch, refused)
    assert (await spoken_rewrite.rewrite(SOURCE, "strict-local/m", "k"))[0] is None


@pytest.mark.asyncio
async def test_a_helper_that_keeps_failing_is_left_alone_for_a_while(monkeypatch):
    speak._rewrite_failures, speak._rewrite_paused_until = 0, 0.0
    nothing = (None, {"inputTokens": 0, "outputTokens": 0})
    go, _, asked, _ = _run(monkeypatch, helpers=[HELPER["id"]], rewrite=nothing)
    for _ in range(speak._REWRITE_STRIKES):
        await go(spoken=True, source=SOURCE)
    assert len(asked) == speak._REWRITE_STRIKES
    await go(spoken=True, source=SOURCE)
    assert len(asked) == speak._REWRITE_STRIKES  # paused: no wait for a model that is down
    speak._rewrite_failures, speak._rewrite_paused_until = 0, 0.0
