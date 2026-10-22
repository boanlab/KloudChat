"""A self-hosted writer's odd Korean words (a gloss-mismatched word, a misspelling, English or
Chinese fused into Korean) are named by the judge model as {from, to} pairs and swapped by code
only where they stand verbatim and keep every number, citation, name and acronym; an external
writer's text gets no such pass, and a strict-local writer's checker stays strict-local."""

from __future__ import annotations

import json
import time

import pytest

from app.services import report, word_fix

_TEXT = ("검색 지수(index) 갱신 이후 지수 날짜를 기록한다[3]. 평가는 코드르 실행해 2027년 3월에 "
         "시작하며, citizens 이 체감하는 정확도는 일관되ently 측정한다. RAG 응답은 过时한 근거를 "
         "줄인다.")


def test_a_pair_is_swapped_wherever_it_stands_and_a_gloss_carries_the_word_across():
    out, applied, refused = word_fix.apply(_TEXT, [
        ("지수(index)", "색인(index)"), ("코드르", "코드를"), ("citizens 이", "시민이"),
        ("일관되ently", "일관되게"), ("过时한", "낡은"),
    ])
    assert refused == []
    assert out.startswith("검색 색인(index) 갱신 이후 색인 날짜를 기록한다[3].")
    assert "코드를 실행해 2027년 3월" in out and "시민이 체감" in out
    assert "일관되게 측정" in out and "낡은 근거" in out


@pytest.mark.parametrize(("old", "new", "why"), [
    ("2027년 3월", "2027년 4월", "digits"),
    ("기록한다[3]", "기록한다", "digits"),
    ("기록한다", "**기록한다**", "marks"),
    ("RAG 응답은", "검색 응답은", "name or acronym"),
    ("측정한다", "measure한다", "English added"),
    ("줄인다", "줄이지 않는다", "negation"),
    ("정확도는", "신뢰도는", "word swapped"),
    ("없는 말이", "없는 말을", "not in the text"),
    ("citizens", "시민", "no Korean around"),
])
def test_a_pair_that_changes_numbers_citations_names_or_meaning_or_is_not_verbatim_is_refused(
        old, new, why):
    out, applied, refused = word_fix.apply(_TEXT, [(old, new)])
    assert out == _TEXT and applied == []
    assert refused == [(old, new, why)]


def test_the_persons_own_words_and_code_and_links_stay():
    text = "담당 김민수 팀장이 `코드르` 블록과 [링크](https://a.kr/코드르)를 보낸다. 코드르 실행."
    out, _, refused = word_fix.apply(
        text, [("김민수 팀장이", "김민서 팀장이"), ("코드르", "코드를")], given="담당: 김민수 팀장")
    assert out == text.replace("코드르 실행", "코드를 실행")
    assert refused == [("김민수 팀장이", "김민서 팀장이", "the person's words")]


def test_a_word_inside_a_longer_word_or_an_english_phrase_is_not_taken_apart():
    out, _, refused = word_fix.apply("지수함수와 dense retrieval과 지수(index)를 쓴다.",
                                     [("지수(index)", "색인(index)"), ("retrieval과", "검색과")])
    assert out == "지수함수와 dense retrieval과 색인(index)을 쓴다."
    # a word carried across takes the particle its last syllable needs
    carried = word_fix.apply("지수(index)와 지수를 지수로 본다.", [("지수(index)", "색인(index)")])
    assert carried[0] == "색인(index)과 색인을 색인으로 본다."
    assert refused == [("retrieval과", "검색과", "not in the text")]


def test_code_names_fused_words_chinese_lowercase_english_and_glosses_for_the_checker():
    found = word_fix.candidates(_TEXT + " Kim과 GPU를 쓴다.\n```\nimport 수식x\n```")
    assert {"지수(index)", "citizens 이", "일관되ently", "过时한"} <= set(found)
    assert not any("Kim" in c or "GPU" in c or "수식x" in c for c in found)
    assert "- 일관되ently" in word_fix.prompt(_TEXT)


def test_only_a_self_hosted_writers_korean_prose_is_proofread():
    from app.services import model_tier

    assert word_fix.wanted(model_tier.group("local/qwen3.8-27b"), _TEXT)
    assert word_fix.wanted(model_tier.group("strict-local/qwen3.8-27b"), _TEXT)
    assert not word_fix.wanted(model_tier.group("anthropic/claude-sonnet-5"), _TEXT)
    assert not word_fix.wanted(model_tier.group("deepseek/deepseek-v4.1-flash"), _TEXT)
    assert not word_fix.wanted(model_tier.group("local/qwen3.8-27b"), "표 | 1 | 2")


def test_a_strict_local_writer_is_proofread_inside_its_boundary():
    assert word_fix.checker("strict-local/qwen3.8-27b", "local/qwen3.8-flash") == \
        "strict-local/qwen3.8-flash"
    assert word_fix.checker("strict-local/qwen3.8-27b", "openai/gpt-6") == \
        "strict-local/qwen3.8-27b"
    assert word_fix.checker("strict-local/qwen3.8-27b", "strict-local/qwen3.8-flash") == \
        "strict-local/qwen3.8-flash"
    assert word_fix.checker("local/qwen3.8-27b", "local/qwen3.8-flash") == "local/qwen3.8-flash"


