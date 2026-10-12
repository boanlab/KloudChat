"""Post-writing guards keep what is true: the persona-sweep cases, with their own strings.

A guard (grounding trace, skill post-check scripts, copy check, a rewrite) used to blank a
value it could not match as 「[수치]」 or drop the sentence. The value guard settles each
repair: a value the evidence supports goes back as written, any other value stays marked
「(확인 필요)」, and no paragraph is emptied nor a table left with its header alone.
"""

from __future__ import annotations

import asyncio

from app.pipeline import chat_stages
from app.pipeline.check_stages import AnswerGrounding, SkillChecks
from app.pipeline.stage import Context
from app.routers import sessions
from app.services import check_skills, current_evidence, internal_words, report, skill_scripts
from app.services.value_guard import Evidence, settle

# A16 T2: the change list the person pasted (no blank line under the ask)
A16_REQUEST = (
    "이번 업데이트 내용으로 사내 공지랑 고객용 릴리스 노트 둘 다 써 줘. 사내 공지는 "
    "영업·고객지원팀이 알아야 할 것 위주로.\n"
    "(개발팀 변경 목록 — 한빛소프트 「한빛워크」 3.4.1, 가상, 배포 2026-11-04 수요일 22시~24시 "
    "점검 중 배포)\n"
    "- fix(meeting): 화면 공유 중 해상도가 720p로 떨어지던 문제\n"
    "- fix(docs): 공동편집 중 표 셀 병합이 되돌려지던 문제 (고객 문의 37건)\n"
    "- feat(admin): 관리자 콘솔에서 퇴사자 계정 일괄 비활성화 (CSV 업로드)\n"
    "- change(billing): 클라우드형 요금 청구서 메일 발송일 매월 1일 → 매월 3일\n"
    "- security: 외부 공유 링크 기본 만료 기간 무제한 → 30일 (기존 링크는 유지)\n"
    "- known issue: 안드로이드 12 이하에서 다크 모드 일부 화면 깨짐, 3.4.2에서 수정 예정\n"
    "- 점검 중(22~24시) 웹·앱 접속 불가"
)

A16_WRITTEN = (
    "두 문서 모두 개발팀 변경 목록 7개 항목만 근거로 썼습니다.\n\n"
    "**제목** 한빛워크 3.4.1 배포 안내 (11/4 수 22:00~24:00 점검)\n\n"
    "2026년 11월 4일 수요일 22:00~24:00 점검 중 한빛워크 3.4.1이 배포되고, 이 시간에는 웹과 앱 "
    "모두 접속할 수 없습니다.\n\n"
    "- 시행: 2026. 11. 4.(수) 22:00~24:00 점검 중 배포\n\n"
    "# 한빛워크 3.4.1 (2026. 11. 4.)\n\n"
    "- 클라우드형 요금 청구서 메일 발송일이 매월 1일에서 매월 3일로 바뀝니다. 별도 조치는 필요 "
    "없으며, 11월 청구서는 11월 3일에 도착합니다.\n\n"
    "뺀 항목: 없음 (7개 항목 모두 반영, 빠짐 0)"
)

# what the release-notes check and the rewrite left of it (the final answer as shipped)
A16_GUARDED = (
    "두 문서 모두 개발팀 변경 목록 [수치]개 항목만 근거로 썼습니다.\n\n"
    "**제목** 한빛워크 3.4.1 배포 안내 (11/[수치] 수 22:00~24:00 점검)\n\n"
    "2026년 11월 [수치] 수요일 22:00~24:00 점검 중 한빛워크 3.4.1이 배포되고, 이 시간에는 웹과 앱 "
    "모두 접속할 수 없습니다.\n\n"
    "- 시행: 2026. 11. [수치](수) 22:00~24:00 점검 중 배포\n\n"
    "# 한빛워크 3.4.1 (2026. 11. [수치])\n\n"
    "- 클라우드형 요금 청구서 메일 발송일이 매월 1일에서 매월 3일로 바뀝니다. 별도 조치는 필요 "
    "없으며, 11월 청구서는 [날짜]에 도착합니다.\n\n"
    "뺀 항목: 없음 ([수치]개 항목 모두 반영, 빠짐 0)"
)


def _settle(before: str, after: str, *texts: str) -> str:
    return settle(before, after, Evidence.of(list(texts), answer=before)).text


# ── dates in any notation ────────────────────────────────────────────────


def test_a_given_date_matches_in_every_notation():
    ev = Evidence.of(["배포 2026-11-04 수요일"])
    for y, m, d in ((2026, 11, 4), (None, 11, 4)):
        assert ev.supports_day(y, m, d)
    from app.services.value_guard import supported

    for written in ("11월 4일", "11월 4일(수)", "11/4", "2026. 11. 4.", "11. 4.(수)",
                    "2026년 11월 4일"):
        assert supported(written, written, 0, ev, 0), written
    assert not supported("11월 3일", "11월 3일", 0, ev, 0)


