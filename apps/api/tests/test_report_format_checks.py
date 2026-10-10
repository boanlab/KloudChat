"""The lineup formats' pre-pass and checks: resolved deadlines kept (W3), conflicting values kept
side by side (W4), and each format's code checks found, repaired once and checked again (W2)."""

from __future__ import annotations

import datetime as dt
import json

from app.services import checks, key_figures, process_settings, report, report_checks, verify

MINUTES_1 = (
    "[메모 — 컴퓨터공학과 교육과정위원회, 2026-10-12(월) 15:00~16:10, 공학관 302호]\n"
    "참석: 김교수(위원장), 박교수, 이교수, 학생대표 최○○, 조교 정다은 / 불참: 윤교수(출장)\n"
    "- 신설 과목 강의계획서 초안 → 이교수, 다음 주 금요일까지\n"
    "- 학생 수요 설문 → 최○○, 이번 주 금요일까지 회수\n"
    "- ‘자료구조’ 선수과목 지정 → 박교수 “검토해 볼게요”\n"
    "- 교무처 제출 서류 취합 → 정조교 (교무처 마감 11/2)\n"
)

ACTIONS = (
    "| 할 일 | 담당 | 기한 | 비고 |\n|---|---|---|---|\n"
    "| 신설 과목 강의계획서 초안 | 이교수 | 2026-10-23(금) | 다음 주 금요일 |\n"
    "| 학생 수요 설문 회수 | 최○○ | 2026-10-16(금) | 이번 주 금요일 |\n"
    "| 교무처 제출 서류 취합 | 정조교 | 2026-11-02(월) | 교무처 마감 |\n"
    "| 자료구조 선수과목 검토 | 박교수 | 2026-10-30(금) | |\n"
)


def _bound(material: str = MINUTES_1) -> list[dict]:
    rows = report_checks.skill_scripts.dates().scan(material)
    return [r for r in rows if r["status"] in ("ok", "repeat")]


# ── W3: a resolved deadline is kept because of its source line ─────────


def test_a_resolved_deadline_survives_the_owner_date_guard():
    out = report._unsourced_owner_dates(ACTIONS, MINUTES_1, _bound()).splitlines()
    assert "2026-10-23(금)" in out[2] and "2026-10-16(금)" in out[3]
    assert "2026-11-02(월)" in out[4]
    # No line gives 박교수's item a date: the invented one goes.
    assert "| [기한] |" in out[5] and "2026-10-30" not in out[5]


def test_without_the_resolution_the_old_guard_still_blanks_it():
    # The repro from the review: the table form loses 「다음 주 금요일」's date.
    out = report._unsourced_owner_dates(ACTIONS, MINUTES_1).splitlines()
    assert "| [기한] |" in out[2]


def test_a_date_on_another_line_does_not_bind_a_row():
    material = MINUTES_1 + "- 축제 일정 공지 → 김교수, 10/30 게시\n"
    table = ("| 할 일 | 담당 | 기한 |\n|---|---|---|\n"
             "| 강의계획서 초안 | 이교수 | 2026-10-30(금) |\n")
    bound = _bound(material)
    assert "| [기한] |" in report._unsourced_owner_dates(table, material, bound)
    # The old reading accepted any 10/30 anywhere in the material.
    assert "2026-10-30" in report._unsourced_owner_dates(table, material)


def test_the_date_block_names_the_phrase_its_line_and_the_base():
    pre = report_checks.Prepass(format_id="minutes")
    rows = report_checks.skill_scripts.dates().scan(MINUTES_1)
    block = report_checks._date_block(rows, "2026-10-12")
    assert "「다음 주 금요일」 → 2026-10-23(금)" in block
    assert "강의계획서 초안 → 이교수" in block and "기준 2026-10-12" in block
    assert pre.bound_dates == []


async def test_minutes_written_as_a_table_keep_the_resolved_deadline(monkeypatch):
    draft = "## 회의 개요\n교육과정위원회.\n## 실행 항목\n**표 1. 실행 항목**\n\n" + ACTIONS

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return draft, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request="회의록으로 정리해 줘", model="m", api_key="k", web_search=False,
        untrusted_context=[MINUTES_1], template_id="doc-minutes",
        approved_plan={"title": "교육과정위원회 회의록", "sections": ["회의 개요", "실행 항목"]},
    )]
    sections = next(e["sections"] for e in events if e["type"] == "report")
    assert "2026-10-23(금)" in sections[1]["content"]
    steps = {e["id"]: e for e in events if e["type"] == "step"}
    assert "date-calc" in steps["prepass"]["detail"]
    assert "format_checks" in steps



