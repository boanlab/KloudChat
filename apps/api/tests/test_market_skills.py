"""Market and competitor skills: unsourced values and unsupported generalities found by code."""

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
)
ALL = (*MARKET_SKILLS, "source-check")

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


def test_the_market_skills_ship_published_with_their_source():
    packages = skill_packages.packages()
    for key in ALL:
        package = packages[key]
        assert not package.draft, key
        # the research skills are listed; the check they call is a helper, not a choice
        assert package.listed == (key != "source-check"), key
        # The research skills search when the person did not hand them a material pack.
        searches = key in ("company-research", "competitor-analysis")
        assert package.required_tools == (("run_skill_script", "web_search", "fetch_url")
                                          if searches else ("run_skill_script",)), key
        # source-check also guides a report writer on sourcing
        assert package.kinds == (("chat", "report") if key == "source-check" else ("chat",)), key
        assert "출처:" in package.body, key
        # Korean front matter, English instructions.
        assert any("가" <= ch <= "힣" for ch in package.name), key
    assert set(packages["source-check"].scripts) == {"sources", "claims", "frame", "post"}


def test_borrowed_text_names_its_licence_and_commit():
    packages = skill_packages.packages()
    for key in ("competitor-analysis",):
        body = packages[key].body
        assert "phuryn/pm-skills" in body and "MIT" in body, key
        assert "8607e3b077817f89bf4a9b623246219734ac3be0" in body, key
    # The CC BY-NC-SA originals are named but not used.
    assert "CC BY-NC-SA" in packages["company-research"].body


def test_the_market_skills_and_their_check_run_with_drafts_off(monkeypatch):
    monkeypatch.setattr(settings, "skill_drafts", False)
    assert {spec["key"] for spec in skill_packages.catalog_specs()} >= set(ALL)
    # the skills' procedure calls run_skill_script skill source-check
    assert "source-check" in {p.key for p in skill_packages.scripted()}
    assert skill_packages.find("경쟁사 분석").key == "competitor-analysis"


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
