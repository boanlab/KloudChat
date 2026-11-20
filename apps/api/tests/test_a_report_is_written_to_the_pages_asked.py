"""A report asked for 「A4 3~5쪽」 is written to that length, not to a third of it.

The 11-25 benchmark's reports came to 4.4k–6.8k characters for three-to-five-page asks: the
page rule named only the document's range and warned against running past it, so every
section call wrote 500–900 characters. Each section is now told its share of the pages, its
token budget makes room for it, and a document still short of the pages has its shortest
sections added to once, by the writer, from the material. The computed-table swap no longer
throws away the writer's month columns (r11b's table 1 lost 7·8·9월 to 「3분기 합계」)."""

from __future__ import annotations

import pytest

from app.pipeline import report_stages
from app.pipeline.bundle import Bundle, Request
from app.services import calc, doc_stages, report

ASK = ("아래 자료로 사내 메신저 도입 검토 보고서를 써 줘. 대표이사 결재용이고 A4 3~5쪽 분량.\n"
       "- 후보 3개: A. '팀링크' 연 2,496만 원, B. '온톡 비즈' 연 1,716만 원")
HEADINGS = ["요약", "현황과 문제", "후보 비교", "추천안과 근거", "도입 일정", "위험과 대응"]


def test_each_section_gets_its_share_of_the_pages() -> None:
    targets = report.section_targets(ASK, HEADINGS)
    assert targets[0] == report.SUMMARY_CHARS
    middle = {targets[i] for i in range(1, len(HEADINGS))}
    assert len(middle) == 1
    share = middle.pop()
    # 3~5쪽: about four pages in all
    assert 6_000 <= report.SUMMARY_CHARS + share * 5 <= 7_500
    assert "1,3" in report.length_line(share) or "1,2" in report.length_line(share)


def test_a_request_with_no_pages_sets_no_length() -> None:
    assert report.section_targets("사내 메신저 도입 검토 보고서를 써 줘.", HEADINGS) == {}
    assert report.length_block({}, HEADINGS) == ""


def test_the_page_rule_holds_the_floor_as_well_as_the_ceiling() -> None:
    rule = report.page_length_rule(ASK)
    assert "Falling short" in rule and "middle" in rule


def test_only_a_document_short_of_its_pages_is_lengthened() -> None:
    targets = report.section_targets(ASK, HEADINGS)
    thin = [{"heading": h, "content": "가" * (200 if i == 0 else 600)}
            for i, h in enumerate(HEADINGS)]
    short = report.short_sections(thin, targets)
    assert short and 0 not in short
    full = [{"heading": h, "content": "가" * targets[i]} for i, h in enumerate(HEADINGS)]
    assert report.short_sections(full, targets) == []


def test_a_picture_does_not_count_toward_the_length() -> None:
    body = "본문입니다.\n\n![그림 1](data:image/png;base64," + "A" * 5000 + ")"
    assert report.body_chars(body) < 20


def _bundle() -> Bundle:
    bundle = Bundle(request=Request(text=ASK, model="local/qwen3.8-flash", api_key="k"))
    bundle.plan.headings = list(HEADINGS)
    bundle.usage = {"inputTokens": 0, "outputTokens": 0}
    return bundle


@pytest.fixture(autouse=True)
def _no_stages():
    doc_stages.CURRENT.set(None)
    yield
    doc_stages.CURRENT.set(None)


@pytest.mark.asyncio
async def test_a_short_section_is_added_to_and_keeps_every_sentence(monkeypatch) -> None:
    bundle = _bundle()
    first = "현재 임직원 260명이 카카오톡 개인 계정과 이메일을 함께 씁니다."
    sections = [{"id": f"s{i}", "heading": h,
                 "content": "요약입니다." if i == 0 else f"{first}\n\n둘째 문단입니다."}
                for i, h in enumerate(HEADINGS)]
    asked: list[str] = []

    async def fake(model, messages, api_key, max_tokens):
        asked.append(str(messages[-1]["content"]))
        more = "업무 연락이 개인 계정에 섞여 있어 퇴사자 관리가 어렵습니다. " * 20
        return f"{first}\n\n둘째 문단입니다.\n\n{more.strip()}", {"inputTokens": 1,
                                                                 "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", fake)
    targets = report.section_targets(ASK, HEADINGS)
    events = [e async for e in report_stages._lengthened(bundle, sections, targets, "")]
    assert asked and "Do not change or delete" in asked[0]
    assert events and all(e["type"] == "section" for e in events)
    grown = [s for s in sections[1:] if len(s["content"]) > 600]
    assert grown and all(first in s["content"] for s in grown)
    assert "요약입니다." == sections[0]["content"]


