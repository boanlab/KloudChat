"""시장·경쟁 스킬(초안): 시장 규모는 코드가 계산하고, 조사 글·비교표·분석 틀은
출처 없는 값과 근거 없는 일반론을 코드가 찾는다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import skill_packages, skill_scripts

SKILLS = Path(skill_packages.__file__).resolve().parent.parent / "skills"
MARKET_SKILLS = (
    "company-research",
    "competitor-analysis",
    "competitive-battlecard",
    "market-sizing",
    "industry-analysis",
    "market-segments",
)
ALL = (*MARKET_SKILLS, "source-check")

size = skill_scripts.module("market-sizing", "size")
sources = skill_scripts.module("source-check", "sources")
claims = skill_scripts.module("source-check", "claims")
frame = skill_scripts.module("source-check", "frame")


def _cli(key: str, script: str, args: dict) -> dict:
    done = subprocess.run(
        [
            sys.executable,
            str(SKILLS / key / "scripts" / f"{script}.py"),
            json.dumps(args, ensure_ascii=False),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


# ── packages ────────────────────────────────────────────────────────────


def test_the_market_skills_ship_as_drafts_with_their_source():
    packages = skill_packages.packages()
    for key in ALL:
        package = packages[key]
        assert package.draft, key
        # The research skills search when the person did not hand them a material pack.
        searches = key in ("company-research", "competitor-analysis", "competitive-battlecard")
        assert package.required_tools == (("run_skill_script", "web_search", "fetch_url")
                                          if searches else ("run_skill_script",)), key
        # source-check also guides a report writer on sourcing
        assert package.kinds == (("chat", "report") if key == "source-check" else ("chat",)), key
        assert "출처:" in package.body, key
        # Korean front matter, English instructions.
        assert any("가" <= ch <= "힣" for ch in package.name), key
    assert set(packages["source-check"].scripts) == {"sources", "claims", "frame", "post"}
    assert set(packages["market-sizing"].scripts) == {"size"}


def test_merged_skills_keep_the_original_names_as_aliases():
    packages = skill_packages.packages()
    assert "tam-sam-som-calculator" in packages["market-sizing"].when_to_use
    for alias in ("pestel-analysis", "porters-five-forces"):
        assert alias in packages["industry-analysis"].when_to_use
        assert alias in packages["industry-analysis"].body


def test_borrowed_text_names_its_licence_and_commit():
    packages = skill_packages.packages()
    for key in (
        "market-sizing",
        "competitor-analysis",
        "competitive-battlecard",
        "market-segments",
    ):
        body = packages[key].body
        assert "phuryn/pm-skills" in body and "MIT" in body, key
        assert "8607e3b077817f89bf4a9b623246219734ac3be0" in body, key
    # The CC BY-NC-SA originals are named but not used.
    assert "CC BY-NC-SA" in packages["company-research"].body


def test_market_drafts_stay_out_unless_enabled(monkeypatch):
    monkeypatch.setattr(settings, "skill_drafts", False)
    assert not {spec["key"] for spec in skill_packages.catalog_specs()} & set(ALL)
    assert skill_packages.find("source-check") is None
    monkeypatch.setattr(settings, "skill_drafts", True)
    assert {"source-check", "market-sizing"} <= {p.key for p in skill_packages.scripted()}
    assert skill_packages.find("시장 규모 추정").key == "market-sizing"


# ── market-sizing: size ─────────────────────────────────────────────────

SOURCED = {"source": "○○협회 2025 시장 보고서", "date": "2025-03-10"}


def _example(**over) -> dict:
    args = {
        "market": "국내 중소기업 HR SaaS",
        "year": 2026,
        "unit": "억원",
        "top_down": {
            "tam": [
                {
                    "label": "국내 HR SaaS 시장",
                    "value": 5200,
                    "unit": "억원",
                    "year": 2024,
                    **SOURCED,
                }
            ],
            "growth": {"label": "연평균 성장률", "value": 8, "unit": "%", **SOURCED},
            "sam": [
                {
                    "label": "중소기업 비중",
                    "value": 40,
                    "unit": "%",
                    "assumption": True,
                    "basis": "KOSIS",
                }
            ],
            "som": [
                {
                    "label": "점유율",
                    "value": 3,
                    "unit": "%",
                    "assumption": True,
                    "basis": "영업 5명",
                }
            ],
        },
    }
    args.update(over)
    return args


def test_top_down_grows_the_base_year_and_narrows_to_sam_and_som():
    result = size.run(_example())
    td = result["results"]["하향식"]
    # 5200 × 1.08² = 6065.28 → 6,065.3; × 40% = 2,426.1; × 3% = 72.8
    assert (td["tam"], td["sam"], td["som"]) == ("6,065.3", "2,426.1", "72.8")
    assert td["som_share_of_sam_pct"] == "3"
    assert result["ok"] and not result["issues"]
    assert "(1+8%)^2(2024→2026)" in result["formulas"][0]
    statuses = {row["label"]: row["status"] for row in result["assumptions"]}
    assert statuses == {
        "국내 HR SaaS 시장": "출처",
        "연평균 성장률": "출처",
        "중소기업 비중": "가정",
        "점유율": "가정",
    }


def test_bottom_up_normalises_units_and_currency():
    result = size.run(
        {
            "year": 2026,
            "unit": "억원",
            "fx": {"USD": {"rate": 1400, "source": "한국은행 ECOS", "date": "2026-09-30"}},
            "bottom_up": {
                "tam": [
                    {
                        "label": "대상 사업체 수",
                        "value": 12,
                        "unit": "만개",
                        "source": "KOSIS",
                        "date": "2025-12",
                    },
                    {
                        "label": "연 객단가",
                        "value": 300,
                        "unit": "USD",
                        "assumption": True,
                        "basis": "해외 요금",
                    },
                ],
                "sam": [
                    {
                        "label": "수도권",
                        "value": 50,
                        "unit": "%",
                        "source": "KOSIS",
                        "date": "2025-12",
                    }
                ],
                "som": {
                    "direct": [
                        {
                            "label": "3년 차 고객 수",
                            "value": 900,
                            "unit": "개",
                            "assumption": True,
                            "basis": "월 25곳",
                        },
                        {
                            "label": "연 객단가",
                            "value": 42,
                            "unit": "만원",
                            "assumption": True,
                            "basis": "300달러×1,400원",
                        },
                    ]
                },
            },
        }
    )
    bu = result["results"]["상향식"]
    # 120,000 × 300 × 1,400 = 504억원; × 50% = 252억원; 900 × 42만원 = 3.78억원
    assert (bu["tam"], bu["sam"], bu["som"]) == ("504", "252", "3.8")
    assert "환율 1,400원/달러" in result["formulas"][0]
    assert any(row["label"] == "환율(USD)" for row in result["assumptions"])


def test_parse_unit_reads_scales_and_currencies():
    assert size.parse_unit("억원")["scale"] == 10**8
    usd = size.parse_unit("USD million")
    assert usd["currency"] == "USD" and usd["scale"] == 10**6
    assert size.parse_unit("백만달러")["scale"] == 10**6
    assert size.parse_unit("%")["kind"] == "rate"
    count = size.parse_unit("만명")
    assert count["kind"] == "count" and count["scale"] == 10**4 and count["label"] == "명"


def test_unsourced_and_basisless_inputs_are_flagged_not_hidden():
    args = _example()
    args["top_down"]["tam"][0] = {"label": "시장", "value": 5200, "unit": "억원", "year": 2026}
    args["top_down"]["som"][0] = {"label": "점유율", "value": 3, "unit": "%", "assumption": True}
    result = size.run(args)
    kinds = {issue["kind"] for issue in result["issues"]}
    assert {"unsourced", "assumption_without_basis"} <= kinds
    assert not result["ok"]
    assert "출처 미확인" in result["assumptions_markdown"]


def test_a_different_data_year_without_growth_is_reported():
    args = _example()
    del args["top_down"]["growth"]
    result = size.run(args)
    assert any(issue["kind"] == "year_mismatch" for issue in result["issues"])
    assert result["results"]["하향식"]["tam"] == "5,200"


def test_old_sources_are_stale():
    args = _example()
    args["top_down"]["tam"][0]["date"] = "2019-05"
    args["top_down"]["tam"][0]["year"] = 2019
    kinds = {issue["kind"] for issue in size.run(args)["issues"]}
    assert "stale" in kinds


def test_tam_sam_som_order_and_rates_are_checked():
    args = _example()
    args["top_down"]["sam"][0]["value"] = 140
    result = size.run(args)
    kinds = {issue["kind"] for issue in result["issues"]}
    assert {"rate_out_of_range", "order"} <= kinds
    assert sum(issue["kind"] == "rate_out_of_range" for issue in result["issues"]) == 1
    assert not result["ok"]


def test_two_approaches_far_apart_are_flagged():
    args = _example(
        bottom_up={
            "tam": [
                {"label": "사업체 수", "value": 1000, "unit": "개", **SOURCED},
                {"label": "객단가", "value": 1000, "unit": "만원", **SOURCED},
            ]
        }
    )
    result = size.run(args)
    assert any(issue["kind"] == "approach_gap" for issue in result["issues"])


def test_sensitivity_brackets_the_base_result():
    result = size.run(_example(sensitivity={"pct": 10}))
    rows = {row["label"]: row for row in result["sensitivity"]}
    assert rows["점유율"]["result_low"] == "65.5" and rows["점유율"]["result_high"] == "80.1"
    assert set(rows) == {"중소기업 비중", "점유율", "불확실한 값 모두"}
    assert "민감도" not in result["sensitivity_markdown"]  # the header is the CLI's
    assert result["sensitivity_markdown"].startswith("| 방식 |")


def test_money_times_money_is_an_error_and_the_cli_reports_it():
    out = _cli(
        "market-sizing",
        "size",
        {
            "top_down": {
                "tam": [
                    {"label": "a", "value": 1, "unit": "억원", **SOURCED},
                    {"label": "b", "value": 2, "unit": "만원", **SOURCED},
                ]
            }
        },
    )
    assert out["ok"] is False and "금액끼리" in out["error"]
    assert _cli("market-sizing", "size", _example())["results"]["하향식"]["som"] == "72.8"


# ── source-check: sources ───────────────────────────────────────────────

REPORT = """## 시장 개요
국내 HR SaaS 시장은 5,200억원 규모다 [1].
시장은 연 8% 성장한다.
중소기업 비중은 40%로 가정했다(KOSIS 비중 참고).
경쟁이 치열하다.
3개월 안에 출시하는 1단계 목표.
자사 고객은 320곳이다.
| 항목 | 값 | 출처 |
|---|---|---|
| 점유율 | 12% | 협회 보고서 2025 |
| 매출 | 80억원 | |