async def test_a_memo_pasted_under_the_request_gets_the_same_pre_pass(monkeypatch):
    # Short memo lines read as instruction; the pre-pass still reads them as material.
    draft = "## 회의 개요\n교육과정위원회.\n## 실행 항목\n**표 1. 실행 항목**\n\n" + ACTIONS

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return draft, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request="어제 위원회 메모야. 정식 회의록으로 만들어 줘.\n\n" + MINUTES_1,
        model="m", api_key="k", web_search=False, template_id="doc-minutes",
        approved_plan={"title": "교육과정위원회 회의록", "sections": ["회의 개요", "실행 항목"]},
    )]
    sections = next(e["sections"] for e in events if e["type"] == "report")
    assert "2026-10-23(금)" in sections[1]["content"]
    steps = {e["id"]: e for e in events if e["type"] == "step"}
    assert "date-calc" in steps["prepass"]["detail"]


def test_a_one_paragraph_request_has_no_pasted_memo():
    assert report._pasted_without_marker("회의록으로 정리해 줘. 작성자는 나로.") == ""
    assert report._pasted_without_marker("정리해 줘.\n\n짧은 메모") == ""

# ── W4: two values for one thing are kept, never merged ────────────────

BACKUP = (
    "세이프볼트 제품 소개서: 1TB 복구 2시간.\n"
    "독립 측정(2026-09): 세이프볼트 1TB 복구 4.5시간.\n"
    "백업원 요금 2025년 TB당 46,000원, 2026년 공지 TB당 52,000원.\n"
)


def test_the_model_s_conflict_list_is_kept_with_where_and_when():
    reply = json.dumps([
        {"name": "1TB 복구 시간", "values": [
            {"value": "2시간", "where": "제품 소개서"},
            {"value": "4.5시간", "where": "독립 측정", "when": "2026-09"}]},
        {"name": "백업원 TB당 요금", "values": [{"value": "46,000원", "when": "2025"},
                                           {"value": "52,000원", "when": "2026"}]},
        {"name": "없는 값", "values": [{"value": "9시간"}, {"value": "2시간"}]},
    ], ensure_ascii=False)
    figures, conflicts = key_figures.parse_all(reply, BACKUP)
    assert figures == []
    assert [c.name for c in conflicts] == ["1TB 복구 시간", "백업원 TB당 요금"]
    first = conflicts[0].values
    assert [v["value"] for v in first] == ["2시간", "4.5시간"]
    assert "소개서" in first[0]["line"] and first[1]["when"] == "2026-09"
    block = key_figures.conflict_block(
        conflicts, process_settings.for_format("comparison").conflict_rule)
    assert "자료와 충돌" in block and "2시간(제품 소개서)" in block and "4.5시간" in block


def test_a_figure_the_material_contests_becomes_a_conflict_not_a_drop():
    material = "예산은 600만 원으로 정함.\n메일: 예산은 520만 원으로 조정합니다.\n"
    reply = json.dumps([{"name": "예산", "value": "600만 원"}], ensure_ascii=False)
    figures, conflicts = key_figures.parse_all(reply, material)
    assert figures == []
    assert conflicts and {v["value"].replace(" ", "") for v in conflicts[0].values} >= {"600만원"}
    assert len(conflicts[0].values) == 2
    # The old entry point still drops it, for the deck.
    assert key_figures.parse(reply, material) == []


async def test_the_verify_stage_does_not_merge_a_conflict():
    sections = [{"heading": "자료와 충돌", "content": (
        "세이프볼트의 1TB 복구 시간은 2시간이다(제품 소개서).\n\n"
        "독립 측정에서 세이프볼트의 1TB 복구 시간은 4.5시간이다.")}]
    doc = verify.Doc(sections, BACKUP)
    plain, _ = await checks.metrics(doc)
    conflict = key_figures.Conflict("1TB 복구 시간", [{"value": "2시간"}, {"value": "4.5시간"}])
    kept, _ = await checks.metrics(verify.Doc(sections, BACKUP, conflicts=[conflict]))
    assert kept == []
    if plain:  # where the metric check reads it at all, it is the conflict that silences it
        assert all(f.code == "metric_conflict" for f in plain)


