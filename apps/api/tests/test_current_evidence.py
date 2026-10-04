"""Present-state answers without read evidence are given, dated and marked unverified."""

from __future__ import annotations

import copy
import json
from datetime import date

import pytest

from app.services import agent, current_evidence
from app.services.tools.base import SearchEvidence, Tool, ToolContext, ToolResult

_AS_OF = date(2026, 9, 12)
_KR = "현재 대한민국 대통령"
_CAVEAT = current_evidence.caveat(_KR)


def test_an_offline_answer_keeps_its_words_and_gains_a_caveat():
    raw = "2022년 5월에 취임한 사람은 윤석열입니다."
    assert current_evidence.render(raw, _KR, as_of=_AS_OF) == raw + _CAVEAT


@pytest.mark.parametrize(
    "raw",
    [
        "2022년 기준으로는 A였지만 현재 상태는 이번 요청에서 확인하지 못했습니다.",
        "제가 아는 마지막 값은 4.2이며, 이는 학습 시점 기준이라 바뀔 수 있습니다.",
        "The last version I know of is 4, as of 2024; this was not verified in this request.",
    ],
)
def test_an_answer_that_already_hedges_is_left_alone(raw):
    assert current_evidence.render(raw, _KR, as_of=_AS_OF) == raw


def test_an_empty_answer_states_that_nothing_was_verified():
    assert "확인하지 않았습니다" in current_evidence.render("", _KR)
    assert "not verified" in current_evidence.render("", "Latest version?")


def test_the_english_caveat_follows_an_english_request():
    out = current_evidence.render("Version 4 came out in 2022.", "Latest product version")
    assert out.startswith("Version 4 came out in 2022.")
    assert "training-time knowledge" in out


def _tool(name, result, *, source="builtin", read_only=True):
    async def run(_arguments):
        return result

    return Tool(
        name=name,
        description="Synthetic read fixture",
        parameters={"type": "object"},
        run=run,
        label=name,
        source=source,
        read_only=read_only,
    )


_SEARCH = ToolResult(
    content="[1] Synthetic release\nhttps://example.test/release\nThe latest version is 4.",
    search_evidence=SearchEvidence(("https://example.test/release",)),
)


@pytest.mark.parametrize(
    "name,result,usable",
    [
        ("web_search", _SEARCH, True),
        ("web_search", ToolResult(content="Link only"), False),
        ("fetch_url", ToolResult(content="Retrieved page body"), True),
        ("fetch_url", ToolResult(content="오류: failed"), False),
        ("weather", ToolResult(content="기준 시각: 2026-09-12T12:00\n현재: 23.5°C"), True),
        ("weather", ToolResult(content="기준 시각: ?\n현재: None°C"), False),
        ("search_knowledge", ToolResult(content="A passage", detail="1개 대목"), True),
        ("search_knowledge", ToolResult(content="Document body", detail="자료 2건 전문"), True),
        ("search_knowledge", ToolResult(content="No matching passage", detail="해당 없음"), False),
        ("calculate", ToolResult(content="2022 + 5 = 2027"), False),
        ("execute_code", ToolResult(content="stdout: current leader is X"), False),
        ("create_artifact", ToolResult(content="Artifact created"), False),
    ],
)
def test_only_structurally_usable_read_results_release_the_guard(name, result, usable):
    assert current_evidence.usable_read_result(_tool(name, result), result) is usable
    assert (
        current_evidence.usable_read_result(_tool(name, result, read_only=False), result) is False
    )
    failed = copy.copy(result)
    failed.failed = True
    assert current_evidence.usable_read_result(_tool(name, failed), failed) is False
    empty = copy.copy(result)
    empty.empty = True
    assert current_evidence.usable_read_result(_tool(name, empty), empty) is False


@pytest.mark.parametrize("read_only", [False, None, True])
@pytest.mark.parametrize("failed", [False, True])
def test_authorized_mcp_read_material_is_usable_but_write_unknown_and_failure_are_not(
    read_only, failed
):
    result = ToolResult(content='{"inventory": 12}', failed=failed)
    tool = _tool("inventory_lookup", result, source="connector", read_only=read_only)
    assert current_evidence.usable_read_result(tool, result) is (read_only is True and not failed)


@pytest.mark.parametrize("content", ["error: unavailable", "오류: 조회 실패", "  "])
def test_mcp_error_text_or_empty_body_is_not_current_material(content):
    result = ToolResult(content=content)
    tool = _tool("inventory_lookup", result, source="connector", read_only=True)
    assert current_evidence.usable_read_result(tool, result) is False


def _mock_model(monkeypatch, steps, snapshots):
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


async def _turn(tools=(), **kwargs):
    return [
        event
        async for event in agent.run_turn(
            "synthetic/local",
            [{"role": "system", "content": "BASE_POLICY"}, {"role": "user", "content": _KR}],
            list(tools),
            ToolContext(user_id="synthetic", session_id="synthetic"),
            freshness_request=kwargs.pop("freshness_request", _KR),
            **kwargs,
        )
    ]


