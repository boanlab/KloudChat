"""Chinese words a writer slipped into a Korean section (「数据来源」, 「两名」) go back to the
writer as that paragraph, instead of being read in Hanja into words Korean does not have
(「수거래원」, 「양명」); a rewrite that loses a number or citation is not taken."""

from __future__ import annotations

import time

import pytest

from app.pipeline import report_stages
from app.services import hangul, report

_SLIPPED = ("기존 연구는 공공 FAQ를 数据来源로 삼아 1,200건을 평가했다[2]. 평가 데이터 集合을两名 "
            "annotators가 검토했다.")
_TABLE = "| 항목 | 값 |\n|---|---|\n| 数据 | 3 |"


def test_hanja_reading_of_a_chinese_word_is_no_korean_word():
    # the class the send-back exists for: the reading is a non-word
    assert "수거래원" in hangul.read_back(_SLIPPED)[0]


def test_only_prose_paragraphs_with_slips_not_in_the_material_are_sent():
    body = (f"첫 문단은 한국어로만 쓴다.\n\n{_SLIPPED}\n\n{_TABLE}\n\n"
            "접미어(接尾語)는 괄호 안에 둔다.")
    assert report.chinese_paragraphs(body) == [(1, ["数据来源", "集合", "两名"])]
    # a word the person or a page wrote is theirs
    assert report.chinese_paragraphs(body, "자료: 数据来源 集合 两名") == []
    # a Chinese paragraph is no slip in Korean
    assert hangul.chinese_slips("本研究使用数据来源进行评估。") == []


def test_a_rewrite_stands_only_when_it_keeps_every_number_citation_and_term():
    good = ("기존 연구는 공공 FAQ를 데이터 출처로 삼아 1,200건을 평가했다[2]. "
            "평가 데이터 집합을 두 명의 annotators가 검토했다.")
    assert report.korean_again(_SLIPPED, good)
    assert not report.korean_again(_SLIPPED, good.replace("[2]", ""))
    assert not report.korean_again(_SLIPPED, good.replace("1,200", "1,300"))
    assert not report.korean_again(_SLIPPED, good.replace("annotators", "평가자"))
    assert not report.korean_again(_SLIPPED, good.replace("두 명의", "两名"))
    assert not report.korean_again(_SLIPPED, "평가했다.")


@pytest.mark.asyncio
async def test_the_slipped_paragraph_is_written_again_and_a_bad_answer_is_left(monkeypatch):
    sent: list[str] = []
    answers = iter([
        ("기존 연구는 공공 FAQ를 데이터 출처로 삼아 1,200건을 평가했다[2]. 평가 데이터 집합을 두 "
         "명의 annotators가 검토했다.", {"inputTokens": 5, "outputTokens": 5}),
        ("숫자를 잃은 답", {"inputTokens": 5, "outputTokens": 5}),
    ])

    async def complete(model, messages, api_key, max_tokens):
        sent.append(messages[-1]["content"])
        return next(answers)

    monkeypatch.setattr(report, "_complete", complete)
    bundle = report_stages.bundle_for(request="RAG 연구 계획서를 써 줘", model="m", api_key="k")
    body = f"머리 문단.\n\n{_SLIPPED}"
    slipped = report.chinese_paragraphs(body)
    out = await report_stages._korean_again(bundle, "선행연구", body, slipped)
    assert out == ("머리 문단.\n\n기존 연구는 공공 FAQ를 데이터 출처로 삼아 1,200건을 평가했다[2]. "
                   "평가 데이터 집합을 두 명의 annotators가 검토했다.")
    assert "「数据来源」" in sent[0] and _SLIPPED in sent[0]
    # the second answer loses the figures: the paragraph stays for the Hanja reading
    out = await report_stages._korean_again(bundle, "선행연구", body, slipped)
    assert out == body


def test_the_slip_checks_finish_on_a_long_blank_input():
    started = time.perf_counter()
    report.chinese_paragraphs(" " * 50_000 + "数据")
    report.korean_again(" " * 50_000, " " * 50_000 + "1")
    assert time.perf_counter() - started < 0.5