def test_the_comparison_amendment_asks_for_a_missing_value():
    conflict = key_figures.Conflict("1TB 복구 시간", [
        {"value": "2시간", "where": "제품 소개서", "when": "", "line": "소개서"},
        {"value": "4.5시간", "where": "독립 측정", "when": "2026-09", "line": "측정"}])
    sections = [{"heading": "결론과 권고", "content": "세이프볼트 권고."},
                {"heading": "자료와 충돌", "content": "세이프볼트 복구 2시간."}]
    pre = report_checks.Prepass(format_id="comparison")
    found = report_checks.amendments(pre, sections, BACKUP, "", [conflict])
    assert [a.code for a in found] == ["conflict_missing"] and found[0].section == 1
    assert "4.5시간" in found[0].instruction and "독립 측정" in found[0].instruction
    sections[1]["content"] += "\n독립 측정은 4.5시간(2026-09)."
    assert not found[0].missing(sections)


# ── client_proposal: the quote, the requirement IDs, the validity ─────

PROPOSAL = (
    "[RFP] 한빛물류 상담 요약 PoC. 예산 4,800만 원(VAT 별도). 견적 유효기간 30일.\n"
    "COR-001 상담 요약, COR-002 키워드 추출, COR-003 관리자 화면.\n"
    "투입: PM 1.0MM × 9,000,000원, AI 엔지니어 3.0MM × 8,000,000원, "
    "데이터 엔지니어 1.5MM × 7,000,000원, GPU 3개월 × 1,200,000원.\n"
)
QUOTE_REPLY = json.dumps({
    "vat_basis": "excl",
    "items": [{"name": "PM", "mm": 1.0, "monthly_price": 9000000},
              {"name": "AI 엔지니어", "mm": 3.0, "monthly_price": 8000000},
              {"name": "데이터 엔지니어", "mm": 1.5, "monthly_price": 7000000},
              {"name": "GPU", "qty": 3, "unit": "개월", "unit_price": 1200000},
              {"name": "지어낸 항목", "qty": 1, "unit_price": 5555555}],
    "budget": {"amount": 48000000, "vat_basis": "excl"},
}, ensure_ascii=False)


async def _proposal_pre() -> report_checks.Prepass:
    async def complete(model, messages, api_key, max_tokens):
        return QUOTE_REPLY, {"inputTokens": 3, "outputTokens": 2}

    usage: dict = {}
    pre = await report_checks.prepare("client_proposal", "고객 제안서 써 줘", PROPOSAL,
                                      complete=complete, model="m", api_key="k", usage=usage)
    # The quote is read twice and used when both readings agree.
    assert usage == {"inputTokens": 6, "outputTokens": 4}
    return pre


async def test_the_proposal_pre_pass_settles_the_quote():
    pre = await _proposal_pre()
    assert pre.quote["total"] == 51_810_000
    assert "지어낸 항목" in pre.runs[0]["summary"] or "지어낸 항목" in pre.runs[-1]["summary"]
    figures = {f.name: f.value for f in pre.figures}
    assert figures["견적 합계(VAT 포함)"] == "51,810,000원"
    block = key_figures.block(pre.figures)
    assert "견적 합계(VAT 포함): 51,810,000원" in block
    quote_block = next(b for b in pre.blocks if b.startswith("# 견적 확정 블록"))
    assert "금51,810,000원(금오천일백팔십일만원)" in quote_block and "여유 900,000원" in quote_block


async def test_a_wrong_quote_total_is_found_repaired_and_checked_again():
    pre = await _proposal_pre()
    table = ("| 항목 | 금액 |\n|---|---:|\n| 공급가액 | 47,100,000 |\n| 부가세 | 4,710,000 |\n"
             "| 총액 | 51,800,000 |\n")
    sections = [{"heading": "견적", "content": table}]
    doc = verify.Doc(sections, PROPOSAL)
    found, _ = await verify.find(doc, report_checks.checks(pre))
    assert [f.code for f in found] == ["quote_total"] and "51,810,000원" in found[0].why

    async def complete(model, messages, api_key, max_tokens):
        return json.dumps(["| 총액 | 51,810,000 |"]), {"inputTokens": 1, "outputTokens": 1}

    fixed, left, _ = await verify.verify_and_repair(
        doc, report_checks.checks(pre), complete=complete, model="m", api_key="k")
    assert left == [] and "51,810,000" in fixed[0]["content"]


