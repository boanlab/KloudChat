"""A writer that cites a project paper by its file name or arXiv id (「[2309.06180]」) has the
citation numbered like every other, so it reaches the reference list."""

from __future__ import annotations

from app.services import citations

SOURCES = [
    {"ordinal": 1, "title": "Efficient Memory Management for LLM Serving with PagedAttention",
     "fileName": "2309.06180.pdf", "url": "https://arxiv.org/abs/2309.06180"},
    {"ordinal": 2, "title": "DistServe", "fileName": "2401.09670.pdf"},
]


def test_file_names_and_arxiv_ids_become_numbers():
    text = ("PagedAttention은 블록 단위로 나눈다[2309.06180]. 분리 서빙은 [2401.09670.pdf] "
            "처리량을 높인다[arXiv:2309.06180v2].")
    assert citations.numbered_by_name(text, SOURCES) == (
        "PagedAttention은 블록 단위로 나눈다[1]. 분리 서빙은 [2] 처리량을 높인다[1].")


def test_unknown_keys_and_numbers_stay():
    text = "값은 [1]이고 [담당자]와 [2208.00001]은 그대로다."
    assert citations.numbered_by_name(text, SOURCES) == text


def test_tidy_lists_a_source_cited_by_its_file_name():
    sections = [{"heading": "관련 연구", "content": "분리[2401.09670]와 페이징[2309.06180]."}]
    tidied, sources = citations.tidy(sections, SOURCES)
    assert "[2401.09670]" not in tidied[0]["content"]
    assert len(sources) == 2
