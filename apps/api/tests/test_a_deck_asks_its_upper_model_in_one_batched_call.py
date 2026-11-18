"""A staged deck's hard slides and its repairs are asked of the upper model in ONE batched call
each (a JSON object with one entry per slide, rewritten one for one), the upper calls of a deck
are capped, and the model reads the passages about its slides rather than the whole source."""

from __future__ import annotations

import json

import pytest
from test_documents_are_written_in_stages import CATALOGUE, GLM, LOCAL_27B

from app.core.config import settings
from app.services import deck, doc_stages, model_health, source_digest


@pytest.fixture(autouse=True)
def _clean():
    model_health.reset()
    doc_stages.CURRENT.set(doc_stages.from_ids(
        {"hard": GLM["id"], "repair": GLM["id"], "plan": GLM["id"]}, CATALOGUE))
    yield
    doc_stages.CURRENT.set(None)


def _fake(monkeypatch, answers):
    calls: list[tuple[str, str, str]] = []

    async def complete(model, messages, api_key, max_tokens):
        calls.append((model, messages[-1]["content"], "\n".join(m["content"] for m in messages)))
        return answers(len(calls)), {"inputTokens": 100, "outputTokens": 40}

    monkeypatch.setattr(deck, "_complete", complete)
    return calls


def _args(prompts, **extra):
    return dict(prompts=prompts, stage="repair", model=LOCAL_27B["id"], api_key="k",
                request="슬라이드를 고쳐 줘", trusted_context=None, untrusted_context=["자료"],
                research_rule="", titles=[f"장 {i}" for i in range(len(prompts))], usage={},
                **extra)


@pytest.mark.asyncio
async def test_several_slides_are_one_call_and_come_back_one_for_one(monkeypatch) -> None:
    calls = _fake(monkeypatch, lambda n: json.dumps({"slides": [
        {"bullets": ["가"], "notes": "하나"}, {"bullets": ["나"], "notes": "둘"}]}))
    args = _args(["prompt one", "prompt two", "prompt three"])
    texts, spent = await deck.batched_slides(**args)
    assert len(calls) == 1 and calls[0][0] == GLM["id"]
    assert "### Slide 1" in calls[0][1] and "prompt three" in calls[0][1]
    # one entry per slide asked, in order; the slide the answer left out is None (the writer's)
    assert [json.loads(t)["bullets"] for t in texts[:2]] == [["가"], ["나"]] and texts[2] is None
    assert spent == {"inputTokens": 0, "outputTokens": 0}
    assert args["usage"]["stageUsage"]["repair"]["calls"] == 1


@pytest.mark.asyncio
async def test_a_batch_answer_that_is_no_json_leaves_every_slide_to_the_writer(monkeypatch) -> None:
    _fake(monkeypatch, lambda n: "죄송합니다")
    texts, _ = await deck.batched_slides(**_args(["a", "b"]))
    assert texts == [None, None]


@pytest.mark.asyncio
async def test_the_upper_calls_of_a_deck_are_capped(monkeypatch) -> None:
    monkeypatch.setattr(settings, "document_upper_calls", 2)
    calls = _fake(monkeypatch, lambda n: json.dumps({"slides": [{"bullets": ["x"]}]}))
    for _ in range(3):
        await deck.batched_slides(**_args(["p"]))
    # two on the upper model, the third is the writer's
    assert [c[0] for c in calls] == [GLM["id"], GLM["id"], LOCAL_27B["id"]]


@pytest.mark.asyncio
async def test_the_upper_model_reads_the_passages_about_its_slides(monkeypatch) -> None:
    paper = ("Introduction text. " * 200
             + "\nPagedAttention improves vLLM throughput by 2-4x over prior systems. "
             + "Unrelated filler sentence about weather. " * 300)
    calls = _fake(monkeypatch, lambda n: json.dumps({"slides": [{"bullets": ["x"]}]}))
    args = _args(["p"])
    args["untrusted_context"] = [paper]
    args["titles"] = ["PagedAttention throughput"]
    await deck.batched_slides(**args)
    assert "2-4x" in calls[0][2] and len(calls[0][2]) < len(paper) / 2


@pytest.mark.asyncio
async def test_a_rewrite_from_a_batched_answer_is_one_slide_for_one_slide() -> None:
    slides = [{"id": "s1", "layout": "bullets", "title": "하나", "bullets": ["옛"]},
              {"id": "s2", "layout": "bullets", "title": "둘", "bullets": ["옛"]}]
    fixed, spent = await deck.rewrite_slide(
        request="r", slides=slides, target_id="s2", model=LOCAL_27B["id"], api_key="k",
        note="n", text=json.dumps({"bullets": ["새 항목"], "notes": "말할 내용입니다."}))
    assert fixed["id"] == "s2" and fixed["bullets"] == ["새 항목"] and fixed["title"] == "둘"
    assert spent == {"inputTokens": 0, "outputTokens": 0} and len(slides) == 2


def test_a_deck_sends_fewer_slides_up_than_a_report_does() -> None:
    table = "분기,a,b\n1분기,100,200\n2분기,110,210\n3분기,120,220"
    slides = [{"layout": "title", "title": "표지"}, {"layout": "chart", "title": "매출 추이"},
              {"layout": "table", "title": "비교표"}, {"layout": "metrics", "title": "핵심 수치"},
              {"layout": "bullets", "title": "요약과 결정"}, {"layout": "bullets", "title": "배경"},
              {"layout": "bullets", "title": "과제"}, {"layout": "closing", "title": "끝"}]
    hard = doc_stages.hard_slides(slides, table)
    assert len(hard) <= 2 and 4 in hard or len(hard) <= 2
    assert settings.document_deck_hard_share < settings.document_hard_share