async def test_missing_requirement_ids_and_validity_are_amended():
    pre = await _proposal_pre()
    sections = [{"heading": "요구사항 대응표", "content": (
        "| 요구 ID | 대응 |\n|---|---|\n| COR-001 | 요약 |\n| COR-002 | 추출 |\n")},
        {"heading": "견적", "content": "공급가액 47,100,000원."}]
    found = {a.code: a for a in report_checks.amendments(pre, sections, PROPOSAL, "", [])}
    assert set(found) == {"requirement_missing", "validity_missing"}
    assert "COR-003" in found["requirement_missing"].instruction
    assert found["requirement_missing"].section == 0
    assert found["validity_missing"].append == "- 견적 유효기간: 30일"
    sections[0]["content"] += "| COR-003 | 관리자 화면 |\n"
    assert not found["requirement_missing"].missing(sections)


# ── minutes rows ────────────────────────────────────────────────────────


async def test_minutes_rows_need_owner_due_reason_and_the_resolved_date():
    rows = report_checks.skill_scripts.dates().scan(MINUTES_1)
    table = ("| 할 일 | 담당 | 기한 | 비고 |\n|---|---|---|---|\n"
             "| 강의계획서 초안 | 이교수 | 2026-10-22(목) | |\n"
             "| 학생 수요 설문 | 최○○ | 이번 주 금요일까지 회수 | |\n"
             "| 선수과목 검토 | 박교수 | 미정 | |\n"
             "| 서류 취합 | 정조교 | 2026-11-02(월) | 교무처 마감 |\n")
    doc = verify.Doc([{"heading": "실행 항목", "content": table}], MINUTES_1)
    found, _ = await report_checks.minutes_rows(doc, rows)
    by_row = {f.sentence.split("|")[1].strip(): f for f in found}
    assert set(by_row) == {"강의계획서 초안", "학생 수요 설문", "선수과목 검토"}
    assert "2026-10-23(금)" in by_row["강의계획서 초안"].why
    assert "값만" in by_row["학생 수요 설문"].why
    assert "사유" in by_row["선수과목 검토"].why
    fixed = "| 강의계획서 초안 | 이교수 | 2026-10-23(금) | 다음 주 금요일 |"
    assert not by_row["강의계획서 초안"].still(fixed)


# ── comparison ──────────────────────────────────────────────────────────

LMS = {"criteria": [{"name": "비용", "weight": 50, "kind": "cost"},
                    {"name": "앱", "weight": 30, "kind": "yesno"},
                    {"name": "인사 연동", "weight": 20, "kind": "yesno"}],
       "options": [
           {"name": "런큐브", "values": {"비용": 18000000, "앱": "있음", "인사 연동": None}},
           {"name": "에듀노트", "values": {"비용": 17280000, "앱": "없음", "인사 연동": None}}]}


def test_scores_follow_s1_to_s4():
    scored = report_checks.scores(LMS)
    lc, en = scored["options"]["런큐브"], scored["options"]["에듀노트"]
    assert lc["parts"]["비용"] == 48.0 and en["parts"]["비용"] == 50.0
    assert (lc["total"], en["total"]) == (78.0, 50.0)
    assert (lc["renormalized"], en["renormalized"]) == (97.5, 62.5)
    assert scored["missing"] == ["인사 연동"] and scored["leaders"] == ["런큐브"]
    tie = report_checks.scores({"criteria": [{"name": "가격", "weight": 50, "kind": "cost"}],
                                "options": [{"name": "X", "values": {"가격": 100}},
                                            {"name": "Y", "values": {"가격": 100}}]})
    assert tie["tie"] and "동점" in report_checks._score_block(tie)


