"""A report is drawn while it is written in the look it is saved with.

UX audit (eval instance, 「사내 클라우드 보안 점검 결과」): the panel showed 「편집형 / 보라」
while the sections streamed and switched to 「미니멀 / 먹」 when the document arrived. The
look was decided only in `report-save`; until then the panel had nothing but its own
default. Now `approved-plan` announces the same decision before the first section.
"""

from __future__ import annotations

import pytest

from app.pipeline.report_stages import report_design
from app.services import deck, design, report

REQUEST = (
    "사내 클라우드 보안 점검 결과를 정리한 1쪽 보고서. 점검 항목 5개, 발견 사항, 개선 권고로 구성"
)
#: What the outline offered in that run: the planner chose 미니멀.
PLAN = {
    "title": "사내 클라우드 보안 점검 결과",
    "sections": ["점검 개요 및 범위", "점검 항목 5개 기준", "발견 사항", "개선 권고"],
    "visualStyle": "minimal",
}


async def _events(monkeypatch, **kwargs) -> list[dict]:
    async def complete(model, messages, api_key, max_tokens, **_):
        return "점검 결과를 정리했다.", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    return [
        e
        async for e in report.write(
            request=REQUEST, model="m", api_key="k", web_search=False, approved_plan=PLAN,
            figures_plan=[], **kwargs,
        )
    ]


async def test_the_look_is_announced_before_the_first_section(monkeypatch):
    events = await _events(monkeypatch)
    kinds = [e["type"] for e in events]

    assert "design" in kinds
    assert kinds.index("design") < kinds.index("section")
    announced = next(e["design"] for e in events if e["type"] == "design")
    # The planner's 미니멀 and the subject's 먹 — what the saved report carried.
    assert announced["visualStyle"] == "minimal"
    assert announced["accent"] == deck.topic_accent(REQUEST) == "#334155"
    # Exactly what `report-save` stores.
    assert announced == report_design(REQUEST, PLAN, None, None)


async def test_a_project_design_is_announced_as_it_is(monkeypatch):
    tokens = design.normalise_tokens({"visualStyle": "poster", "accent": "#0f766e"})

    events = await _events(monkeypatch, design_tokens=tokens)

    assert next(e["design"] for e in events if e["type"] == "design") == tokens


@pytest.mark.parametrize(
    ("plan", "tokens", "style"),
    [
        (PLAN, None, "minimal"),
        ({**PLAN, "visualStyle": "editorial"}, None, "editorial"),
        (PLAN, {**design.DEFAULT_TOKENS, "visualStyle": "poster"}, "poster"),
    ],
)
def test_one_decision_for_the_draft_and_the_saved_report(plan, tokens, style):
    look = report_design(REQUEST, plan, tokens, None)

    assert look is not None and look["visualStyle"] == style
