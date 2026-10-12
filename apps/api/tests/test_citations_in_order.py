"""The reference list holds only what the body cites, numbered by first citation."""

import io
import zipfile

from app.services import citations, report_export, research


def _src(n: int, title: str, **more) -> dict:
    return {"id": f"s{n}", "ordinal": n, "title": title, "url": f"https://example.org/{n}",
            **more}


_SOURCES = [_src(n, f"Work {n}") for n in range(1, 7)]


def test_uncited_sources_go_and_the_rest_follow_first_citation():
    sections = [
        {"id": "a", "heading": "개요",
         "content": "요약 연구가 늘었다 [3]. 사실성 문제도 있다 [5, 3]."},
        {"id": "b", "heading": "동향", "content": "| 연구 | 출처 |\n|---|---|\n| 하나 | [1] |\n"
                                                 "범위로 묶어도 [3-5] 같다."},
    ]
    out, kept = citations.cite_order(sections, _SOURCES)
    assert [s["title"] for s in kept] == ["Work 3", "Work 5", "Work 1", "Work 4"]
    assert [s["ordinal"] for s in kept] == [1, 2, 3, 4]
    assert out[0]["content"] == "요약 연구가 늘었다 [1]. 사실성 문제도 있다 [1, 2]."
    assert "| 하나 | [3] |" in out[1]["content"]
    assert "범위로 묶어도 [1, 2, 4] 같다." in out[1]["content"]
    # The panel's 「이 자료를 쓴 절」 reads the same numbers.
    assert citations.cited(out) == [1, 2, 3, 4]


def test_left_alone_when_nothing_is_cited_or_already_in_order():
    plain = [{"id": "a", "heading": "개요", "content": "인용 번호가 없는 문서다."}]
    assert citations.cite_order(plain, _SOURCES) == (plain, _SOURCES)
    ordered = [{"id": "a", "heading": "개요", "content": "[1] 다음 [2]."}]
    two = _SOURCES[:2]
    assert citations.cite_order(ordered, two) == (ordered, two)
    # A reference list written into the text keeps the writer's numbering.
    written = [*ordered, {"id": "r", "heading": "참고문헌", "content": "[1] A\n[2] B"}]
    assert citations.cite_order(written, _SOURCES)[1] == _SOURCES


def test_code_links_and_footnotes_are_not_citations():
    sections = [{"id": "a", "heading": "본문",
                 "content": "앞 [2]. 링크 [1](https://x.org) 각주[^1].\n```\nx = a[1]\n```"}]
    out, kept = citations.cite_order(sections, _SOURCES)
    assert [s["title"] for s in kept] == ["Work 2"]
    assert out[0]["content"] == "앞 [1]. 링크 [1](https://x.org) 각주[^1].\n```\nx = a[1]\n```"


def test_exports_number_the_list_as_the_body_does():
    sections = [{"id": "a", "heading": "동향", "content": "사실성 [4]. 요약 [2]."}]
    out, kept = citations.cite_order(sections, _SOURCES)
    with_refs = report_export.with_references(out, kept, "IEEE")
    listing = with_refs[-1]["content"].split("\n\n")
    # IEEE entries numbered once, by their own 「[n]」
    assert listing[0].startswith("[1] ") and "Work 4" in listing[0]
    assert listing[1].startswith("[2] ") and "Work 2" in listing[1]
    assert len(listing) == 2
    docx = zipfile.ZipFile(io.BytesIO(report_export.to_docx("문서", with_refs)))
    body = docx.read("word/document.xml").decode()
    assert "Work 6" not in body and "Work 4" in body


def test_a_venue_is_not_named_as_the_author():
    sources = [
        _src(1, "Atomic Contrastive Verification: Fine-Grained Fact-Checking via Claim "
                "Decomposition", publisher="MDPI"),
        _src(2, "Survey on factuality in large language models", publisher="ACM",
             author="Cunxiang Wang, Xiaoze Liu, Yuanhao Yue 외"),
        _src(3, "국내 교원 연구", publisher="한국교육개발원"),
    ]
    sections = [{"id": "a", "heading": "동향",
                 "content": "| 지식 그래프 기반 | MDPI 연구(2026) [1] |\n"
                            "ACM 논문 [2]은 사실성을 정리했다. 한국교육개발원 연구 [3]도 있다."}]
    out = citations.name_works(sections, sources)[0]["content"]
    assert "Atomic Contrastive Verification 연구(2026) [1]" in out
    assert "Wang 외 논문 [2]" in out
    # An institute named as publisher may be the author: left as written.
    assert "한국교육개발원 연구 [3]" in out


def test_a_cut_off_title_is_read_whole():
    cut = {"title": "FELM: Benchmarking Factuality Evaluation of", "url": "https://a.org/1"}
    whole = {"title": "FELM: Benchmarking Factuality Evaluation of Large Language Models",
             "url": "https://b.org/2"}
    assert research._fuller(cut, [cut, whole])["title"] == whole["title"]
    page = "Menu\n# FELM: Benchmarking Factuality Evaluation of Large Language Models\nAbstract"
    assert research._titled_from_page(cut, page)["title"] == whole["title"]
    # A whole title is not touched.
    assert research._titled_from_page(whole, page) == whole


# ── the writer's own 참고문헌 beside the server's list ──────

RESTATED = (
    "[1] A systematic survey of text summarization: From statistical methods to large "
    "language models. ACM Computing Surveys.\n\n"
    "[2] Hu, Q., Long, Q., & Wang, W. (2025). Decomposition dilemmas: Does claim "
    "decomposition boost or burden fact-checking performance? In Proceedings of the 2025 "
    "Conference.\n\nAlbuquerque, New Mexico.\n"
)
SOURCES_R08 = [
    {"title": "A systematic survey of text summarization: From statistical methods to large "
              "language models", "url": "https://doi.org/10.1/a", "ordinal": 1},
    {"title": "Decomposition dilemmas: Does claim decomposition boost or burden fact-checking "
              "performance?", "url": "https://doi.org/10.1/b", "ordinal": 2},
]


def test_a_reference_list_restating_the_server_s_sources_is_dropped():
    sections = [{"heading": "배경", "content": "요약 연구가 있다[1]. 분해도 있다[2]."},
                {"heading": "참고문헌", "content": RESTATED}]
    out, sources = citations.tidy(sections, SOURCES_R08)
    assert [s["heading"] for s in out] == ["배경"]
    assert len(sources) == 2


def test_an_entry_the_server_does_not_list_keeps_the_section():
    extra = RESTATED + "\n[3] 홍길동. (2024). 『국내 요약 평가 연구』. 한국정보과학회.\n"
    sections = [{"heading": "배경", "content": "요약 연구가 있다[1]."},
                {"heading": "참고문헌", "content": extra}]
    out = citations.drop_restated_references(sections, SOURCES_R08)
    assert out[1]["content"] == "[3] 홍길동. (2024). 『국내 요약 평가 연구』. 한국정보과학회."
