"""Round-7 skill-script fixes: each check reads the real round-7 strings the way the skill
meant them (a worked-out first-apply date, 「22:00」 for 「22시」, 「iOS」, a deprecation
notice, 개조식 labels, end = deletion with an export window, a 「비고」 source, 「10~17시」,
a missing base_date)."""

from __future__ import annotations

import json
import re

from app.services import skill_scripts


def _notes():
    return skill_scripts.module("release-notes", "notes")


# ── release-notes (r7 A16-T2, A16-T1) ───────────────────────────────────

A16_T2_LOG = "\n".join(
    [
        "- fix(meeting): 화면 공유 중 해상도가 720p로 떨어지던 문제",
        "- fix(docs): 공동편집 중 표 셀 병합이 되돌려지던 문제 (고객 문의 37건)",
        "- feat(admin): 관리자 콘솔에서 퇴사자 계정 일괄 비활성화 (CSV 업로드)",
        "- change(billing): 클라우드형 요금 청구서 메일 발송일 매월 1일 → 매월 3일",
        "- security: 외부 공유 링크 기본 만료 기간 무제한 → 30일 (기존 링크는 유지)",
        "- known issue: 안드로이드 12 이하에서 다크 모드 일부 화면 깨짐, 3.4.2에서 수정 예정",
        "- 점검 중(22~24시) 웹·앱 접속 불가",
    ]
)
BILLING = ("클라우드형 요금 청구서 메일이 매월 1일에서 매월 3일로 발송됩니다. "
           "12월 청구분부터 새 일정이 적용됩니다.")
MAINTENANCE = "2026. 11. 4.(수) 22:00~24:00 점검 시간 동안 웹과 앱에 접속할 수 없습니다."


def _ids(items: list[dict], word: str) -> list[str]:
    return [i["id"] for i in items if word in i["text"]]


def _types(result: dict, kind: str) -> list[dict]:
    return [f for f in result["findings"] if f["type"] == kind]


def test_the_first_apply_month_and_a_colon_time_are_not_invented_figures():
    notes = _notes()
    items = notes.parse(A16_T2_LOG)
    entries = [
        {"section": "주의", "sources": _ids(items, "청구서"), "text": BILLING},
        {"section": "주의", "sources": _ids(items, "점검"), "text": MAINTENANCE},
    ]
    written = "# 한빛워크 3.4.1 (2026. 11. 4.)\n- 첫 발송은 2026. 12. 3.(목)입니다."
    result = notes.check(items, entries, version="3.4.1", date="2026-11-04", notes=written)
    assert not _types(result, "invented_figure"), result["findings"]
    assert not _types(result, "date_mismatch"), result["findings"]
    # a figure the item does not give still blocks
    wrong = [{**entries[0], "text": "청구서가 5일 늦게 옵니다."}]
    assert _types(notes.check(items, wrong, version="3.4.1", date="2026-11-04"), "invented_figure")


def test_an_entry_names_a_date_it_worked_out_in_derived():
    notes = _notes()
    items = notes.parse(A16_T2_LOG)
    entry = {"section": "주의", "sources": _ids(items, "공유 링크"),
             "text": "11월 4일에 만든 링크는 2026. 12. 4.에 만료됩니다."}
    plain = notes.check(items, [entry], version="3.4.1", date="2026-11-04", notes=entry["text"])
    assert _types(plain, "invented_figure") and _types(plain, "date_mismatch")
    derived = notes.check(items, [{**entry, "derived": ["2026-12-04"]}], version="3.4.1",
                          date="2026-11-04", notes=entry["text"])
    assert not _types(derived, "invented_figure") and not _types(derived, "date_mismatch")


A16_T1_LOG = "\n".join(
    [
        "- feat(chat): 메시지 예약 전송 (최대 30일 뒤)",
        "- fix(mobile): iOS 18에서 알림이 두 번 오던 문제",
        "- deprecate: 구 API v1의 /messages/list 엔드포인트 2027-03-31 종료 예정"
        " (v2 /messages 사용)",
    ]
)


