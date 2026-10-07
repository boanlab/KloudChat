"""Checks only find; one repair rewrites; a rewrite is kept only when the check that
raised it no longer does, and when it keeps its citations (adding only its evidence's)."""

from __future__ import annotations

import asyncio
import json

from app.services import verify


def _doc(content: str) -> verify.Doc:
    return verify.Doc([{"heading": "a", "content": content}], material="판매가 20,000원")


def test_two_checks_on_one_sentence_become_one_finding_with_both_reasons():
    doc = _doc("판매가는 2,000원입니다 [1]. 끝.")

    async def first(d):
        sentence = "판매가는 2,000원입니다 [1]."
        return [verify.Finding(0, sentence, "a", "값이 다르다.", rank=3)], {}

    async def second(d):
        sentence = "판매가는 2,000원입니다 [1]."
        return [verify.Finding(0, sentence, "b", "자릿수가 다르다.", rank=1)], {"inputTokens": 4}

    found, usage = asyncio.run(verify.find(doc, [first, second]))
    assert len(found) == 1 and "또한 자릿수가 다르다." in found[0].why and found[0].rank == 1
    assert usage["inputTokens"] == 4


def test_a_rewrite_is_kept_only_when_fixed_and_cited_as_before():
    sentence = "판매가는 2,000원입니다 [1]."
    doc = _doc(f"{sentence} 고정비는 300만 원입니다. 끝.")
    findings = [
        verify.Finding(0, sentence, "settled", "정해진 값은 20,000원",
                       still=lambda s: "2,000원" in s),
        verify.Finding(0, "고정비는 300만 원입니다.", "settled", "3,000만 원", may_cite=[4]),
    ]
    replies = iter([["판매가는 20,000원입니다.", "고정비는 3,000만 원입니다 [4]."]])

    async def complete(model, messages, api_key, max_tokens):
        return json.dumps(next(replies), ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    sections, left, _ = asyncio.run(
        verify.repair(doc, findings, complete=complete, model="m", api_key="k"))
    body = sections[0]["content"]
    # The first lost its [1]: refused. The second added its evidence's [4]: kept.
    assert sentence in body and "고정비는 3,000만 원입니다 [4]." in body
    assert [f.code for f in left] == ["settled"] and left[0].sentence == sentence


def test_a_rewrite_still_wrong_by_its_check_is_refused():
    sentence = "판매가는 2,000원입니다."
    doc = _doc(sentence)
    finding = verify.Finding(0, sentence, "settled", "20,000원", still=lambda s: "2,000원" in s)

    async def complete(model, messages, api_key, max_tokens):
        return json.dumps(["판매가는 약 2,000원입니다."], ensure_ascii=False), {}

    sections, left, _ = asyncio.run(
        verify.repair(doc, [finding], complete=complete, model="m", api_key="k"))
    assert sections[0]["content"] == sentence and left == [finding]


def test_the_model_checks_run_once_and_the_code_checks_again_after_the_repair():
    calls = {"code": 0, "model": 0}
    doc = _doc("판매가는 2,000원입니다.")

    async def code(d):
        calls["code"] += 1
        wrong = "2,000원" in d.sections[0]["content"]
        return ([verify.Finding(0, "판매가는 2,000원입니다.", "c", "20,000원",
                                still=lambda s: "2,000원" in s)] if wrong else []), {}

    @verify.model_check
    async def judged(d):
        calls["model"] += 1
        return [], {"inputTokens": 2}

    async def complete(model, messages, api_key, max_tokens):
        return json.dumps(["판매가는 20,000원입니다."], ensure_ascii=False), {"inputTokens": 3}

    sections, remaining, usage = asyncio.run(verify.verify_and_repair(
        doc, [code, judged], complete=complete, model="m", api_key="k"))
    assert sections[0]["content"] == "판매가는 20,000원입니다." and remaining == []
    assert calls == {"code": 2, "model": 1} and usage["inputTokens"] == 5


def test_a_finding_no_check_can_confirm_is_shown_and_left_as_written():
    """A web claim or a teaching judgement is a judge's word: listed for the reader, never
    rewritten on it alone."""
    sentence = "시장은 3,821억 원입니다."
    doc = _doc(sentence)

    @verify.model_check
    async def web(d):
        return [verify.Finding(0, sentence, "fact", "3,800억이라 한다", auto=False)], {}

    async def complete(model, messages, api_key, max_tokens):
        raise AssertionError("nothing to repair")

    sections, remaining, _ = asyncio.run(verify.verify_and_repair(
        doc, [web], complete=complete, model="m", api_key="k"))
    assert sections[0]["content"] == sentence
    assert [f.code for f in remaining] == ["fact"]
