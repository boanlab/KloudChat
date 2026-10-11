"""Minutes keep to what the material holds."""

from __future__ import annotations

import datetime as dt

from app.services import report_checks

# ── a recording says no date aloud ───────────────────────────

#: What the recording says, as the transcript gives it: no date anywhere in the words.
SPOKEN = """한교수: 자, 시작합시다. 오늘은 세 가지. KCC 논문, GPU 서버, 그리고 학회 출장.
한교수: 먼저 KCC. 마감이 11월 20일 맞죠?
오○○: 네, 11월 20일 금요일 자정이요. 제가 제1저자로 내려고 하고, 지금 실험은 70퍼센트 정도 됐어요.
한교수: 좋아요. 그럼 KCC는 오○○ 제1저자로 냅시다. 서○○는 데이터 정제 쪽을 맡아 줘요. 언제까지 돼요?
서○○: 다음 주 수요일까지 하겠습니다.
한교수: 그래요. 다음 주 수요일.
문○○: 저 교수님, 그 정제 스크립트 제가 예전에 짠 거 있는데 — 
서○○: 아 그거 쓰면 되겠다.
한교수: 그건 둘이 알아서 맞춰요. 다음, GPU 서버. A100 두 대를 서○○ 먼저 쓰게 하자고 했었는데…
한교수: 아니다, 마감이 급한 건 오○○니까 오○○ 먼저. 다음 주까지는 오○○ 우선으로 합시다.
오○○: 감사합니다.
한교수: 예약표는 누가 관리하죠? 문○○가 다음 주부터 매주 월요일에 갱신해 줘요.
문○○: 네, 알겠습니다.
한교수: 마지막, 학회 출장. 12월 부산 학회는 서○○랑 문○○ 둘이 가는 걸로 하고 싶은데, 연구비가 되는지 행정실에 확인해 봐야 돼요. 그건 내가 확인할게요.
문○○: 숙소는 저희가 알아볼까요?
한교수: 연구비 확인되고 나서. 그 전엔 예약하지 마요.
한교수: 아 그리고 오○○, 초록 초안은 (잡음) …까지 나한테 보내 줘요.
오○○: 네.
한교수: 끝. 수고했어요."""  # noqa: E501 — the recording as said
RECORDING = "## 녹음_연구실주간미팅_20261014_1000.mp3\n" + SPOKEN
#: The run's action table: the prose said 10월 21일 while the cell stayed 미정.
RUN_ACTIONS = (
    "**표 2. 실행 항목별 담당과 기한**\n\n"
    "| 할 일 | 담당 | 기한 | 출처 | 비고 |\n| --- | --- | --- | --- | --- |\n"
    "| 데이터 정제 | 서○○ | 미정 | 회의 | 정제 스크립트는 기존에 만든 것을 활용할 예정 |\n"
    "| KCC 논문(제1저자) | 오○○ | 2026-11-20(금) | 회의 | 자정까지 제출 |\n"
    "| GPU 서버 예약표 갱신 | 문○○ | 미정 | 회의 |  |\n"
    "| 12월 부산 학회 연구비 확인 | 한교수 | 미정 | 회의 | 행정실 확인 |\n"
)


async def _no_model(*args, **kwargs):
    raise AssertionError("minutes take no model call here")


def test_the_file_name_gives_the_meeting_date_when_the_words_do_not():
    assert report_checks.meeting_date("회의록으로 정리해 줘.", RECORDING) is None
    assert report_checks.meeting_base("", RECORDING, dt.date(2026, 10, 15)) == (
        dt.date(2026, 10, 14), "file")
    for name in ("회의_2026-10-14.m4a", "회의_2026_10_14.txt", "회의 20261014.mp3"):
        assert report_checks.file_date(f"## {name}\n본문") == dt.date(2026, 10, 14)
    # The day the material came in is not the meeting's.
    assert report_checks.meeting_base("", "## 메모.txt\n본문", dt.date(2026, 10, 15)) == (None, "")
    said = "## 녹음_20261014.mp3\n[회의 2026-10-07(수) 메모]\n다음 주 수요일까지"
    assert report_checks.meeting_base("", said, None) == (dt.date(2026, 10, 7), "text")