## 출처
[1] ○○협회, 「HR SaaS 시장 보고서」, 2019-03, https://example.org/r
[2] 무명 블로그
"""


def test_sources_finds_unsourced_numbers_generic_lines_and_weak_references():
    findings = sources.check(REPORT, "자사 고객 320곳", {"today": "2026-10-08"})
    by_kind: dict[str, list] = {}
    for f in findings:
        by_kind.setdefault(f["kind"], []).append(f)
    assert [f["text"] for f in by_kind["unsourced"]] == ["8%", "80억원"]
    assert [f["text"] for f in by_kind["generic"]] == ["경쟁이 치열"]
    assert {f["text"] for f in by_kind["stale"]} == {"[1]"}
    assert {f["text"] for f in by_kind["ref_no_date"]} == {"[2]"}
    # 3개월·1단계 are not business quantities; 320곳 is in the material; 40% is marked 가정.
    texts = {f["text"] for f in findings}
    assert not texts & {"3개", "40%", "320곳", "12%"}


def test_sources_fix_marks_lines_and_table_cells():
    result = sources.run(
        {"text": REPORT, "material": "자사 고객 320곳", "today": "2026-10-08", "fix": True}
    )
    fixed = result["fixed_text"].split("\n")
    assert fixed[2].endswith("「출처 미확인」")
    assert "80억원 | 「출처 미확인」 |" in result["fixed_text"]
    assert not result["ok"]
    again = sources.check(result["fixed_text"], "자사 고객 320곳", {"today": "2026-10-08"})
    assert not [f for f in again if f["kind"] == "unsourced"]


def test_an_undefined_citation_blocks():
    result = sources.run(
        {"text": "점유율은 12%다 [3].\n\n## 출처\n[1] KOSIS, 2026-01, https://kosis.kr"}
    )
    assert [f["kind"] for f in result["findings"]] == ["undefined_ref"]
    assert not result["ok"]


def test_a_generic_line_with_evidence_passes():
    text = (
        "경쟁이 치열하다: 상위 3사 요금이 월 9,900~12,000원으로 붙어 있다 [1].\n\n"
        "## 출처\n[1] 각사 요금 페이지, 2026-09-01, https://example.com/pricing"
    )
    assert sources.check(text, "", {"today": "2026-10-08"}) == []


# ── source-check: claims ────────────────────────────────────────────────

CARD = """## 기능 비교
| 기능 | 우리(노트핏) | 경쟁사 A | 우위 | 출처 |
|---|---|---|---|---|
| 가격 | 월 12,000원 | 월 9,900원 [2] | 경쟁사 | |
| 화자 구분 | 지원 (자사 자료) | 우수 | 우리 | |
| 보안 | 국내 리전 (자사 자료) | 확인 필요 | 우리 | |
| 연동 | 슬랙 (자사 자료) | | 동등 | 공식 문서 [3] |

