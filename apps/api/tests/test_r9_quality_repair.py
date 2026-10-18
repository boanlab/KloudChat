"""Quality-read findings mended or marked, never only listed."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.pipeline import report_stages
from app.pipeline.bundle import Bundle, Request
from app.pipeline.stage import Context
from app.services import deck, model_tier, quality_gate, report, table_integrity, table_mend, verify

L27, LFL = "local/qwen3.8-27b", "local/qwen3.8-flash"
STRONG_EXT = "deepseek/deepseek-v4-pro-0813"
GROUPS = pytest.mark.parametrize("writer", [LFL, STRONG_EXT])

#: a minutes 「할 일」 section as written
SHIPPED_ACTIONS = """표: 할 일·담당·기한
할 일 | 담당 | 기한 | 비고
---|---|---|---

**표 1. 할 일**

| 2학기 AI 활용 교수법 워크숍 모바일 화면 검수 체크리스트 작성 후 회의 공유 \
| 김도윤(대리) | 2026-10-15(목) | 「다음 회의 때」 |
|---|---|---|---|
| LMS 학생 테스트 그룹 모집(학생회 통해, 10명 목표) | 최하늘(주임) | 미정 | 회의에서 정하지 않음 |

미제출 원고 3편 재요청(교수님들께) | 최하늘(주임) | 2026-10-16(금) |
11월 예산 집행계획 본인이 제출(각 담당 사업) | 미정 | 2026-10-23(금) | 회의에서 지정하지 않음

