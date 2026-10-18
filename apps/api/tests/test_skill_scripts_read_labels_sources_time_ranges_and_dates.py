"""Skill-script checks on 개조식 labels, a 「비고」 source, time ranges and a missing base_date."""

from __future__ import annotations

import json
import re

from app.services import skill_scripts

# ── generic-language-killer _dash ───────────────────────────────────────

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


# ── source-check claims ─────────────────────────────────────────────────

PRODUCT_MATERIAL = (
    "- 설치형(온프레미스)과 클라우드형 모두 제공, 설치형은 망분리 환경 지원\n"
    "- 가격: 클라우드형 1인 월 6,000원(부가세 별도), 설치형은 100명 기준 연 1,800만원\n"
    "- 약점: 모바일 앱 평점 3.6, 외부 서비스 연동(구글·슬랙 등)이 적음, 화상회의 최대 50명"
)
PRICE_COMPARISON_TABLE = "\n".join(
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
    findings = claims.check(PRICE_COMPARISON_TABLE, PRODUCT_MATERIAL)
    assert not findings, findings
    assert "확인하지 못함 (확인 필요)" not in claims.fix(PRICE_COMPARISON_TABLE, findings)
    # the skill's own 「확인하지 못함」 is an open cell, even with no source in the row
    open_row = ("| 구분 | 네이버웍스 |\n|---|---|\n"
                "| 설치형 | 공개 자료에서 설치형 제공 확인하지 못함 |\n| 온프레미스 | 확인 못 함 |")
    assert not claims.check(open_row, PRODUCT_MATERIAL)
    norm = claims._norm(PRODUCT_MATERIAL)
    assert claims._in_material("6,000원 (클라우드형)", PRODUCT_MATERIAL, norm)
    assert claims._in_material("1인/월, 부가세 별도", PRODUCT_MATERIAL, norm)
    # a figure with a word the material lacks, in a row with no source, is still unsourced
    bare = "| 구분 | 한빛워크 |\n|---|---|\n| 가격 | 6,000원 최저가 보장 |"
    assert [f["kind"] for f in claims.check(bare, PRODUCT_MATERIAL)] == ["unsourced_cell"]


# ── internal-comms facts ────────────────────────────────────────────────


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


# ── date-calc ───────────────────────────────────────────────────────────


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
    # 「2026-11-04는 수요일」: a weekday check every writer needs
    dates = skill_scripts.module("date-calc", "dates")
    result = dates.run({"op": "weekday", "dates": ["2026-11-04", "2026-11-20"]})
    assert [r["shown"] for r in result["items"]] == ["2026-11-04(수)", "2026-11-20(금)"]
    assert dates.run({"op": "weekday", "date": "2026-12-03"})["items"][0]["weekday"] == "목"


def test_a_kakao_moment_channel_is_measured():
    # 「kakao_moment」 refused the whole length call
    length = skill_scripts.module("copy-check", "length")
    row = length.measure({"channel": "kakao_moment", "field": "title", "label": "제목",
                          "text": "회의가 끝나면 요약이 도착합니다"})
    assert row["channel"] == "kakao_display" and row["limit"] == 25
    assert length.channel_key("Kakao-Talkboard") == "kakao_bizboard"
    assert length.channel_key("facebook") == "meta"
    assert all(target in length.SPECS for target in length.ALIASES.values())
    assert length.listing("kakao_moment")
