"""A citation follows what its sentence says: a work named by its own name, a decimal figure
only one other page has, Latin terms the attached paper has with its hyphens dropped; every
marker form and the app's prose blocks are renumbered with the list."""

from __future__ import annotations

import time

from app.services import citations

VILLAGE = {"ordinal": 1, "title": "Web-Based Village Administration Services Using the "
           "Prototype Method with RAG Chatbot Integration", "author": "C Lestari, MY Rachmad",
           "year": "2026", "origin": "web"}
HALLU = {"ordinal": 3, "title": "HalluDetect: Detecting, Mitigating, and Benchmarking "
         "Hallucinations in Conversational Systems in the Legal Domain",
         "author": "S Anaokar, S Ganatra", "year": "2025", "origin": "web"}
FACE = {"ordinal": 5, "title": "Face4Rag: Factual Consistency Evaluation for Retrieval "
        "Augmented Generation in Chinese", "author": "Yunqi Xu, Tianchi Cai", "year": "2024",
        "origin": "web"}
PUBLIC = {"ordinal": 7, "title": "Evaluating chatbot architectures for public service "
          "delivery", "author": "T Papadopoulos, C Alexopoulos", "year": "2025", "origin": "web"}
PAPER = {"ordinal": 11, "title": "t3_paper.pdf", "origin": "file", "attached": True,
         "originLabel": "첨부 파일"}
SOURCES = [VILLAGE, HALLU, FACE, PUBLIC, PAPER]

PAPER_TEXT = (
    "## t3_paper.pdf\nRetrieval-Augmented Generation for Korean Public-Sector Question "
    "Answering\nKorean text poses specific challenges: agglutinative morphology reduces the "
    "effectiveness of lexical matching, and legal documents use long nested clauses.\n"
    "Corpus: 8,400 documents (3,100 statute articles, 5,300 FAQ entries). Retrieval: BM25 "
    "with a Korean morphological analyzer; dense retrieval with multilingual-e5large; "
    "reranking with a cross-encoder on the top 20 candidates. Error analysis shows that 41% "
    "of remaining errors stem from retrieval misses, and 27% from the generator ignoring "
    "retrieved evidence. The study is limited to a single 7B model and a static corpus; no "
    "user study was conducted. Latency was measured on one GPU without batching.")
TEXTS = {
    1: "[1] Web-Based Village Administration\n출처: https://ieeexplore.ieee.org/1\n본문 발췌:\n"
       "The Selajambe village system integrates a RAG chatbot built on FAISS and Gemini "
       "embeddings. Processing time fell from 1-2 working days to about 60 minutes; SUS "
       "score 80.53, UAT success 100%. 41 respondents took part in 27 sessions.",
    3: "[3] HalluDetect\n출처: https://aclanthology.org/3\n본문 발췌:\nA hallucination "
       "detector for legal consumer-grievance chatbots reaches an F1 of 68.92%.",
    5: "[5] Face4Rag\n출처: https://doi.org/5\n본문 발췌:\nA factual consistency evaluation "
       "benchmark independent of the underlying LLM.",
    7: "[7] Evaluating chatbot architectures\n출처: https://doi.org/7\n본문 발췌:\nPublic "
       "service chatbots must balance functionality, safety, ethics and adaptability.",
    11: PAPER_TEXT,
}


def _section(content: str, heading: str = "본문") -> dict:
    return {"id": heading, "heading": heading, "level": 1, "status": "done",
            "content": content}


def _credited(content: str) -> str:
    return citations.credit_attachments([_section(content)], SOURCES, TEXTS)[0]["content"]


def test_a_work_named_by_its_own_name_cites_that_work():
    assert _credited("RAGPerf의 모듈별 방법[2]과 Face4RAG의 모델 독립적 사실적 일관성 평가 "
                     "접근[3]은 토대가 된다.") == (
        "RAGPerf의 모듈별 방법[2]과 Face4RAG의 모델 독립적 사실적 일관성 평가 접근[5]은 "
        "토대가 된다.")
    # the cited work itself, or a page that names it, keeps its number
    kept = "HalluDetect는 F1 68.92%를 보고했다[3]. L-Face4RAG를 제안한다[5]."
    assert _credited(kept) == kept


def test_a_decimal_figure_only_one_other_page_has_cites_that_page():
    assert _credited("Selajambe 마을 사례는 SUS 점수 80.53을 기록했다[7].") == (
        "Selajambe 마을 사례는 SUS 점수 80.53을 기록했다[1].")
    # a percent only as a percent: the page's 「41 respondents」 is not 「41%」
    assert _credited("검색 실패(41%)와 생성 문제(27%)의 비율을 재검증한다[1].") == (
        "검색 실패(41%)와 생성 문제(27%)의 비율을 재검증한다[11].")
    # a figure the cited page has, or a round one, stays
    kept = "처리 시간이 약 60분으로 줄었고 UAT 성공률 100%를 기록했다[1]. 성공률 100%였다[7]."
    assert _credited(kept) == kept


def test_latin_terms_match_the_paper_with_its_hyphens_dropped():
    # the PDF's 「multilingual-e5large」 is the sentence's 「multilingual-e5-large」
    assert _credited("검색은 BM25 대신 multilingual-e5-large와 cross-encoder 재순위를 "
                     "쓴다[1].") == (
        "검색은 BM25 대신 multilingual-e5-large와 cross-encoder 재순위를 쓴다[11].")
    # Korean method words alone, or a sentence about the cited page, stay
    kept = ("전처리 시 한국어 형태소 분석기를 적용하고 재순위화 모델은 크로스 엔코더를 쓴다[1]. "
            "Selajambe 마을은 FAISS와 Gemini 임베딩으로 행정 문서를 다룬다[1].")
    assert _credited(kept) == kept


def test_every_marker_form_and_the_prose_blocks_follow_the_renumbered_list():
    sources = [{"ordinal": n, "title": f"Work {n}", "url": f"https://example.org/{n}"}
               for n in range(1, 13)]
    sections = [_section(
        "앞 [12]. 묶음 [7, 11]. 연달아 [7][11]. 범위 [7–9]. 물결 [7~9]. 쌍반점 [7; 11].\n\n"
        "| 연구 | 출처 |\n|---|---|\n| 표 안 | [9] |\n\n"
        "```callout\n주의\n- 지연이 늘었다 [8]\n```\n\n```mermaid\ngraph LR\nA[1] --> B\n```")]
    out, kept = citations.cite_order(sections, sources)
    assert [s["title"] for s in kept] == ["Work 12", "Work 7", "Work 11", "Work 8", "Work 9"]
    assert out[0]["content"] == (
        "앞 [1]. 묶음 [2, 3]. 연달아 [2][3]. 범위 [2, 4, 5]. 물결 [2, 4, 5]. 쌍반점 [2, 3].\n\n"
        "| 연구 | 출처 |\n|---|---|\n| 표 안 | [5] |\n\n"
        "```callout\n주의\n- 지연이 늘었다 [4]\n```\n\n```mermaid\ngraph LR\nA[1] --> B\n```")


def test_long_runs_of_spaces_finish_quickly():
    text = "[1" + " " * 50_000 + "x " + "형태소 " + " " * 50_000 + "[1]"
    began = time.perf_counter()
    citations.cite_order([_section(text)], [VILLAGE])
    citations.credit_attachments([_section(text)], SOURCES, TEXTS)
    assert time.perf_counter() - began < 0.5