async def test_a_recording_s_dates_are_read_from_its_file_name_and_the_step_says_so():
    pre = await report_checks.prepare("minutes", "연구실 주간 미팅 녹음 파일이야. 회의록으로 정리해 줘.",  # noqa: E501
                                      RECORDING, complete=_no_model, model="m", api_key="k",
                                      today=dt.date(2026, 10, 15))
    assert pre.base_date == "2026-10-14" and pre.base_source == "file"
    run = next(r for r in pre.runs if r["skill"] == "date-calc")
    assert "(기준 2026-10-14, 첨부 파일 이름의 날짜)" in run["summary"]
    shown = {r["text"]: r.get("shown") for r in pre.dates if r["status"] == "ok"}
    assert "2026-10-21(수)" in shown.values()


async def test_a_resolved_deadline_reaches_the_table_cell():
    pre = await report_checks.prepare("minutes", "회의록으로 정리해 줘.", RECORDING,
                                      complete=_no_model, model="m", api_key="k")
    sections = [{"heading": "실행 항목", "content": RUN_ACTIONS}]
    filled, n = report_checks.minutes_due_fill(sections, pre.dates)
    content = filled[0]["content"]
    assert ("| 데이터 정제 | 서○○ | 2026-10-21(수) | 회의 | 정제 스크립트는 기존에 만든 것을 "
            "활용할 예정, 원문 「다음 주 수요일") in content
    assert "| GPU 서버 예약표 갱신 | 문○○ | 매주 월요일(2026-10-19부터) |" in content
    # Nothing in the recording dates the 연구비 확인: it stays 미정.
    assert "| 12월 부산 학회 연구비 확인 | 한교수 | 미정 |" in content
    assert n == 2


# ── a seven-line memo: no booth specs made up as 확정 ──────────────

MEMO_3 = (
    "축제 부스 회의\n- 부스 위치 A동 앞으로 (회장)\n- 메뉴: 닭꼬치 + 음료. 단가는 다음에\n"
    "- 포스터 → 지민, 다음 주 금요일까지\n- 재료 업체 견적 → 도윤, 모레까지\n"
    "- 위생교육 이수증 → 학생처 제출 필요, 누가?\n"
    "- (끝날 때) 총학 공지 보니까 A동 앞은 무대 설치 구역이래 → 그럼 B동 앞으로 하자 (회장)\n"
    "- 지민 중간고사 겹쳐서 포스터는 수아가 하기로\n"
)
REQUEST_3 = "축제 준비 회의 때 휴대폰에 막 적은 거야. 회의록으로 만들어 줘."
#: Excerpts of a minutes draft as written.
SPEC_SECTION = (
    "운영 환경 요건은 다음과 같습니다. 냉각장비는 1대(미정)를 배치하고, 음료 보관을 위한 "
    "냉동고를 1대 "
    "준비합니다. 배수 시설은 별도 공사가 필요하지 않습니다.\n\n"
    "| 항목 | 내용 | 비고 |\n| --- | --- | --- |\n"
    "| 최종 위치 | B동 앞 | A동 앞(무대 설치 구역)에서 변경 |\n"
    "| 부스 크기 | 폭 4m × 깊이 6m | 높이는 2.5m까지 |\n"
    "| 전기 공급 | 220V 콘센트 2개 | 누전 차단기 설치 필수 |\n"
)
DECISIONS = (
    "| 항목 | 내용 | 담당 | 기한 |\n| --- | --- | --- | --- |\n"
    "| 부스 위치 변경 | A동 앞 → B동 앞 (무대 설치 구역 반영) | 전체 참석자 합의 | 확정 |\n"
    "| 부스 물리적 요건 | 폭 4m, 깊이 6m, 높이 2.5m 이하 | 시설관리 부서 협의 | 운영 당일 전 |\n"
    "| 안전 요건 점검 | 소화기 배치, 비상 대피로, 1m 이내 안전 표지판 | (미정) | 운영 당일 전 |\n"
    "| 홍보물 제작 | 포스터 제작 | 수아 | (확인 필요) |\n"
    "| 재료 견적 수집 | 업체별 공급 단가 및 납품 조건 확인 | 도윤 | 모레까지 |\n"
)


