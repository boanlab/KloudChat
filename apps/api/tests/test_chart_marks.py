"""A chart's 「표시」 is drawn: marked x values ride on the chart, not only in its caption."""

from app.services.tools import builtin
from app.services.tools.base import ToolContext

MONTHS = [f"2025-{m:02d}" for m in range(1, 13)] + [f"2026-{m:02d}" for m in range(1, 13)]


async def _chart(**extra):
    ctx = ToolContext(user_id="u", session_id="s")
    args = {"chartType": "line", "title": "월별 발전량",
            "series": [{"name": "발전량", "points": [{"x": m, "y": i} for i, m in
                                                    enumerate(MONTHS)]}], **extra}
    result = await builtin.create_chart(args, ctx)
    assert not result.failed
    return ctx.pending_artifacts[0]["data"]


async def test_marks_are_kept_on_the_chart_s_own_categories():
    data = await _chart(marks=[{"x": "2025-08", "label": "폭염 특보"}, {"x": "1999-01"},
                               {"x": "2025-08", "label": "또"}])
    assert data["marks"] == [{"x": "2025-08", "label": "폭염 특보"}]


async def test_a_caption_that_says_marked_marks_the_months_it_names():
    data = await _chart(caption="2025-08(폭염 특보 23일)과 2026-03(태양광 가동)에 표시")
    assert [m["x"] for m in data["marks"]] == ["2025-08", "2026-03"]
    assert (await _chart(caption="2025-08이 가장 높다"))["marks"] == []