def test_a_deprecation_notice_is_not_breaking_and_ios_is_not_jargon():
    notes = _notes()
    items = notes.parse(A16_T1_LOG)
    notice = next(i for i in items if "deprecate" in i["text"])
    assert notice["notice"] and not notice["breaking"] and notice["section"] == "주의"
    entries = [
        {"section": "새 기능", "sources": _ids(items, "예약"),
         "text": "메시지를 최대 30일 뒤로 예약해 보낼 수 있습니다."},
        {"section": "수정", "sources": _ids(items, "iOS"),
         "text": "iOS 18에서 알림이 두 번 오던 문제를 고쳤습니다."},
        {"section": "주의", "sources": [notice["id"]],
         "text": "구 API v1의 /messages/list 엔드포인트가 2027-03-31에 종료됩니다. "
                 "그 전에 v2 /messages로 옮겨 주세요."},
    ]
    result = notes.check(items, entries, version="3.4.0", previous_version="3.3.0",
                         date="2026-10-28")
    assert not _types(result, "version_bump"), result["findings"]
    assert not [f for f in _types(result, "jargon") if "iOS" in f["text"]]
    # still not to be left out
    dropped = notes.check(items, entries[:2], [{"id": notice["id"], "reason": "개발자용"}],
                          version="3.4.0", date="2026-10-28")
    assert _types(dropped, "breaking_omitted")
    # real code names are still jargon
    code = [{**entries[1], "text": "notifyTwice 버그를 고쳤습니다."}]
    assert _types(notes.check(items, code, version="3.4.0", date="2026-10-28"), "jargon")


# ── generic-language-killer _dash (r7 A02-T2, A12-T2) ───────────────────

_DASH = re.compile(r"[ \t]*(?:—|―)[ \t]*|!{2,}")


def _undash(line: str) -> str:
    dash = skill_scripts.module("generic-language-killer", "scan")._dash
    return _DASH.sub(lambda m: m.group(0) if dash(m) is None else dash(m), line)


def test_a_dash_after_a_label_or_quote_becomes_a_colon_not_a_comma():
    impact = "RISE 과제 배분 불확실성 확대"
    assert _undash(f"□ 영향 1 — {impact}") == f"□ 영향 1: {impact}"
    plan, steps = "○ 지역 연계 성과계획 수립 준비", "지자체 협의 창구 확보, 2027. 3. 제출 일정 역산"
    assert _undash(f"{plan} — {steps}") == f"{plan}: {steps}"
    assert _undash("2단계 — 지자체 협의") == "2단계: 지자체 협의"
    quoted = "1. #13 「AI 회의록 요약 베타, 11월 한 달 기존 고객 무료」"
    assert _undash(f"{quoted} — 기존 고객에게") == f"{quoted}: 기존 고객에게"
    # the bold is the label; the line's own dash stays
    line = "1. **#5** 회의 종료 후 5분 안에 요약·할 일 목록 발송 — 구체성. 기능의 핵심"
    assert _undash(line) == line
    # unchanged: a sentence end, a range
    assert _undash("끝났다. — 다음") == "끝났다. 다음"
    assert _undash("10—12") == "10—12"


# ── eol-message timeline (r7 A14-T2) ────────────────────────────────────

A14_T2_MATERIAL = (
    "- 일정: 2026년 11월 2일 신규 가입 중단 → 12월 31일 메모 작성·수정 중단(읽기 전용) → "
    "2027년 3월 31일 서비스 완전 종료 및 데이터 삭제\n"
    "- 내보내기: 앱 설정 > 데이터 내보내기에서 텍스트(.txt)·마크다운(.md) 파일로 저장 가능, "
    "2027년 3월 31일까지"
)


def _same_day(result: dict) -> list[dict]:
    return [f for f in result["findings"] if f["type"] == "same_day"]


def test_end_equal_to_deletion_with_an_export_window_or_as_one_step_is_not_flagged():
    timeline = skill_scripts.module("eol-message", "timeline")
    dates = {"announce": "2026-10-14", "stop_new": "2026-11-02", "end": "2027-03-31",
             "delete": "2027-03-31"}
    assert _same_day(timeline.check(dates))
    assert not _same_day(timeline.check({**dates, "export_until": "2027-03-31"}))
    assert not _same_day(timeline.check(dates, material=A14_T2_MATERIAL))
    # announce = end is still a same-day warning
    day = "2027-03-31"
    same = {**dates, "export_until": day, "announce": day, "stop_new": day}
    assert _same_day(timeline.check(same))


# ── source-check claims (r7 A11-T2) ─────────────────────────────────────

A11_T2_MATERIAL = (
    "- 설치형(온프레미스)과 클라우드형 모두 제공, 설치형은 망분리 환경 지원\n"
    "- 가격: 클라우드형 1인 월 6,000원(부가세 별도), 설치형은 100명 기준 연 1,800만원\n"
    "- 약점: 모바일 앱 평점 3.6, 외부 서비스 연동(구글·슬랙 등)이 적음, 화상회의 최대 50명"
)
A11_T2_TABLE = "\n".join(
    [
        "| 구분 | 한빛워크 | 네이버웍스 | 비고 |",
        "|---|---|---|---|",
        "| 기준 | 1인/월, 부가세 별도 | 1인/월, 세금 별도, 연간 계약 | [12] |",
        "| 메신저+기본 협업 | 6,000원 (클라우드형) | 3,000원 (Lite) "
        "| 네이버웍스 Lite는 메일·드라이브·결재 미포함 [12] |",
        "| 설치형 (온프레미스) | 100명 기준 연 1,800만원 (1인당 월 15,000원) "
        "| 공개 자료에서 확인하지 못함 | (자사 자료) |",
        "| 모바일 앱 평점 | 3.6 | 확인하지 못함 | (자사 자료) |",
    ]
)