async def test_comparison_cells_blank_and_wrong_score():
    scored = report_checks.scores(LMS)
    table = ("| 기준 | 런큐브 | 에듀노트 |\n|---|---|---|\n| 비용 | 18,000,000원 | 17,280,000원 |\n"
             "| 인사 연동 | - | 자료 없음 |\n| 총점 | 78 | 55 |\n")
    doc = verify.Doc([{"heading": "비교표", "content": table}])
    found, _ = await report_checks.comparison_cells(doc, scored)
    assert sorted(f.code for f in found) == ["comparison_blank", "comparison_score"]
    score = next(f for f in found if f.code == "comparison_score")
    assert "에듀노트" in score.why and not score.still("| 총점 | 78 | 50 |")


# ── career history ──────────────────────────────────────────────────────

CAREER = (
    "- 브랜드웨이브(현 위드브랜드) 2019.04 ~ 2021.01 정규직\n"
    "- 계약직 2021.10 ~ 2022.03\n"
    "- 리브랩 2022.04 ~ 2026.09 정규직, 파트 리드\n"
    "- 프리랜서(리브랩 재직 중 병행) 2023.05 ~ 2023.11\n"
    "- 성과: 팔로워 1.2만 → 4.8만, CAC 18,000원 → 11,500원, D30 21% → 27%\n"
)


def test_career_periods_leave_side_work_out_of_the_total():
    span = report_checks.career_periods(CAREER, "마케팅 경력기술서", dt.date(2026, 10, 7))
    assert span["total_months"] == 82 and span["total_text"] == "6년 10개월"
    assert [p["counted"] for p in span["periods"]] == [True, True, True, False]
    block = report_checks._career_block(span)
    assert "총 경력(겹침 제외): 82개월 = 6년 10개월" in block and "8개월" in block
    vague = report_checks.career_periods("A사 19년 봄 ~ 2021.01\n" + CAREER, "",
                                         dt.date(2026, 10, 7))
    assert vague["vague"] and "ask for confirmation" in report_checks._career_block(vague)


async def test_career_numbers_and_total_are_checked():
    span = report_checks.career_periods(CAREER, "", dt.date(2026, 10, 7))
    sections = [{"heading": "경력 요약", "content": (
        "총 경력 7년 5개월.\n\nCAC를 36.1% 줄였다.\n\n잔반을 20% 줄였다.")}]
    found, _ = await report_checks.career_numbers(verify.Doc(sections, CAREER), span)
    assert {f.code for f in found} == {"career_total", "career_number"}
    strays = [f for f in found if f.code == "career_number"]
    assert any("잔반" in f.sentence and "20" in f.why for f in strays)
    # 36.1% is derived from 18,000 → 11,500 (36.11%), so it is not a stray.
    assert not any("36.1" in f.sentence for f in strays)
    total = next(f for f in found if f.code == "career_total")
    assert not total.still("총 경력 6년 10개월.") and total.still("총 경력 7년 5개월.")


def test_periods_are_written_one_way():
    sections = [{"heading": "경력", "content": (
        "브랜드웨이브 2019년 4월 – 2021년 1월\n리브랩 2022.4~2026.9\n한결소프트 2023.03~ 재직 중\n"
        "새봄테크 2020.07 ~ 2022.12.")}]
    out, changed = report_checks.tidy_periods(sections)
    assert out[0]["content"].splitlines() == [
        "브랜드웨이브 2019.04 ~ 2021.01", "리브랩 2022.04 ~ 2026.09", "한결소프트 2023.03 ~ 현재",
        "새봄테크 2020.07 ~ 2022.12."]
    assert changed == 3


# ── page limit and the unrelated reports ───────────────────────────────


def test_page_limits():
    gaejo = process_settings.for_format("gaejo")
    assert report_checks.page_limit(gaejo, "개조식으로 정리해 줘") == 1
    assert report_checks.page_limit(gaejo, "두 쪽 이내로") == 2
    assert report_checks.page_limit(process_settings.for_format("lab"), "실험 보고서") is None


async def test_a_long_gaejo_body_is_counted_and_condensed_once():
    long = "\n".join(f"- 항목 {i}: 세부 경위와 설명이 길게 이어지는 줄이다. 금액 {i * 1000}원."
                     for i in range(160))
    sections = [{"heading": "현황", "content": long},
                {"heading": "붙임", "content": "1. 세부 1부."}]
    pages = await report_checks.page_count("보고", sections, None)
    assert pages and pages > 1

    async def complete(model, messages, api_key, max_tokens):
        return "- 핵심만 남김 1000원\n※ 세부는 붙임", {"inputTokens": 1, "outputTokens": 1}

    condensed = await report_checks.condense(sections, pages=pages, limit=1, complete=complete,
                                             model="m", api_key="k")
    # The rewrite drops figures the body had, so it is not kept.
    assert condensed[0]["content"] == long
    assert condensed[1] == sections[1]