def test_the_release_note_dates_and_counts_come_back_and_only_the_wrong_day_is_marked():
    out = _settle(A16_WRITTEN, A16_GUARDED, A16_REQUEST)
    assert "[수치]" not in out and "[날짜]" not in out
    assert "(11/4 수 22:00~24:00 점검)" in out
    assert "2026년 11월 4일 수요일" in out and "2026. 11. 4.(수)" in out
    assert "# 한빛워크 3.4.1 (2026. 11. 4.)" in out
    assert "변경 목록 7개 항목" in out and "(7개 항목 모두 반영" in out
    # 11월 청구서 on 11월 3일: no list line says so
    assert "11월 청구서는 11월 3일(확인 필요)에 도착합니다." in out


def test_the_release_notes_script_reads_the_list_s_date_in_another_notation():
    notes = skill_scripts.module("release-notes", "notes")
    text = ("2026년 11월 4일(수) 22:00~24:00 점검 중 배포합니다. 변경 목록 7개 항목을 모두 "
            "반영했습니다.")
    out = notes.post(text, {"material": A16_REQUEST, "final": True})
    assert out["text"] == text and not out["fixed"]


def test_an_ordinal_is_not_a_quantity_for_the_notice_check():
    check = skill_scripts.module("internal-comms", "check")
    text = "**앱 스토어 설명 (짧은 판, 1차 화면용)**"
    out = check.post(text, {"material": "3.4.0 릴리스 노트", "final": True})
    assert out["text"] == text


# ── this turn's web search and fetched pages are evidence ────────────────

A15_PARAGRAPH = ("대웅제약은 지속가능경영보고서에서 정보보안·개인정보보호를 핵심 정책 영역으로 "
                 "다루고 있고, 2025년 정보보호 공시에서 정보보호 투자액 32억 원을 밝혔습니다.")
A15_SEARCH = ("[1] 대웅제약 지속가능경영보고서 2025 — 정보보안·개인정보보호를 핵심 정책 영역으로 "
              "관리 … 2025년 정보보호 공시: 정보보호 투자액 32억 원")
A15_TABLE = ("| 출처 | 내용 |\n|---|---|\n"
             "| 지속가능경영보고서 | 정보보안·개인정보보호 핵심 정책 |\n"
             "| 정보보호 공시 | 투자액 32억 원 |")


def test_a_research_paragraph_a_search_supports_is_kept_unmarked():
    before = f"안녕하세요.\n\n{A15_PARAGRAPH}\n\n{A15_TABLE}\n\n감사합니다."
    after = "안녕하세요.\n\n| 출처 | 내용 |\n|---|---|\n\n감사합니다."
    out = _settle(before, after, "대웅제약 총무·IT 담당자에게 보낼 첫 영업 메일", A15_SEARCH)
    assert A15_PARAGRAPH in out and f"{A15_PARAGRAPH} (확인 필요)" not in out
    # never a table with its header alone
    assert "| 정보보호 공시 | 투자액 32억 원 |" in out


def test_without_the_search_the_kept_paragraph_is_marked():
    before = f"안녕하세요.\n\n{A15_PARAGRAPH}\n\n감사합니다."
    out = _settle(before, "안녕하세요.\n\n감사합니다.", "대웅제약에 보낼 영업 메일")
    assert f"{A15_PARAGRAPH} (확인 필요)" in out


def test_search_results_reach_the_answer_grounding_stage():
    answer = "대웅제약은 2025년 정보보호 공시에서 투자액 32억 원을 밝혔습니다."
    bundle = chat_stages.bundle_for(
        answer=answer, request="대웅제약에 보낼 메일", model={"id": "m"}, actual_model=None,
        api_key="k", session_id="s", today=None, first_message="", messages=[],
        material="대웅제약에 보낼 메일", check_keys=(), check_material="영업 기록: 첫 연락",
        tool_outputs=[], judge="", kept_steps=[], usage={}, traced=True,
        evidence=[A15_SEARCH])

    async def go():
        return [e async for e in AnswerGrounding().run(bundle, Context())]

    asyncio.run(go())
    assert bundle.document.answer == answer


# ── the answer's own tables and simple arithmetic ────────────────────────

