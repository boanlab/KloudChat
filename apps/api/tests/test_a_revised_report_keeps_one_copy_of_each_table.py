"""Revising a finished 결과보고서 over several turns: a table edit goes to the part holding
that table, the table is edited not copied, captions stay over tables, block tokens never
reach the page, 「제목을 「…」로」 renames the document, and the reply says when an asked
table shape is not there (2026 하계 SW 캠프, four follow-ups)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.pipeline import revise_stages
from app.pipeline.stage import Context
from app.services import consistency, report, revise, revised_tables

DOC = json.loads((Path(__file__).parent / "fixtures" / "revise-table" / "sw_camp.json")
                 .read_text())
V1, V4 = DOC["v1"], DOC["v4"]
NAMES = [s["heading"] for s in V1]
BUDGET = "예산 표에 집행률(%) 열 추가해 주고 합계 행도 넣어 줘."
COMPLETION = "과정별 표에 수료율(%) 열 추가해 줘."
INTO_TABLE = "개선점을 '문제-원인-조치' 3열 표로 바꿔 줘."
TITLE = "맨 앞에 3줄 요약 넣고, 제목을 「2026 하계 SW 캠프 운영 결과 보고」로 바꿔 줘."

#: what the writer gave back for the budget request: the budget table redrawn under an item
BUDGET_BODY = """- 운영 실적
  - 고교생 80명 모집, 76명 참가, 71명 수료
- 예산 집행 실적
  - 항목별 편성·집행 현황:

표: 예산 편성·집행 현황
    | 항목 | 편성(천원) | 집행(천원) | 집행률(%) |
    |---|---|---|---|
    | 강사비 | 12,000 | 11,400 | 95.0% |
    | 교재·재료비 | 4,000 | 3,620 | 90.5% |
    | 식비 | 6,080 | 5,890 | 96.9% |
    | 홍보비 | 1,500 | 1,500 | 100.0% |
    | 기타 | 920 | 410 | 44.6% |
    | 합계 | 24,500 | 22,820 | 93.1% |
