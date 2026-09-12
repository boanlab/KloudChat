"""Present-state claims without read evidence never bypass the display contract."""

from __future__ import annotations

import copy
import json
from datetime import date

import pytest

from app.services import agent, current_evidence
from app.services.tools.base import SearchEvidence, Tool, ToolContext, ToolResult

_AS_OF = date(2026, 9, 12)
_KR = "현재 대한민국 대통령"
_UNKNOWN = "현재 상태는 이번 요청에서 확인하지 못했습니다."


@pytest.mark.parametrize(
    "raw",
    [
        "현재 대한민국의 대통령은 윤석열입니다. 다만 이 답변은 부정확할 수 있습니다.",
        "- 과거 사실: 윤석열이 대통령입니다.\n- 현재 상태: 윤석열이 재임 중입니다.",
        "- 과거 사실: 2022년부터 윤석열은 대통령입니다.\n- 현재 상태: 윤석열입니다.",
        "- 과거 사실: 2022년에 취임했으며 현재 대통령입니다.\n- 현재 상태: 윤석열입니다.",
        "- 과거 사실: 2026년에 취임했다.\n- 현재 상태: 윤석열입니다.",
        "- 과거 사실: 2027년에 취임했다.\n- 현재 상태: 윤석열입니다.",
        "- 과거 사실: 2022년에 취임했고 오늘도 재임 중이다.\n- 현재 상태: 윤석열입니다.",
        "- 과거 사실: 2022년에 취임했다. 현재도 재임했다.\n- 현재 상태: 윤석열입니다.",
        "- 과거 사실: 2022년에 취임했다. https://example.test\n- 현재 상태: 윤석열입니다.",
        "- 과거 사실: [2022년에 취임했다.](https://example.test)\n- 현재 상태: 윤석열입니다.",
        "- 과거 사실: 2022년에 현\u200b재 취임했다.\n- 현재 상태: 윤석열입니다.",
        "- Past fact: He was elected in 2022.\n- Current status: He is the president.",
        "",
    ],
)
def test_invalid_or_present_claims_never_pass_through_the_offline_renderer(raw):
    assert current_evidence.render(raw, _KR, as_of=_AS_OF) == _UNKNOWN


@pytest.mark.parametrize("question", [_KR, "현재 회사 대표이사", "최신 제품 버전"])
def test_short_dated_past_fact_is_kept_but_model_current_status_is_ignored(question):
    raw = "- 과거 사실: 2022년에 기존 담당자가 취임했다.\n- 현재 상태: UNSUPPORTED_CURRENT_CLAIM"
    answer = current_evidence.render(raw, question, as_of=_AS_OF)
    assert answer == (
        f"- 학습지식의 과거 정보(미검증): 2022년에 기존 담당자가 취임했다.\n- 현재 상태: {_UNKNOWN}"
    )
    assert "UNSUPPORTED_CURRENT_CLAIM" not in answer


@pytest.mark.parametrize(
    "past",
    [
        "The product was launched in 2022 and is the current release.",
        "The product was launched in 2022 and is version 4.",
        "The product was launched in 2022 and remains version 4.",
        "The product was launched in 2027.",
        "The product was launched in 2022. " + "Long background " * 30,
    ],
)
def test_english_current_or_oversized_background_is_rejected(past):
    raw = f"- Past fact: {past}\n- Current status: CURRENT_GUESS"
    assert current_evidence.render(raw, "Latest product version", as_of=_AS_OF) == (
        "The current state was not verified in this request."
    )


def test_english_past_fact_uses_english_status_and_not_the_generated_current_field():
    raw = "- Past fact: Version 3 was released in 2022.\n- Current status: CURRENT_GUESS"
    answer = current_evidence.render(raw, "Latest product version", as_of=_AS_OF)
    assert answer == (
        "- Remembered past information (not verified): Version 3 was released in 2022.\n"
        "- Current status: The current state was not verified in this request."
    )


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


@pytest.mark.parametrize(
    "past",
    [
        "2022년 3월 선거에서 당선되어 2022년 5월 10일 기존 담당자가 취임함.",
        "2014년 2월부터 2025년까지 기존 담당자가 CEO를 맡아 왔습니다.",
        "2022년에 기존 담당자가 취임했다.",
    ],
)
def test_one_valid_past_field_survives_extra_prose_but_the_extra_claim_never_renders(past):
    raw = (
        "Copied format instructions, not answer content\n"
        "- 과거 사실: <과거 사실을 적으세요>\n"
        f"- 과거 사실: {past}\n"
        "An extra model paragraph\n"
        "- 현재 상태: UNSUPPORTED_CURRENT_CLAIM\n"
        "현재는 UNSUPPORTED_CURRENT_CLAIM입니다."
    )
    answer = current_evidence.render(raw, _KR, as_of=_AS_OF)
    assert answer == f"- 학습지식의 과거 정보(미검증): {past}\n- 현재 상태: {_UNKNOWN}"
    assert "UNSUPPORTED_CURRENT_CLAIM" not in answer
    assert "Copied format" not in answer


def test_distinct_valid_past_fields_are_ambiguous_and_not_arbitrarily_selected():
    raw = "- 과거 사실: 2022년에 취임했다.\n- 과거 사실: 2023년에 취임했다."
    assert current_evidence.render(raw, _KR, as_of=_AS_OF) == _UNKNOWN


@pytest.mark.parametrize(
    "past",
    [
        "2014년부터 기존 담당자가 CEO를 맡아 왔습니다.",
        "2022년부터 기존 담당자가 대통령으로 재임 중임.",
        "2022년에 취임했고 기존 담당자가 직책을 맡고 있음.",
    ],
)
def test_open_ended_or_ongoing_service_cannot_be_smuggled_into_a_past_field(past):
    raw = f"- 과거 사실: {past}\n- 현재 상태: UNVERIFIED"
    assert current_evidence.render(raw, _KR, as_of=_AS_OF) == _UNKNOWN


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
async def test_offline_current_claim_and_its_cutoff_disclaimer_never_stream(monkeypatch, strict):
    snapshots = []
    _mock_model(
        monkeypatch,
        [{"text": "현재 대한민국의 대통령은 윤석열입니다. 다만 학습 기준을 확인할 수 없습니다."}],
        snapshots,
    )
    events = await _turn(strict_local=strict)
    assert _visible(events) == _UNKNOWN
    assert "윤석열" not in json.dumps(events, ensure_ascii=False)
    assert current_evidence.instruction(_KR) in snapshots[0][0]["content"]
    assert [message["role"] for message in snapshots[0]] == ["system", "user"]
    assert len(snapshots) == 1
    assert events[-1] == {"type": "usage", "inputTokens": 3, "outputTokens": 5}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["web_search", "fetch_url", "weather", "search_knowledge"])
async def test_actual_read_material_removes_fixed_format_without_an_extra_model_call(
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
    assert _visible(events) == _UNKNOWN
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
    assert _visible(events) == _UNKNOWN
    assert "SYNTHETIC_PRIVATE_VALUE" not in json.dumps(snapshots)
    assert any(event["type"] == "privacy_route" for event in events)
    assert current_evidence.instruction(_KR) in snapshots[0][0]["content"]


@pytest.mark.asyncio
async def test_stable_task_does_not_receive_fixed_format_or_output_filter(monkeypatch):
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