def test_a_focused_digest_puts_the_sentences_about_the_slide_first() -> None:
    text = ("Opening paragraph. " * 60 + "\n"
            + "".join(f"Filler item {i} was measured at {i}0 ms in the lab setup here.\n"
                      for i in range(40))
            + "The cache hit rate reached 93% in the key experiment on the large model.\n")
    plain = source_digest.digest_one(text)
    focused = source_digest.digest_one(text, "cache hit rate experiment")
    assert "93%" in focused and "93%" not in plain


def test_the_count_a_deck_is_named_with_is_the_total_beside_a_parts_count() -> None:
    ask = ("전 직원 대상 정보보안 기본 교육 슬라이드 8장을 만들어 줘. 피싱, 비밀번호·MFA, "
           "사고 발생 시 신고 절차, 퀴즈 1장 포함.")
    assert deck.requested_slides(ask) == 8
    # two counts both named with the deck stay no total
    assert deck.requested_slides("슬라이드 8장 또는 슬라이드 10장") is None


# -- notes are instructions, a cut-off batch, the outline's retries -------------------------------


def test_a_line_of_the_note_never_stays_on_the_slide() -> None:
    note = ("Of the 차시 sequence in the material, 7장 is missing from the deck. 7장은 필수 요소로 "
            "확인. Put the material's sequence on this slide in full.")
    slide = {"id": "s", "layout": "bullets", "title": "일정",
             "bullets": ["7장은 필수 요소로 확인", "1~6차시 수업 계획",
                         "7장은 필수 요소로 확인합니다"],
             "body": "", "notes": "7장은 필수 요소로 확인"}
    out = deck.strip_note_echo(slide, note)
    assert out["bullets"] == ["1~6차시 수업 계획"] and out["notes"] == ""
    # a line of the slide's own is never taken for the note's
    own = {"bullets": ["컨테이너 격리는 커널 공유로 가벼움"], "notes": "발표자 설명 문장입니다."}
    assert deck.strip_note_echo(own, note) == own
    assert deck.strip_note_echo(own, "") == own


@pytest.mark.asyncio
async def test_a_rewrite_drops_the_note_it_was_written_under() -> None:
    slides = [{"id": "s1", "layout": "bullets", "title": "일정", "bullets": ["옛"]}]
    note = "이 장에 차시 전체 흐름을 넣어 주세요. 7장은 필수 요소로 확인."
    text = json.dumps({"bullets": ["1~3차시 기초", "7장은 필수 요소로 확인"],
                       "notes": "차시 흐름을 말합니다."})
    fixed, _ = await deck.rewrite_slide(request="r", slides=slides, target_id="s1",
                                        model=LOCAL_27B["id"], api_key="k", note=note, text=text)
    assert fixed["bullets"] == ["1~3차시 기초"]


@pytest.mark.asyncio
async def test_a_batch_cut_off_by_its_budget_keeps_its_whole_entries(monkeypatch) -> None:
    whole = json.dumps({"bullets": ["가"], "notes": "하나"}, ensure_ascii=False)
    cut = '{"slides": [' + whole + ', {"bullets": ["나", "다'
    _fake(monkeypatch, lambda n: cut)
    texts, _ = await deck.batched_slides(**_args(["a", "b", "c"]))
    # the entry that came whole is used; the cut one and the missing one are the writer's
    assert json.loads(texts[0])["bullets"] == ["가"] and texts[1:] == [None, None]


@pytest.mark.asyncio
async def test_a_slide_the_batch_lost_is_written_by_the_writer_alone(monkeypatch) -> None:
    calls = _fake(monkeypatch, lambda n: '{"slides": []}' if n == 1 else json.dumps(
        {"bullets": ["직접 쓴 항목"], "notes": "말할 내용입니다."}))
    texts, _ = await deck.batched_slides(**_args(["a"]))
    assert texts == [None]
    slides = [{"id": "s1", "layout": "bullets", "title": "장", "bullets": ["옛"]}]
    fixed, _ = await deck.rewrite_slide(request="r", slides=slides, target_id="s1",
                                        model=LOCAL_27B["id"], api_key="k", note="n")
    assert calls[-1][0] == LOCAL_27B["id"] and fixed["bullets"] == ["직접 쓴 항목"]


def test_a_slide_with_a_long_note_is_kept_out_of_the_batch() -> None:
    assert deck.BATCH_NOTE_CHARS == 500
    assert "never text for the slide" in deck._BATCH_PROMPT


@pytest.mark.asyncio
async def test_an_unsound_outline_is_asked_again_once_and_told_why(monkeypatch) -> None:
    doc_stages.CURRENT.set(None)
    english = json.dumps({"title": "T", "slides": [
        {"title": "Intro", "layout": "title"}, {"title": "Problem", "layout": "bullets"},
        {"title": "Idea", "layout": "bullets"}, {"title": "Results", "layout": "bullets"}]})
    korean = json.dumps({"title": "제목", "slides": [
        {"title": "소개", "layout": "title"}, {"title": "문제", "layout": "bullets"},
        {"title": "아이디어", "layout": "bullets"}, {"title": "결과", "layout": "bullets"}]})
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        asked.append(messages[-1]["content"])
        return (english if len(asked) == 1 else korean), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(deck, "_complete", complete)
    events = [e async for e in deck.write(request="논문 발표 슬라이드 4장을 만들어 줘",
                                          model=LOCAL_27B["id"], api_key="k", web_search=False)]
    assert len(asked) == 2 and "rejected" in asked[1] and "Korean" in asked[1]
    assert any(e["type"] == "proposal" for e in events)