- 편성 24,500천원 / 집행 22,820천원 / 잔액 1,680천원(집행률 93.1%)"""


def _texts(sections):
    return [str(s.get("content") or "") for s in sections]


def test_a_table_edit_goes_to_the_part_holding_that_table():
    wrong = revise.Plan(scope="parts", targets=[0], note="예산 표 수정")
    assert revise.table_target(wrong, NAMES, _texts(V1), BUDGET).targets == [2]
    assert revise.table_target(wrong, NAMES, _texts(V1), COMPLETION).targets == [2]
    guessed = revise.Plan(scope="parts", targets=[2], note="개선점 표로")
    assert revise.table_target(guessed, NAMES, _texts(V1), INTO_TABLE).targets == [4]
    # No table named, nothing moved.
    assert revise.table_target(wrong, NAMES, _texts(V1), "문장을 더 짧게").targets == [0]


def test_reshaping_a_table_does_not_rewrite_the_parts_that_restate_its_figures():
    for ask in (BUDGET, COMPLETION, INTO_TABLE, TITLE):
        assert not consistency.changes_content(ask), ask
    assert consistency.changes_content("예산 표의 강사비 집행을 11,000천원으로 고쳐 줘.")


def test_a_title_with_no_part_named_is_the_documents():
    assert revise.title_rename(TITLE, NAMES) == ("2026 하계 SW 캠프 운영 결과 보고", None)
    assert revise.without_title_clause(TITLE) == "맨 앞에 3줄 요약 넣고"
    assert revise.without_title_clause("제목을 「새 제목」으로 바꿔 줘.") == ""
    assert revise.title_rename("개요 절 제목을 「캠프 개요」로 바꿔 줘", NAMES) == ("캠프 개요", 0)
    assert revise.title_rename("이 장 제목을 「배경」으로", NAMES) == ("배경", -1)


def test_a_redrawn_table_under_an_item_is_not_restored_as_lost():
    before = V1[2]["content"]
    body = report._keeps_its_tables(BUDGET_BODY, before, BUDGET)
    tables = [m.group(0) for m in revised_tables.TABLE.finditer(body)]
    # the budget table once (the redrawn one); the course and satisfaction tables kept
    assert sum("강사비" in t for t in tables) == 1
    assert sum("파이썬 기초 | 30" in t for t in tables) == 1
    assert sum("| 4.6 |" in t for t in tables) == 1


def test_the_revised_document_keeps_one_copy_of_each_table_and_no_tokens():
    rank = {2: 2, 3: 1}
    tidied = report.caption_figures_and_tables(revised_tables.tidy(V4, rank))
    whole = "\n".join(_texts(tidied))
    tables = [m.group(0) for m in revised_tables.TABLE.finditer(whole)]
    for row in ("| 강사비 |", "| 파이썬 기초 | 30", "| 일시 |"):
        assert sum(row in t for t in tables) == 1, row
    # the fuller course table (with 수료율) is the one kept
    assert any("수료율" in t and "파이썬 기초" in t for t in tables)
    assert "〔" not in whole
    lines = whole.split("\n")
    for i, line in enumerate(lines):
        if revised_tables.CAPTION.match(line) and not line.lstrip().startswith("|"):
            nxt = next(x for x in lines[i + 1:] if x.strip())
            assert nxt.lstrip().startswith("|"), line


def test_an_asked_table_shape_missing_from_the_document_is_said():
    assert revise.missing_shapes(INTO_TABLE, _texts(V1)) == ["「문제-원인-조치」 표"]
    assert revise.missing_shapes(INTO_TABLE, _texts(V4)) == []
    assert revise.missing_shapes(COMPLETION, _texts(V1)) == []
    assert revise.missing_shapes(BUDGET, _texts(V1[:1])) == ["「집행률」 열", "합계 행"]
    assert revise.missing_shapes(BUDGET, _texts(V1)) == []


def _bundle(instruction, sections):
    return revise_stages.bundle_for(
        request="「2026 하계 SW 캠프」 결과보고서 써 줘. 표 위주로.", instruction=instruction,
        model={"id": "m"}, planner=None, api_key="k", is_deck=False,
        title="2026 하계 SW 캠프 결과", data={}, parts=json.loads(json.dumps(sections)),
        material=[], plan=None, format_id="", usage={"inputTokens": 0, "outputTokens": 0})


async def _run(bundle):
    events = []
    for stage in (revise_stages.Route(), revise_stages.Widen(), revise_stages.Rewrite()):
        async for event in stage.run(bundle, Context()):
            events.append(event)
    return events


def test_the_budget_request_edits_the_budget_table_in_its_own_part(monkeypatch):
    async def plan(**_kwargs):
        return revise.Plan(scope="parts", targets=[0], note="예산 표에 집행률 열과 합계 행")

    async def complete(model, messages, api_key, max_tokens, **_kwargs):
        return BUDGET_BODY, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise, "plan", plan)
    monkeypatch.setattr(report, "_complete", complete)
    bundle = _bundle(BUDGET, V1)
    asyncio.run(_run(bundle))
    sections = bundle.document.sections
    assert bundle.plan.revision.targets == [2] and bundle.plan.widened == []
    assert sections[0]["content"] == V1[0]["content"]
    whole = "\n".join(_texts(sections))
    tables = [m.group(0) for m in revised_tables.TABLE.finditer(whole)]
    assert sum("| 강사비 |" in t for t in tables) == 1
    assert any("집행률" in t and "합계" in t for t in tables)
    assert bundle.document.missed == []


def test_the_title_request_renames_the_document_not_a_part(monkeypatch):
    async def plan(**kwargs):
        assert "제목" not in kwargs["message"]
        return revise.Plan(scope="parts", targets=[0], note="맨 앞에 3줄 요약")

    async def complete(model, messages, api_key, max_tokens, **_kwargs):
        assert "운영 결과 보고" not in messages[-1]["content"].split("사용자가 입력한 문장")[-1]
        return "- 3줄 요약\n  - 76명 참가·71명 수료\n\n" + V1[0]["content"], {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise, "plan", plan)
    monkeypatch.setattr(report, "_complete", complete)
    bundle = _bundle(TITLE, V1)
    asyncio.run(_run(bundle))
    assert bundle.plan.title == "2026 하계 SW 캠프 운영 결과 보고"
    assert [s["heading"] for s in bundle.document.sections] == NAMES
    title_only = _bundle("제목을 「2026 하계 SW 캠프 운영 결과 보고」로 바꿔 줘.", V1)
    asyncio.run(_run(title_only))
    assert title_only.plan.title == "2026 하계 SW 캠프 운영 결과 보고"
    assert title_only.document.changed == 1
    assert title_only.document.sections == V1
