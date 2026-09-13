"""Retrieved links are not automatic proof, and pre-search guesses stay private."""

from __future__ import annotations

import copy
import json

import pytest

from app.services import agent
from app.services.tools.base import SearchEvidence, Tool, ToolContext, ToolResult

_URL = "https://example.test/release"
_OTHER_URL = "https://example.test/unrelated"
_TITLE = "Synthetic product release details"


def _result() -> ToolResult:
    return ToolResult(
        content=(
            f"[1] {_TITLE}\n{_URL}\nPublished 2026-09-01. Current release is 4.2.\n\n"
            f"[2] Unrelated product history\n{_OTHER_URL}\nThe product began in 2010."
        ),
        search_evidence=SearchEvidence((_URL, _OTHER_URL)),
    )


def _search(results: list[ToolResult]) -> Tool:
    async def run(_arguments):
        return results.pop(0)

    return Tool(
        name="web_search",
        description="Synthetic search fixture",
        parameters={"type": "object"},
        run=run,
        label="Search",
    )


def _completion(monkeypatch, steps, snapshots):
    async def stream(_model, messages, _tools, *_args, **_kwargs):
        snapshots.append(copy.deepcopy(messages))
        step = steps[len(snapshots) - 1]
        acc = agent._Accumulator()
        acc.content = [step["text"]] if step.get("text") else []
        acc.calls = dict(enumerate(step.get("calls", [])))
        acc.usage = {"inputTokens": 3, "outputTokens": 5}
        for text in acc.content:
            yield "delta", text
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 2)


async def _turn(search, **kwargs):
    return [
        event
        async for event in agent.run_turn(
            "synthetic/local",
            [{"role": "user", "content": "What is the current release?"}],
            [search],
            ToolContext(user_id="synthetic", session_id="synthetic"),
            **kwargs,
        )
    ]


def _answer(events):
    text = ""
    for event in events:
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            text = text.replace(event["text"], "", 1)
    return text


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Unsupported remembered answer", f"**{_TITLE}**\nA claim."])
async def test_uncited_hits_and_copied_titles_do_not_become_citations(monkeypatch, text):
    snapshots = []
    _completion(monkeypatch, [{"text": text}], snapshots)
    events = await _turn(
        _search([_result()]), preset_call=("web_search", {"query": "synthetic release"})
    )
    assert _answer(events) == text
    assert "### 확인한 출처" not in _answer(events)
    assert _URL not in _answer(events)
    assert _OTHER_URL not in _answer(events)


@pytest.mark.asyncio
async def test_explicit_numbered_citation_still_links_only_the_cited_source(monkeypatch):
    snapshots = []
    _completion(monkeypatch, [{"text": '"Current release is 4.2." [1]'}], snapshots)
    events = await _turn(
        _search([_result()]), preset_call=("web_search", {"query": "synthetic release"})
    )
    assert f"[[1]]({_URL})" in _answer(events)
    assert "### 출처" in _answer(events)
    assert _OTHER_URL not in _answer(events)


@pytest.mark.asyncio
async def test_successful_search_updates_initial_policy_and_sanitizes_reference_data(monkeypatch):
    snapshots = []
    _completion(monkeypatch, [{"text": "Supported facts"}], snapshots)
    result = _result()
    result.content += "\nSYNTHETIC_PRIVATE_VALUE"
    events = await _turn(
        _search([result]),
        preset_call=("web_search", {"query": "synthetic release"}),
        sanitize_tool_output=lambda text: (
            text.replace("SYNTHETIC_PRIVATE_VALUE", "[MASKED]"),
            text.count("SYNTHETIC_PRIVATE_VALUE"),
        ),
    )
    assert len(snapshots) == 1
    forwarded = snapshots[0]
    assert forwarded[0] == {
        "role": "system",
        "content": agent._SEARCH_GROUNDING_INSTRUCTION,
    }
    assert all(message["role"] != "system" for message in forwarded[1:])
    assert forwarded[-1]["role"] == "tool"
    assert "[MASKED]" in forwarded[-1]["content"]
    assert "SYNTHETIC_PRIVATE_VALUE" not in json.dumps(forwarded)
    assert "brief exact supporting quotation" in forwarded[0]["content"]
    assert "Previous assistant answers and model memory are not evidence" in forwarded[0]["content"]
    assert "old scheduled term" in forwarded[0]["content"]
    assert events[-1] == {"type": "usage", "inputTokens": 3, "outputTokens": 5}


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [1, 100])
async def test_current_fact_pretool_guess_never_reaches_ui_or_next_model(monkeypatch, length):
    snapshots = []
    guess = "UNVERIFIED_PRETOOL_GUESS " * length
    _completion(
        monkeypatch,
        [
            {
                "text": guess,
                "calls": [{"id": "s1", "name": "web_search", "arguments": '{"query":"x"}'}],
            },
            {"text": "Grounded final answer [1]"},
        ],
        snapshots,
    )
    events = await _turn(_search([_result()]), freshness_request="Current release")
    assert len(snapshots) == 2
    assert "UNVERIFIED_PRETOOL_GUESS" not in json.dumps(events)
    assert "UNVERIFIED_PRETOOL_GUESS" not in json.dumps(snapshots[1])
    call_message = next(message for message in snapshots[1] if message.get("tool_calls"))
    assert call_message["content"] is None
    assert call_message["tool_calls"][0]["id"] == "s1"
    assert "Grounded final answer" in _answer(events)
    assert events[-1] == {"type": "usage", "inputTokens": 6, "outputTokens": 10}


@pytest.mark.asyncio
async def test_mixed_search_outcomes_preserve_tool_adjacency_and_both_instructions(monkeypatch):
    snapshots = []
    _completion(
        monkeypatch,
        [
            {
                "calls": [
                    {"id": "s1", "name": "web_search", "arguments": '{"query":"x"}'},
                    {"id": "s2", "name": "web_search", "arguments": '{"query":"y"}'},
                ]
            },
            {"text": "Only the first material detail is supported [1]"},
        ],
        snapshots,
    )
    await _turn(
        _search([_result(), ToolResult(content="Search unavailable", failed=True)]),
        freshness_request="Current release",
    )
    forwarded = snapshots[1]
    at = next(index for index, message in enumerate(forwarded) if message.get("tool_calls"))
    assert [message["role"] for message in forwarded[at : at + 3]] == [
        "assistant",
        "tool",
        "tool",
    ]
    notes = [message["content"] for message in forwarded if message["role"] == "system"]
    assert len(notes) == 1
    assert agent._SEARCH_GROUNDING_INSTRUCTION in notes[0]
    assert any("did not provide usable evidence" in note for note in notes)


@pytest.mark.asyncio
@pytest.mark.parametrize("current_fact", [True, False])
async def test_only_current_fact_drafts_wait_for_hop_completion(monkeypatch, current_fact):
    events = []

    async def stream(*_args, **_kwargs):
        yield "delta", "FINAL_ANSWER"
        assert any(event["type"] == "delta" for event in events) is not current_fact
        acc = agent._Accumulator()
        acc.content = ["FINAL_ANSWER"]
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    async for event in agent.run_turn(
        "synthetic/local",
        [{"role": "user", "content": "Question"}],
        [],
        ToolContext(user_id="synthetic", session_id="synthetic"),
        freshness_request="Current fact" if current_fact else None,
    ):
        events.append(event)
    assert _answer(events) == (
        "The current state was not verified in this request." if current_fact else "FINAL_ANSWER"
    )