async def test_the_gaejo_page_count_also_reads_the_hwpx_through_the_attachment_line():
    short = [{"heading": "현황", "content": "- 짧은 항목 1건."},
             {"heading": "붙임", "content": "1. 세부 1부."}]
    assert await report_checks.hwpx_page_count("보고", short, None) == 1
    long = "\n".join(f"- 항목 {i}: 세부 경위와 설명이 길게 이어지는 줄이다." for i in range(160))
    over = [{"heading": "현황", "content": long}, *short[1:]]
    assert (await report_checks.hwpx_page_count("보고", over, None) or 0) > 1
    serif = await report_checks.hwpx_page_count("보고", over, None, {"font": "serif"})
    assert serif and serif > 1


def test_unrelated_formats_have_no_pre_pass_or_checks():
    assert not any(process_settings.for_format(f).prepass for f in ("lab", "term"))
    assert report_checks.checks(None) == []
    assert report_checks.checks(report_checks.Prepass(format_id="lab")) == []
    assert report_checks.amendments(None, [{"heading": "a", "content": "b"}], "", "", []) == []


async def test_an_unrelated_report_writes_as_before(monkeypatch):
    draft = "## 실험 목적\n저항 측정.\n## 결과\n| 담당 | 기한 |\n|---|---|\n| 김철수 | 9월 10일 |\n"
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        asked.append(json.dumps(messages, ensure_ascii=False))
        return draft, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request="실험 보고서 써 줘", model="m", api_key="k", web_search=False,
        untrusted_context=["담당 김철수, 9월 10일까지 측정."],
        approved_plan={"title": "저항 측정", "sections": ["실험 목적", "결과"]},
    )]
    ids = [e["id"] for e in events if e["type"] == "step"]
    assert "prepass" not in ids and "format_checks" not in ids
    assert not any("확정 날짜" in a or "자료끼리 다른 값" in a for a in asked)
    sections = next(e["sections"] for e in events if e["type"] == "report")
    assert "9월 10일" in sections[1]["content"]


# ── client_proposal: the schedule (lineup v1 client-proposal-3__r1) ───

KAFI_RFP = (
    "1. 사업명 : 대표 홈페이지 웹 접근성 개선 및 품질인증 취득 사업\n"
    "2. 사업기간 : 계약일로부터 5개월\n"
    "3. 소요예산 : 금 99,000,000원(부가가치세 포함)\n"
)
KAFI_SCHEDULE = (
    "표: 사업 추진 일정(안)\n\n| 단계 | 수행 기간 | 주요 업무 |\n| :--- | :--- | :--- |\n"
    "| 착수·현황 진단 | 2026. 12. | 전 페이지 접근성 진단 |\n"
    "| 품질인증 심사 대응 | 2027. 5. | 품질인증 심사 신청 |\n\n"
    "![사업 4단계 수행 흐름 및 고객사 협조 시점](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA"
    "nTp17tOnb25uzvbtZ1wivW//vmh+d40Y0reYR8d/M6/SdlLlB1P8hgE5eY5pv/)\n"
)


async def test_a_picture_in_the_schedule_is_not_a_schedule_line():
    """The run's only 「일정이 사업 기간을 넘음」 was the figure's base64 (「/M6/」),
    which no repair can mend."""
    rfp = report_checks.rfp_terms(KAFI_RFP, "")
    assert rfp["months"] == 5
    doc = verify.Doc([{"heading": "추진 일정", "content": KAFI_SCHEDULE}], KAFI_RFP)
    found, _ = await report_checks.proposal_schedule(doc, rfp)
    assert found == []


