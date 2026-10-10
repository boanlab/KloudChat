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
])
def test_a_question_about_the_app_is_recognised(text):
    assert app_help.about_app(text)


@pytest.mark.parametrize("text", [
    "어제 회의 메모야. 회의록으로 만들어 줘.", "이 표의 합계를 계산해 줘", "서울 날씨 어때?",
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
