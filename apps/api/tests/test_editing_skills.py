"""교정 스킬(초안): 「교정·윤문」의 맞춤법·띄어쓰기·어투 점검과 값 보존 점검, 「상투어 걷어내기」의
표현 목록·자동 고침·밀도가 코드로 정해진 대로 나온다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services import skill_packages, skill_scripts

SKILLS = Path(skill_packages.__file__).resolve().parent.parent / "skills"
KEYS = ("copy-editing", "generic-language-killer")


def _cli(key: str, script: str, args: dict) -> subprocess.CompletedProcess:
    path = SKILLS / key / "scripts" / f"{script}.py"
    return subprocess.run([sys.executable, str(path), json.dumps(args, ensure_ascii=False)],
                          capture_output=True, text=True, timeout=60)


def _run(key: str, script: str, args: dict) -> dict:
    done = _cli(key, script, args)
    assert done.returncode == 0, done.stderr + done.stdout
    return json.loads(done.stdout.strip().splitlines()[-1])


def _run_raw(key: str, script: str, args: dict) -> dict:
    """The result line of a run that may fail on purpose."""
    return json.loads(_cli(key, script, args).stdout.strip().splitlines()[-1])


def proof():
    return skill_scripts.module("copy-editing", "proof")


def preserve():
    return skill_scripts.module("copy-editing", "preserve")


def scan():
    return skill_scripts.module("generic-language-killer", "scan")


# ── packages ────────────────────────────────────────────────────────────


def test_both_ship_as_drafts_with_their_source():
    packages = skill_packages.packages()
    for key in KEYS:
        package = packages[key]
        assert package.draft, key
        assert package.required_tools == ("run_skill_script",)
        assert "출처:" in package.body and "commit" in package.body, key
    assert packages["copy-editing"].scripts.keys() == {"proof", "preserve"}
    assert packages["copy-editing"].stage == ""
    assert packages["generic-language-killer"].scripts.keys() == {"scan"}
    killer = packages["generic-language-killer"]
    assert (killer.stage, killer.entry) == ("post", "scan")
    assert "LICENSE 파일은 없음" in packages["generic-language-killer"].body


def test_the_check_entry_imports_with_its_contract():
    module = skill_scripts.entry("generic-language-killer")
    for name in ("check", "fix", "summary"):
        assert callable(getattr(module, name))


# ── copy-editing: proof ─────────────────────────────────────────────────

FIXES = [
    ("그렇게 하면 안되요.", "안되요.", "안돼요."),
    ("일이 잘 됬다.", "됬다.", "됐다."),
    ("내일 뵈요.", "뵈요.", "봬요."),
    ("회의는 몇일에 하나요?", "몇일에", "며칠에"),
    ("제가 할께요.", "할께요.", "할게요."),
    ("일일히 확인했다.", "일일히", "일일이"),
    ("깨끗히 치웠다.", "깨끗히", "깨끗이"),
    ("꼼꼼이 봤다.", "꼼꼼이", "꼼꼼히"),
    ("갯수를 센다.", "갯수를", "개수를"),
    ("문제가 있슴.", "있슴.", "있음."),
    ("그건 할수 있다.", "할수", "할 수"),
    ("그가 한것 같다.", "한것", "한 것"),
    ("갈때 연락해.", "갈때", "갈 때"),
    ("운전할줄 안다.", "운전할줄", "운전할 줄"),
    ("말한데로 했다.", "말한데로", "말한 대로"),
    ("가지않았다.", "가지않았다.", "가지 않았다."),
    ("사과 한개를 샀다.", "한개를", "한 개를"),
    ("설겆이를 했다.", "설겆이를", "설거지를"),
    ("참 희안하다.", "희안하다.", "희한하다."),
    ("담배는 삼가해 주세요.", "삼가해", "삼가"),
    ("오랫만이다.", "오랫만이다.", "오랜만이다."),
    ("일을 할려고 했다.", "할려고", "하려고"),
]


@pytest.mark.parametrize(("text", "before", "after"), FIXES)
def test_proof_finds_the_frequent_errors_with_a_basis(text, before, after):
    found = proof().scan(text)
    assert [(f["before"], f["after"]) for f in found] == [(before, after)], found
    assert found[0]["basis"] and found[0]["sure"]


def test_proof_leaves_correct_words_and_quotes_alone():
    clean = ("별것 아닌 일로 그런대로 넘어갔다. 말수 없는 사람이 날것을 먹었다. 갈수록 좋아진다. "
             "되도록 빨리 오세요. 한번 해 보세요. 「할수 있다」는 인용이다. https://a.kr/할수있다")
    assert proof().scan(clean) == []
    # After a noun 「만큼」 is a particle: reported, but as a judgment call.
    assert [(f["after"], f["sure"]) for f in proof().scan("산만큼 높다.")] == [("산 만큼", False)]


def test_proof_marks_context_dependent_fixes_unsure_and_applies_only_sure_ones():
    text = "우리의 바램은 금새 이뤄졌다. 몇일 걸렸다."
    found = proof().scan(text)
    assert {f["after"]: f["sure"] for f in found} == {"바람은": False, "금세": False, "며칠": True}
    assert proof().apply(text, found) == "우리의 바램은 금새 이뤄졌다. 며칠 걸렸다."


def test_register_names_the_body_register_and_the_sentences_off_it():
    text = ("행사는 10월 24일에 열립니다. 참가 신청은 총무팀에서 받습니다.\n"
            "자리가 부족하면 연락 주세요.\n별것 아니다.\n- 장소 확정함\n- 예산을 검토했습니다")
    reg = proof().register(text)
    assert reg["main"] == "합니다체" and reg["bullet_main"] in ("개조식", "합니다체")
    off = {r["sentence"]: r["register"] for r in reg["off"]}
    assert off["자리가 부족하면 연락 주세요."] == "해요체"
    assert off["별것 아니다."] == "한다체"
    assert proof().register(text, "haeyo")["main"] == "해요체"


def test_proof_cli_applies_and_counts():
    result = _run("copy-editing", "proof",
                  {"text": "몇일 뒤에 할께요. 그건 할수 있어요.", "apply": True})
    row = result["items"][0]
    assert row["corrected"] == "며칠 뒤에 할게요. 그건 할 수 있어요."
    assert row["counts"] == {"spelling": 2, "spacing": 1, "standard": 0}
    assert row["left"] == 0
    bad = _run_raw("copy-editing", "proof", {"text": "x", "register": "formal"})
    assert bad["ok"] is False


# ── copy-editing: preserve ──────────────────────────────────────────────

ORIGINAL = ("2026년 10월 8일(목) 오후 3시에 김민수 팀장과 (주)한결바이오 담당자가 만났다.\n"
            "예산은 1,200만 원이며 참석자는 12명이다. "
            "KloudChat 도입은 반드시 11월까지 마쳐야 한다.\n"
            "문의: 02-1234-5678, help@kloud.kr\n「고객이 먼저다」라는 원칙을 따른다.")


def test_protected_values_cover_dates_numbers_names_and_quotes():
    text = ORIGINAL + " GPT-4o 5GB 두 명 박 과장 2026. 10. 8."
    found = {(t["kind"], t["raw"]) for t in preserve().protected(text)}
    for item in [("date", "2026년 10월 8일"), ("weekday", "(목)"), ("time", "오후 3시"),
                 ("person", "김민수"), ("org", "(주)한결바이오"), ("number", "1,200만 원"),
                 ("number", "12명"), ("name", "KloudChat"),
                 ("phone", "02-1234-5678"), ("email", "help@kloud.kr"), ("quote", "고객이 먼저다"),
                 ("name", "GPT-4o"), ("number", "5GB"), ("number", "두 명"), ("person", "박"),
                 ("date", "2026. 10. 8.")]:
        assert item in found, item
    assert ("person", "담당") not in found


def test_a_faithful_edit_passes_and_lists_its_changes():
    edited = ORIGINAL.replace("만났다.", "만났습니다.").replace("이며", "이고")
    result = preserve().report(ORIGINAL, edited)
    assert result["pass"] and not result["format_changed"]
    assert [(c["before"], c["after"]) for c in result["changes"]] == [
        ("만났다.", "만났습니다."), ("원이며", "원이고")]
    assert result["risky"] == []


def test_lost_added_and_reformatted_values_are_caught():
    edited = (ORIGINAL.replace("1,200만 원", "1200만 원").replace("12명", "13명")
              .replace("김민수 팀장", "팀장").replace("따른다.", "따른다. 만족도는 95%이다."))
    result = preserve().report(ORIGINAL, edited)
    assert not result["pass"]
    assert {t["raw"] for t in result["lost"]} == {"12명", "김민수"}
    assert {t["raw"] for t in result["added"]} == {"13명", "95%"}
    assert [(t["raw"], t["now"]) for t in result["format_changed"]] == [("1,200만 원", "1200만 원")]
    # The user asked for it: no longer a loss.
    allowed = preserve().report(ORIGINAL, edited, allow=["12명", "13명", "김민수"])
    assert {t["raw"] for t in allowed["lost"]} == set()
    assert {t["raw"] for t in allowed["added"]} == {"95%"}


def test_meaning_risks_are_flagged_and_spacing_is_not():
    result = preserve().report(
        "반드시 11월까지 마쳐야 한다. 3명 이상 필요하다. 승인하지 않았다. 할수 있다.",
        "11월까지 마치면 된다. 3명 필요하다. 승인했다. 할 수 있다.",
    )
    risks = {c["before"]: set(c["risk"]) for c in result["changes"]}
    assert "modality" in risks["반드시"]
    assert "modality" in risks["마쳐야 한다."]
    assert "scope" in risks["이상"]
    assert "negation" in risks["승인하지 않았다."]
    spacing = [c for c in result["changes"] if c["type"] == "spacing"]
    assert [(c["before"], c["after"], c["risk"]) for c in spacing] == [("할수", "할 수", [])]
    assert result["pass"]


def test_preserve_cli_and_protect_list():
    result = _run("copy-editing", "preserve", {
        "original": "노트핏 프로 요금제는 월 12,000원입니다.",
        "edited": "노트핏 요금제는 월 12,000원입니다.",
        "protect": ["노트핏 프로"],
    })
    assert result["ok"] and not result["pass"]
    assert [(t["kind"], t["raw"]) for t in result["lost"]] == [("protect", "노트핏 프로")]
    assert _run_raw("copy-editing", "preserve", {"original": "x"})["ok"] is False


# ── generic-language-killer ─────────────────────────────────────────────

AUTO = [
    ("업무를 수행함에 있어서 소통이 필요하다.", "업무를 수행할 때 소통이 필요하다."),
    ("핵심은 소통이라고 할 수 있습니다.", "핵심은 소통입니다."),
    ("이 일은 중요하다고 할 수 있습니다.", "이 일은 중요합니다."),
    ("그는 이미 끝냈다고 할 수 있다.", "그는 이미 끝냈다."),
    ("점검할 필요가 있습니다.", "점검해야 합니다."),
    ("곧 안내하도록 하겠습니다.", "곧 안내하겠습니다."),
    ("결과가 보여지는 화면이다.", "결과가 보이는 화면이다."),
    ("문제가 개선되어질 것이다.", "문제가 개선될 것이다."),
    ("서울에 위치한 본사", "서울에 있는 본사"),
    ("지금 개발하는 중에 있습니다.", "지금 개발하고 있습니다."),
    ("둘 다 하는 것이 가능합니다.", "둘 다 할 수 있습니다."),
    ("매우 중요한 일이다.", "중요한 일이다."),
    ("고객의 니즈를 파악한다.", "고객의 요구를 파악한다."),
    ("좋다 — 그래도 늦었다!!", "좋다, 그래도 늦었다!"),
    ("It is important to note that we ship in order to learn.", "We ship to learn."),
]


@pytest.mark.parametrize(("text", "fixed"), AUTO)
def test_safe_rewrites_are_applied_by_code(text, fixed):
    found = scan().scan(text)
    assert scan().apply(text, found) == fixed


def test_phrases_that_need_a_fact_get_a_placeholder_not_a_fix():
    text = ("다양한 기능으로 업무 효율을 효과적으로 극대화하는 혁신적인 서비스입니다. "
            "처리 시간이 크게 줄었습니다.")
    found = scan().scan(text)
    by = {f["match"].strip(): f for f in found}
    assert by["다양한"]["placeholder"] == "[구체적 항목]" and not by["다양한"]["auto"]
    assert by["효과적으로"]["placeholder"] == "[방법]"
    assert by["혁신적"]["placeholder"] == "[차별점]"
    assert by["크게"]["placeholder"] == "[구체적 수치]"
    assert any("극대화" in m for m in by)
    assert scan().apply(text, found) == text


def test_categories_cover_the_listed_kinds():
    text = ("오늘날 빠르게 변화하는 시장에서 시너지를 낼 것으로 보입니다. 교육을 통해 개선한다. "
            "결론적으로 어느 정도 성과가 있었다. We leverage seamless tools.")
    cats = {f["category"] for f in scan().scan(text)}
    assert cats == {"ai", "buzzword", "hedge", "translationese", "english"}
    only = scan().scan(text, categories=["hedge"])
    assert {f["category"] for f in only} == {"hedge"}


def test_literal_uses_and_quotes_are_not_fixed():
    text = "집에 있어서 못 갔다. 「매우 혁신적인」이라는 표현을 썼다."
    found = scan().scan(text)
    assert not any(f["category"] == "translationese" for f in found)
    assert all(not f["auto"] for f in found)
    assert all(f["quoted"] for f in found)


def test_density_counts_weighted_hits_per_hundred_eojeol():
    clean = scan().density("회의가 끝나면 요약을 참석자 메일로 보냅니다.", [])
    assert clean == {"eojeol": 6, "hits": 0, "weighted": 0.0, "density": 0.0, "grade": "낮음",
                     "by_category": {}}
    text = "매우 다양한 혁신적인 서비스를 통해 고객의 니즈를 효과적으로 극대화합니다."
    d = scan().density(text, scan().scan(text))
    assert d["grade"] == "높음" and d["hits"] >= 5


def test_the_server_check_fixes_only_safe_phrases_outside_the_material():
    module = skill_scripts.entry("generic-language-killer")
    text = "사업을 추진함에 있어서 매우 다양한 의견을 들었다.\n역량 강화 사업은 계속한다."
    found = module.check(text, "자료: 역량 강화 사업 공고")
    assert [(f["kind"], f["text"], f["placeholder"]) for f in found] == [
        ("translationese", "추진함에 있어서", "추진할 때"), ("intensifier", "매우 ", "")]
    for f in found:
        assert {"kind", "text", "line", "start", "end", "placeholder"} <= set(f)
    fixed = module.fix(text, found)
    assert fixed == "사업을 추진할 때 다양한 의견을 들었다.\n역량 강화 사업은 계속한다."
    assert module.check(fixed, "자료: 역량 강화 사업 공고") == []
    assert module.summary(found).startswith("상투어 2개 고침")
    assert module.summary([]) == ""


def test_scan_cli_reports_density_before_and_after():
    result = _run("generic-language-killer", "scan", {
        "text": ("업무를 수행함에 있어서 매우 중요한 것은 소통이라고 할 수 있습니다. "
                 "다양한 방법이 있습니다."),
        "apply": True,
    })
    row = result["items"][0]
    assert row["fixed"] == "업무를 수행할 때 중요한 것은 소통입니다. 다양한 방법이 있습니다."
    assert row["after"]["density"] < row["density"]
    assert result["placeholders"] == ["[구체적 항목]"]
    assert "concretize" in result["guide"]
    bad = _run_raw("generic-language-killer", "scan", {"text": "x", "categories": ["nope"]})
    assert bad["ok"] is False