async def test_the_schedule_repair_is_told_the_period_and_checked_again():
    rfp = report_checks.rfp_terms(KAFI_RFP, "")
    over = KAFI_SCHEDULE + "\n- 6개월 차: 재진단 결과 보고\n"
    doc = verify.Doc([{"heading": "추진 일정", "content": over}], KAFI_RFP)
    found, _ = await report_checks.proposal_schedule(doc, rfp)
    assert [f.sentence for f in found] == ["- 6개월 차: 재진단 결과 보고"]
    assert "사업 기간 5개월" in found[0].why and "계약일로부터 5개월" in found[0].evidence

    async def complete(model, messages, api_key, max_tokens):
        assert "계약일로부터 5개월" in messages[0]["content"]
        return json.dumps(["- 5개월 차: 재진단 결과 보고"], ensure_ascii=False), {}

    async def schedule(doc):
        return await report_checks.proposal_schedule(doc, rfp)

    fixed, left, _ = await verify.verify_and_repair(
        doc, [schedule], complete=complete, model="m", api_key="k")
    assert left == [] and "5개월 차: 재진단" in fixed[0]["content"]


# ── 개조식: the body counted through the 붙임 list as exported (B3) ──────

_GAEJO_BLOCK = {"head": "memo", "label": "개조식 보고서", "lists": "gaejo", "format": "gaejo",
                "closing": True, "numbering": "korean",
                "fields": [["보고일", ""], ["보고자(부서)", ""], ["보고 대상", ""]]}


def _gaejo_sections(lines: int) -> list[dict]:
    body = "\n".join(f"- 항목 {i}: 세부 경위 설명 줄" for i in range(lines))
    return [{"heading": "현황", "content": body},
            {"heading": "붙임", "content": "1. 세부 자료 1부.\n2. 회의 자료 1부.\n\n"
                                           "| 가 | 나 |\n|---|---|\n| 1 | 2 |"}]


async def test_the_attachment_line_that_spills_counts_as_a_body_page():
    # The body alone fits one page; its 붙임 lines and 「끝.」 fall to page 2, as the export has it.
    sections = _gaejo_sections(32)
    assert await report_checks.page_count("보고", sections, _GAEJO_BLOCK) == 1
    assert await report_checks.body_pages("보고", sections, _GAEJO_BLOCK) == (2, 2)


async def test_a_table_attached_in_full_is_not_a_body_page():
    # The moved table goes on a page of its own after 「끝.」 and is left out of the count.
    assert await report_checks.body_pages("보고", _gaejo_sections(10), _GAEJO_BLOCK) == (1, 1)


# ── lead times: 「발주 후 8주」 from the material's own date (B2) ─────────

AV_PACK = (
    "# 자료 1 — 교육청 공문 요지(2026. 10. 1.)\n"
    "- 교부액: 46,000,000원(부가가치세 포함 집행 기준)\n"
    "- 집행 기한: 설치·검수 완료 2026. 12. 18.(금)까지, 미집행분 반납\n"
    "# 자료 2 — 내부 검토 메모(교무팀)\n"
    "- 발주(계약) 예정일: 2026. 10. 19.(월)\n"
    "# 자료 3 — 3사 견적 요약(업체 제출 그대로)\n"
    "| 업체 | 견적 금액 | 부가세 | 납기 | 비고 |\n|---|---|---|---|---|\n"
    "| A사(에이브이원) | 45,650,000원 | 포함 | 발주 후 8주 | 무상 유지보수 2년 |\n"
    "| B사(사운드큐브) | 41,800,000원 | 별도 | 발주 후 10주 | 무상 유지보수 3년 |\n"
    "| C사(스마트클래스) | 44,000,000원 | 별도 | 발주 후 7주 | 무상 유지보수 2년 |\n"
)


def test_lead_times_count_from_the_order_date_and_meet_the_deadline():
    lead = report_checks.skill_scripts.dates().lead_times(AV_PACK)
    assert lead["deadline"] == "2026-12-18"
    got = {r["label"][:2]: (r["date"], r["within"]) for r in lead["items"]}
    assert got == {"A사": ("2026-12-14", True), "B사": ("2026-12-28", False),
                   "C사": ("2026-12-07", True)}


def test_a_lead_time_without_its_anchor_date_is_not_guessed():
    lead_times = report_checks.skill_scripts.dates().lead_times
    lead = lead_times("- 납품: 착수 후 2주\n- 계약일 2026-11-02")
    assert lead["items"][0]["status"] == "unresolved"
    lead = lead_times("계약일로부터 30일 이내 납품\n계약일: 2026-11-02")
    assert lead["items"][0]["date"] == "2026-12-02" and lead["items"][0]["label"] == "납품"