※ 2번·4번의 기한 또는 담당자가 회의에서 정해지지 않아 확인이 필요합니다."""

#: the draft shape that made it: a header and rule with no leading pipe, a row ending in 「|」
DRAFTED_ACTIONS = """표: 할 일·담당·기한
할 일 | 담당 | 기한 | 비고
---|---|---|---
모바일 화면 검수 체크리스트 작성 후 회의 공유 | 김도윤(대리) | 2026-10-15(목) | 다음 회의 때
LMS 학생 테스트 그룹 모집(학생회 통해, 10명 목표) | 최하늘(주임) | 미정 | 회의에서 정하지 않음
미제출 원고 3편 재요청(교수님들께) | 최하늘(주임) | 2026-10-16(금) |
11월 예산 집행계획 본인이 제출(각 담당 사업) | 미정 | 2026-10-23(금) | 회의에서 지정하지 않음"""


def _tables(body: str) -> list[tuple[str, list[str]]]:
    lines = body.split("\n")
    out = []
    for start, end in table_integrity._tables(lines):
        out.append((lines[start], lines[start + 2:end]))
    return out


def _codes(sections: list[dict]) -> list[str]:
    found = [x["code"] for x in quality_gate.report_findings(sections)]
    for sec in sections:
        found += ["table_rows"] * len(table_integrity.problems(sec["content"], ""))
    return found


# ── tables rebuilt by code ──────────────────────────────────────────────


def test_a_drafted_bare_table_is_never_split_into_a_header_and_loose_lines():
    # before: its first two rows became a table of their own, the last two stayed as text
    body = report.repair_pipe_tables(DRAFTED_ACTIONS)
    (header, rows), = _tables(body)
    assert header == "| 할 일 | 담당 | 기한 | 비고 |"
    assert len(rows) == 4 and all(r.startswith("| ") and r.count("|") == 5 for r in rows)
    assert "| 미제출 원고 3편 재요청(교수님들께) | 최하늘(주임) | 2026-10-16(금) |  |" in rows
    assert "\n---|" not in body


def test_the_shipped_action_table_is_put_back_under_its_header():
    body = table_mend.rebuild(SHIPPED_ACTIONS)
    (header, rows), = _tables(body)
    assert header == "| 할 일 | 담당 | 기한 | 비고 |"
    assert [r.split("|")[1].strip()[:6] for r in rows] == ["2학기 AI", "LMS 학생", "미제출 원고",
                                                           "11월 예산"]
    # the caption stays above it, the label it made redundant goes
    assert body.startswith("**표 1. 할 일**\n\n| 할 일 |")
    assert "표: 할 일" not in body
    assert body.rstrip().endswith("확인이 필요합니다.")


def test_whole_tables_stay_as_written():
    two = ("**표 1. 예산**\n\n| 항목 | 금액 |\n|---|---|\n| 시상금 | 2,000 |\n\n"
           "**표 2. 만족도**\n\n| 항목 | 점수 |\n|---|---|\n| 전반 | 4.5 |")
    assert table_mend.rebuild(two) == two
    # a second captioned table of the same width is a table of its own
    stacked = ("| 항목 | 금액 |\n|---|---|\n| 시상금 | 2,000 |\n\n**표 2. 기타**\n\n"
               "| 항목 | 금액 |\n|---|---|\n| 식음료 | 870 |")
    assert table_mend.rebuild(stacked) == stacked
    fenced = "```\na | b | c\n---|---|---\n1 | 2 | 3\n```"
    assert table_mend.rebuild(fenced) == fenced


def test_pipe_lines_no_table_takes_are_written_as_items():
    body = "자료 정리:\n\n미제출 원고 3편 재요청 | 최하늘(주임) | 2026-10-16(금)\n\n끝."
    assert "- 미제출 원고 3편 재요청 · 최하늘(주임) · 2026-10-16(금)" in table_mend.flatten(body)


# ── the quality read mends what it finds, then reads again ───────────────


def test_the_minutes_quality_findings_are_mended_and_read_again():
    sections = [{"heading": "할 일", "content": SHIPPED_ACTIONS}]
    before = _codes(sections)
    assert {"stray_caption", "pipe_rows_not_a_table", "unmentioned"} <= set(before)
    mended = report_stages.mend_shapes(sections, [{"code": c} for c in before])
    assert _codes(mended) == []
    assert mended[0]["content"].startswith("할 일은 표 1과 같습니다.\n\n**표 1. 할 일**")


#: tables under a heading and after a list item, none named in the text
RESULT_REPORT = [
    {"heading": "개요", "content": "**표 1. 행사 기본 현황**\n\n| 구분 | 내용 |\n|---|---|\n"
                                   "| 행사명 | 2026 한빛 AI 해커톤 |\n| 주관 | SW중심대학사업단 |"},
    {"heading": "주요 결과·실적", "content": "- 참여 규모: 48개 팀 172명(선발)\n"
     "- 예산 집행: 총 4,215만 원\n\n**표 2. 예산 집행 내역(단위: 만 원)**\n\n"
     "| 항목 | 집행액 | 비율 |\n|---|---|---|\n"
     "| 시상금 | 2,000 | 47.4% |\n| 합계 | 4,215 | 100% |"},
]


def test_a_table_the_text_never_names_is_pointed_at():
    findings = quality_gate.report_findings(RESULT_REPORT)
    assert [x["where"] for x in findings if x["code"] == "unmentioned"] == ["표 1", "표 2"]
    mended = report_stages.mend_shapes(RESULT_REPORT, findings)
    assert _codes(mended) == []
    assert mended[0]["content"].startswith("행사 기본 현황은 표 1과 같습니다.")
    # a list item right above the table carries the pointer
    assert "- 예산 집행: 총 4,215만 원(표 2)\n" in mended[1]["content"]


def test_the_pointer_s_particles_follow_the_number():
    assert table_mend.cite("**표 2. 할 일 목록**\n\n| a | b |\n|---|---|\n| 1 | 2 |", "표 2") \
        .startswith("할 일 목록은 표 2와 같습니다.")
    assert table_mend.cite("**표 3. 다음 회의 일정**\n\n| a | b |\n|---|---|\n| 1 | 2 |", "표 3") \
        .startswith("다음 회의 일정은 표 3과 같습니다.")
    assert table_mend.cite("본문입니다.\n\n![그림 1. 구조](x.png)", "그림 1") \
        .startswith("본문입니다(그림 1).")


def test_a_header_with_no_rows_is_marked_where_it_stands():
    sections = [{"heading": "할 일",
                 "content": "**표 1. 할 일**\n\n| 할 일 | 담당 |\n|---|---|\n\n끝(표 1)."}]
    mended = report_stages.mend_shapes(sections, [{"code": "table_rows"}])
    assert report_stages._EMPTY_TABLE_MARK in mended[0]["content"]
    # once
    again = report_stages.mend_shapes(mended, [{"code": "table_rows"}])
    assert again[0]["content"].count(report_stages._EMPTY_TABLE_MARK) == 1


def test_this_meeting_and_the_next_are_no_value_conflict():
    # 「일시」 in the meeting's table and in the next meeting's
    sections = [
        {"heading": "회의 개요", "content": "**표 1. 회의 개요**\n\n| 구분 | 내용 |\n|---|---|\n"
                                        "| 일시 | 2026-10-08(목) 14:00 |\n\n표 1과 같다."},
        {"heading": "다음 회의 일정", "content": "**표 2. 다음 회의 일정**\n\n"
                                          "| 구분 | 내용 |\n|---|---|\n"
                                          "| 일시 | 2026-10-15(목) 14:00 |\n\n표 2와 같다."},
    ]
    assert "table_value_conflict" not in _codes(sections)
    # two figures for one item still are
    sections[1]["content"] = sections[1]["content"].replace("일시 | 2026-10-15(목) 14:00",
                                                            "일시 | 60명")
    sections[0]["content"] = sections[0]["content"].replace("일시 | 2026-10-08(목) 14:00",
                                                            "일시 | 40명")
    assert "table_value_conflict" in _codes(sections)


def test_the_quality_step_counts_what_it_mended(monkeypatch):
    bundle = Bundle(request=Request(text="회의록 작성해 줘", model=LFL, api_key="k"))
    bundle.document.sections = [{"heading": "할 일", "content": SHIPPED_ACTIONS}]
    events = asyncio.run(_collect(report_stages.Quality().run(bundle, Context())))
    step = next(e for e in events if e.get("id") == "quality")
    assert step["status"] == "done" and "고침 3곳" in step["label"], step
    shipped = next(e for e in events if e["type"] == "report")["sections"][0]["content"]
    (header, rows), = _tables(shipped)
    assert header.startswith("| 할 일 |") and len(rows) == 4


async def _collect(gen):
    return [e async for e in gen]


# ── a number tied to another item, confirmed by code, is repaired once, then marked ──

QUOTES = """[견적 2] 다솜시스템
- 단가 1,390,000원(부가세 포함) × 30대
- 납기 2주, 기존 기기 수거는 대당 10,000원(부가세 포함)"""
BOUND_SENTENCE = ("다만 데이터 삭제 증빙 제공 여부가 자료상 명시되지 않아 조건 충족 확인이 "
                  "불가합니다. 또한 납기 14달은 지급 기한 1달을 초과하여 탈락 사유에 해당합니다.")
BOUND_WHY = ("「14달」은 자료에서 「납기 2주 (자료의 다솜시스템 납기 조건)」의 값일 수 있다"
             "(자료상 납기 2주). 확인 필요.")


THIRTY_WHY = "「30대」은 자료에서 「수거 대수」의 값일 수 있다. 확인 필요."


def test_code_confirms_a_bound_number_only_when_no_evidence_has_it():
    finding = verify.Finding(0, BOUND_SENTENCE, "number_binding", BOUND_WHY, auto=False)
    assert report_stages.bound_token(finding, QUOTES) == "14달"
    # the judge giving the bare number: the unit is read off the sentence
    bare = verify.Finding(0, BOUND_SENTENCE, "number_binding",
                          BOUND_WHY.replace("「14달」", "「14」"), auto=False)
    assert report_stages.bound_token(bare, QUOTES) == "14달"
    # a value the material has stays the judge's reading: shown, never acted on
    thirty = verify.Finding(0, "단가 1,390,000원 × 30대입니다.", "number_binding",
                            THIRTY_WHY, auto=False)
    assert report_stages.bound_token(thirty, QUOTES) == ""


@GROUPS
def test_a_confirmed_bound_number_is_fixed_once_then_marked(writer, monkeypatch):
    finding = verify.Finding(0, BOUND_SENTENCE, "number_binding", BOUND_WHY, rank=2, auto=False)

    async def found(doc, checks, **kwargs):
        return doc.sections, [finding], {}

    async def repair(doc, findings, **kwargs):
        repair.model = kwargs["model"]
        repair.items = findings
        return doc.sections, findings, {}  # the repair could not fix it

    monkeypatch.setattr(report_stages.verify, "verify_and_repair", found)
    monkeypatch.setattr(report_stages.verify, "repair", repair)
    bundle = Bundle(request=Request(text=f"비교 검토 보고서 써 줘.\n{QUOTES}", model=writer,
                                    api_key="k", outline_model="flash"))
    bundle.document.sections = [{"heading": "대안 비교", "content": BOUND_SENTENCE}]
    asyncio.run(report_stages.Verify().apply(bundle, Context()))
    # the writer repairs its own sentence when strong; the planner otherwise
    assert repair.model == (STRONG_EXT if model_tier.strong(writer) else "flash")
    item, = repair.items
    assert item.auto and item.still("납기 14 달은") and not item.still("납기 2주는")
    assert "납기 2주" in item.evidence
    assert "탈락 사유에 해당합니다 (확인 필요)." in bundle.document.sections[0]["content"]


def test_an_unconfirmed_bound_number_is_never_rewritten(monkeypatch):
    finding = verify.Finding(0, "단가 1,390,000원 × 30대입니다.", "number_binding",
                             THIRTY_WHY, auto=False)

    async def found(doc, checks, **kwargs):
        return doc.sections, [finding], {}

    async def repair(doc, findings, **kwargs):
        raise AssertionError("no rewrite on a reading code cannot confirm")

    monkeypatch.setattr(report_stages.verify, "verify_and_repair", found)
    monkeypatch.setattr(report_stages.verify, "repair", repair)
    bundle = Bundle(request=Request(text=f"보고서\n{QUOTES}", model=LFL, api_key="k"))
    bundle.document.sections = [{"heading": "비용", "content": "단가 1,390,000원 × 30대입니다."}]
    asyncio.run(report_stages.Verify().apply(bundle, Context()))
    assert bundle.document.sections[0]["content"] == "단가 1,390,000원 × 30대입니다."


# ── a deck's comparison table without values ─────────────────────────────


def test_a_header_kept_apart_from_its_rows_is_read_as_one_table():
    rows = deck._rows_any({"header": ["알고리즘", "평균 대기", "평균 반환"],
                           "rows": [["FCFS", "4.75", "8.75"], ["SJF", "3.5", "7.5"]]}, "")
    assert rows[0] == ["알고리즘", "평균 대기", "평균 반환"] and len(rows) == 3


def test_the_deck_read_finds_a_table_with_no_values():
    empty = {"layout": "table", "title": "네 알고리즘 비교",
             "rows": [["알고리즘", "평균 대기"], ["FCFS", ""], ["SJF", ""]]}
    assert "empty_table" in [f["code"] for f in quality_gate.deck_findings([empty])]
    full = {**empty, "rows": [["알고리즘", "평균 대기"], ["FCFS", "4.75"]]}
    assert quality_gate.deck_findings([full]) == []


LECTURE = ("운영체제 50분 강의용 슬라이드 만들어 줘. FCFS, SJF, SRTF, RR 비교. 마지막에 비교 표.\n"
           "P1 도착 0 실행 7, P2 도착 2 실행 4, P3 도착 4 실행 1, P4 도착 5 실행 4")
#: the comparison slide came back as its names only
NAMES_ONLY = {"title": "네 알고리즘 비교", "layout": "table",
              "columns": ["알고리즘", "FCFS", "SJF", "SRTF", "RR(q=2)"],
              "notes": "네 알고리즘을 비교합니다. 평균 대기시간이 가장 짧은 것은 SRTF입니다."}
TABLE = {"rows": [["알고리즘", "평균 대기", "평균 반환"], ["FCFS", "4.75", "8.75"],
                  ["SRTF", "3", "7"]],
         "notes": "SRTF의 평균 대기가 가장 짧습니다. FCFS는 P1 뒤에서 오래 기다립니다."}


async def _deck(monkeypatch, rewritten: dict) -> tuple[list[dict], dict]:
    draft = {"slides": [
        {"title": "CPU 스케줄링", "layout": "title"},
        {"title": "FCFS 동작", "layout": "bullets", "bullets": ["도착 순서대로 실행"],
         "notes": "먼저 온 프로세스가 먼저 실행됩니다. P1이 0부터 7까지 실행됩니다."},
        NAMES_ONLY,
    ]}

    async def complete(model, messages, api_key, max_tokens=0, **kwargs):
        text = messages[-1]["content"]
        if "came out without value rows" in text:
            complete.asked += 1
            return json.dumps(rewritten, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}
        return json.dumps(draft, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    complete.asked = 0

    async def no_figures(**kwargs):
        return [], {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(deck, "_complete", complete)
    monkeypatch.setattr(deck.diagrams, "plan", no_figures)
    events = [e async for e in deck._write_slides(
        plan=[{"title": s["title"], "layout": s["layout"]} for s in draft["slides"]],
        title="CPU 스케줄링", subtitle="", accent="", request=LECTURE, model="m", api_key="k",
        trusted_context=[], untrusted_context=[], usage={"inputTokens": 0, "outputTokens": 0})]
    slides = next(e["slides"] for e in events if e["type"] == "deck")
    step = next(e for e in events if e.get("id") == "quality")
    assert complete.asked == 1  # once, never a loop
    return slides, step


@pytest.mark.asyncio
async def test_a_comparison_slide_with_no_values_is_written_once_more_as_a_table(monkeypatch):
    slides, step = await _deck(monkeypatch, TABLE)
    last = next(s for s in slides if s["title"] == "네 알고리즘 비교")
    assert last["layout"] == "table" and deck.table_values(last)
    assert "bullets" not in last
    assert step["status"] == "done", step


@pytest.mark.asyncio
async def test_a_comparison_slide_still_without_values_is_reported(monkeypatch):
    slides, step = await _deck(monkeypatch, {"rows": [["알고리즘", "FCFS", "SJF"]],
                                             "bullets": ["알고리즘", "FCFS"]})
    last = next(s for s in slides if s["title"] == "네 알고리즘 비교")
    # never a table with names and no values: the slide as it was, and the read says so
    assert last["layout"] != "table"
    assert step["status"] == "error" and "품질 점검 1건" in step["label"], step
