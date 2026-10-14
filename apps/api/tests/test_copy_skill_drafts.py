"""카피·콘텐츠 스킬(초안): 패키지가 초안으로 묶이고, 스크립트가 사실 근거·채널 한도·광고 표기를
잰다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import skill_packages, skill_scripts, starter

SKILLS = Path(skill_packages.__file__).resolve().parent.parent / "skills"
COPY_SKILLS = (
    "copywriting",
    "ad-creative",
    "headline-matrix",
    "social-content",
    "content-strategy",
    "cold-email",
)


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
    for key in (*COPY_SKILLS, "copy-check"):
        package = packages[key]
        assert package.draft, key
        assert package.required_tools == ("run_skill_script",)
        assert "출처:" in package.body and "commit" in package.body, key


def test_drafts_stay_out_of_the_catalogue_and_the_tool_unless_enabled(monkeypatch):
    monkeypatch.setattr(settings, "skill_drafts", False)
    keys = {spec["key"] for spec in skill_packages.catalog_specs()}
    assert not keys & {*COPY_SKILLS, "copy-check"}
    assert {"xlsx-clean", "text-fit"} <= keys
    assert skill_packages.find("copy-check") is None
    assert "copy-check" not in {p.key for p in skill_packages.scripted()}
    # The server still imports a draft's script for its own checks and tests.
    assert skill_scripts.module("copy-check", "claims").scan("업계 1위")
    # The shipped catalogue (built at import, drafts off by default) has none.
    assert not {spec["key"] for spec in starter._SKILLS} & set(COPY_SKILLS)

    monkeypatch.setattr(settings, "skill_drafts", True)
    specs = {spec["key"]: spec for spec in skill_packages.catalog_specs()}
    assert specs["cold-email"]["draft"] is True and specs["xlsx-clean"]["draft"] is False
    assert skill_packages.find("cold-email").key == "cold-email"
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
    assert rows["cold-email"].visibility == Visibility.private
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


# ── headline-matrix ─────────────────────────────────────────────────────


def test_the_grid_spreads_the_count_and_marks_angles_that_need_facts():
    grid = _run("headline-matrix", "matrix", {"count": 25})
    assert sum(a["quota"] for a in grid["angles"]) == 25 and len(grid["angles"]) == 7
    assert {a["angle"] for a in grid["angles"] if a["needs_fact"]} == {"구체성", "증거", "긴급성"}
    grid = _run("headline-matrix", "matrix", {"count": 7, "facts": ["회의 후 5분 안에 요약 발송"]})
    assert not any(a["needs_fact"] for a in grid["angles"])


def test_the_matrix_measures_length_duplicates_and_repeated_shapes():
    heads = [
        {"angle": "호기심", "text": "회의록, 아직 손으로 쓰세요?"},
        {"angle": "호기심", "text": "회의록 아직 손으로 쓰세요"},
        {"angle": "구체성", "text": "회의 끝나고 5분 안에 요약이 메일로 도착합니다"},
        {"angle": "정체성", "text": "팀장님을 위한 회의록"},
    ]
    result = _run("headline-matrix", "matrix", {"count": 7, "limit": 15, "headlines": heads})
    assert result["duplicates"] == [[1, 2]]
    assert result["over"] == [1, 3]
    assert {row["angle"] for row in result["short_angles"]} == {
        "증거",
        "긴급성",
        "역발상",
        "작동 원리",
    }
    assert result["clean"] is False


def test_google_counts_hangul_double():
    matrix = skill_scripts.module("headline-matrix", "matrix")
    assert matrix.count("회의록 AI", "chars_dw") == 9
    assert matrix.count("회의록 AI", "chars") == 6
    assert matrix.count("👍🏽좋아요", "chars") == 4


# ── cold-email ──────────────────────────────────────────────────────────


def test_an_advertising_email_needs_the_label_sender_contact_and_opt_out():
    args = {
        "advertising": True,
        "consent": "opt_in",
        "sender": {"name": "한빛데이터"},
        "emails": [
            {"subject": "(광/고) 데이터 정리 제안", "body": "안녕하세요. 무료 진단을 드립니다."}
        ],
    }
    result = _run("cold-email", "check", args)
    kinds = {f["kind"] for f in result["emails"][0]["findings"] if f["severity"] == "block"}
    assert kinds == {"ad_label", "sender", "contact", "opt_out", "opt_out_en"}
    args["emails"] = [
        {
            "subject": "(광고) 데이터 정리 제안",
            "body": "한빛데이터 영업팀 박서연 · 02-555-0101 · seoyeon@hanbit.example\n"
            "서울 마포구 월드컵북로 10\n"
            "수신을 원하지 않으시면 https://hanbit.example/unsub 에서 수신거부해 주세요.\n"
            "If you do not wish to receive this email, unsubscribe at the link above.",
        }
    ]
    assert _run("cold-email", "check", args)["block"] == 0
    args["consent"] = "none"
    assert _run("cold-email", "check", args)["block"] == 1


def test_an_undecided_email_is_asked_and_tokens_are_filled_per_recipient():
    args = {
        "emails": [
            {"subject": "{{회사}} 정산 자동화", "body": "{{이름}}님, 무료 체험 안내드립니다."}
        ],
        "recipients": [{"이름": "김민준", "회사": "가온물산"}, {"이름": "이서연"}],
    }
    result = _run("cold-email", "check", args)
    assert "ad_decision" in {f["kind"] for f in result["emails"][0]["findings"]}
    assert [r["missing"] for r in result["recipients"]] == [[], ["회사"]]


def test_a_fake_reply_subject_blocks():
    result = _run(
        "cold-email",
        "check",
        {"advertising": False, "emails": [{"subject": "RE: 지난번 문의", "body": "안녕하세요"}]},
    )
    assert result["block"] == 1


# ── content-strategy ────────────────────────────────────────────────────


def test_the_plan_scores_ranks_and_dates_without_inventing_basis():
    args = {
        "topics": [
            {
                "title": "A",
                "type": "searchable",
                "scores": {"impact": 8, "fit": 9, "search": 7, "resources": 6},
                "basis": {
                    "impact": "상담 기록",
                    "fit": "제품 기능",
                    "search": "사용자 제공 검색량",
                    "resources": "보유 자료",
                },
            },
            {
                "title": "B",
                "type": "shareable",
                "scores": {"impact": 6, "fit": 7, "search": 9, "resources": 8},
            },
            {"title": "C", "type": "searchable", "scores": {"impact": 5, "fit": 5}},
        ],
        "calendar": {"start": "2026-10-12", "weeks": 2, "per_week": 2, "weekdays": ["화", "목"]},
    }
    result = _run("content-strategy", "plan", args)
    assert [(r["title"], r["score"]) for r in result["ranked"]] == [
        ("A", 7.9),
        ("B", 7.1),
        ("C", 5.0),
    ]
    assert result["ranked"][1]["assumed"] == ["impact", "fit", "search", "resources"]
    assert result["ranked"][2]["missing"] == ["search", "resources"]
    assert [r["date"] for r in result["calendar"]] == [
        "2026-10-13",
        "2026-10-15",
        "2026-10-20",
        "2026-10-22",
    ]
    assert [r["type"] for r in result["calendar"]].count("searchable") >= 2


def test_each_channel_gets_its_own_frequency():
    # r7 A13-T2: 「11월 한 달 회사 SNS(인스타그램, 링크드인) 게시물 계획 짜 줘. 주 2회씩.」
    # came out 8 posts for both channels together (인스타 5·링크드인 3)
    topics = [{"title": f"주제 {n}", "type": "searchable",
               "scores": {"impact": 5, "fit": 5, "search": 5, "resources": 5}} for n in range(20)]
    topics[0] = {**topics[0], "title": "AI 회의록 요약 베타 출시", "channel": "링크드인"}
    args = {"topics": topics, "calendar": {
        "start": "2026-11-01", "end": "2026-11-30", "channels": [
            {"name": "인스타그램", "per_week": 2, "weekdays": ["화", "목"]},
            {"name": "링크드인", "per_week": 2, "weekdays": ["수", "금"]}]}}
    result = _run("content-strategy", "plan", args)
    assert result["posts_per_channel"] == {"인스타그램": 8, "링크드인": 8}
    dates = [r["date"] for r in result["calendar"]]
    assert dates == sorted(dates) and dates[-1] <= "2026-11-30"
    insta = [r["date"] for r in result["calendar"] if r["channel"] == "인스타그램"]
    assert insta[:2] == ["2026-11-03", "2026-11-05"]
    # a topic meant for LinkedIn lands there
    assert next(r for r in result["calendar"] if r["title"] == "AI 회의록 요약 베타 출시")[
        "channel"] == "링크드인"
    # channel names alone take the calendar's frequency
    names = _run("content-strategy", "plan", {"topics": topics, "calendar": {
        "start": "2026-11-02", "weeks": 1, "per_week": 2, "weekdays": ["화", "목"],
        "channels": ["인스타그램", "링크드인"]}})
    assert names["posts_per_channel"] == {"인스타그램": 2, "링크드인": 2}


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


def test_a_consent_request_without_consent_is_advertising_too():
    args = {
        "advertising": False,
        "consent": "none",
        "emails": [{"subject": "엑스포에서 뵈었습니다", "body": "자료를 보내 드려도 될까요?"}],
    }
    result = _run("cold-email", "check", args)
    assert {f["kind"] for f in result["emails"][0]["findings"]} >= {"consent_request"}