async def _gaejo_pre() -> report_checks.Prepass:
    async def complete(*_args, **_kwargs):
        raise RuntimeError("no model in this test")

    return await report_checks.prepare("gaejo", "장비 구매 검토 보고서", AV_PACK,
                                       complete=complete, model="m", api_key="k")


async def test_the_gaejo_pre_pass_dates_lead_times_with_no_meeting_date():
    pre = await _gaejo_pre()
    summary = next(r["summary"] for r in pre.runs if r["skill"] == "date-calc")
    assert "기준일 없음" not in summary and "발주 2026-10-19" in summary and "초과 1개" in summary
    block = next(b for b in pre.blocks if "기준 날짜 + 기간" in b)
    assert "2026-12-14(월)" in block and "2026-12-28(월)" in block and "2026-12-07(월)" in block
    assert "기한 2026-12-18 초과(10일 늦음)" in block


async def test_a_wrong_lead_date_and_a_turned_verdict_are_found_and_cleared():
    pre = await _gaejo_pre()
    body = ("- 납기 기준: A사(8주) > B사(10주) > C사(7주)\n"
            "  - A사 납기(8주) 완료일: 2026. 12. 25. (기한 2026. 12. 18.초과)\n"
            "  - C사 납기(7주) 완료일: 2026. 12. 18. (기한 2026. 12. 18. 충족)\n"
            "  - B사 납기(10주) 완료일: 2026. 12. 28. (기한 2026. 12. 18. 초과)\n"
            "  - A사는 기한(2026. 12. 18.)에 근접하여 설치 지연 시 반납 위험 있음\n")
    doc = verify.Doc([{"heading": "문제점 및 검토", "content": body}], AV_PACK, "")
    found, _ = await report_checks.lead_time_dates(doc, pre.lead)
    assert [f.sentence[:3] for f in found] == ["- A", "- C"]
    assert "거꾸로" in found[0].why and "2026-12-14(월)" in found[0].why
    assert not found[0].still("- A사 납기(8주) 완료일: 2026. 12. 14. (기한 2026. 12. 18. 이내)")
    assert found[1].still("- C사 납기(7주) 완료일: 2026. 12. 18. (기한 내)")


async def test_a_vendor_column_with_the_wrong_date_is_found():
    pre = await _gaejo_pre()
    table = ("| 항목 | A사 | B사 | C사 |\n|---|---|---|---|\n"
             "| 완료 예정일 | 2026. 12. 14. | 2026. 12. 18. | 2026. 12. 7. |\n")
    doc = verify.Doc([{"heading": "붙임", "content": table}], AV_PACK, "")
    found, _ = await report_checks.lead_time_dates(doc, pre.lead)
    assert len(found) == 1 and "사운드큐브" in found[0].why
    assert not found[0].still("| 완료 예정일 | 2026. 12. 14. | 2026. 12. 28. | 2026. 12. 7. |")


async def test_lead_dates_the_document_leaves_out_are_added_by_code():
    pre = await _gaejo_pre()
    sections = [{"heading": "현황", "content": "- 3사 견적 확보"},
                {"heading": "문제점 및 검토", "content": "- 납기: A사 8주, B사 10주, C사 7주"}]
    added = [a for a in report_checks.amendments(pre, sections, AV_PACK, "", [])
             if a.code == "lead_date_missing"]
    assert added and added[0].section == 1
    assert "2026-12-28(기한 2026-12-18 초과(10일 늦음))" in added[0].append
    sections[1]["content"] += "\n" + added[0].append
    assert not added[0].missing(sections)


async def test_a_lead_date_in_a_due_column_is_kept_and_a_guessed_one_blanked():
    pre = await _gaejo_pre()
    table = ("| 업체 | 납기 | 완료일 |\n|---|---|---|\n| A사 | 8주 | 2026-12-14(월) |\n"
             "| B사 | 10주 | 2027-01-08(금) |\n")
    out = report._unsourced_owner_dates(table, AV_PACK, pre.bound_dates).splitlines()
    assert "2026-12-14(월)" in out[2] and "2027-01-08" not in out[3]