A06_TABLE = (
    "| 부서 | 사업명 | 예산액(원) | 9월말 집행액(원) | 집행률(%) | 잔여액(원) "
    "| 월평균 집행 필요액(원) | 9월까지 월평균 집행액(원) |\n|---|---|---|---|---|---|---|---|\n"
    "| 교육혁신팀 | LMS 개편 | 180,000,000 | 54,000,000 | 30.0 | 126,000,000 | 42,000,000 | "
    "6,000,000 |\n"
    "| 학생지원팀 | 진로 멘토링 | 42,000,000 | 35,700,000 | 85.0 | 6,300,000 | 2,100,000 | "
    "3,966,667 |\n"
    "| 국제교류팀 | 해외 단기연수 | 250,000,000 | 237,500,000 | 95.0 | 12,500,000 | 4,166,667 | "
    "26,388,889 |\n"
    "| 국제교류팀 | 외국인 유학생 적응 지원 | 38,000,000 | 11,400,000 | 30.0 | 26,600,000 | "
    "8,866,667 | 1,266,667 |\n\n"
    "| 부서 | 집행액 합(원) |\n|---|---|\n| 교육혁신팀 | 63,600,000 |\n"
    "| 국제교류팀 | 248,900,000 |\n"
    "| 산학협력팀 | 146,750,000 |\n| 학생지원팀 | 115,740,000 |"
)


def test_values_the_answer_s_own_table_holds_are_never_blanked():
    lines = [
        ("전체 예산 8억 8천만 원 중 5억 7,499만 원을 집행해 65.3%입니다.",
         "전체 예산 8억 8천만 원 중 [수치]을 집행해 65.3%입니다."),
        ("앞으로 월 4,200만 원으로 집행 속도를 올려야 합니다.",
         "앞으로 월 [수치]로 집행 속도를 올려야 합니다."),
        ("각각 월 700만 원, 887만 원의 집행이 필요하지만, 현재 월평균 집행액은 100만 원, 127만 원 "
         "수준입니다.",
         "각각 월 700만 원, [수치]의 집행이 필요하지만, 현재 월평균 집행액은 100만 원, [수치] "
         "수준입니다."),
        ("9월까지 월평균 집행액이 2,639만 원이므로, 남은 3개월간 같은 속도면 약 6,667만 원이 더 "
         "필요합니다.",
         "9월까지 월평균 집행액이 [수치]이므로, 남은 3개월간 같은 속도면 약 [수치]이 더 "
         "필요합니다."),
        ("9월까지 월평균 397만 원보다 낮으므로 예산 안에서 마무리할 수 있습니다.",
         "9월까지 월평균 [수치]보다 낮으므로 예산 안에서 마무리할 수 있습니다."),
    ]
    material = "9월말 예산 집행 현황 분석해 줘. 적정 집행률은 75%"
    for written, guarded in lines:
        before, after = f"{written}\n\n{A06_TABLE}", f"{guarded}\n\n{A06_TABLE}"
        assert _settle(before, after, material) == before, written


def test_a_computed_total_and_a_delivery_day_are_supported():
    # A05: a total of the given amounts; A02: the rest of a share
    total = "숙박비와 교통비를 합해 285,600원입니다."
    assert _settle(total, "숙박비와 교통비를 합해 [수치]입니다.",
                   "숙박 120,000원 2박, 교통 45,600원") == total
    assert _settle("체크리스트 나머지 30% 마무리", "체크리스트 나머지 [수치] 마무리",
                   "모바일 검수 체크리스트 초안 70% 작성") == "체크리스트 나머지 30% 마무리"
    # A04: 10월 12일 계약, 납기 3주 → 11월 2일
    text = "가온정보통신의 납기 3주(10월 12일 계약 시 11월 2일 완료) 조건은 충족됩니다."
    material = "가온정보통신 납기 3주, 누리컴 납기 5주. 계약은 10월 12일 예정."
    traced = check_skills.ground(("grounding-check",), text, material=material, computed=[],
                                 options={"today": "2026-10-12"})
    assert traced.text == text


def test_money_a_search_source_states_stays_in_a_report():
    # A07: 「약 75억 원」 kept, never 「75억 [금액]」
    text = "2026년 클라우드 전환 지원 예산은 약 75억 원으로, 전년 대비 두 배 이상입니다."
    out = report._without_invented_money(text, ["행정안전부 2026 예산: 클라우드 전환 지원 75억 원"])
    assert out == text
    assert report._without_invented_money(text, []).count("(확인 필요)") == 1


# ── order, counts and character counts are not claims ────────────────────


