"""A web result the writer cited all through a section for the attached paper's own design
(its models, retrievers and settings in Korean words, no figures) cites the paper; a sentence
about the result's own page, and a result cited for its own work, keep their number."""

from __future__ import annotations

import time

from app.services import citations

_PAPER = ("## paper.pdf\nRetrieval for Korean public-sector QA. Corpus: 8,400 documents. "
          "Retrieval: BM25 with a Korean morphological analyzer; dense retrieval with "
          "multilingual-e5-large; reranking with a cross-encoder. Generation: a 7B "
          "instruction-tuned model with a citation-forcing prompt. Latency on one GPU.")
_WEB = ("[2] Chatbots for public service agencies\n출처: https://example.org/p\n"
        "We build a chatbot for an Indonesian public agency with a vector database and GPT-4.")
_SOURCES = [
    {"ordinal": 1, "title": "paper.pdf", "attached": True, "origin": "file"},
    {"ordinal": 2, "title": "Chatbots for public service agencies", "origin": "web",
     "url": "https://example.org/p"},
]
_TEXTS = {1: _PAPER, 2: _WEB}

_BODY = (
    "검색 단계에서 BM25 경로에는 한국어 형태소 분석기를 적용한다[2]. "
    "고밀도 경로는 multilingual-e5-large 모델로 문서를 임베딩한다[2]. "
    "생성 단계에는 7B 지시 학습(instruction-tuned) 모델을 공통으로 쓴다[2]. "
    "지연은 단일 GPU에서 측정한다[2]. "
    "생성기에는 citation-forcing 프롬프트를 둔다[2]. "
    "인도네시아 공공기관 챗봇은 vector database와 GPT-4를 결합했다[2]. "
    "이 소집합은 네 하위 집합에서 고르게 뽑는다[2]."
)


def _cites(body: str) -> list[int]:
    out = citations.credit_attachments([{"id": "s", "heading": "연구 방법", "content": body}],
                                       _SOURCES, _TEXTS)
    return [n for m in citations._CITATION.finditer(out[0]["content"])
            for n in citations._numbers(m.group(1))]


def test_the_papers_design_cited_to_a_web_result_moves_to_the_paper():
    # the paper's terms move; the page's own sentence and a sentence with no terms stay
    assert _cites(_BODY) == [1, 1, 1, 1, 1, 2, 2]


def test_a_web_result_cited_for_its_own_work_keeps_its_number():
    own = ("인도네시아 공공기관은 vector database를 썼다[2]. 응답 생성에는 GPT-4를 썼다[2]. "
           "Indonesian agency 사례는 chatbot 도입 효과를 보였다[2]. BM25 비교는 없었다[2].")
    assert _cites(own) == [2, 2, 2, 2]


def test_two_or_three_sentences_are_left_to_the_per_sentence_check():
    few = ("고밀도 경로는 multilingual-e5-large 모델로 문서를 임베딩한다[2]. "
           "생성기에는 citation-forcing 프롬프트를 둔다[2].")
    assert _cites(few) == [2, 2]


def test_the_stand_in_check_finishes_on_a_long_blank_input():
    started = time.perf_counter()
    _cites(" " * 50_000 + "[2]" + " " * 50_000 + "[2]")
    assert time.perf_counter() - started < 0.5
