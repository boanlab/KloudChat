"""Cached prompt tokens reported by the provider reach the turn's usage (and the stored
message), whatever the provider calls them; a provider that reports none changes nothing."""

from __future__ import annotations

import pytest

from app.services import agent, usage_fields
from app.services.tools.base import Tool, ToolContext, ToolResult


@pytest.mark.parametrize(
    "raw, cached",
    [
        ({"prompt_tokens": 900, "prompt_tokens_details": {"cached_tokens": 640}}, 640),
        ({"prompt_tokens": 900, "prompt_cache_hit_tokens": 512, "prompt_cache_miss_tokens": 388},
         512),
        ({"prompt_tokens": 900, "cache_read_input_tokens": 300}, 300),
        ({"prompt_tokens": 900, "prompt_tokens_details": None}, 0),
        ({"prompt_tokens": 900, "prompt_tokens_details": {"cached_tokens": "x"}}, 0),
        ({"prompt_tokens": 900}, 0),
        (None, 0),
    ],
)
def test_every_provider_spelling_is_read(raw, cached):
    assert usage_fields.cached_tokens(raw) == cached
    spent = usage_fields.spent(raw)
    assert spent["inputTokens"] == (900 if raw else 0)
    assert spent.get(usage_fields.CACHED, 0) == cached
    if not cached:
        # nothing reported: the usage dict keeps its old two keys
        assert set(spent) == {"inputTokens", "outputTokens"}


def test_a_re_asked_call_keeps_both_calls_cached_tokens():
    merged = usage_fields.merged_raw(
        {"prompt_tokens": 10, "completion_tokens": 1,
         "prompt_tokens_details": {"cached_tokens": 4}},
        {"prompt_tokens": 20, "completion_tokens": 2, "prompt_cache_hit_tokens": 8},
    )
    assert usage_fields.spent(merged) == {"inputTokens": 30, "outputTokens": 3,
                                          "cachedInputTokens": 12}
    assert usage_fields.merged_raw({"prompt_tokens": 1}, None) == {
        "prompt_tokens": 1, "completion_tokens": 0}


def test_add_cached_sums_only_what_was_reported():
    total = {"inputTokens": 5, "outputTokens": 1}
    usage_fields.add_cached(total, {"inputTokens": 5, "outputTokens": 1})
    assert total == {"inputTokens": 5, "outputTokens": 1}
    usage_fields.add_cached(total, {"cachedInputTokens": 3})
    usage_fields.add_cached(total, {"cachedInputTokens": 4})
    usage_fields.add_cached(total, None)
    assert total["cachedInputTokens"] == 7


def test_the_stream_accumulator_reads_the_usage_chunk():
    acc = agent._Accumulator()
    acc.add_chunk({"choices": [{"delta": {"content": "hi"}}]})
    acc.add_chunk({"choices": [], "usage": {"prompt_tokens": 1200, "completion_tokens": 7,
                                            "prompt_tokens_details": {"cached_tokens": 1024}}})
    assert acc.usage == {"inputTokens": 1200, "outputTokens": 7, "cachedInputTokens": 1024}
    plain = agent._Accumulator()
    plain.add_chunk({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}})
    assert plain.usage == {"inputTokens": 3, "outputTokens": 1}


@pytest.mark.asyncio
async def test_a_tool_turn_sums_each_hops_cached_tokens(monkeypatch):
    hops = [
        {"calls": [{"id": "c1", "name": "lookup", "arguments": "{}"}],
         "usage": {"inputTokens": 1000, "outputTokens": 10, "cachedInputTokens": 0}},
        {"text": "답입니다.",
         "usage": {"inputTokens": 1100, "outputTokens": 20, "cachedInputTokens": 960}},
    ]
    seen = []

    async def completion(_model, messages, tools, *_args, **_kwargs):
        step = hops[len(seen)]
        seen.append(len(messages))
        acc = agent._Accumulator()
        acc.calls = dict(enumerate(step.get("calls", [])))
        acc.content = [step["text"]] if step.get("text") else []
        acc.usage = {k: v for k, v in step["usage"].items() if v or k != "cachedInputTokens"}
        for text in acc.content:
            yield "delta", text
        yield "done", acc

    async def run(_arguments):
        return ToolResult(content="SYNTHETIC_RESULT")

    monkeypatch.setattr(agent, "_stream_once", completion)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 3)
    tool = Tool(name="lookup", description="synthetic", parameters={"type": "object"},
                run=run, label="lookup")
    events = [
        event async for event in agent.run_turn(
            "synthetic/model", [{"role": "user", "content": "찾아 줘"}], [tool],
            ToolContext(user_id="u", session_id="s"),
        )
    ]
    usage = next(e for e in events if e["type"] == "usage")
    assert len(seen) == 2
    assert usage["inputTokens"] == 2100 and usage["outputTokens"] == 30
    assert usage["cachedInputTokens"] == 960