def test_ordinals_item_numbers_and_character_counts_come_back():
    scan = skill_scripts.module("generic-language-killer", "scan")
    material = "11월 한 달 기존 고객에게 AI 회의록 요약을 무료로 엽니다."
    before = ("| # | 헤드라인 | 글자 수 |\n|---|---|---|\n"
              "| 19 | 회의록 대신 요약이 먼저 도착합니다 | 17 |\n\n"
              "**기능 블록 1 — 결재 알림 연동**\n\n"
              "### 먼저 시험할 3개\n\n"
              "11월 한 달 기존 고객 무료\n\n"
              "**제목** (광고) 10월 20일 업데이트와 시범 사용 건 (제목 26자)\n\n"
              "### [1차 메일] (10월 6일, 자료 보내고 4일 뒤)")
    guarded = scan.post(before, {"material": material, "request": "헤드라인 써 줘",
                                 "final": True})["text"]
    # the script no longer cuts 「11월」 to 「[구체적 수치]1월」
    assert "[구체적 수치]1월" not in guarded
    out = _settle(before, guarded, material, "헤드라인 써 줘")
    for kept in ("| 19 | 회의록 대신", "| 17 |", "기능 블록 1 —", "먼저 시험할 3개",
                 "11월 한 달 기존 고객 무료", "(제목 26자)", "[1차 메일]"):
        assert kept in out, kept
    assert "[구체적 수치]" not in out


def test_one_place_is_an_idiom_not_a_count():
    claims = skill_scripts.module("copy-check", "claims")
    found = claims.check("메일로 오가던 첨부 파일을, 한 곳에서 함께 봅니다.", "협업툴 소개")
    assert not [f for f in found if f["kind"] == "number"]


# ── the body keeps no caveat, check block or echoed prompt heading ───────


def test_a_pasted_change_list_is_the_person_s_material_not_a_present_fact():
    assert sessions._reads_pasted_material(A16_REQUEST)
    assert sessions._memo_pasted(A16_REQUEST)
    assert not sessions._reads_pasted_material("삼성전자 대표가 누구야?")


def test_the_answer_on_given_material_drops_the_caveat_the_check_block_and_echoed_heading():
    answer = ("# Answer\n두 문서 모두 변경 목록만 근거로 썼습니다.\n\n"
              "보내기 전에 채울 것: [문의처]\n\n"
              "**검증 결과**\n- 릴리스 노트: 항목 전부 노트에 반영(빠짐 0, 뺌 0)\n"
              "- 사내 공지: 남은 빈칸은 [문의처] 1개."
              + current_evidence.caveat("릴리스 노트 써 줘"))
    bundle = chat_stages.bundle_for(
        answer=answer, request=A16_REQUEST, model={"id": "m"}, actual_model=None, api_key="k",
        session_id="s", today=None, first_message=A16_REQUEST, messages=[],
        material=A16_REQUEST, check_keys=(), check_material="", tool_outputs=[], judge="",
        kept_steps=[], usage={}, traced=True)

    async def go():
        return [e async for e in chat_stages.Normalize().run(bundle, Context())]

    events = asyncio.run(go())
    out = bundle.document.answer
    assert out == "두 문서 모두 변경 목록만 근거로 썼습니다.\n\n보내기 전에 채울 것: [문의처]"
    step = next(e for e in events if e["type"] == "step")
    assert "빠짐 0" in step["detail"]


def test_an_unrelated_answer_keeps_its_caveat():
    answer = "기준금리는 2.5%였습니다." + current_evidence.caveat("기준금리 얼마야")
    assert internal_words.strip_echoed_heading(answer) == answer
    bundle = chat_stages.bundle_for(
        answer=answer, request="기준금리 얼마야", model={"id": "m"}, actual_model=None,
        api_key="k", session_id="s", today=None, first_message="기준금리 얼마야", messages=[],
        material="기준금리 얼마야", check_keys=(), check_material="", tool_outputs=[], judge="",
        kept_steps=[], usage={}, traced=False)
    asyncio.run(chat_stages.Normalize().apply(bundle, Context()))
    assert current_evidence.has_caveat(bundle.document.answer)


def test_a_document_s_own_inspection_results_section_stays():
    text = ("## 점검 결과\n- 3호기 브레이크 마모, 교체 필요\n- 1·2호기 정상\n\n"
            "## 조치 계획\n- 11월 교체")
    assert internal_words.strip_check_block(text) == (text, "")


def test_the_skill_check_stage_leaves_no_value_blank(monkeypatch):
    async def complete(model, messages, api_key, max_tokens, **kwargs):
        prompt = messages[-1]["content"]
        return ('{"unsupported": []}' if "Reply with JSON" in prompt else prompt), {}

    monkeypatch.setattr(report, "_complete", complete)
    bundle = chat_stages.bundle_for(
        answer=A16_WRITTEN, request=A16_REQUEST, model={"id": "m"}, actual_model=None,
        api_key="k", session_id="s", today=None, first_message=A16_REQUEST, messages=[],
        material=A16_REQUEST, check_keys=(), check_material="", tool_outputs=[], judge="",
        kept_steps=[], usage={}, skill_keys=("release-notes", "internal-comms"), traced=True)

    async def go():
        return [e async for e in SkillChecks().run(bundle, Context())]

    asyncio.run(go())
    out = bundle.document.answer
    assert "[수치]" not in out and "[날짜]" not in out
    assert "2026. 11. 4.(수)" in out and "7개 항목" in out