def _visible(events):
    return "".join(event["text"] for event in events if event["type"] == "delta")


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_an_offline_current_answer_streams_live_and_ends_with_a_caveat(monkeypatch, strict):
    snapshots = []
    _mock_model(
        monkeypatch,
        [{"text": "2022년 5월에 취임한 대통령은 윤석열입니다."}],
        snapshots,
    )
    events = await _turn(strict_local=strict)
    # The words stream as written, and the caveat follows them.
    assert _visible(events) == "2022년 5월에 취임한 대통령은 윤석열입니다." + _CAVEAT
    assert [e["text"] for e in events if e["type"] == "delta"][0].startswith("2022년")
    assert current_evidence.instruction(_KR) in snapshots[0][0]["content"]
    assert [message["role"] for message in snapshots[0]] == ["system", "user"]
    assert len(snapshots) == 1
    assert events[-1] == {"type": "usage", "inputTokens": 3, "outputTokens": 5}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["web_search", "fetch_url", "weather", "search_knowledge"])
async def test_actual_read_material_removes_the_caveat_without_an_extra_model_call(
    monkeypatch, name
):
    results = {
        "web_search": _SEARCH,
        "fetch_url": ToolResult(content="A retrieved body"),
        "weather": ToolResult(content="기준 시각: 2026-09-12T12:00\n현재: 23.5°C"),
        "search_knowledge": ToolResult(content="A retrieved passage", detail="1개 대목"),
    }
    snapshots = []
    _mock_model(monkeypatch, [{"text": "ANSWER_FROM_RETRIEVED_MATERIAL"}], snapshots)
    events = await _turn(
        [_tool(name, copy.copy(results[name]))], preset_call=(name, {"query": "synthetic"})
    )
    assert _visible(events) == "ANSWER_FROM_RETRIEVED_MATERIAL"
    assert current_evidence.instruction(_KR) not in snapshots[0][0]["content"]
    assert all(message["role"] != "system" for message in snapshots[0][1:])
    assert len(snapshots) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["calculate", "execute_code", "create_artifact"])
async def test_calculation_or_write_is_not_current_state_evidence(monkeypatch, name):
    snapshots = []
    _mock_model(monkeypatch, [{"text": "UNSUPPORTED_CURRENT_CLAIM"}], snapshots)
    events = await _turn(
        [_tool(name, ToolResult(content="2022 + 5 = 2027"))],
        preset_call=(name, {"expression": "2022+5"}),
    )
    # A calculation or a write is not evidence for the present: the caveat stays.
    assert _visible(events) == "UNSUPPORTED_CURRENT_CLAIM" + _CAVEAT
    assert current_evidence.instruction(_KR) in snapshots[0][0]["content"]
    assert len(snapshots) == 1


@pytest.mark.asyncio
async def test_failed_search_is_still_masked_before_the_next_model_hop(monkeypatch):
    snapshots = []
    _mock_model(monkeypatch, [{"text": "UNSUPPORTED_CURRENT_CLAIM"}], snapshots)
    events = await _turn(
        [_tool("web_search", ToolResult(content="SYNTHETIC_PRIVATE_VALUE", failed=True))],
        preset_call=("web_search", {"query": "synthetic"}),
        sanitize_tool_output=lambda text: (
            text.replace("SYNTHETIC_PRIVATE_VALUE", "[MASKED]"),
            text.count("SYNTHETIC_PRIVATE_VALUE"),
        ),
    )
    assert _visible(events) == "UNSUPPORTED_CURRENT_CLAIM" + _CAVEAT
    assert "SYNTHETIC_PRIVATE_VALUE" not in json.dumps(snapshots)
    assert any(event["type"] == "privacy_route" for event in events)
    assert current_evidence.instruction(_KR) in snapshots[0][0]["content"]


@pytest.mark.asyncio
async def test_stable_task_does_not_receive_the_instruction_or_a_caveat(monkeypatch):
    snapshots = []
    _mock_model(monkeypatch, [{"text": "1+1=2"}], snapshots)
    events = await _turn(freshness_request=None)
    assert _visible(events) == "1+1=2"
    assert current_evidence.instruction(_KR) not in snapshots[0][0]["content"]


@pytest.mark.asyncio
async def test_existing_calculation_preflight_gate_remains_authoritative(monkeypatch):
    snapshots = []
    _mock_model(
        monkeypatch,
        [
            {"calls": [{"id": "n1", "name": "check_ncs_answer", "arguments": "{}"}]},
            {"text": "CALCULATION_VERIFIED_ANSWER"},
        ],
        snapshots,
    )
    events = await _turn(
        [_tool("check_ncs_answer", ToolResult(content="Valid arithmetic"))],
        preflight_tool="check_ncs_answer",
    )
    assert _visible(events) == "CALCULATION_VERIFIED_ANSWER"
    assert all(
        current_evidence.instruction(_KR) not in snapshot[0]["content"] for snapshot in snapshots
    )
    assert len(snapshots) == 2
