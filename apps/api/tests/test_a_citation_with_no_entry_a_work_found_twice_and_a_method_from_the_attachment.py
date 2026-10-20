"""A citation number no entry has leaves the text; one paper found twice is listed once; a
method sentence whose terms come from the attached paper cites the paper."""

from __future__ import annotations

from app.services import citations

CUBE = "CuBE: A Customizable Bounds Evaluation Framework for Automated Assessment of RAG Systems"
REVIEW = {"ordinal": 1, "title": "Groundedness-Aware Retrieval in Government Document Chatbots",
          "author": "FR Rumambi, DD Prasetya", "year": "2026", "url": "https://itegam-jetia.org/3013"}
CUBE_DOI = {"ordinal": 2, "title": CUBE, "author": "Bolun Yang, Xu-Hong Yu", "year": "2025",
            "url": "https://doi.org/10.3390/app151910447"}
ARGUS = {"ordinal": 3, "title": "ARGUS: Retrieval-Augmented QA System for Government Services",
         "url": "https://doi.org/10.3390/electronics14122445"}
CUBE_PAGE = {"ordinal": 4, "title": CUBE + ".", "author": "B Yang, X Yu", "year": "2025",
             "url": "https://www.mdpi.com/2076-3417/15/19/10447"}
PAPER = {"ordinal": 5, "title": "t3_paper.pdf", "origin": "file", "attached": True,
         "originLabel": "첨부 파일"}
SOURCES = [REVIEW, CUBE_DOI, ARGUS, CUBE_PAGE, PAPER]


def _section(content: str) -> dict:
    return {"id": "a", "heading": "본문", "level": 1, "status": "done", "content": content}


def test_a_number_no_entry_has_is_dropped_even_when_the_list_is_in_order():
    sources = [REVIEW, CUBE_DOI, ARGUS]
    sections = [_section("검색이 중요하다 [1]. 평가 틀이 있다 [2]. 향후 과제가 남는다. [4][3]")]
    out, kept = citations.cite_order(sections, sources)
    assert out[0]["content"] == "검색이 중요하다 [1]. 평가 틀이 있다 [2]. 향후 과제가 남는다. [3]"
    assert [s["ordinal"] for s in kept] == [1, 2, 3]


def test_one_paper_found_as_its_doi_and_its_publisher_page_is_listed_once():
    sections = [_section("평가 틀이 있다 [2]. 지표를 정해야 한다 [4]. 검색이 중요하다 [1]. "
                         "둘 다 본다 [2, 4].")]
    out, kept = citations.cite_order(sections, SOURCES)
    assert out[0]["content"] == ("평가 틀이 있다 [1]. 지표를 정해야 한다 [1]. 검색이 중요하다 [2]. "
                                 "둘 다 본다 [1].")
    assert [s["title"] for s in kept] == [CUBE, REVIEW["title"], "t3_paper.pdf"]
    assert citations.same_works(SOURCES) == {4: 2}
    # the same link or DOI is one work too; an attached file never merges
    assert citations.same_works([
        {"ordinal": 1, "title": "A", "url": "https://doi.org/10.1234/ab.5"},
        {"ordinal": 2, "title": "B", "url": "http://dx.doi.org/10.1234/AB.5"},
        {"ordinal": 3, "title": "t3_paper.pdf", "attached": True},
        {"ordinal": 4, "title": "t3_paper.pdf", "attached": True}]) == {2: 1}


TEXTS = {
    1: "[1] Groundedness-Aware Retrieval\n출처: https://itegam-jetia.org/3013\n본문 발췌:\n"
       "A systematic review of retrieval in government chatbots and a semantic alignment score.",
    5: "## t3_paper.pdf\nGeneration: a 7B instruction-tuned model with a citation-forcing "
       "prompt. Retrieval: BM25 with a Korean morphological analyzer; dense retrieval with "
       "multilingual-e5-large.",
}


def test_a_method_sentence_whose_terms_only_the_attached_paper_has_cites_the_paper():
    sections = [_section(
        "RAG 시스템은 7B 규모의 지시-tuning된 언어 모델을 기반으로 하며, 인용 출처를 강제하는 "
        "프롬프트(Generation: citation-forcing prompt)를 삽입한다 [1]. "
        "기존 연구는 semantic alignment score를 제안했다 [1]. "
        "밀집 검색에는 다국어 임베딩 모델을 채택한다 [1].")]
    out = citations.credit_attachments(sections, SOURCES, TEXTS)
    assert out[0]["content"] == (
        "RAG 시스템은 7B 규모의 지시-tuning된 언어 모델을 기반으로 하며, 인용 출처를 강제하는 "
        "프롬프트(Generation: citation-forcing prompt)를 삽입한다 [5]. "
        "기존 연구는 semantic alignment score를 제안했다 [1]. "
        "밀집 검색에는 다국어 임베딩 모델을 채택한다 [1].")


def test_an_english_sentence_or_one_sharing_a_term_with_its_page_keeps_its_citation():
    sections = [_section(
        "The model uses a citation-forcing prompt with a 7B generator and BM25 [1]. "
        "정부 챗봇 검토(systematic review)는 BM25와 citation-forcing prompt를 다룬다 [1].")]
    assert citations.credit_attachments(sections, SOURCES, TEXTS) == sections
