"""A paper found as a publisher page with its title cut short (「…Studen....」) and as its DOI
is listed once; a bare fence whose first line names the block (「steps: …」) is that block."""

from __future__ import annotations

import time

from app.services import citations, report

FULL = ("Design and Performance Evaluation of LLM-Based RAG Pipelines for Chatbot Services "
        "in International Student Admissions")
PAGE = {"ordinal": 6, "title": FULL[:-17] + "....", "publisher": "MDPI",
        "url": "https://www.mdpi.com/2079-9292/14/15/3095"}
DOI = {"ordinal": 9, "title": FULL, "author": "Maksuda Khasanova Zafar kizi, Youngjung Suh",
       "year": "2025", "url": "https://doi.org/10.3390/electronics14153095"}
OTHER = {"ordinal": 2, "title": "Design and Performance Evaluation of LLM-Based RAG Pipelines "
         "for Legal Chatbots", "url": "https://example.org/2"}


def test_a_title_cut_short_on_a_search_page_is_the_same_work_as_the_whole_one():
    assert citations.same_works([OTHER, PAGE, DOI]) == {9: 6}
    assert citations.same_works([OTHER, DOI, PAGE]) == {6: 9}
    # a whole title that only starts the same is another work
    assert citations.same_works([OTHER, DOI]) == {}
    short = {**PAGE, "title": "Design and RAG...."}
    assert citations.same_works([short, DOI]) == {}


def test_a_block_named_under_a_bare_fence_becomes_that_block():
    body = ("측정한다.\n\n```\nsteps: 자료 전처리 | 인덱스 구축\n폐쇄형 실행 | 지연 기록\n```\n\n"
            "```\nSteps\n수집 | 모은다\n```")
    assert report.unglue_blocks(body) == (
        "측정한다.\n\n```steps\n자료 전처리 | 인덱스 구축\n폐쇄형 실행 | 지연 기록\n```\n\n"
        "```steps\n수집 | 모은다\n```")
    # a plain code fence stays a code fence
    code = "```\nx = steps(1)\n```"
    assert report.unglue_blocks(code) == code


def test_long_runs_of_spaces_finish_quickly():
    began = time.perf_counter()
    report.unglue_blocks("```" + " " * 50_000 + "\n" + " " * 50_000 + "steps")
    citations.same_works([{**PAGE, "title": "a" * 40 + " " * 50_000 + "...."}, DOI])
    assert time.perf_counter() - began < 0.5
