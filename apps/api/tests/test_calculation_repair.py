"""One bounded repair of ignored named tool choice, without recycling the draft."""

from copy import deepcopy

import pytest

from app.services import agent
from app.services.tools.arithmetic import CALCULATE
from app.services.tools.base import ToolContext


@pytest.mark.asyncio
@pytest.mark.parametrize("obeys_repair", [False, True])
async def test_missing_calculator_call_gets_one_schema_only_repair(monkeypatch, obeys_repair):
    seen = []
    draft = "Unverified draft says 80 to 100 is 20 percent."

    async def stream(_model, messages, tools, *_args, **kwargs):
        seen.append(deepcopy(messages))
        assert len(seen) <= (3 if obeys_repair else 2)
        acc = agent._Accumulator()
        if len(seen) == 1 or not obeys_repair:
            acc.content.append(draft)
            yield "delta", draft
        elif len(seen) == 2:
            assert [tool.name for tool in tools] == ["calculate"]
            assert kwargs["force_tool"] == "calculate"
            assert not any(draft in str(row) for row in messages)
            acc.calls[0] = {
                "id": "repair", "name": "calculate",
                "arguments": '{"expression":"(100-80)/80*100"}',
            }
        else:
            acc.content.append("25%")
            yield "delta", "25%"
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", stream)
    context = ToolContext(user_id="qa", session_id="qa")
    events = [event async for event in agent.run_turn(
        "synthetic/model", [], [CALCULATE], context,
        preflight_tool="calculate", calculation_required=True,
    )]
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert draft not in text
    if obeys_repair:
        assert len(seen) == 3
        assert context.tool_calls["calculate"] == 1
        assert text == "25%"
    else:
        assert len(seen) == 2
        assert context.tool_calls == {}
        assert "확정할 수 없습니다" in text
