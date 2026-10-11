"""A question about KloudChat itself is answered from its own user guide, read in process.

「에이전트를 어떻게 만들어?」 got generic advice (persona A/B, both builds): the chat did not
know the app. The guide's sections ship with the API (`app/help/docs.json`) and the best few
are given to the turn as a trusted block.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

from app.services import app_help

_API = Path(__file__).resolve().parents[1]
_SCRIPT = _API / "scripts" / "build_help.py"
#: P3's question (persona A/B, 2nd run): missed, then answered with a feature that is not there.
_PERSONA = "매주 과장님께 올릴 1쪽 요약을 대신 써 줄 에이전트를 만들고 싶어. 여기서 어떻게 만들어?"


def test_the_shipped_guide_is_the_websites():
    spec = importlib.util.spec_from_file_location("build_help", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not module.SOURCES["ko"].is_dir():
        pytest.skip("the website is not beside the API here")
    shipped = json.loads((_API / "app" / "help" / "docs.json").read_text(encoding="utf-8"))
    assert shipped == module.sections(), "run apps/api/scripts/build_help.py"


@pytest.mark.parametrize("text", [
    "에이전트를 어떻게 만들어?", "스킬은 어디서 추가해?", "어디서 메모리를 지워?",
    "이 앱에서 테마 바꾸는 법 알려줘", "How do I create an agent?", "KloudChat 단축키 뭐야",
    # Persona A/B 2nd run (P3): the reason before the question pushed 「어떻게」 past 12 letters.
    _PERSONA,
    "여기서 내 에이전트 만들 수 있어?", "회의록을 워드 파일로 받고 싶은데 여기서 할 수 있어?",
    "KloudChat에서 파일로 내보내려면 어디를 눌러?", "이 앱에서 결과를 파일로 받으려면?",
    "에이전트에 빠진 정보 질문을 넣고 싶은데 그건 어디서 해?",
])
def test_a_question_about_the_app_is_recognised(text):
    assert app_help.about_app(text)


@pytest.mark.parametrize("text", [
    "어제 회의 메모야. 회의록으로 만들어 줘.", "이 표의 합계를 계산해 줘", "서울 날씨 어때?",
    "여기서 핵심만 세 줄로 요약해 줘", "이 양식에 맞춰 보고서 써 줘",
    "다음 분기 프로젝트 계획 초안 써 줘",
    "과장님께 올릴 1쪽 개조식 요약을 써 줘. 결정할 사항을 맨 위에.",
])
def test_a_task_is_not_a_question_about_the_app(text):
    assert not app_help.about_app(text)


def test_the_guide_answers_how_to_make_an_agent():
    found = app_help.search("에이전트를 어떻게 만들어?")
    assert found and found[0]["path"] == "chat/agents"
    block = app_help.block("에이전트를 어떻게 만들어?")
    assert "사용자 지정" in block and block.startswith("# KloudChat 사용 안내")
    english = app_help.search("How do I create an agent?", lang="en")
    assert english and english[0]["lang"] == "en"


def test_the_pattern_stays_fast_on_a_long_message():
    started = time.perf_counter()
    app_help.about_app("에이전트 " * 5000 + "어" * 5000)
    assert time.perf_counter() - started < 0.5


def test_the_persona_question_reads_the_guides_agent_page():
    question = _PERSONA
    assert app_help.search(question)[0]["path"] == "chat/agents"
    block = app_help.block(question)
    assert "에이전트 만들기" in block and "사용자 지정" in block


@pytest.mark.parametrize("question", [_PERSONA, "How do I create an agent?"])
def test_an_app_answer_is_held_to_the_guide(question):
    """The answer names only what the guide describes; the fallback once invented 「매주 정해진
    요일에 자동 발송」. What the guide does not cover is said, with where to look."""
    lang = "en" if question.isascii() else "ko"
    block = app_help.block(question, lang=lang)
    assert "Never describe a feature they do not mention" in block
    assert "automatic sending" in block and "사용 안내에 없는 내용입니다" in block
    # A task that only looked like a question is left alone.
    assert "ignore this block" in block


def test_a_question_the_guide_does_not_cover_says_so_and_points_somewhere(monkeypatch):
    monkeypatch.setattr(app_help, "search", lambda *_a, **_k: [])
    block = app_help.block("여기서 우주선 어떻게 띄워?")
    assert block.startswith("# KloudChat 사용 안내")
    assert "has no section on this question" in block and "사용자 지정 › 에이전트" in block


def _agents_page(lang: str) -> dict[str, str]:
    rows = json.loads((_API / "app" / "help" / "docs.json").read_text(encoding="utf-8"))
    return {
        r["heading"]: r["text"]
        for r in rows
        if r["lang"] == lang and r["path"] == "chat/agents"
    }


def test_the_guide_says_export_is_its_own_tick_not_part_of_finish():
    """Persona A/B 3rd run (P3): the answer said ticking 마무리하기 adds export; in the form
    「다 쓰면 파일로 내보내기」 is a checkbox of its own, off in every base process."""
    ko, en = _agents_page("ko"), _agents_page("en")
    assert "**다 쓰면 파일로 내보내기** 체크 상자가 따로" in ko["묶음"]
    assert "마무리하기가 체크된 채 내보내기는 꺼져" in ko["묶음"]
    assert "a separate **Export a file when done** checkbox" in en["Bundles"]
    # What a question about getting a file back reads holds that line.
    asked = "회의록을 워드 파일로 받고 싶은데 여기서 할 수 있어?"
    assert "다 쓰면 파일로 내보내기" in app_help.block(asked)


def test_the_guide_puts_document_format_where_the_form_shows_it():
    """「템플릿·테마에서 문서 형식도 고릅니다」 was wrong for an agent on a base process: the
    field shows only on 바로 답하기 or 직접 구성; a base's template sets the format."""
    ko = _agents_page("ko")
    shape = ko["템플릿·테마와 자료"]
    assert "**문서 형식** 칸은 보고서를 쓰는 에이전트가 바로 답하기나 직접 구성일 때만" in shape
    assert "문서 형식도 고를 수 있습니다" not in "\n".join(ko.values())


