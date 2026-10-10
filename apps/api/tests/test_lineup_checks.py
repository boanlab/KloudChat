"""The lineup checks the step 3/4 report left open: 개조식 shape, the proposal against its RFP,
minutes rows and columns, comparison must-haves and recommendation, and the judged (122B)
checks — each found, repaired once (or shown) and checked again."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services import cover_letter, process_settings, report_checks, verify

# ── 개조식 ───────────────────────────────────────────────────────────────

GAEJO_MEMO = (
    "시설과장님께 올릴 내용 정리입니다. 본관 엘리베이터 3대는 1998년에 설치되어 올해로 28년째 쓰고 "
    "있습니다. 승강기 안전관리법상 설치 후 25년이 지나면 정밀안전검사를 3년마다 받아야 합니다."
)


async def test_a_memo_pasted_as_is_breaks_the_gaejo_shape_and_is_repaired_once():
    pre = report_checks.Prepass(format_id="gaejo")
    sections = [{"heading": "현황", "content": GAEJO_MEMO + "\n- 고장 신고 2025년 14건\n"
                 "- 3호기는 조건부 합격을 받았습니다."}]
    doc = verify.Doc(sections, GAEJO_MEMO)
    found, _ = await verify.find(doc, report_checks.checks(pre))
    assert sorted(f.code for f in found) == ["gaejo_ending", "gaejo_prose"]
    added = {a.code for a in report_checks.amendments(pre, sections, "", "", [])}
    assert added == {"summary_missing"}

    async def complete(model, messages, api_key, max_tokens):
        # One rewrite per finding, worst first: the prose line, then the sentence ending.
        return json.dumps(["- 본관 승강기 3대 1998년 설치, 28년째 사용 중임",
                           "- 3호기 조건부 합격 판정을 받음"], ensure_ascii=False), {
            "inputTokens": 1, "outputTokens": 1}

    fixed, left, _ = await verify.verify_and_repair(
        doc, report_checks.checks(pre), complete=complete, model="m", api_key="k")
    assert left == []
    assert "28년째 사용 중임" in fixed[0]["content"] and "받았습니다" not in fixed[0]["content"]


def test_noun_endings_pass_and_sentence_endings_do_not():
    for line in ("- 보수 필요", "- 12월 착수 예정", "- 예산 부족 우려됨", "- 비용 과다",
                 "- 고장 19건(1~9월)", "  - 2호기 갇힘 사고 1건 [1]", "| 구분 | 했다 |"):
        assert not report_checks._verb_ended(line), line
    for line in ("- 3호기 교체를 건의합니다.", "- 고장이 늘었다", "- 검토 바랍니다(시설팀)"):
        assert report_checks._verb_ended(line), line


def test_the_two_line_summary_is_read_from_the_first_callout():
    two = "```callout\n핵심\n3호기 우선 교체 필요\n교체 승인과 예산 반영 결정 요청\n```\n- 현황"
    three = "```callout\n**핵심**\n가\n나\n다\n```"
    assert report_checks.summary_lines([{"heading": "현황", "content": two}]) == 2
    assert report_checks.summary_lines([{"heading": "현황", "content": three}]) == 3
    assert report_checks.summary_lines([{"heading": "현황", "content": "- 현황"}]) is None
    pre = report_checks.Prepass(format_id="gaejo")
    [shown] = report_checks.amendments(pre, [{"heading": "현황", "content": three}], "", "", [])
    assert shown.code == "summary_lines" and shown.report_only
    assert shown.missing([{"heading": "현황", "content": three}])


# ── proposal against the RFP ────────────────────────────────────────────

PROPOSAL_RFP = (
    "[RFP 발췌 — 한빛물류(주) 「고객센터 상담 요약 자동화 PoC」]\n"
    "2. 사업 기간: 2026. 11. 2. ~ 2027. 1. 29. (3개월)\n"
    "3. 예산: 48,000,000원 (부가가치세 별도)\n"
    "[우리 회사 — (주)누리데이터]\n- 수행 실적: 2024 지역 택배사 콜센터 상담 분류\n"
)
KAFI = (
    "1. 사업명 : 대표 홈페이지 웹 접근성 개선 및 품질인증 취득 사업\n"
    "2. 사업기간 : 계약일로부터 5개월\n"
    "Ⅳ. 제출 서류: 제안서 본문 30쪽 이내, 가격제안서 별도 봉인\n"
)
GAON = (
    "발주기관: 가온구청 문화체육과(가상)  ·  공고일 2026. 10. 5.\n"
    "- 사업 기간: 계약일 ~ 2027. 6. 30.(계약 예정일 2026. 12. 1.)\n"
    "- 제안서 제출: 2026. 11. 9.(월) 10:00까지 조달청 나라장터, 제안서 본문 50쪽 이내\n"
)


def test_the_rfp_terms_are_read_by_code():
    hanbit = report_checks.rfp_terms(PROPOSAL_RFP, "제안서 써 줘")
    assert hanbit["clients"] == ["한빛물류(주)"] and hanbit["end"] == "2027-01-29"
    kafi = report_checks.rfp_terms("제안요청서  |  한국농식품정보원  |  2026\n" + KAFI, "")
    assert (kafi["months"], kafi["pages"], kafi["end"]) == (5, 30, None)
    assert kafi["clients"] == ["한국농식품정보원"]
    # A mail header's date is not a client.
    mail = report_checks.rfp_terms("[고객 메일 — 2026-10-06(화), 도담푸드 경영지원팀]\n", "")
    assert mail is None
    gaon = report_checks.rfp_terms(GAON, "")
    assert gaon["clients"] == ["가온구청 문화체육과"] and gaon["end"] == "2027-06-30"
    assert gaon["pages"] == 50
    proposal = process_settings.for_format("client_proposal")
    assert report_checks.page_limit(proposal, "초안 써 줘", KAFI) == 30
    assert report_checks.page_limit(proposal, "10쪽 이내로", KAFI) == 10
    assert report_checks.page_limit(proposal, "초안 써 줘", "") is None


def test_the_cover_names_the_rfp_client():
    rfp = report_checks.rfp_terms(PROPOSAL_RFP, "")

    def head(client: str, ours: str = "(주)누리데이터") -> dict:
        return {"format": "client_proposal", "fields": [["고객사", client], ["제안사", ours]]}

    assert report_checks.client_head(head("한빛물류"), rfp) == (None, "")
    # A name the request gives is kept even when the RFP header spells it otherwise.
    assert report_checks.client_head(head("한빛"), rfp, "한빛 고객센터 건") == (None, "")
    fixed, problem = report_checks.client_head(head(""), rfp)
    assert fixed["fields"][0] == ["고객사", "한빛물류(주)"] and "비었다" in problem
    fixed, problem = report_checks.client_head(head("(주)누리데이터"), rfp)
    assert fixed["fields"][0][1] == "한빛물류(주)" and "다르다" in problem
    # The proposer's past client (실적) is never read as the client.
    assert "택배사" not in " ".join(rfp["clients"])


async def test_a_schedule_past_the_rfp_period_is_found_and_repaired():
    pre = report_checks.Prepass(format_id="client_proposal",
                                rfp=report_checks.rfp_terms(PROPOSAL_RFP, ""))
    table = ("| 단계 | 기간 |\n|---|---|\n| 착수 | 2026. 11. 2. |\n| 최종 보고 | 2027. 2. 26. |\n"
             "| 하자 보수 | 2027. 12. 31. |\n")
    doc = verify.Doc([{"heading": "추진 일정", "content": table}], PROPOSAL_RFP)
    found, _ = await verify.find(doc, report_checks.checks(pre))
    assert [f.code for f in found] == ["schedule_over"] and "2027-01-29" in found[0].why

    async def complete(model, messages, api_key, max_tokens):
        return json.dumps(["| 최종 보고 | 2027. 1. 29. |"]), {"inputTokens": 1, "outputTokens": 1}

    fixed, left, _ = await verify.verify_and_repair(
        doc, report_checks.checks(pre), complete=complete, model="m", api_key="k")
    assert left == [] and "2027. 1. 29." in fixed[0]["content"]
    months = report_checks.Prepass(format_id="client_proposal",
                                   rfp=report_checks.rfp_terms(KAFI, ""))
    over, _ = await report_checks.proposal_schedule(
        verify.Doc([{"heading": "추진 일정", "content": "- M1~M6 품질인증 심사 대응"}]), months.rfp)
    assert [f.code for f in over] == ["schedule_over"] and "5개월" in over[0].why


# ── minutes: rows form and columns ──────────────────────────────────────

MINUTES = (
    "[메모 — 컴퓨터공학과 교육과정위원회, 2026-10-12(월) 15:00~16:10, 공학관 302호]\n"
    "- 신설 과목 강의계획서 초안 → 이교수, 다음 주 금요일까지\n"
    "- 학생 수요 설문 → 최○○, 이번 주 금요일까지 회수\n"
)


def _dates(material: str = MINUTES) -> list[dict]:
    return report_checks.skill_scripts.dates().scan(material)


async def test_minutes_rows_written_as_a_list_are_read_like_table_rows():
    pre = report_checks.Prepass(format_id="minutes", dates=_dates())
    content = (
        "- **강의계획서 초안** 회의에서 정함. <small>이교수 · 다음 주 금요일까지 제출</small>\n"
        "- **학생 수요 설문 회수** 회의에서 정함. <small>최○○ · 2026-10-16(금)</small>\n"
        "- **선수과목 검토** <small>미정 · 미정</small>\n"
    )
    doc = verify.Doc([{"heading": "실행 항목", "content": content}], MINUTES)
    found, _ = await verify.find(doc, report_checks.checks(pre))
    by_item = {f.sentence.split("**")[1]: f.why for f in found}
    assert set(by_item) == {"강의계획서 초안", "선수과목 검토"}
    assert "값에는" in by_item["강의계획서 초안"]
    assert "사유" in by_item["선수과목 검토"] and "출처" in by_item["선수과목 검토"]
    still = next(f for f in found if "강의계획서" in f.sentence).still
    assert not still("- **강의계획서 초안** 회의에서 정함(다음 주 금요일). "
                     "<small>이교수 · 2026-10-23(금)</small>")


def test_an_action_table_without_source_and_note_columns_gets_them():
    table = ("| 할 일 | 담당 | 기한 |\n|---|---|---|\n"
             "| 신설 과목 강의계획서 초안 | 이교수 | 2026-10-23(금) |\n"
             "| 현수막 시안 | 미정 | 미정 |\n")
    sections, added = report_checks.minutes_columns(
        [{"heading": "실행 항목", "content": table}], _dates())
    assert added == 1
    lines = sections[0]["content"].split("\n")
    assert lines[0] == "| 할 일 | 담당 | 기한 | 출처 | 비고 |"
    assert lines[2].endswith("| 회의 | - |") and lines[3].endswith("| [출처] | - |")
    again, more = report_checks.minutes_columns(sections, _dates())
    assert more == 0 and again == sections


# ── comparison: must-haves and the recommendation ───────────────────────

MESSENGER = {
    "criteria": [],
    "musts": [{"name": "SSO", "kind": "yesno"},
              {"name": "화상회의 인원", "kind": "min", "limit": 60, "unit": "명"}],
    "options": [
        {"name": "톡플로우", "musts": {"SSO": "있음", "화상회의 인원": 100}},
        {"name": "팀링크", "musts": {"SSO": "있음", "화상회의 인원": 50}},
        {"name": "워크닷", "musts": {"SSO": "있음", "화상회의 인원": 300}},
    ],
}
MESSENGER_TEXT = "SSO 필수, 화상회의 60명. 톡플로우 100명, 팀링크 50명, 워크닷 300명"


def test_must_haves_drop_an_option_by_code():
    numbers = report_checks._material_numbers(MESSENGER_TEXT)
    result = report_checks.must_status(MESSENGER, numbers)
    assert {o: r["status"] for o, r in result["options"].items()} == {
        "톡플로우": "충족", "팀링크": "탈락", "워크닷": "충족"}
    assert "50명 < 조건 60명" in result["options"]["팀링크"]["failed"][0]
    block = report_checks._must_block(result)
    assert "- 팀링크: 탈락" in block
    # A value or limit the material does not give is not judged on.
    made_up = json.loads(json.dumps(MESSENGER))
    made_up["options"][0]["musts"]["화상회의 인원"] = 999
    judged = report_checks.must_status(made_up, numbers)["options"]["톡플로우"]
    assert judged["status"] == "조건 미확인"


def test_vat_bases_are_put_on_the_condition_s_basis():
    must = {"name": "가격", "kind": "max", "limit": 4000000, "vat_basis": "incl"}
    assert report_checks._must_value({"value": 4200000, "vat_basis": "excl"}, must) == 4620000
    assert report_checks._must_value(3900000, must) == 3900000


async def test_recommending_a_dropped_option_is_found_and_repaired():
    numbers = report_checks._material_numbers(MESSENGER_TEXT)
    pre = report_checks.Prepass(format_id="comparison",
                                musts=report_checks.must_status(MESSENGER, numbers))
    content = ("- 연간 비용이 가장 낮은 팀링크(기본 요금제)를 권고한다.\n"
               "- 포기하는 것: 해외 데이터센터 감수\n")
    doc = verify.Doc([{"heading": "결론과 권고", "content": content}], MESSENGER_TEXT)
    found, _ = await verify.find(doc, report_checks.checks(pre))
    assert [f.code for f in found] == ["recommend_mismatch"] and "탈락" in found[0].why

    async def complete(model, messages, api_key, max_tokens):
        return json.dumps(["- 팀링크는 화상회의 50명으로 탈락, 톡플로우를 권고한다."],
                          ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    fixed, left, _ = await verify.verify_and_repair(
        doc, report_checks.checks(pre), complete=complete, model="m", api_key="k")
    assert left == [] and "톡플로우를 권고" in fixed[0]["content"]
    assert report_checks.amendments(pre, fixed, MESSENGER_TEXT, "", []) == []
    # The dropped option must be shown as dropped; code adds the line when it is not.
    [status] = report_checks.amendments(pre, doc.sections, MESSENGER_TEXT, "", [])
    assert status.code == "status_missing" and status.append.startswith("- 팀링크: 탈락")
    assert not status.missing([{"heading": "상태", "content": status.append}])


def test_the_recommendation_follows_the_scores_unless_a_reason_is_given():
    scored = report_checks.scores({
        "criteria": [{"name": "비용", "weight": 60, "kind": "cost"},
                     {"name": "성능", "weight": 40, "kind": "benefit"}],
        "options": [{"name": "엑스북", "values": {"비용": 2500000, "성능": 12000}},
                    {"name": "와이북", "values": {"비용": 2000000, "성능": 10200}}]})
    leader = scored["leaders"][0]
    other = next(o for o in scored["options"] if o != leader)
    assert report_checks._recommend_problem(f"- {other}을 권고한다.", None, scored)
    assert not report_checks._recommend_problem(f"- {leader}를 권고한다.", None, scored)
    assert not report_checks._recommend_problem(
        f"- 점수는 낮지만 배터리 때문에 {other}을 권고한다.", None, scored)
    every = {"musts": ["SSO"], "options": {"A": {"status": "탈락", "failed": ["SSO 없음"],
                                                 "unknown": []},
                                           "B": {"status": "탈락", "failed": ["SSO 없음"],
                                                 "unknown": []}}}
    assert "충족하는 대안 없음" in report_checks._recommend_problem("- A를 권고한다.", every, None)
    assert not report_checks._recommend_problem(
        "- 조건을 모두 충족하는 대안 없음. 권고하지 않는다.", every, None)


# ── the judge (122B) ────────────────────────────────────────────────────


def _judge_reply(items: list[dict]):
    calls: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        calls.append(model)
        reply = json.dumps({"items": items}, ensure_ascii=False)
        return reply, {"inputTokens": 5, "outputTokens": 2}

    return complete, calls


async def test_an_owner_change_the_judge_finds_is_shown_not_rewritten():
    table = ("| 할 일 | 담당 | 기한 |\n|---|---|---|\n| 버스 견적 | 강주무관 | 2026-10-20(화) |\n")
    material = "버스 견적은 강주무관이 맡기로 했다가 남주무관으로 바꿈(강주무관 출장)."
    complete, calls = _judge_reply([{"row": "| 버스 견적 | 강주무관 | 2026-10-20(화) |",
                                     "final_owner": "남주무관", "why": "출장으로 변경"}])
    pre = report_checks.Prepass(format_id="minutes")
    checks = report_checks.model_checks(pre, complete=complete, judge="local/qwen3.5-122b",
                                        api_key="k")
    doc = verify.Doc([{"heading": "실행 항목", "content": table}], material)

    async def no_repair(model, messages, api_key, max_tokens):
        raise AssertionError("a judged finding is never rewritten")

    sections, left, usage = await verify.verify_and_repair(
        doc, checks, complete=no_repair, model="m", api_key="k")
    assert [f.code for f in left] == ["owner_change"] and not left[0].auto
    assert "남주무관" in left[0].why and sections == doc.sections
    assert calls == ["local/qwen3.5-122b"] and usage["inputTokens"] == 5


async def test_a_judge_that_fails_or_times_out_finds_nothing(monkeypatch):
    async def broken(model, messages, api_key, max_tokens):
        return "not json", {}

    async def slow(model, messages, api_key, max_tokens):
        await asyncio.sleep(5)
        return "{}", {}

    monkeypatch.setattr(report_checks, "JUDGE_TIMEOUT", 0.05)
    for complete in (broken, slow):
        assert await report_checks.judge_json(complete, "j", "k", "prompt") == ([], {})


async def test_a_number_on_the_wrong_option_is_a_binding_candidate():
    material = "판교 A타워 월 임대료 8,400,000원 / 성수 B빌딩 월 임대료 7,200,000원"
    content = "- 판교 A타워 월 임대료 7,200,000원으로 가장 저렴함"
    complete, _ = _judge_reply([{"sentence": content, "number": "7,200,000원",
                                 "belongs_to": "성수 B빌딩", "why": "다른 후보의 값"}])
    pre = report_checks.Prepass(format_id="comparison")
    [check] = report_checks.model_checks(pre, complete=complete, judge="j", api_key="k")
    found, _ = await check(verify.Doc([{"heading": "비교", "content": content}], material))
    assert [f.code for f in found] == ["number_binding"] and "성수 B빌딩" in found[0].why


def test_judged_checks_are_chosen_by_format_and_need_a_judge():
    complete, _ = _judge_reply([])
    pick = lambda fmt, judge="j": report_checks.model_checks(  # noqa: E731
        report_checks.Prepass(format_id=fmt), complete=complete, judge=judge, api_key="k")
    assert [c.__name__ for c in pick("minutes")] == ["owner_change"]
    assert [c.__name__ for c in pick("client_proposal")] == ["number_binding"]
    assert [c.__name__ for c in pick("career_history")] == ["experience_repeat"]
    assert pick("gaejo") == [] and pick("minutes", judge="") == []
    assert all(getattr(c, "uses_model", False) for c in pick("minutes"))


@pytest.mark.asyncio
async def test_a_cover_letter_telling_one_experience_twice_is_flagged_by_the_judge():
    second = "안내판 위치를 바꿔 민원을 23건에서 9건으로 줄인 경험이 있습니다."
    answer = ("[제출용 답안 1]\n"
              "축제 첫날 민원 23건을 안내판 위치 변경으로 둘째 날 9건으로 줄였습니다.\n\n"
              f"[제출용 답안 2]\n{second}\n")
    complete, calls = _judge_reply([{"sentence": second, "repeats": "답안 1",
                                     "why": "같은 상황·행동·결과"}])
    outcome = await cover_letter.post_process(
        answer, request="두 문항 써 줘", material="민원 첫날 23건 → 둘째 날 9건, 안내판 위치 변경",
        complete=complete, model="w", api_key="k", judge="j")
    assert outcome is not None and outcome.repeats == [f"답안 2: 「{second}」"]
    assert "같은 경험 반복 후보" in outcome.detail and calls == ["j"]
    assert cover_letter.wire(outcome)["repeats"] == outcome.repeats


# ── wired into the report pipeline ──────────────────────────────────────


async def test_a_gaejo_report_with_nothing_attached_is_still_checked_for_its_shape(monkeypatch):
    from app.services import report

    draft = "## 현황\n" + GAEJO_MEMO + "\n## 향후 조치\n- 3호기 우선 교체 승인 필요\n"

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return draft, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request="승강기 교체 건 개조식 보고서 써 줘", model="m", api_key="k", web_search=False,
        template_id="doc-gaejo-report",
        approved_plan={"title": "승강기 교체 검토", "sections": ["현황", "향후 조치"]},
    )]
    steps = {e["id"]: e for e in events if e["type"] == "step"}
    detail = steps["format_checks"]["detail"]
    assert "개조식 본문의 줄글 문단 1 → " in detail and "핵심 두 줄 없음 1 → " in detail


async def test_the_minutes_judge_runs_once_and_its_candidate_is_listed(monkeypatch):
    from app.services import report

    table = ("| 할 일 | 담당 | 기한 | 출처 | 비고 |\n|---|---|---|---|---|\n"
             "| 버스 견적 | 강주무관 | 2026-10-20(화) | 회의 | - |\n")
    draft = "## 회의 개요\nOT 준비 회의.\n## 실행 항목\n" + table
    judged: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        prompt = str(messages[-1]["content"])
        if "do not give the final owner" in prompt:
            judged.append(model)
            row = table.split("\n")[2]
            item = {"row": row, "final_owner": "남주무관", "why": "출장으로 변경"}
            reply = json.dumps({"items": [item]}, ensure_ascii=False)
            return reply, {"inputTokens": 1, "outputTokens": 1}
        return draft, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    material = ("[OT 준비 회의 전사 — 2026-10-12(월) 10:00]\n"
                "버스 견적은 강주무관이 하기로 했다가 남주무관으로 바꿈(강주무관 출장). "
                "견적은 2026-10-20(화)까지.\n")
    events = [e async for e in report.write(
        request="회의록으로 정리해 줘", model="m", api_key="k", web_search=False,
        untrusted_context=[material], template_id="doc-minutes", outline_model="judge-122b",
        approved_plan={"title": "OT 준비 회의록", "sections": ["회의 개요", "실행 항목"]},
    )]
    assert judged == ["judge-122b"]
    quality = next(e for e in events if e["type"] == "step" and e["id"] == "quality")
    assert "담당 변경 미반영 후보" in quality["label"] and "남주무관" in quality["detail"]
    sections = next(e["sections"] for e in events if e["type"] == "report")
    assert "강주무관" in sections[1]["content"]
