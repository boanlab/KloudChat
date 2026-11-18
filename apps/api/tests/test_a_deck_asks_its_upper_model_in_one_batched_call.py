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