## 우리가 이기는 곳
- 한국어 화자 구분이 정확하다
- 국내 리전 저장 (자사 자료)
- 녹음 보관 기간을 물어보셨나요?
"""


def test_claims_flags_vague_empty_unsourced_cells_and_early_verdicts():
    findings = claims.check(CARD, "요금 1인 월 12,000원", {})
    kinds = [(f["kind"], f["row"]) for f in findings]
    assert ("vague_cell", "화자 구분") in kinds
    assert ("verdict_without_evidence", "화자 구분") in kinds
    assert ("verdict_without_evidence", "보안") in kinds
    assert ("empty_cell", "연동") in kinds
    assert ("unsourced_bullet", "") in kinds
    # 12,000원 is in the material; 9,900원 is cited; 동등 is not a verdict to hold.
    assert ("unsourced_cell", "가격") not in kinds
    assert ("verdict_without_evidence", "가격") not in kinds
    assert not any("물어보셨나요" in f["text"] for f in findings)


def test_claims_fix_holds_verdicts_and_marks_cells():
    result = claims.run({"text": CARD, "material": "요금 1인 월 12,000원", "fix": True})
    fixed = result["fixed_text"]
    assert "| 화자 구분 | 지원 (자사 자료) | 우수 (확인 필요) | 판단 보류 |" in fixed
    assert "| 연동 | 슬랙 (자사 자료) | 자료 없음 | 동등 |" in fixed
    assert "한국어 화자 구분이 정확하다 (확인 필요)" in fixed
    again = claims.check(fixed, "요금 1인 월 12,000원", {})
    assert not again


# ── source-check: frame ─────────────────────────────────────────────────


def _item(claim, evidence, **extra):
    return {
        "claim": claim,
        "evidence": evidence,
        "source": "농림축산검역본부 고시",
        "date": "2026-03-02",
        **extra,
    }


def test_pestel_requires_every_factor_and_rejects_generic_filler():
    result = frame.run(
        {
            "framework": "pestel",
            "today": "2026-10-08",
            "factors": {
                "정치": [_item("수입 원료 조달 기간 증가", "검역 기간 5일→10일", impact="위협")],
                "economic": [{"claim": "경쟁이 치열하다"}],
                "사회": [{"status": "자료 없음", "to_find": "KOSIS 반려동물 양육 가구"}],
            },
        }
    )
    kinds = {(f["kind"], f["where"]) for f in result["findings"]}
    assert ("generic", "경제") in kinds and ("unsourced", "경제") in kinds
    assert {("missing_factor", name) for name in ("기술", "환경", "법률")} <= kinds
    assert not any(where == "사회" for _, where in kinds)
    assert not result["ok"]
    assert "| 사회 | 자료 없음 | 확인할 자료: KOSIS 반려동물 양육 가구 |" in result["markdown"]


def test_a_complete_pestel_passes():
    factors = {
        name: [_item(f"{name} 판단", "고시 제2026-12호 검역 기간 10일", impact="중립")]
        for name in frame.PESTEL
    }
    result = frame.run({"framework": "pestle", "today": "2026-10-08", "factors": factors})
    assert result["ok"], result["findings"]
    assert result["markdown"].count("\n") == 7


def test_five_forces_rating_needs_a_sourced_item():
    result = frame.run(
        {
            "framework": "porters_five_forces",
            "forces": {
                "rivalry": {
                    "rating": "높음",
                    "items": [_item("상위 3사 가격 근접", "월 9,900~12,000원")],
                },
                "공급자": {"rating": "높음", "items": [{"claim": "클라우드 의존"}]},
                "대체재": {"status": "자료 없음", "to_find": "수기 회의록 비중 설문"},
            },
        }
    )
    kinds = {(f["kind"], f["where"]) for f in result["findings"]}
    assert ("rating_without_evidence", "공급자 교섭력") in kinds
    assert not any(kind == "bad_rating" for kind, _ in kinds)
    assert "| 기존 경쟁 | 높음 |" in result["markdown"]
    assert "| 공급자 교섭력 | 판단 보류 |" in result["markdown"]
    assert "| 대체재 위협 | 판단 보류 |" in result["markdown"]


def test_segments_need_criteria_size_and_evidence():
    seg = {
        "name": "중견 제조",
        "criteria": "직원 50~299명 제조업",
        "size": {
            "value": 12000,
            "unit": "개사",
            "source": "KOSIS 전국사업체조사",
            "date": "2025-12",
        },
        "needs": [
            {
                "claim": "근태 자동화",
                "evidence": "인터뷰 8곳 중 6곳 수기 근태",
                "source": "사용자 자료",
            }
        ],
        "pains": [{"status": "자료 없음", "to_find": "VOC"}],
        "alternatives": [
            {"claim": "엑셀", "evidence": "인터뷰 8곳 중 5곳", "source": "사용자 자료"}
        ],
    }
    ok = frame.run(
        {
            "framework": "segments",
            "today": "2026-10-08",
            "segments": [seg, {**seg, "name": "B"}, {**seg, "name": "C"}],
        }
    )
    assert ok["ok"], ok["findings"]
    assert "| 중견 제조 | 직원 50~299명 제조업 | 12,000개사 |" in ok["markdown"]
    bad = frame.run({"framework": "segments", "segments": [seg, {"name": "B"}]})
    kinds = {(f["kind"], f["where"]) for f in bad["findings"]}
    assert {
        ("segment_count", "segments"),
        ("no_criteria", "B"),
        ("no_size", "B"),
        ("missing_factor", "B 니즈"),
    } <= kinds


def test_templates_come_with_a_no_data_state():
    out = _cli("source-check", "frame", {"template": "five_forces"})
    assert set(out["template"]["forces"]) == set(frame.FORCES)
    assert "자료 없음" in out["rules"]
    assert _cli("source-check", "frame", {"template": "swot"})["ok"] is False


@pytest.mark.parametrize("script", ["sources", "claims", "frame"])
def test_every_checker_cli_ends_with_json(script):
    out = _cli("source-check", script, {"text": "", "framework": "pestel", "factors": {}})
    assert "ok" in out