def test_made_up_specs_and_owners_are_repaired_by_code():
    sections = [{"heading": "부스 위치 및 운영 환경", "content": SPEC_SECTION},
                {"heading": "결정 사항 및 조치 계획", "content": DECISIONS}]
    fixed, codes = report_checks.minutes_grounding(sections, f"{REQUEST_3}\n{MEMO_3}")
    spec, decisions = fixed[0]["content"], fixed[1]["content"]
    assert "4m" not in spec and "220V" not in spec and "냉동고" not in spec
    assert "| 최종 위치 | B동 앞 |" in spec and "배수 시설은" in spec
    assert "| 부스 물리적 요건 | [수치] | [담당자] | 운영 당일 전 |" in decisions
    assert ("| 안전 요건 점검 | 소화기 배치, 비상 대피로, [수치] 이내 안전 표지판 "
            "| (미정) |") in decisions
    assert ("| 부스 위치 변경 | A동 앞 → B동 앞 (무대 설치 구역 반영) | 전체 참석자 합의 |"
            in decisions)
    assert "| 홍보물 제작 | 포스터 제작 | 수아 |" in decisions
    assert codes.count("minutes_owner") == 1
    assert codes.count("minutes_spec") == 5
    # Checked again: nothing is left to repair.
    assert report_checks.minutes_grounding(fixed, f"{REQUEST_3}\n{MEMO_3}")[1] == []


# ── owners and amounts the memo and mails hold stay ───────────

MATERIAL_5 = (
    "[2차 회의 메모 — 2026-10-07(수) 16:00, 인문관 410호, 참석: 배교수(학과장 대행), 신조교, 류○○, "
    "하○○(대학원생대표)]\n- 기조연사: 배교수 섭외 완료(10/1 수락), 1명\n"
    "- 예산 상한 600만 원 (배교수)\n"
    "- 다과: 120명분, 업체 선정 → 신조교, 기한 미정\n"
    "[메일 1 — 10/9(금), 배교수 → 참석자] 단과대 배정 결과 예산 상한을 520만 원으로 조정합니다.\n"
    "[메일 3 — 10/14(수), 배교수 → 참석자] 우수 포스터상은 2팀 각 20만 원으로 확정합니다. "
    "다과 업체 "
    "선정은 신조교님 대신 하○○ 님이 맡아 주시고, 10/23(금)까지 견적 2곳 비교해 주세요.\n"
)
TABLES_5 = (
    "| 항목 | 최종 금액 | 확정 시점 | 비고 |\n|---|---|---|---|\n"
    "| 총예산 상한 | 520만원 | 2026-10-09(금) | 600만원 → 메일 1에서 변경 |\n"
    "| 우수 포스터상 | 2팀 각 20만원 | 2026-10-14(수) | 메일 3에서 변경 |\n\n"
    "| 할 일 | 담당 | 기한 | 출처 | 비고 |\n| --- | --- | --- | --- | --- |\n"
    "| 다과 업체 견적 2곳 비교 | 하○○(대학원생대표) | 2026-10-23(금) | 메일 3 | 120명분 |\n"
    "| 다과 업체 선정 | 배교수 | 미정 | 2차 회의 | 견적 비교 후 |\n"
    "| 포스터 접수 현황 확인 | 조교실 | 2026-10-30(금) | 메일 2 | |\n"
)


def test_owners_and_amounts_in_the_material_are_kept_and_others_are_not():
    fixed, codes = report_checks.minutes_grounding([{"heading": "결정", "content": TABLES_5}],
                                                   MATERIAL_5)
    content = fixed[0]["content"]
    assert "| 총예산 상한 | 520만원 |" in content and "| 우수 포스터상 | 2팀 각 20만원 |" in content
    assert "| 다과 업체 견적 2곳 비교 | 하○○(대학원생대표) |" in content
    assert "| 다과 업체 선정 | 배교수 |" in content
    assert "| 포스터 접수 현황 확인 | [담당자] |" in content
    assert codes == ["minutes_owner"]


async def test_the_written_minutes_are_repaired_and_the_step_records_it(monkeypatch):
    from app.services import report

    draft = ("## 부스 위치 및 운영 환경\n" + SPEC_SECTION
             + "\n## 결정 사항 및 조치 계획\n**표 1. 결정·조치**\n\n" + DECISIONS)

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return draft, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request=REQUEST_3, model="m", api_key="k", web_search=False,
        untrusted_context=[MEMO_3], template_id="doc-minutes",
        approved_plan={"title": "축제 부스 회의록",
                       "sections": ["부스 위치 및 운영 환경", "결정 사항 및 조치 계획"]},
    )]
    sections = next(e["sections"] for e in events if e["type"] == "report")
    text = "\n".join(s["content"] for s in sections)
    assert "220V" not in text and "폭 4m" not in text and "시설관리 부서 협의" not in text
    steps = {e["id"]: e for e in events if e["type"] == "step"}
    assert "자료에 없는 수치 5 → 0" in steps["format_checks"]["detail"]
