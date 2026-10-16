"""카피 점검 스킬(초안): 패키지가 초안으로 묶이고, 스크립트가 사실 근거·채널 한도를 잰다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import skill_packages, skill_scripts, starter

SKILLS = Path(skill_packages.__file__).resolve().parent.parent / "skills"


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


# ── draft gating ────────────────────────────────────────────────────────


def test_the_copy_skills_ship_as_drafts_with_their_source():
    packages = skill_packages.packages()
    package = packages["copy-check"]
    assert package.draft
    assert package.required_tools == ("run_skill_script",)
    assert "출처:" in package.body and "commit" in package.body


def test_drafts_stay_out_of_the_catalogue_and_the_tool_unless_enabled(monkeypatch):
    monkeypatch.setattr(settings, "skill_drafts", False)
    keys = {spec["key"] for spec in skill_packages.catalog_specs()}
    assert "copy-check" not in keys
    assert {"xlsx-clean", "text-fit"} <= keys
    assert skill_packages.find("copy-check") is None
    assert "copy-check" not in {p.key for p in skill_packages.scripted()}
    # The server still imports a draft's script for its own checks and tests.
    assert skill_scripts.module("copy-check", "claims").scan("업계 1위")
    # The shipped catalogue (built at import, drafts off by default) has none.
    assert "copy-check" not in {spec["key"] for spec in starter._SKILLS}

    monkeypatch.setattr(settings, "skill_drafts", True)
    specs = {spec["key"]: spec for spec in skill_packages.catalog_specs()}
    assert specs["copy-check"]["draft"] is True and specs["xlsx-clean"]["draft"] is False
    assert skill_packages.find("카피 점검").key == "copy-check"
    assert "copy-check" in {p.key for p in skill_packages.scripted()}


def test_status_must_be_known(tmp_path):
    package = tmp_path / "demo"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: 데모\ndescription: d\nwhen_to_use: w\nstatus: beta\n---\n본문\n"
    )
    with pytest.raises(skill_packages.PackageError, match="status"):
        skill_packages.load(package)
    (package / "SKILL.md").write_text(
        "---\nname: 데모\ndescription: d\nwhen_to_use: w\nstatus: draft\n---\n본문\n"
    )
    assert skill_packages.load(package).draft


@pytest.mark.asyncio
async def test_a_draft_is_seeded_private_and_only_while_enabled(monkeypatch):
    from app.models.workspace import Skill, Visibility

    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class Db:
        def __init__(self):
            self.added: list = []
            self.deleted: list = []

        async def exec(self, _query):
            return Rows([])

        def add(self, row):
            self.added.append(row)

        async def delete(self, row):
            self.deleted.append(row)

    monkeypatch.setattr(settings, "skill_drafts", True)
    monkeypatch.setattr(starter, "_SKILLS", skill_packages.catalog_specs())
    monkeypatch.setattr(starter, "_AGENTS", [])
    db = Db()
    await starter.seed_catalog(db, "owner")
    rows = {row.catalog_key: row for row in db.added if isinstance(row, Skill)}
    assert rows["copy-check"].visibility == Visibility.private
    assert rows["xlsx-clean"].visibility == Visibility.org


# ── copy-check: claims ──────────────────────────────────────────────────

claims = skill_scripts.module("copy-check", "claims")


def _kinds(findings, severity=None):
    return {
        (f["kind"], f["match"]) for f in findings if severity is None or f["severity"] == severity
    }


def test_ungrounded_superlatives_comparisons_and_testimonials_block():
    found = claims.scan('국내 최초 AI 회의록, 타사보다 저렴합니다. "정말 편해요" - 김OO 고객', "")
    blocked = {kind for kind, _ in _kinds(found, "block")}
    assert {"superlative", "comparison", "testimonial"} <= blocked


def test_a_claim_in_the_material_is_grounded_not_blocked():
    material = "2025년 한국서비스품질지수 조사 업계 1위. 월 구독료 29,000원."
    found = claims.scan("업계 1위 회의록 서비스, 월 29,000원", material)
    assert ("superlative", "업계 1위") in _kinds(found, "check")
    assert not [f for f in found if f["kind"] == "number"]


def test_numbers_missing_from_the_material_are_flagged():
    found = claims.scan(
        "회의록 작성 시간을 70% 줄이고 3분 만에 공유합니다", "회의가 끝나면 요약을 메일로 보냅니다"
    )
    assert {("number", "70%"), ("number", "3분")} <= _kinds(found, "block")


def test_job_titles_upper_bounds_and_placeholders_are_not_claims():
    found = claims.scan(
        "최고경영자 인사말. 최대 20% 할인, 최초 가입 시 [혜택] 제공", "할인 최대 20%"
    )
    assert not _kinds(found, "block")
    assert ("placeholder", "[혜택]") in _kinds(found)


def test_disease_claims_block_for_health_food_and_check_otherwise():
    text = "면역력 강화와 혈당 관리에 좋습니다"
    assert {k for k, _ in _kinds(claims.scan(text, "", "health_food"), "block")} == {"medical"}
    assert {k for k, _ in _kinds(claims.scan(text, "", "general"), "check")} == {"medical"}


# ── copy-check: length ──────────────────────────────────────────────────


def test_channel_limits_count_the_way_each_platform_does():
    items = [
        {
            "label": "n",
            "channel": "naver_powerlink",
            "field": "title",
            "text": "세무사가 직접 하는 온라인 기장",
        },
        {"label": "d", "channel": "naver_powerlink", "field": "description", "text": "짧은 설명"},
        {
            "label": "g",
            "channel": "google_rsa",
            "field": "headline",
            "text": "접속기록 자동 보관 서비스",
        },
        {
            "label": "i",
            "channel": "instagram",
            "field": "caption",
            "text": "새 빵 #a #b #c #d #e #f",
        },
        {"label": "m", "channel": "meta", "field": "headline", "text": "가" * 45},
    ]
    rows = {row["label"]: row for row in _run("copy-check", "length", {"items": items})["items"]}
    assert rows["n"]["count"] == 17 and rows["n"]["over"] == 2 and rows["n"]["hard"]
    assert rows["d"]["under"] == 15
    assert rows["g"]["count"] == 25 and rows["g"]["over"] == 0
    assert rows["i"]["hashtags"] == {"count": 6, "limit": 5, "over": 1}
    assert rows["m"]["over"] == 5 and not rows["m"]["hard"] and rows["m"]["shown"] == "가" * 40


def test_every_channel_lists_its_source():
    specs = _run("copy-check", "length", {"list": True})["specs"]
    assert {s["channel"] for s in specs} >= {
        "naver_powerlink",
        "kakao_display",
        "meta",
        "google_rsa",
        "instagram",
        "linkedin",
        "youtube",
    }
    assert all(s["source"] for s in specs)


def test_offers_word_numbers_and_numbering_are_read_the_way_people_mean_them():
    material = "무료 체험 14일. 영업 10:00~20:00"
    found = claims.scan(
        "1. 14일 무료 체험\n2. 도입 상담 예약하기\n"
        "오후 8시까지. 한두 시간이 사라집니다. 20분 통화 어떠세요?",
        material,
    )
    assert ("offer", "14일 무료") in _kinds(found, "check")
    assert ("offer", "도입 상담 예약") in _kinds(found, "block")
    assert ("number", "한두 시간") in _kinds(found, "block")
    numbers = {match for kind, match in _kinds(found) if kind == "number"}
    assert numbers == {"한두 시간"}
