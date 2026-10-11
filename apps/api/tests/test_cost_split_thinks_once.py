"""Two cost readings that differ are settled by one more on the outline model, thinking on.

Mix lane: Flash read 12/15 cost cases plain and 15/15 with thinking (150 s median); the
thinking reading is capped in time, and past the cap the plain third reading settles."""

from __future__ import annotations

import asyncio
import json

import httpx
from test_a_comparison_settles_its_totals import PACK, REPLY, REQUEST, _by_turn, _wrong

from app.services import report, report_checks, thinking

FLASH = "local/qwen3.8-flash"


def _thinking(reply: dict | None, *, delay: float = 0.0):
    asked: list[tuple[str, int]] = []

    async def deliberate(model, messages, api_key, max_tokens):
        asked.append((model, max_tokens))
        if delay:
            await asyncio.sleep(delay)
        text = json.dumps(reply, ensure_ascii=False) if reply is not None else "모르겠음"
        return text, {"inputTokens": 100, "outputTokens": 4000}
    return deliberate, asked


async def test_a_split_is_settled_by_the_outline_model_thinking():
    complete, asked = _by_turn(_wrong(3), REPLY)
    deliberate, thought = _thinking(REPLY)
    usage: dict = {}
    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=complete,
                                      model=FLASH, api_key="k", tiebreak_model=FLASH,
                                      deliberate=deliberate, usage=usage)
    assert asked == [FLASH, FLASH]  # no plain third reading
    assert thought == [(FLASH, report_checks.DELIBERATE_TOKENS)]
    assert pre.costs["options"]["구매"]["total"] == 197_900_000
    run = next(r for r in pre.runs if r["skill"] == "compare-cost")
    assert run["summary"].endswith("세 번 읽어 둘이 같은 값 (세 번째는 생각을 켜고 읽음)")
    assert usage["outputTokens"] >= 4000  # the thinking reading's tokens are counted


async def test_agreeing_readings_never_ask_the_thinking_model():
    complete, asked = _by_turn(REPLY, REPLY)
    deliberate, thought = _thinking(REPLY)
    await report_checks.prepare("comparison", REQUEST, PACK, complete=complete, model=FLASH,
                                api_key="k", tiebreak_model=FLASH, deliberate=deliberate)
    assert asked == [FLASH, FLASH] and thought == []


async def test_a_thinking_reading_past_the_cap_leaves_the_plain_third_reading(monkeypatch):
    monkeypatch.setattr(report_checks, "DELIBERATE_SEC", 0.05)
    complete, asked = _by_turn(_wrong(3), REPLY, REPLY)
    deliberate, thought = _thinking(REPLY, delay=5)
    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=complete,
                                      model=FLASH, api_key="k", tiebreak_model="big",
                                      deliberate=deliberate)
    assert len(thought) == 1 and asked == [FLASH, FLASH, "big"]
    run = next(r for r in pre.runs if r["skill"] == "compare-cost")
    assert run["summary"].endswith("세 번 읽어 둘이 같은 값")
    assert pre.costs["options"]["구매"]["total"] == 197_900_000


async def test_a_thinking_reading_that_reads_nothing_leaves_the_plain_third_reading():
    complete, asked = _by_turn(_wrong(3), REPLY, REPLY)
    deliberate, thought = _thinking(None)
    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=complete,
                                      model=FLASH, api_key="k", tiebreak_model="big",
                                      deliberate=deliberate)
    assert len(thought) == 1 and asked == [FLASH, FLASH, "big"]
    assert pre.costs is not None


async def test_a_thinking_reading_that_disagrees_with_both_withholds_the_totals():
    complete, asked = _by_turn(_wrong(3), REPLY)
    deliberate, _ = _thinking(_wrong(5))
    pre = await report_checks.prepare("comparison", REQUEST, PACK, complete=complete,
                                      model=FLASH, api_key="k", tiebreak_model=FLASH,
                                      deliberate=deliberate)
    assert asked == [FLASH, FLASH]
    assert pre.costs is None and pre.costs_withheld


async def test_a_model_without_a_thinking_switch_keeps_the_plain_third_reading():
    complete, asked = _by_turn(_wrong(3), REPLY, REPLY)
    deliberate, thought = _thinking(REPLY)
    await report_checks.prepare("comparison", REQUEST, PACK, complete=complete, model="m",
                                api_key="k", tiebreak_model="big", deliberate=deliberate)
    assert thought == [] and asked == ["m", "m", "big"]


async def test_the_thinking_call_turns_thinking_on_and_drops_inline_reasoning(monkeypatch):
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "<think>{\"x\": 1} 고민</think>{\"ok\": 2}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 900},
        })

    real = httpx.AsyncClient

    def client(**kwargs):
        return real(transport=httpx.MockTransport(handler), **kwargs)

    async def config():
        return "http://litellm.test", ""

    monkeypatch.setattr(report.httpx, "AsyncClient", client)
    monkeypatch.setattr(report.settings_store, "litellm_config", config)
    text, usage = await report._complete_thinking(FLASH, [{"role": "user", "content": "q"}],
                                                  "k", 12000)
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert "reasoning" not in sent[0] and sent[0]["max_tokens"] == 12000
    assert text == '{"ok": 2}' and usage == {"inputTokens": 10, "outputTokens": 900}
    assert thinking.template_switch_on("local/qwen3.8-27b") == {}
