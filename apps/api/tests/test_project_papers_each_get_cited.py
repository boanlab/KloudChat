"""A request to cite the project's papers on a topic: each paper that belongs to it is cited."""

# ruff: noqa: E501
from __future__ import annotations

import asyncio

from app.pipeline import report_stages
from app.pipeline.bundle import Bundle, Part, Request
from app.pipeline.stage import Context
from app.services import paper_cites, report

REQUEST = ("LLM 서빙 성능 최적화를 주제로 학위논문 2장 '관련 연구' 절을 Word로 써 줘. "
           "프로젝트 논문 중 해당되는 것들을 인용하고 참고문헌도 달아 줘. 분량은 A4 2~3쪽 정도.")

#: name, title, opening text
FILES = [
    ("2309.06180.pdf", "Efficient Memory Management for Large Language Model Serving with PagedAttention",
     "Serving large language models needs batching; the KV cache memory is large and dynamic. "
     "PagedAttention manages KV cache in blocks and vLLM reaches 2-4x throughput."),
    ("2401.09670.pdf", "DistServe: Disaggregating Prefill and Decoding for Goodput-optimized Large Language Model Serving",
     "DistServe disaggregates prefill and decoding onto different GPUs and optimizes goodput; "
     "it serves 7.4x more requests under latency targets."),
    ("2306.14048.pdf", "H$_2$O: Heavy-Hitter Oracle for Efficient Generative Inference of Large Language Models",
     "The KV cache of generative inference grows with sequence length. Heavy-Hitter Oracle keeps "
     "heavy hitter tokens and evicts the rest, improving throughput up to 29x."),
    ("2211.17192.pdf", "Fast Inference from Transformers via Speculative Decoding",
     "Speculative decoding runs a small approximation model to propose tokens and verifies them "
     "in parallel, a 2-3x speedup on T5-XXL without retraining."),
    ("2307.15043.pdf", "Universal and Transferable Adversarial Attacks on Aligned Language Models",
     "Adversarial suffixes make aligned language models produce objectionable content."),
    ("1708.06733.pdf", "BadNets: Identifying Vulnerabilities in the Machine Learning Model Supply Chain",
     "Backdoored neural networks trained by an outsourced provider misclassify triggered inputs."),
    ("2003.03423.pdf", "Serverless in the Wild: Characterizing and Optimizing the Serverless Workload at a Large Cloud Provider",
     "Characterization of the serverless workload of a cloud provider's function invocations."),
]


def _project():
    items, sources = [], []
    for n, (name, title, _) in enumerate(FILES, 1):
        items.append({"id": f"f{n}", "name": name, "state": "included",
                      "paper": {"title": title}})
        sources.append({"id": f"f{n}", "ordinal": n, "title": title, "fileName": name})
    return items, sources


def _material():
    return ["# 프로젝트 지식\n\n" + "\n\n".join(f"## {name}\n{title}\n{text}"
                                              for name, title, text in FILES)]


DOC = [
    {"id": "s1", "heading": "메모리 관리", "content": "vLLM은 PagedAttention으로 KV cache를 관리한다 [1]."},
    {"id": "s2", "heading": "단계 분리", "content": "DistServe는 prefill과 decoding을 분리한다 [2]."},
    {"id": "s3", "heading": "디코딩 가속",
     "content": "H2O(Heavy-Hitter Oracle)는 KV cache에서 중요한 토큰만 남긴다. "
                "추측 디코딩(Speculative Decoding)은 작은 모델이 초안을 낸다."},
]


def test_the_request_asks_for_the_projects_papers():
    assert paper_cites.asks_project_papers(REQUEST)
    assert paper_cites.asks_project_papers("Cite the project papers that fit the topic.")
    assert not paper_cites.asks_project_papers("학위논문 2장을 Word로 써 줘.")


def test_a_paper_is_named_by_its_own_words_not_by_the_topics():
    items, sources = _project()
    papers = {p.ordinal: p for p in paper_cites.papers_of(items, sources)}
    assert paper_cites.names("H2O(Heavy-Hitter Oracle)는 캐시를 줄인다", papers[3])
    assert paper_cites.names("추측 디코딩(Speculative Decoding)", papers[4])
    # words every title shares name no paper
    assert not paper_cites.names("Large Language Model Serving", papers[3])
    assert not paper_cites.names("Large Language Model Serving", papers[5])
    assert paper_cites.names("DistServe 논문", papers[2])