@pytest.mark.asyncio
async def test_a_failed_or_garbled_check_keeps_the_text():
    async def broken(model, messages, api_key, max_tokens):
        raise RuntimeError("down")

    async def garbled(model, messages, api_key, max_tokens):
        return "고칠 말: 코드르", {"inputTokens": 3, "outputTokens": 3}

    for complete in (broken, garbled):
        out, _ = await word_fix.fix(_TEXT, model="local/f", complete=complete, api_key="k")
        assert out == _TEXT


_BODY = ("본 연구는 검색 지수(index)를 매주 갱신하고 지수 날짜를 기록한다[2]. 평가는 2027년 3월에 "
         "시작하며 코드르 실행한 결과를 citizens 이 체감하는 정확도와 비교한다. 이를 통해 민원 "
         "상담의 신뢰성을 높이는 기준을 마련한다.")
_FIXES = {"fixes": [{"from": "지수(index)", "to": "색인(index)", "kind": "wrong_word"},
                    {"from": "코드르", "to": "코드를", "kind": "misspelling"},
                    {"from": "2027년 3월", "to": "2027년 5월", "kind": "wrong_word"}]}


async def _write(monkeypatch, model: str, outline_model: str = "") -> tuple[str, list[str]]:
    checked: list[str] = []

    async def complete(model_id, messages, api_key, max_tokens, **kwargs):
        if messages[-1]["content"].startswith("You proofread Korean prose"):
            checked.append(model_id)
            return json.dumps(_FIXES, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}
        return _BODY, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request="RAG 챗봇 연구 계획서의 연구 방법 절을 써 줘.", model=model, api_key="k",
        outline_model=outline_model, web_search=False,
        approved_plan={"title": "RAG 챗봇 평가 계획", "sections": [{"heading": "연구 방법"}]},
    )]
    sections = next(e["sections"] for e in events if e["type"] == "report")
    return "\n".join(str(s["content"]) for s in sections), checked


@pytest.mark.asyncio
async def test_a_self_hosted_section_gets_the_judges_safe_swaps(monkeypatch):
    body, checked = await _write(monkeypatch, "local/qwen3.8-27b", "local/qwen3.8-flash")
    assert checked == ["local/qwen3.8-flash"]
    assert "색인(index)" in body and "색인 날짜" in body and "코드를 실행한" in body
    # a number is never the checker's to change
    assert "2027년 3월" in body


@pytest.mark.asyncio
async def test_a_strict_local_section_is_checked_by_a_strict_local_model(monkeypatch):
    _, checked = await _write(monkeypatch, "strict-local/qwen3.8-27b", "local/qwen3.8-flash")
    assert checked == ["strict-local/qwen3.8-flash"]


@pytest.mark.asyncio
async def test_an_external_writers_section_is_left_as_written(monkeypatch):
    body, checked = await _write(monkeypatch, "anthropic/claude-sonnet-5", "local/qwen3.8-flash")
    assert checked == []
    assert "지수(index)" in body and "코드르" in body


def test_the_word_checks_finish_on_a_long_blank_input():
    started = time.perf_counter()
    word_fix.candidates(" " * 50_000 + "지수(index)")
    word_fix.apply(" " * 50_000 + "(" * 5_000, [("지수", "색인")])
    assert time.perf_counter() - started < 0.5


async def _deck(monkeypatch, model: str, checker: str) -> tuple[str, list[str]]:
    from app.services import deck

    draft = {"slides": [
        {"title": "평가 계획", "layout": "title"},
        {"title": "평가 설계", "layout": "bullets",
         "bullets": ["검색 지수(index)를 매주 갱신하고 지수 날짜를 남김",
                     "코드르 실행한 결과를 비교", "민원 상담 답변의 정확도를 측정함"],
         "notes": "평가 설계는 검색 지수와 생성 답변을 나누어 봅니다."}]}
    checked: list[str] = []

    async def complete(model_id, messages, api_key, max_tokens=0, **kwargs):
        if messages[-1]["content"].startswith("You proofread Korean prose"):
            checked.append(model_id)
            return json.dumps(_FIXES, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}
        return json.dumps(draft, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    async def no_figures(**kwargs):
        return [], {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(deck, "_complete", complete)
    monkeypatch.setattr(deck.diagrams, "plan", no_figures)
    events = [e async for e in deck._write_slides(
        plan=[{"title": s["title"], "layout": s["layout"]} for s in draft["slides"]],
        title="평가 계획", subtitle="", accent="", request="RAG 챗봇 평가 계획 발표 자료",
        model=model, api_key="k", trusted_context=[], untrusted_context=[],
        usage={"inputTokens": 0, "outputTokens": 0, "outlineInputTokens": 0,
               "outlineOutputTokens": 0}, checker=checker)]
    slides = next(e["slides"] for e in events if e["type"] == "deck")
    return json.dumps(slides, ensure_ascii=False), checked


@pytest.mark.asyncio
async def test_a_self_hosted_deck_draft_is_proofread_once_and_an_external_one_is_not(monkeypatch):
    shown, checked = await _deck(monkeypatch, "local/qwen3.8-27b", "local/qwen3.8-flash")
    assert checked == ["local/qwen3.8-flash"]
    assert "검색 색인(index)을 매주 갱신하고 색인 날짜를" in shown and "검색 색인과 생성" in shown
    shown, checked = await _deck(monkeypatch, "openai/gpt-6", "local/qwen3.8-flash")
    assert checked == [] and "검색 지수(index)를" in shown