@pytest.mark.asyncio
async def test_an_answer_that_rewrites_the_section_is_not_taken(monkeypatch) -> None:
    bundle = _bundle()
    sections = [{"id": f"s{i}", "heading": h, "content": "원래 문장입니다. 바꾸면 안 됩니다."}
                for i, h in enumerate(HEADINGS)]

    async def fake(model, messages, api_key, max_tokens):
        return "완전히 다른 글입니다. " * 80, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", fake)
    targets = report.section_targets(ASK, HEADINGS)
    async for _ in report_stages._lengthened(bundle, sections, targets, ""):
        pass
    assert all(s["content"] == "원래 문장입니다. 바꾸면 안 됩니다." for s in sections)


COMPUTED = ("# 코드로 계산한 값\n"
            "배송 지연 | 3분기 합계 = 1651 건\n"
            "상품 불량 | 3분기 합계 = 617 건\n"
            "환불·교환 절차 | 3분기 합계 = 1071 건\n"
            "앱 결제 오류 | 3분기 합계 = 596 건\n")
MONTHS = ("표: 유형별 월별 문의 건수\n\n"
          "| 유형 | 7월 | 8월 | 9월 | 3분기 합계 |\n|---|---|---|---|---|\n"
          "| 배송 지연 | 412 | 538 | 701 | 1,651 |\n"
          "| 상품 불량 | 205 | 198 | 214 | 617 |\n"
          "| 환불·교환 절차 | 330 | 352 | 389 | 1,170 |\n"
          "| 앱 결제 오류 | 96 | 88 | 412 | 596 |\n")


def test_the_computed_table_keeps_the_writers_month_columns() -> None:
    out, changed = calc.replace_tables([{"heading": "추이", "content": MONTHS}], COMPUTED)
    text = out[0]["content"]
    assert "| 7월 | 8월 | 9월 |" in text and "| 412 | 538 | 701 |" in text
    # a wrong total takes the computed one; a right one keeps how the writer wrote it
    assert "1,170" not in text and "1071 건" in text and "1,651" in text
    assert changed == 1


def test_a_table_with_no_computed_column_stays_as_written() -> None:
    growth = ("| 유형 | 8월 | 9월 | 증가분 |\n|---|---|---|---|\n"
              "| 배송 지연 | 538 | 701 | 163 |\n| 상품 불량 | 198 | 214 | 16 |\n"
              "| 환불·교환 절차 | 352 | 389 | 37 |\n")
    out, changed = calc.replace_tables([{"heading": "9월", "content": growth}], COMPUTED)
    assert out[0]["content"] == growth and changed == 0


def test_a_table_of_the_computed_columns_only_is_still_replaced() -> None:
    totals = ("| 항목 | 3분기 합계 |\n|---|---|\n| 배송 지연 | 1,600 |\n| 상품 불량 | 617 |\n"
              "| 환불·교환 절차 | 1071 |\n")
    out, changed = calc.replace_tables([{"heading": "합계", "content": totals}], COMPUTED)
    assert changed == 1 and "1651 건" in out[0]["content"]


def test_a_month_column_is_not_a_computed_rate_that_ends_in_that_month() -> None:
    rates = ("# 코드로 계산한 값\n"
             "배송 지연 | 7월->8월 증감률 = 30.58 %\n"
             "상품 불량 | 7월->8월 증감률 = -3.41 %\n"
             "환불·교환 절차 | 7월->8월 증감률 = 6.67 %\n")
    out, _ = calc.replace_tables([{"heading": "추이", "content": MONTHS}], rates)
    assert "| 412 | 538 | 701 |" in out[0]["content"] and "30.58" not in out[0]["content"]