def test_papers_the_document_discusses_without_citing_are_required():
    items, sources = _project()
    papers = paper_cites.papers_of(items, sources)
    certain, maybe = paper_cites.required(papers, DOC, REQUEST, _material(), report._REFERENCE_HEADING)
    assert {p.ordinal for p in certain} == {3, 4}
    assert {p.ordinal for p in maybe} == {5, 6, 7}


def test_a_paper_on_the_cited_ones_subject_is_required_though_the_document_never_names_it():
    items, sources = _project()
    papers = paper_cites.papers_of(items, sources)
    doc = DOC[:2]
    certain, maybe = paper_cites.required(papers, doc, REQUEST, _material(), report._REFERENCE_HEADING)
    assert 3 in {p.ordinal for p in certain}
    assert not {5, 6, 7} & {p.ordinal for p in certain}


def test_the_planner_and_the_writer_are_told_to_give_each_relevant_paper_its_place():
    items, sources = _project()
    rule = paper_cites.rule(paper_cites.papers_of(items, sources))
    assert "[3] H$_2$O" in rule and "its own" in rule


def test_the_repair_paragraph_keeps_only_figures_its_paper_gives():
    wanted = {3: FILES[2][2] + REQUEST}
    answer = ("### [3] -> 1\nH2O는 중요한 토큰만 유지한다 [3]. 처리량은 최대 29배 향상된다 [3]. "
              "비용은 40% 줄었다 [3].\n### [4] SKIP\n")
    paragraphs = report_stages.paper_paragraphs(answer, wanted, 3)
    at, text = paragraphs[3]
    assert at == 1 and "29배" in text and "40%" not in text and 4 not in paragraphs


async def _run(monkeypatch, sections, answer):
    items, sources = _project()
    calls = []

    async def fake(model, messages, api_key, max_tokens, **_):
        calls.append(messages)
        return answer, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", fake)
    bundle = Bundle(request=Request(text=REQUEST, model="m", api_key="k",
                                    project_sources=items))
    bundle.usage.update(inputTokens=0, outputTokens=0)
    bundle.material.sources = sources
    bundle.material.parts = [Part(t, "attachment") for t in _material()]
    bundle.document.sections = [dict(s) for s in sections]
    events = [e async for e in report_stages.PaperCites().run(bundle, Context())]
    return bundle, events, calls


def test_missing_papers_get_one_targeted_paragraph_and_are_cited(monkeypatch):
    answer = ("### [3] -> 3\nH2O는 어텐션 점수가 큰 토큰만 남겨 KV cache를 줄이며 처리량을 최대 29배 높인다 [3].\n"
              "### [4] -> 3\n추측 디코딩은 작은 모델이 제안한 토큰을 큰 모델이 병렬로 검증해 2~3배 빨라진다 [4].\n"
              "### [5] SKIP\n### [6] SKIP\n### [7] SKIP\n")
    bundle, events, calls = asyncio.run(_run(monkeypatch, DOC, answer))
    assert len(calls) == 1
    cited = paper_cites.cited_numbers(bundle.document.sections)
    assert cited == {1, 2, 3, 4}
    step = next(e for e in events if e.get("id") == "paper-cites")
    assert step["label"] == "논문 2편 인용 보충"
    assert "빠짐" not in step["label"]


def test_a_paper_the_writer_cannot_supply_is_named_in_the_document(monkeypatch):
    bundle, events, _ = asyncio.run(_run(monkeypatch, DOC, "### [3] SKIP\n### [4] SKIP\n"))
    last = bundle.document.sections[-1]["content"]
    assert "H$_2$O" in last and "반영하지 못했습니다" in last
    step = next(e for e in events if e.get("id") == "paper-cites")
    assert "2편 빠짐" in step["label"]


def test_nothing_is_asked_when_every_paper_is_cited(monkeypatch):
    doc = [{"id": "s1", "heading": "관련 연구",
            "content": " ".join(f"주장 [{n}]." for n in range(1, 8))}]
    bundle, events, calls = asyncio.run(_run(monkeypatch, doc, ""))
    assert not calls
    assert events[0]["label"] == "프로젝트 논문 모두 인용됨"
    assert bundle.document.sections == doc


def test_an_unrelated_request_skips_the_stage():
    bundle = Bundle(request=Request(text="보고서 써 줘", model="m", api_key="k"))
    assert report_stages.PaperCites().skip(bundle)
