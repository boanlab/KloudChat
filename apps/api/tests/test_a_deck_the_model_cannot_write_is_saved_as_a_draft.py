"""A PowerPoint write a small model cannot finish is saved as a draft from the fullest arguments
sent, and the reply says so."""

from __future__ import annotations

import json

import pytest

from app.services import agent, office_claims
from app.services.tools import json_repair
from app.services.tools import pptx as pptx_tools
from app.services.tools.base import Tool, ToolContext, ToolResult

SLIDES = [{"title": f"슬라이드 {n}", "bullets": ["가", "나"]} for n in range(1, 5)]
GOOD = json.dumps({"title": "발표", "slides": SLIDES}, ensure_ascii=False)


def _write_tool(runs: list[dict], refuse_until: int = 99) -> Tool:
    """A pptx_write that refuses its first `refuse_until` plain calls and saves a draft."""

    async def write(args, ctx):
        runs.append(args)
        if args.get("__draft__") is True:
            ctx.pptx.setdefault("saved", []).append({
                "version": 1, "draft": True, "changes": ["초안 저장"],
                "contents": {"slides": len(args["slides"]) + 1}})
            return ToolResult(content="초안으로 저장했습니다. 파일 안: 슬라이드 5장", detail="발표")
        if len([r for r in runs if not r.get("__draft__")]) <= refuse_until:
            return ToolResult(content="장 수가 요청과 다릅니다.", failed=True)
        return ToolResult(content="저장했습니다.")

    return Tool(name="pptx_write", description="d", parameters={"type": "object"}, run=write,
                label="쓰는 중", read_only=False, wants_context=True,
                draft=pptx_tools.draft_args)


def _model(monkeypatch, arguments: list[str], final="끝났습니다."):
    hops: list[list[dict]] = []

    async def completion(_model, messages, tools, *_args, **_kwargs):
        hops.append(messages)
        acc = agent._Accumulator()
        sent = len([h for h in hops if h is not None]) - 1
        if tools and sent < len(arguments):
            acc.calls[0] = {"id": f"c{len(hops)}", "name": "pptx_write",
                            "arguments": arguments[sent]}
        else:
            acc.content = [final]
        acc.usage = {"inputTokens": 3, "outputTokens": 5}
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", completion)
    return hops


async def _turn(model, tool, ctx):
    return [e async for e in agent.run_turn(
        model, [{"role": "user", "content": "PowerPoint 5장 만들어 줘"}], [tool], ctx)]


@pytest.mark.asyncio
async def test_two_refusals_in_a_row_save_a_draft_for_a_self_hosted_model(monkeypatch):
    runs: list[dict] = []
    hops = _model(monkeypatch, [GOOD, GOOD, GOOD])
    ctx = ToolContext(user_id="u", session_id="s")
    events = await _turn("local/qwen3.8-27b", _write_tool(runs), ctx)
    drafts = [r for r in runs if r.get("__draft__")]
    assert len(drafts) == 1 and len(drafts[0]["slides"]) == 4
    assert any(e.get("id", "").endswith("_draft") and e["status"] == "done"
               for e in events if e["type"] == "step")
    # the closing call tells the model it is a draft
    assert "DRAFT" in hops[-1][-1]["content"] or "초안" in hops[-1][-1]["content"]
    assert ctx.pptx["saved"][-1]["draft"] is True


@pytest.mark.asyncio
async def test_a_strong_model_keeps_its_retries(monkeypatch):
    runs: list[dict] = []
    _model(monkeypatch, [GOOD, GOOD, GOOD])
    ctx = ToolContext(user_id="u", session_id="s")
    await _turn("anthropic/claude-sonnet-5", _write_tool(runs, refuse_until=2), ctx)
    assert not [r for r in runs if r.get("__draft__")]
    assert "saved" not in ctx.pptx


@pytest.mark.asyncio
async def test_the_last_calls_save_a_draft_for_any_model(monkeypatch):
    runs: list[dict] = []
    monkeypatch.setattr(agent.settings, "max_tool_hops", 3)
    # a different argument text each time, so no repeat is refused as a repeat
    sends = [json.dumps({"title": f"t{n}", "slides": SLIDES}, ensure_ascii=False)
             for n in range(6)]
    _model(monkeypatch, sends)
    ctx = ToolContext(user_id="u", session_id="s")
    await _turn("anthropic/claude-sonnet-5", _write_tool(runs), ctx)
    assert len([r for r in runs if r.get("__draft__")]) == 1
    assert len(runs) <= 4


@pytest.mark.asyncio
async def test_a_call_cut_off_in_its_array_is_read_back_whole_slides_only(monkeypatch):
    runs: list[dict] = []
    cut = GOOD[:GOOD.rindex('{"title": "슬라이드 4"') + 20]
    assert not cut.endswith("]}")
    _model(monkeypatch, [cut], final="저장했습니다.")
    ctx = ToolContext(user_id="u", session_id="s")
    events = await _turn("local/qwen3.8-27b", _write_tool(runs, refuse_until=0), ctx)
    assert len(runs[0]["slides"]) == 3
    steps = [e for e in events if e["type"] == "step"]
    assert steps[-1]["status"] == "done"


def test_a_reply_that_calls_a_draft_finished_gets_the_sentence():
    ctx = ToolContext(user_id="u", session_id="s")
    ctx.pptx["saved"] = [{"version": 1, "draft": True, "changes": ["초안 저장"],
                          "contents": {"slides": 5}}]
    said = office_claims.note("12장 발표 자료를 완성했습니다.", ctx, {"pptx"})
    assert "초안" in said and "5장" in said
    assert office_claims._draft_note("초안으로 저장했습니다.", ctx) == ""


def test_the_draft_keeps_what_is_there_and_invents_nothing():
    made = pptx_tools.draft_args({
        "title": "", "slides": [
            {"title": "", "bullets": "가\n나"},
            {"title": "표", "rows": [["a", "b"]] + [["x" * 400, "y" * 400]] * 30},
            {"title": "차트", "chart": {"categories": ["a"], "series": []}, "bullets": ["z"]},
            "not a slide"],
        "replace": True})
    assert made is not None and "replace" not in made
    assert [s["title"] for s in made["slides"]] == ["슬라이드 1", "표", "차트"]
    assert made["slides"][0]["bullets"] == ["가", "나"]
    assert "rows" not in made["slides"][1] and "chart" not in made["slides"][2]
    assert pptx_tools.draft_args({"title": "t", "slides": []}) is None


def test_repair_closes_only_whole_slides():
    got = json_repair.repair('{"title":"T","slides":[{"title":"a","bullets":["x"]},{"title":"b","b')
    assert got == ({"title": "T", "slides": [{"title": "a", "bullets": ["x"]}]}, True)
    assert json_repair.repair('{"title":"T"}') == ({"title": "T"}, False)
    assert json_repair.repair("not json") is None
