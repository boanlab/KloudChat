"""공지·안내 스킬(초안): 사내 공지 패키지가 초안으로 묶이고, 스크립트가 자료에 없는 값과
빠진 항목을 잡는다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.core.config import settings
from app.services import skill_packages, skill_scripts

SKILLS = Path(skill_packages.__file__).resolve().parent.parent / "skills"
NOTICE_SKILLS = {"internal-comms": "check"}


def _run(key: str, script: str, args: dict) -> dict:
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
    assert done.returncode == 0, done.stderr + done.stdout
    return json.loads(done.stdout.strip().splitlines()[-1])


def _types(result: dict, severity: str = "block") -> list[str]:
    return [f["type"] for f in result["findings"] if f["severity"] == severity]


# ── packages ────────────────────────────────────────────────────────────


def test_the_notice_skills_ship_published_with_their_source_and_scripts():
    packages = skill_packages.packages()
    for key, script in NOTICE_SKILLS.items():
        package = packages[key]
        # r10: published and listed after the LFLF round (3/3, no high finding)
        assert not package.draft and package.listed, key
        assert package.required_tools == ("run_skill_script",)
        assert script in package.scripts, key
        assert not package.stage, key
        assert "출처:" in package.body and "commit" in package.body, key
    assert "resources/LICENSE-anthropics-skills.txt" in packages["internal-comms"].files


def test_the_notice_skills_are_in_the_catalogue_without_drafts(monkeypatch):
    monkeypatch.setattr(settings, "skill_drafts", False)
    keys = {spec["key"] for spec in skill_packages.catalog_specs()}
    assert set(NOTICE_SKILLS) <= keys
    assert skill_packages.find("internal-comms") is not None


def test_the_server_imports_the_same_scripts():
    assert skill_scripts.module("internal-comms", "check").facts("2026. 11. 2.(월) 10:00")


# ── internal-comms ──────────────────────────────────────────────────────

MATERIAL = (
    "11월부터 출장비 정산은 새 경비 시스템(expense.example.kr)에서만 받음. 시행 2026-11-02. "
    "전 직원 대상. 10월 30일까지 시스템 첫 로그인. 문의 재무팀 내선 4512"
)


def test_a_grounded_notice_passes_with_no_block():
    text = (
        "**제목** 11월 2일부터 출장비 정산은 새 경비 시스템에서만 받습니다\n\n"
        "2026. 11. 2.(월)부터 출장비 정산은 새 경비 시스템(expense.example.kr)에서만 받습니다.\n\n"
        "- 대상: 전 직원\n- 해야 할 일: 10월 30일(금)까지 새 시스템에 한 번 로그인해 주십시오.\n"
        "- 문의: 재무팀 내선 4512"
    )
    result = _run("internal-comms", "check", {"text": text, "material": MATERIAL, "kind": "change"})
    assert result["counts"]["block"] == 0, result["findings"]
    assert not result["questions"]


def test_invented_contacts_dates_and_wrong_weekdays_are_blocked():
    text = (
        "2026. 11. 2.(화)부터 출장비 정산은 새 시스템에서만 받습니다.\n"
        "- 대상: 전 직원\n- 11월 15일까지 지난 영수증을 올려 주십시오.\n"
        "- 문의: 재무팀 김민수 과장(내선 4513, finance@example.kr), 다음 주 설명회"
    )
    result = _run("internal-comms", "check", {"text": text, "material": MATERIAL, "kind": "change"})
    blocked = {f["text"] for f in result["findings"] if f["severity"] == "block"}
    assert "2026. 11. 2.(화)" in blocked  # 2026-11-02 is a Monday
    assert "11월 15일" in blocked
    assert "4513" in blocked and "finance@example.kr" in blocked
    assert "relative_date" in _types(result, "check")


def test_a_relative_date_inside_a_recurring_rule_is_not_flagged():
    # r10 SK-notice-2: 「매월 25일까지 다음 달 근무계획」 is the rule itself
    material = "신청: 매월 25일까지 다음 달 근무계획을 인사시스템에 입력"
    text = "매월 25일까지 다음 달 근무계획을 입력해 주십시오."
    rule = _run("internal-comms", "check", {"text": text, "material": material, "kind": "change"})
    assert "relative_date" not in _types(rule, "check")
    once = _run("internal-comms", "check", {"text": "다음 달부터 근무계획을 입력해 주십시오.",
                                            "material": material, "kind": "change"})
    assert "relative_date" in _types(once, "check")


def test_missing_facts_become_questions_with_placeholders():
    text = "새 경비 시스템을 도입합니다. 많은 관심 부탁드립니다."
    result = _run(
        "internal-comms", "check", {"text": text, "material": "경비 시스템 바꿈", "kind": "notice"}
    )
    facts = {q["fact"]: q["placeholder"] for q in result["questions"]}
    assert facts.get("when") == "[시행일]" and facts.get("contact") == "[문의처]"
    assert "who" in facts


def test_official_register_and_3p_shape():
    official = _run(
        "internal-comms",
        "check",
        {
            "text": "점검 안내\n전 직원 대상이에요.",
            "kind": "general",
            "register": "official",
            "material": "",
        },
    )
    assert "register" in _types(official, "check")
    three = _run(
        "internal-comms",
        "check",
        {
            "text": "플랫폼팀 (10/1~10/7)\nProgress: 배포 2건.\nPlans: 결제 연동.",
            "kind": "3p",
            "material": "배포 2건, 결제 연동",
        },
    )
    missing = [f["text"] for f in three["findings"] if f["type"] == "missing_part"]
    assert missing == ["Problems"]
