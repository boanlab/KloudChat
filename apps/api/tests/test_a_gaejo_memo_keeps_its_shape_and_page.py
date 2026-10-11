"""A 1-page 개조식 보고 keeps its fixed sections, draws no pictures it was not asked for, and is
condensed toward the measured overflow (lineup v1 gaejo-report-1__r0: 7 sections, 2 figures,
3 pages after one condense)."""

from __future__ import annotations

import json

from app.services import diagrams, doc_formats, report, report_checks

#: gaejo-report-1's request, as sent.
REQUEST = (
    "아래 메모를 시설과장님께 올릴 1쪽 개조식 보고로 바꿔 줘. 보고자는 시설팀 김도윤 주무관.\n\n"
    "시설과장님께 올릴 내용 정리입니다. 본관 엘리베이터 3대는 1998년에 설치되어 올해로 28년째 "
    "쓰고 있습니다. 승강기 안전관리법상 설치 후 25년이 지나면 정밀안전검사를 3년마다 받아야 "
    "하고, 작년 정밀검사에서 3호기는 '조건부 합격'(권상기 마모, 1년 내 보수 조건)을 받았습니다. "
    "고장 신고는 2025년 한 해 동안 14건이었는데 2026년에는 1월부터 9월까지만 벌써 19건입니다. "
    "업체(한결엘리베이터) 견적으로는 한 대를 통째로 바꾸는 데 1억 2천만 원, 공사 기간은 대당 "
    "6주 정도라고 합니다. 과장님께서 결정해 주셔야 할 것은 3호기 우선 교체 승인과, 내년 예산 "
    "요구서에 2대분(2억 4천만 원)을 넣을지 여부입니다."
)
#: The planner's outline in that run.
SEVEN = json.dumps({"title": "본관 승강기 교체 검토 보고", "sections": [
    "요약", "현황과 결정할 사안", "안전 관리 현황과 문제점", "교체 비용과 기간 전제",
    "대안 비교", "예산 확보 현황과 제약", "권고안과 다음 단계"]}, ensure_ascii=False)


async def test_the_gaejo_outline_is_the_fixed_one_not_the_planner_s(monkeypatch):
    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return SEVEN, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request=REQUEST, model="m", api_key="k", web_search=False,
        template_id="doc-gaejo-report", may_ask=False,
    )]
    plan = next(e["plan"] for e in events if e["type"] == "proposal")
    assert plan["sections"] == list(doc_formats.GAEJO_OUTLINE)
    assert plan["sections"] == ["현황", "문제점 및 검토", "향후 조치 및 결정 요청", "붙임"]


async def test_a_gaejo_memo_draws_no_figure_it_was_not_asked_for(monkeypatch):
    draft = ("## 현황\n```callout\n핵심\n3호기 우선 교체 필요\n교체 승인 요청\n```\n"
             "- 본관 승강기 3대, 1998년 설치(28년째)\n〔그림: flow | 순차 교체 흐름 | 교체 단계〕\n"
             "## 문제점 및 검토\n- 고장 2025년 14건 → 2026년 9월까지 19건\n"
             "## 향후 조치 및 결정 요청\n- 3호기 우선 교체 승인\n## 붙임\n1. 견적서 1부.\n")

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return draft, {"inputTokens": 1, "outputTokens": 1}

    async def no_planning(**kwargs):
        assert list(kwargs["eligible"]) == [], "개조식에 그림 자리를 열었습니다"
        return [], {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(report, "_complete", complete)
    monkeypatch.setattr(diagrams, "plan", no_planning)
    events = [e async for e in report.write(
        request=REQUEST, model="m", api_key="k", web_search=False,
        template_id="doc-gaejo-report",
        approved_plan={"title": "승강기 교체 검토", "sections": list(doc_formats.GAEJO_OUTLINE)},
    )]
    assert not any(e["type"] == "step" and str(e["id"]).startswith("dia") for e in events)


def test_a_gaejo_memo_that_asks_for_a_figure_still_gets_one():
    assert diagrams.asks_for_diagrams("1쪽 개조식 보고로, 교체 순서는 흐름도로 보여 줘")
    assert not diagrams.asks_for_diagrams(REQUEST)


async def test_condensing_moves_tables_to_the_attachment_and_names_the_lines_to_cut():
    table = ("표: 대안 비교\n| 안 | 비용 | 기간 |\n|---|---|---|\n"
             "| 일괄 교체 | 360,000,000원 | 6주 |\n| 순차 교체 | 360,000,000원 | 18주 |")
    body = "\n".join(f"- 세부 경위 {i}: 고장 신고와 점검 경과가 이어지는 줄." for i in range(30))
    core = "```callout\n핵심\n3호기 우선 교체\n승인 요청\n```"
    sections = [
        {"heading": "현황", "content": f"{core}\n{body}"},
        {"heading": "문제점 및 검토", "content": f"- 순차 교체가 현실적\n{table}"},
        {"heading": "붙임", "content": "1. 견적서 1부."},
    ]
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        asked.append(messages[0]["content"])
        return "- 세부 경위 요약\n※ 세부는 붙임", {}

    out = await report_checks.condense(sections, pages=3, limit=1, complete=complete,
                                       model="m", api_key="k")
    attachment = out[-1]["content"]
    assert "360,000,000원" in attachment and "표: 대안 비교" in attachment
    assert "| 일괄 교체" not in out[1]["content"] and "붙임 1" in out[1]["content"]
    prompt = next(p for p in asked if "「현황」" in p)
    assert "is now 3 pages and must fit in 1" in prompt and "lines or fewer" in prompt
    assert "```callout" in prompt


async def test_a_condense_round_that_loses_a_number_is_dropped_whole():
    sections = [{"heading": "현황", "content": "\n".join(
        f"- 항목 {i}: 세부 경위와 설명이 이어지는 줄이다. 금액 {i * 1000}원." for i in range(40))}]

    async def complete(model, messages, api_key, max_tokens):
        return "- 항목 요약", {}

    out = await report_checks.condense(sections, pages=3, limit=1, complete=complete,
                                       model="m", api_key="k")
    assert out == sections
    assert report_checks.CONDENSE_ROUNDS == 2


async def test_a_fixed_outline_of_one_section_is_offered_not_refused(monkeypatch):
    # Quality lane Q10: a person's 서식 with `fixed_outline` and sections 「본문」 ended every run
    # in 「보고서 개요를 만들지 못했습니다」 (the outline floor of three sections).
    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return json.dumps({"title": "협조 요청", "sections": ["본문"]}, ensure_ascii=False), {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request="각 학과에 보낼 협조 요청 공문을 써 줘.", model="m", api_key="k", web_search=False,
        may_ask=False, process={"fixed_outline": True, "structure": {"sections": ["본문"]}},
    )]
    assert not any(e["type"] == "error" for e in events)
    plan = next(e["plan"] for e in events if e["type"] == "proposal")
    assert plan["sections"] == ["본문"]
