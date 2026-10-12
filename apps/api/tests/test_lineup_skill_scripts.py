"""date-calc, quote-calc and text-fit: the lineup's acceptance numbers, run as the sandbox runs
them and as the server imports them."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services import doc_formats, skill_packages, skill_scripts

SKILLS = Path(__file__).resolve().parents[1] / "app" / "skills"
dates = skill_scripts.dates()
quotes = skill_scripts.quotes()
fit = skill_scripts.text_fit()


def _run(key: str, script: str, args: dict, cwd: Path) -> dict:
    done = subprocess.run(
        [sys.executable, str(SKILLS / key / "scripts" / f"{script}.py"),
         json.dumps(args, ensure_ascii=False)],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr + done.stdout
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_three_packages_ship_with_their_scripts():
    packages = skill_packages.packages()
    assert packages["date-calc"].scripts.keys() == {"dates"}
    assert packages["quote-calc"].scripts.keys() == {"quote"}
    assert packages["text-fit"].scripts.keys() == {"fit"}
    for key in ("date-calc", "quote-calc", "text-fit"):
        assert packages[key].required_tools == ("run_skill_script",)
    assert "월력요항" in packages["date-calc"].body


# ── date-calc ───────────────────────────────────────────────────────────

#: Meeting-memo date phrases: base, phrase, expected date.
MINUTES = [
    ("2026-10-12", "다음 주 금요일", "2026-10-23"),
    ("2026-10-12", "이번 주 금요일", "2026-10-16"),
    ("2026-10-14", "다음 주 수요일", "2026-10-21"),
    ("2026-10-21", "다음 주 금요일", "2026-10-30"),
    ("2026-10-21", "모레", "2026-10-23"),
    ("2026-10-08", "다음 주 금요일", "2026-10-16"),
    ("2026-10-08", "이번 달 말", "2026-10-31"),
    ("2026-10-08", "2주 뒤", "2026-10-22"),
    ("2026-10-08", "모레", "2026-10-10"),
    ("2026-10-07", "다음 주 월요일", "2026-10-12"),
    ("2026-10-20", "다음 주 수요일", "2026-10-28"),
]


@pytest.mark.parametrize(("base", "phrase", "want"), MINUTES)
def test_relative_dates_of_the_minutes_tasks(base, phrase, want):
    row = dates.resolve(phrase, dates.parse_day(base))
    assert row["status"] == "ok" and row["date"] == want


def test_weekends_are_marked_not_moved_and_repeats_list_their_dates():
    saturday = dates.resolve("모레", dt.date(2026, 10, 8))
    assert saturday["date"] == "2026-10-10" and saturday["weekend"] is True
    weekly = dates.resolve("다음 주부터 매주 월요일", dt.date(2026, 10, 14))
    assert weekly["status"] == "repeat" and weekly["first_date"] == "2026-10-19"
    assert weekly["dates"][:2] == ["2026-10-19", "2026-10-26"]
    biweekly = dates.resolve("격주 화요일", dt.date(2026, 10, 14))
    assert biweekly["repeat"] == "biweekly" and biweekly["dates"][1] == "2026-11-03"


def test_ambiguous_and_unheard_phrases_are_not_guessed():
    base = dt.date(2026, 10, 14)
    assert dates.resolve("(잡음) …까지", base)["status"] == "unclear"
    for phrase in ("다음 주까지", "다음 월요일", "다음 달 초", "추후", "다음 회의 때"):
        row = dates.resolve(phrase, base)
        assert row["status"] == "unresolved" and "date" not in row, phrase


def test_month_and_quarter_ends_offsets_and_bare_days():
    base = dt.date(2026, 10, 14)
    assert dates.resolve("다음 달 말", base)["date"] == "2026-11-30"
    assert dates.resolve("분기 말", base)["date"] == "2026-12-31"
    assert dates.resolve("다음 분기 말", base)["date"] == "2027-03-31"
    assert dates.resolve("3일 후", base)["date"] == "2026-10-17"
    assert dates.resolve("일주일 전", base)["date"] == "2026-10-07"
    assert dates.resolve("1월 15일", dt.date(2026, 10, 8))["date"] == "2027-01-15"
    bare = dates.resolve("월요일까지", dt.date(2026, 10, 8))
    assert bare["date"] == "2026-10-12" and bare["assumed"] is True


def test_holidays_and_business_days():
    assert dates.is_holiday(dt.date(2026, 10, 5)) == "대체공휴일(개천절)"
    assert dates.is_holiday(dt.date(2027, 2, 9)) == "대체공휴일(설날)"
    assert dates.is_holiday(dt.date(2026, 7, 17)) == "제헌절"
    # 10/1 (Thu) – 10/12 (Mon): weekends, 10/3 (Sat), 10/5 substitute and 10/9 off.
    assert dates.business_days(dt.date(2026, 10, 1), dt.date(2026, 10, 12)) == 6
    assert dates.add_business_days(dt.date(2026, 9, 30), 5) == dt.date(2026, 10, 8)
    row = dates.resolve("다음 주 금요일", dt.date(2026, 9, 30))
    assert row["date"] == "2026-10-09" and row["holiday"] == "한글날"


def test_a_mail_line_is_read_from_its_sent_date():
    material = (
        "[2차 회의 메모 — 2026-10-07(수) 16:00, 인문관 410호]\n"
        "- 포스터 공고 게시 → 류○○, 다음 주 월요일까지\n"
        "[메일 — 2026-10-20(화) 09:30, 정교수 → TF 전원] 의견을 다음 주 수요일까지 보내 주세요.\n"
        "[메일 3 — 10/14(수), 배교수] 다과 업체는 하○○ 님이, 10/23(금)까지.\n"
    )
    rows = {(r["line"], r["text"]): r for r in dates.scan(material)}
    assert rows[(2, "다음 주 월요일")]["date"] == "2026-10-12"
    mail = rows[(3, "다음 주 수요일")]
    assert mail["date"] == "2026-10-28" and mail["base_kind"] == "mail"
    assert rows[(4, "10/23")]["date"] == "2026-10-23"


def test_career_spans_count_both_ends_and_overlaps_once():
    messy = dates.span([
        {"label": "브랜드웨이브", "start": "2019.04", "end": "2021.01"},
        {"label": "계약직", "start": "2021.10", "end": "2022.03"},
        {"label": "리브랩", "start": "2022.04", "end": "2026.09"},
        {"label": "프리랜서", "start": "2023.05", "end": "2023.11"},
    ], "2026-10")
    assert [p["months"] for p in messy["periods"]] == [22, 6, 54, 7]
    assert messy["total_months"] == 82 and messy["total_text"] == "6년 10개월"
    assert messy["gaps"] == [{"start": "2021-02", "end": "2021-09", "months": 8}]
    ongoing = dates.span([{"start": "2020.07", "end": "2022.12"},
                          {"start": "2023.03", "end": "현재"}], "2026-10")
    assert ongoing["total_months"] == 74 and ongoing["periods"][1]["months"] == 44
    two = dates.span([{"start": "2019-03", "end": "2022-08"},
                      {"start": "2022-09", "end": "2026-08"}])
    assert two["total_months"] == 90 and two["gaps"] == []


def test_delivery_weeks_against_the_deadline():
    due = {w: dates.add("2026-10-19", weeks=w, deadline="2026-12-18") for w in (8, 10, 7)}
    assert {w: r["date"] for w, r in due.items()} == {
        8: "2026-12-14", 10: "2026-12-28", 7: "2026-12-07"}
    assert [due[w]["within"] for w in (8, 10, 7)] == [True, False, True]


def test_date_calc_runs_standalone(tmp_path):
    args = {"op": "resolve", "base_date": "2026-10-12",
            "expressions": [{"text": "다음 주 금요일", "source_line": "x"}]}
    out = _run("date-calc", "dates", args, tmp_path)
    assert out["items"][0]["shown"] == "2026-10-23(금)" and out["items"][0]["source_line"] == "x"


# ── quote-calc ──────────────────────────────────────────────────────────

P1 = {"items": [
    {"name": "PM", "mm": 1.0, "monthly_price": 9_000_000},
    {"name": "AI 엔지니어", "mm": 3.0, "monthly_price": 8_000_000},
    {"name": "데이터 엔지니어", "mm": 1.5, "monthly_price": 7_000_000},
    {"name": "GPU", "qty": 3, "unit": "개월", "unit_price": 1_200_000},
], "budget": {"amount": 48_000_000, "vat_basis": "excl"}}


def test_proposal_1_total_and_headroom():
    result = quotes.run(P1)
    assert (result["supply"], result["vat"], result["total"]) == (47_100_000, 4_710_000, 51_810_000)
    assert result["budget"]["headroom"] == 900_000 and result["budget"]["within"]
    assert result["total_official"] == doc_formats.official_amount(51_810_000)
    assert "| 합계(VAT 포함) |  |  |  | 51,810,000 |" in result["table_markdown"]


def test_proposal_2_per_head_rounds_half_up():
    result = quotes.run({"items": [
        {"name": "강사", "qty": [4, 16], "unit_price": 200_000},
        {"name": "보조", "qty": [4, 16], "unit_price": 50_000},
        {"name": "교재", "qty": 120, "unit_price": 20_000},
    ], "per_head": {"n": 120}})
    assert result["supply"] == 18_400_000 and result["total"] == 20_240_000
    assert result["per_head"] == {"n": 120, "excl": 153_333, "incl": 168_667}


def test_proposal_3_scenarios_against_a_vat_included_budget():
    result = quotes.run({
        "items": [{"name": "PM", "mm": 5, "monthly_price": 7_000_000},
                  {"name": "퍼블리싱", "qty": [2, 4], "unit_price": 5_500_000},
                  {"name": "진단", "qty": 1, "unit_price": 6_000_000},
                  {"name": "인증 수수료", "amount": 3_300_000, "excluded": True}],
        "budget": {"amount": 99_000_000, "vat_basis": "incl"},
        "scenarios": [
            {"name": "6개월", "premise": "PM 6MM", "overrides": [{"name": "PM", "mm": 6}]},
            {"name": "수수료 포함", "overrides": [{"name": "인증 수수료", "excluded": False}]},
        ],
    })
    assert result["total"] == 93_500_000 and result["budget"]["headroom"] == 5_500_000
    by_name = {s["name"]: s for s in result["scenarios"]}
    assert by_name["6개월"]["total"] == 101_200_000
    assert by_name["6개월"]["budget"]["headroom"] == -2_200_000
    assert by_name["수수료 포함"]["total"] == 97_130_000


def test_proposal_4_and_5_totals():
    def p4(stores, app, pm):
        items = [{"name": "매장", "qty": stores, "unit_price": 600_000},
                 {"name": "대시보드", "amount": 18_000_000},
                 {"name": "PM", "qty": pm, "unit_price": 6_000_000},
                 {"name": "교육", "amount": 1_200_000}]
        if app:
            items.append({"name": "앱", "amount": 9_000_000})
        return quotes.run({"items": items, "budget": {"amount": 45_000_000, "vat_basis": "excl"}})

    assert p4(40, True, 3)["supply"] == 70_200_000
    v2 = p4(25, False, 3)
    assert v2["supply"] == 52_200_000 and v2["budget"]["headroom"] == -7_200_000
    assert p4(15, False, 2)["supply"] == 40_200_000
    p5 = quotes.run({"items": [
        {"name": "PM(특급)", "mm": 7, "monthly_price": 10_500_000},
        {"name": "AI(고급)", "mm": 12, "monthly_price": 8_800_000},
        {"name": "개발(중급)", "mm": 8, "monthly_price": 6_900_000},
        {"name": "개발(초급)", "mm": 4, "monthly_price": 5_200_000},
        {"name": "라이선스", "amount": 22_000_000},
        {"name": "GPU", "qty": 7, "unit_price": 1_800_000},
        {"name": "해외 API", "qty": 7, "unit_price": 900_000, "excluded": True},
    ], "budget": {"amount": 330_000_000, "vat_basis": "incl"}})
    assert p5["total"] == 318_670_000 and p5["budget"]["headroom"] == 11_330_000
    assert sum(line["amount"] for line in p5["lines"][:4]) == 255_100_000


def test_vendor_quotes_on_one_basis():
    result = quotes.run({"quotes": [
        {"vendor": "A사", "amount": 45_650_000, "vat_basis": "incl"},
        {"vendor": "B사", "amount": 41_800_000, "vat_basis": "excl"},
        {"vendor": "C사", "amount": 44_000_000, "vat_basis": "excl"},
    ], "budget": {"amount": 46_000_000, "vat_basis": "incl"}})
    rows = {r["vendor"]: r for r in result["quotes"]}
    assert (rows["A사"]["excl"], rows["B사"]["incl"], rows["C사"]["incl"]) == (
        41_500_000, 45_980_000, 48_400_000)
    assert [rows[v]["within_budget"] for v in ("A사", "B사", "C사")] == [True, True, False]


def test_vat_rules_discount_and_ambiguous_budget():
    included = quotes.run({"vat_basis": "incl", "items": [{"name": "x", "amount": 100_000}]})
    assert (included["supply"], included["vat"]) == (90_909, 9_091)
    floor = quotes.run({"items": [{"name": "x", "amount": 12_345}]})
    assert floor["vat"] == 1_234  # 1,234.5 cut below one won
    discounted = quotes.run({"items": [{"name": "x", "amount": 1_000_000}],
                             "discount": {"rate": 10}, "truncate_to": 1000})
    assert discounted["supply"] == 900_000 and discounted["total"] == 990_000
    free = quotes.run({"items": [{"name": "도서", "amount": 50_000}], "vat": False})
    assert free["vat"] == 0 and free["total"] == 50_000
    open_basis = quotes.run({"items": [{"name": "x", "amount": 1_000_000}],
                             "budget": {"amount": 1_050_000}})
    assert open_basis["budget"]["vat_basis"] == "ambiguous"
    budget = open_basis["budget"]
    assert budget["if_excl"]["within"] and not budget["if_incl"]["within"]


def test_quote_calc_runs_standalone(tmp_path):
    assert _run("quote-calc", "quote", P1, tmp_path)["total"] == 51_810_000


# ── text-fit ────────────────────────────────────────────────────────────

MIXED = [
    "데이터로 말합니다.",
    "SQL·Python으로 1만 2천 건을 정리했다.",
    "잔반 412kg → 338kg (약 18%)",
    "줄바꿈\n두 번째 줄",
    "“근거”로 말하는 사람 — 끝까지 확인!",
    "MAE 0.42 → 0.37 kWh 😀",
    "전각（괄호）와 ＡＢＣ",
    "탭\t공백  두 칸",
    "漢字 혼용 문장",
    "Windows 줄\r\n바꿈",
]


@pytest.mark.parametrize("text", MIXED)
def test_counts_match_python(text):
    plain = text.replace("\r\n", "\n")
    assert fit.measure(text, "chars_with_spaces") == len(plain)
    assert fit.measure(text, "chars_no_spaces") == len("".join(plain.split()))
    breaks = plain.count("\n")
    assert fit.measure(text, "bytes_utf8", 1) == len(plain.encode("utf-8"))
    assert fit.measure(text, "bytes_utf8", 2) == len(plain.encode("utf-8")) + breaks
    wide = sum(1 for ch in plain.replace("\n", "") if fit._wide(ch))
    assert fit.measure(text, "bytes_ko2", 2) == len(plain.replace("\n", "")) + wide + 2 * breaks


def test_ko2_counts_hangul_and_fullwidth_two_and_the_rest_one():
    assert fit.measure("가A1 ·→", "bytes_ko2") == 2 + 1 + 1 + 1 + 1 + 1
    assert fit.measure("（Ａ）漢", "bytes_ko2") == 8
    assert fit.measure("가\n나", "bytes_ko2", 2) == 6
    assert fit.measure("·→", "bytes_utf8") == 5


def test_limits_floor_and_status():
    row = fit.fit({"label": "문항 1", "text": "가" * 692, "limit": 700})
    assert row["status"] == "ok" and row["min"] == 560
    assert fit.shown(row) == "공백 포함 692자 / 700자"
    short = fit.fit({"text": "가" * 800, "limit": 1000, "min_ratio": 0.9})
    assert short["status"] == "under" and short["under"] == 100 and short["min"] == 900
    over = fit.fit({"text": "가" * 501, "limit": 1000, "unit": "bytes_ko2"})
    assert over["status"] == "over" and over["over"] == 2
    titled = fit.fit({"text": "1. 지원 동기\n본문", "limit": 700, "skip_first_line": True})
    assert titled["value"] == 2


def test_text_fit_runs_standalone(tmp_path):
    out = _run("text-fit", "fit", {"items": [{"label": "a", "text": "가나 다", "limit": 100,
                                              "unit": "chars_no_spaces"}]}, tmp_path)
    assert out["items"][0]["value"] == 3 and out["under"] == 1
