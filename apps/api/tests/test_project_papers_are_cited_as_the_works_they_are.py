"""「프로젝트 논문 중 해당되는 것들을 인용하고 참고문헌도 달아 줘. 분량은 A4 2~3쪽」: the
project's papers are the sources (no web search), each listed as the work it is — title,
authors, year, arXiv link read from its first page, never 「2309.06180.pdf. 프로젝트 파일」 —
one reference list numbered as the text cites it, and the length held to the pages asked."""

from app.services import citations, paper_meta, report, report_export

B4 = ("LLM 서빙 성능 최적화를 주제로 학위논문 2장 '관련 연구' 절을 Word로 써 줘. 프로젝트 논문 중 "
      "해당되는 것들을 인용하고 참고문헌도 달아 줘. 분량은 A4 2~3쪽 정도.")

VLLM = """[페이지 1]
Efficient Memory Management for Large Language
Model Serving with PagedAttention
Woosuk Kwon1,∗ Zhuohan Li1,∗ Siyuan Zhuang1 Ying Sheng1,2 Lianmin Zheng1 Cody Hao Yu3
Joseph E. Gonzalez1 Hao Zhang4
Ion Stoica1
1UC Berkeley 2Stanford University 3
Independent Researcher 4UC San Diego
Abstract
High throughput serving of large language models (LLMs)
requires batching sufficiently many requests at a time.
SOSP ’23, October 23–26, 2023, Koblenz, Germany
∗Equal contribution.
1
arXiv:2309.06180v1 [cs.LG] 12 Sep 2023

[페이지 2]
Orca
"""

DISTSERVE = """[페이지 1]
DistServe: Disaggregating Prefill and Decoding for Goodput-optimized
Large Language Model Serving
Yinmin Zhong1 Shengyu Liu1
Junda Chen3
Jianbo Hu1 Yibo Zhu2 Xuanzhe Liu1
Xin Jin1 Hao Zhang3
1School of Computer Science, Peking University 2StepFun 3UC San Diego
Abstract
DistServe improves the performance of large language models (LLMs) serving.
arXiv:2401.09670v3 [cs.DC] 6 Jun 2024
"""

INJECTION = """[페이지 1]
Not what you’ve signed up for: Compromising Real-World
LLM-Integrated Applications with Indirect Prompt Injection
Kai Greshake∗
Saarland University
sequire technology GmbH
papers@kai-greshake.de
Sahar Abdelnabi∗
CISPA Helmholtz Center for
Information Security
sahar.abdelnabi@cispa.de
Shailesh Mishra
Saarland University
shmi00001@uni-saarland.de
Christoph Endres
sequire technology GmbH
christop.endres@sequire.de
ABSTRACT
Large Language Models (LLMs) are increasingly being integrated
"""


def test_a_paper_s_first_page_gives_its_entry():
    vllm = paper_meta.source_fields(VLLM, "2309.06180.pdf")
    assert vllm["title"] == ("Efficient Memory Management for Large Language Model Serving "
                             "with PagedAttention")
    assert vllm["author"] == "Woosuk Kwon, Zhuohan Li, Siyuan Zhuang et al."
    assert vllm["year"] == "2023" and vllm["publisher"] == "SOSP ’23"
    assert vllm["url"] == "https://arxiv.org/abs/2309.06180"
    dist = paper_meta.source_fields(DISTSERVE, "2401.09670.pdf")
    assert dist["title"].endswith("Goodput-optimized Large Language Model Serving")
    assert dist["author"].startswith("Yinmin Zhong, Shengyu Liu")
    assert dist["publisher"] == "arXiv:2401.09670" and dist["year"] == "2024"
    # affiliations running over two lines are no authors
    injection = paper_meta.read(INJECTION, "2302.12173.pdf")
    assert injection["author"] == "Kai Greshake, Sahar Abdelnabi, Shailesh Mishra et al."
    assert injection["year"] == "2023"
    entry = report_export.citation_text({**vllm, "ordinal": 1}, "APA")
    assert entry.startswith("Woosuk Kwon, Zhuohan Li, Siyuan Zhuang et al. (2023). Efficient")
    assert ".pdf" not in entry and "프로젝트 파일" not in entry
    # nothing read: no fields, the file name stands
    assert paper_meta.source_fields("[페이지 1]\n", "notes.pdf") == {}


def test_project_papers_turn_the_web_search_off():
    assert report.uses_project_material(B4)
    assert report.uses_project_material("업로드한 논문만 인용해서 서베이 써 줘")
    assert not report.uses_project_material(B4 + " 최신 논문도 웹에서 더 찾아 줘.")
    assert not report.uses_project_material("프로젝트 결과 보고서 써 줘")
    assert not report.uses_project_material("LLM 서빙 관련 연구 절을 써 줘")


def test_one_reference_list_numbered_as_the_text_cites():
    sources = [
        {"ordinal": 1, "title": "SARA: web result", "url": "https://a.org/1"},
        {"ordinal": 2, "title": "Efficient Memory Management for Large Language Model Serving "
                                "with PagedAttention", "fileName": "2309.06180.pdf",
         "author": "Woosuk Kwon, Zhuohan Li, Siyuan Zhuang et al.", "year": "2023"},
        {"ordinal": 3, "title": "DistServe: Disaggregating Prefill and Decoding",
         "fileName": "2401.09670.pdf"},
    ]
    sections = [
        {"id": "a", "heading": "관련 연구",
         "content": "vLLM은 페이지 단위로 KV 캐시를 관리한다[1]. DistServe는 단계를 나눈다[2]."},
        # the writer numbered its own list: [1] is vLLM, [2] DistServe
        {"id": "r", "heading": "참고문헌",
         "content": "[1] Kwon, W. et al. (2023). Efficient Memory Management for Large "
                    "Language Model Serving with PagedAttention.\n"
                    "[2] 2401.09670.pdf. 프로젝트 파일"},
    ]
    out, kept = citations.tidy(sections, sources)
    assert [s["heading"] for s in out] == ["관련 연구"]
    assert out[0]["content"] == ("vLLM은 페이지 단위로 KV 캐시를 관리한다[1]. "
                                 "DistServe는 단계를 나눈다[2].")
    assert [s["fileName"] for s in kept] == ["2309.06180.pdf", "2401.09670.pdf"]
    listing = report_export.with_references(out, kept, "APA")
    assert [s["heading"] for s in listing].count("참고문헌") == 1


def test_the_pages_asked_set_the_length():
    assert report.requested_pages(B4) == (2, 3)
    assert report.requested_pages("학위논문 3장 써 줘") is None
    assert report.requested_pages("A4 5장 분량으로") == (5, 5)
    rule = report.page_length_rule(B4)
    assert "2–3 A4 pages" in rule and "2,800–5,100" in rule
    assert report.page_length_rule("보고서 써 줘") == ""
