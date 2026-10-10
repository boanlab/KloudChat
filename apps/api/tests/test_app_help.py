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