def test_the_guide_says_sources_are_not_resummarised_on_a_schedule():
    ko, en = _agents_page("ko"), _agents_page("en")
    assert "자료를 주기적으로 다시 읽거나 요약하지 않으며" in ko["템플릿·테마와 자료"]
    assert "never runs by itself on a schedule" in en["Creating an agent"]
    # The persona's 「매주」 question reads that line among its sections.
    assert "정해진 때에 저절로 실행되거나" in app_help.block(_PERSONA)


def test_every_agent_page_section_fits_whole():
    """A section longer than the builder's cut loses its end in the help block."""
    spec = importlib.util.spec_from_file_location("build_help", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for lang in ("ko", "en"):
        for heading, text in _agents_page(lang).items():
            assert len(text) < module._MAX, (lang, heading)


@pytest.mark.parametrize("question,lang", [(_PERSONA, "ko"), ("How do I create an agent?", "en")])
def test_a_how_to_answer_is_asked_to_be_brief_and_link_the_guide(question, lang, monkeypatch):
    """R5: a correct how-to answer ran to ~1.1k tokens. The block asks for the starting path,
    at most 6 steps and ~250 words, and a link to the guide page for the rest."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "guide_base_url", "https://guide.example/KloudChat/")
    block = app_help.block(question, lang=lang)
    assert "at most 6 numbered steps" in block and "About 250 words" in block
    # The rule is English; the style example is Korean.
    assert "Style example (Korean" in block and "자세히: 사용 안내 › 에이전트" in block
    prefix = "https://guide.example/KloudChat/" + ("en/" if lang == "en" else "")
    assert f"Guide page: {prefix}docs/chat/agents" in block
    # The brevity rule comes before the sections, so a long section cannot push it out.
    assert block.index("How to answer:") < block.index("## ")
    assert app_help.page_link("admin/index") == "https://guide.example/KloudChat/docs/admin"
    monkeypatch.setattr(settings, "guide_base_url", "")
    assert "Guide page:" not in app_help.block(question, lang=lang)