def test_a_source_in_the_notes_column_and_material_values_with_units_are_sourced():
    claims = skill_scripts.module("source-check", "claims")
    findings = claims.check(A11_T2_TABLE, A11_T2_MATERIAL)
    assert not findings, findings
    assert "확인하지 못함 (확인 필요)" not in claims.fix(A11_T2_TABLE, findings)
    # the skill's own 「확인하지 못함」 is an open cell, even with no source in the row
    open_row = ("| 구분 | 네이버웍스 |\n|---|---|\n"
                "| 설치형 | 공개 자료에서 설치형 제공 확인하지 못함 |\n| 온프레미스 | 확인 못 함 |")
    assert not claims.check(open_row, A11_T2_MATERIAL)
    norm = claims._norm(A11_T2_MATERIAL)
    assert claims._in_material("6,000원 (클라우드형)", A11_T2_MATERIAL, norm)
    assert claims._in_material("1인/월, 부가세 별도", A11_T2_MATERIAL, norm)
    # a figure with a word the material lacks, in a row with no source, is still unsourced
    bare = "| 구분 | 한빛워크 |\n|---|---|\n| 가격 | 6,000원 최저가 보장 |"
    assert [f["kind"] for f in claims.check(bare, A11_T2_MATERIAL)] == ["unsourced_cell"]


# ── internal-comms facts (r7 A14-T2) ────────────────────────────────────


def test_a_time_range_reads_its_first_number_as_a_time():
    check = skill_scripts.module("internal-comms", "check")
    found = check.facts("문의: support@hanbitsoft.example, 평일 10~17시")
    times = [f["key"] for f in found if f["kind"] == "time"]
    assert [600, 1320] in times and [1020] in times
    assert any(f["kind"] == "time" and f["text"] == "10" for f in check.facts("평일 10-17시"))
    result = check.check("문의: support@hanbitsoft.example (평일 10:00~17:00)",
                         "- 문의: support@hanbitsoft.example, 평일 10~17시", kind="general")
    assert not [f for f in result["findings"] if f.get("type") == "unsupported"], result["findings"]
    # a duration range is not a time
    assert not [f for f in check.facts("3~4시간 걸립니다") if f["kind"] == "time"]


# ── date-calc (r7 A16-T2) ───────────────────────────────────────────────


def test_a_missing_base_date_names_the_key_or_uses_a_date_in_the_text(capsys):
    dates = skill_scripts.module("date-calc", "dates")
    # an op that needs a key names it and shows a call
    call = json.dumps({"op": "add", "days": 30})
    assert dates.main(["dates.py", call]) == 1
    error = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["error"]
    assert "date" in error and "예:" in error and "2026-11-04" in error
    result = dates.run({"op": "resolve", "expressions": ["2026-11-04 다음 달 3일"]})
    assert result["items"][0]["base_date"] == "2026-11-04"
    # nothing written: today, and the rows say so
    result = dates.run({"op": "resolve", "expressions": ["다음 달 3일"]})
    assert result["items"][0]["base_date"] == dates.today().isoformat()
    assert result["items"][0]["base_kind"] == "today"


def test_the_weekday_of_given_dates():
    # r7 A16-T2: every writer needed 「2026-11-04는 수요일」 and there was no op for it
    dates = skill_scripts.module("date-calc", "dates")
    result = dates.run({"op": "weekday", "dates": ["2026-11-04", "2026-11-20"]})
    assert [r["shown"] for r in result["items"]] == ["2026-11-04(수)", "2026-11-20(금)"]
    assert dates.run({"op": "weekday", "date": "2026-12-03"})["items"][0]["weekday"] == "목"


def test_a_kakao_moment_channel_is_measured():
    # r7 A12-T2 (QMX): 「kakao_moment」 refused the whole length call
    length = skill_scripts.module("copy-check", "length")
    row = length.measure({"channel": "kakao_moment", "field": "title", "label": "제목",
                          "text": "회의가 끝나면 요약이 도착합니다"})
    assert row["channel"] == "kakao_display" and row["limit"] == 25
    assert length.channel_key("Kakao-Talkboard") == "kakao_bizboard"
    assert length.channel_key("facebook") == "meta"
    assert all(target in length.SPECS for target in length.ALIASES.values())
    assert length.listing("kakao_moment")
