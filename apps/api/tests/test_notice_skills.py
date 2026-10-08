"""공지·안내 스킬(초안): 사내 공지·릴리스 노트·서비스 종료 안내 패키지가 초안으로 묶이고,
스크립트가 자료에 없는 값·빠진 항목·일정 순서와 고지 기간을 잡는다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.core.config import settings
from app.services import skill_packages, skill_scripts

SKILLS = Path(skill_packages.__file__).resolve().parent.parent / "skills"
NOTICE_SKILLS = {"internal-comms": "check", "release-notes": "notes", "eol-message": "timeline"}


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


def test_the_notice_skills_ship_as_drafts_with_their_source_and_scripts():
    packages = skill_packages.packages()
    for key, script in NOTICE_SKILLS.items():
        package = packages[key]
        assert package.draft, key
        assert package.required_tools == ("run_skill_script",)
        assert script in package.scripts, key
        assert not package.stage, key
        assert "출처:" in package.body and "commit" in package.body, key
    assert "resources/LICENSE-anthropics-skills.txt" in packages["internal-comms"].files


def test_drafts_stay_out_of_the_catalogue_unless_enabled(monkeypatch):
    monkeypatch.setattr(settings, "skill_drafts", False)
    keys = {spec["key"] for spec in skill_packages.catalog_specs()}
    assert not keys & set(NOTICE_SKILLS)
    assert skill_packages.find("release-notes") is None
    monkeypatch.setattr(settings, "skill_drafts", True)
    keys = {spec["key"] for spec in skill_packages.catalog_specs()}
    assert set(NOTICE_SKILLS) <= keys


def test_the_server_imports_the_same_scripts():
    assert skill_scripts.module("internal-comms", "check").facts("2026. 11. 2.(월) 10:00")
    assert (
        skill_scripts.module("release-notes", "notes").parse("fix: 오류 수정 (#1)")[0]["id"] == "#1"
    )
    assert skill_scripts.module("eol-message", "timeline").check(
        {"announce": "2026-10-01", "end": "2026-12-31"}
    )


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


# ── release-notes ───────────────────────────────────────────────────────

LOG = "\n".join(
    [
        "a1b2c3d feat(export): PDF 내보내기에 페이지 번호 추가 (#101)",
        "e4f5a6b fix: 엑셀 업로드 시 날짜가 하루 밀리던 문제 (#102)",
        "1234abc chore(ci): 빌드 캐시 설정",
        "9f8e7d6 feat!: v1 보고서 API 제거 (#104)",
    ]
)


def test_parse_splits_items_and_flags_breaking_and_internal():
    items = {
        i["id"]: i for i in _run("release-notes", "notes", {"op": "parse", "log": LOG})["items"]
    }
    assert set(items) == {"#101", "#102", "1234abc", "#104"}
    assert items["#104"]["breaking"] and items["#104"]["section"] == "주의"
    assert items["1234abc"]["kind"] == "internal" and items["1234abc"]["section"] is None
    assert items["#102"]["section"] == "수정"


def test_complete_notes_pass_and_every_gap_is_caught():
    good = {
        "op": "check",
        "log": LOG,
        "version": "3.0.0",
        "previous_version": "2.4.3",
        "date": "2026-10-20",
        "entries": [
            {
                "section": "주의",
                "text": "v1 보고서 API가 없어집니다. [기한]까지 v2로 옮기세요.",
                "sources": ["#104"],
            },
            {
                "section": "새 기능",
                "text": "PDF로 내보낼 때 쪽 번호가 붙습니다.",
                "sources": ["#101"],
            },
            {
                "section": "수정",
                "text": "엑셀을 올리면 날짜가 하루 밀리던 문제를 고쳤습니다.",
                "sources": ["#102"],
            },
        ],
        "omitted": [{"id": "1234abc", "reason": "내부 빌드 설정"}],
        "notes": "# 리포트온 3.0.0 (2026. 10. 20.)\n## 주의\n- v1 보고서 API가 없어집니다.",
    }
    result = _run("release-notes", "notes", good)
    assert result["counts"]["block"] == 0, result["findings"]
    assert result["coverage"]["missing"] == 0 and result["coverage"]["omitted"] == 1

    bad = dict(
        good,
        version="2.5.0",
        notes="# 리포트온 2.5.1 (2026. 10. 21.)",
        omitted=[{"id": "#104", "reason": "x"}],
    )
    bad["entries"] = [
        {
            "section": "새 기능",
            "text": "PDF 쪽 번호가 붙고 내보내기가 3배 빨라졌습니다.",
            "sources": ["#101"],
        },
        {"section": "새 기능", "text": "다크 모드를 지원합니다.", "sources": []},
    ]
    blocks = _types(_run("release-notes", "notes", bad))
    for kind in (
        "invented_figure",
        "no_source",
        "missing",
        "breaking_omitted",
        "version_mismatch",
        "date_mismatch",
    ):
        assert kind in blocks, kind
    checks = _types(_run("release-notes", "notes", bad), "check")
    assert "version_bump" in checks


def test_a_breaking_change_outside_the_warning_section_is_blocked():
    args = {
        "op": "check",
        "items": [{"id": "#9", "text": "설정 파일 옵션 legacy_mode 제거"}],
        "entries": [{"section": "개선", "text": "설정이 단순해졌습니다.", "sources": ["#9"]}],
        "version": "1.3.0",
        "previous_version": "1.2.0",
        "date": "2026-10-20",
    }
    assert "breaking_unflagged" in _types(_run("release-notes", "notes", args))


# ── eol-message ─────────────────────────────────────────────────────────


def test_a_sound_schedule_passes_and_reports_gaps():
    dates = {
        "announce": "2026-10-15",
        "stop_new": "2026-11-02",
        "end": "2026-12-31",
        "export_until": "2027-01-29",
        "delete": "2027-03-31",
    }
    result = _run(
        "eol-message",
        "timeline",
        {
            "dates": dates,
            "telecom": "value_added",
            "min_gaps": [{"from": "announce", "to": "end", "days": 60}],
        },
    )
    assert result["counts"]["block"] == 0, result["findings"]
    assert [g["days"] for g in result["gaps"]] == [18, 59, 90]
    assert (
        result["legal"]["announce_by"] == "2026-12-01"
        and result["legal"]["report_by"] == "2026-12-16"
    )


def test_order_gaps_and_legal_notice_period_are_blocked():
    dates = {
        "announce": "2026-12-10",
        "stop_new": "2026-12-01",
        "end": "2026-12-31",
        "export_until": "2027-02-01",
        "delete": "2027-01-31",
    }
    result = _run(
        "eol-message",
        "timeline",
        {
            "dates": dates,
            "telecom": "value_added",
            "min_gaps": [{"from": "announce", "to": "end", "days": 30}],
        },
    )
    blocks = _types(result)
    assert blocks.count("order") == 2  # 신규 중단 before 공지, 내보내기 after 삭제
    assert "gap" in blocks and "legal_gap" in blocks
    facilities = _run(
        "eol-message",
        "timeline",
        {"dates": {"announce": "2026-11-15", "end": "2026-12-31"}, "telecom": "facilities"},
    )
    assert "legal_gap" in _types(facilities)  # 46일 < 60일


def test_missing_dates_are_asked_and_the_notice_text_is_matched():
    result = _run("eol-message", "timeline", {"dates": {"end": "2026-12-31"}})
    assert {q["fact"] for q in result["questions"]} >= {"announce", "delete", "export_until"}
    assert "missing_date" in _types(result)
    text = (
        "서비스는 2026. 12. 31.(수) 종료합니다. 데이터는 1월 31일 삭제합니다. "
        "다음 달에 다시 안내합니다."
    )
    checked = _run(
        "eol-message",
        "timeline",
        {
            "dates": {"announce": "2026-10-15", "end": "2026-12-31", "delete": "2027-03-31"},
            "text": text,
        },
    )
    blocks = {(f["type"], f["text"]) for f in checked["findings"] if f["severity"] == "block"}
    assert ("weekday", "2026. 12. 31.(수)") in blocks  # it is a Thursday
    assert ("date_not_in_timeline", "1월 31일") in blocks
    assert any(kind == "date_missing_in_text" and "삭제" in t for kind, t in blocks)
    assert "relative_date" in _types(checked, "check")
