"""Quality step label for the reader, and the figure judge's scope."""

from __future__ import annotations

import asyncio

from app.services import key_figures as kf
from app.services import report

RFP = (
    "2. 사업기간 : 계약일로부터 5개월\n"
    "3. 소요예산 : 금 99,000,000원(부가가치세 포함)\n"
    "ACC-01 대표 홈페이지 전 페이지(약 1,200쪽) 접근성 진단 및 결과 보고\n"
    "ACC-02 한국형 웹 콘텐츠 접근성 지침 2.2 24개 검사항목 기준 개선\n"
    "ACC-04 이미지 대체텍스트 일괄 정비(약 3,400건)\n"
)
SETTLED = [
    kf.Figure("소요예산", "99,000,000원", []),
    kf.Figure("사업기간", "5개월", []),
    kf.Figure("검사항목 수", "24개", ["검사항목"]),
    kf.Figure("이미지 정비 건수", "3,400건", ["대체텍스트"]),
]


def test_sentences_that_copy_the_material_are_not_put_to_the_judge(monkeypatch):
    sections = [{"heading": "제안 요약", "content": (
        "한국농식품정보원은 「대표 홈페이지 전 페이지(약 1,200쪽) 접근성 진단 및 결과 보고」를 "
        "통해 현재 웹페이지의 접근성 수준을 객관적으로 파악합니다. "
        "본 사업의 소요예산은 부가가치세 포함 99,000,000원이며, 인건비로 구성됩니다. "
        "검사항목별 점검표 30종을 함께 만듭니다."
    )}]
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        asked.append(messages[0]["content"].split("문장: ", 1)[1].split("\n", 1)[0])
        return "예", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    found, _ = asyncio.run(report.judge_figure_sentences(
        sections, SETTLED, judge="j", api_key="k", material=RFP))
    assert asked == ["검사항목별 점검표 30종을 함께 만듭니다."]
    assert [f[1] for f in found] == asked


def test_the_reader_sees_a_label_not_the_instruction():
    why = ("이 문장은 확정 수치(소요예산 99,000,000원, 사업기간 5개월.)와 어긋난다. "
           "정해진 값으로 고치고 그 값으로 계산한 수치도 맞춰라. "
           "다른 경우를 말하는 문장이면 원문을 그대로 답하라.")
    note = report.reader_note("judged_figure", why)
    assert note == "정해진 수치와 다를 수 있는 문장(확인 필요)"
    assert "맞춰라" not in report.reader_note("schedule_over", "일정이 넘는다. 기간 안으로 옮겨라.")
    web = "웹 자료와 어긋난다: 출시일이 다르다 (근거: 보도자료, https://example.org)"
    assert report.reader_note("fact", web) == web
