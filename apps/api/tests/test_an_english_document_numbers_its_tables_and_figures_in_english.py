"""A document written in English numbers its tables and figures in English (「**Table 1. …**」,
「*Figure 1. …*」, 「See Table 1 for …」); every reader of captions takes both languages; a
Korean document is numbered as before."""

from __future__ import annotations

import re
import time

from app.pipeline import report_stages
from app.services import (
    captions,
    meeting_memo,
    quality_gate,
    report,
    revise,
    revised_tables,
    table_mend,
)

DECISIONS = ("The team met on Oct 19 to review the redesign of the settings page and agreed on "
             "the next steps for the analytics work.")
ACTIONS = ("| Task | Owner | Deadline |\n|---|---|---|\n"
           "| Finish the event tracking spec | Arjun Patel | Oct 28 |\n"
           "| Deliver 40 icons | Lena Fischer | Oct 30 |")
LED = "See Table 2 for Action Items.\n\n**Table 2. Action Items**"


def _minutes() -> list[dict]:
    return [
        {"heading": "Decisions", "content": f"{DECISIONS}\n\n| Decision | Owner |\n|---|---|\n"
                                            "| Ship the new onboarding flow | Arjun Patel |"},
        {"heading": "Action Items", "content": ACTIONS},
    ]


def _hangul(sections: list[dict]) -> list[str]:
    return [line for s in sections for line in s["content"].split("\n")
            if re.search(r"[가-힣]", line)]


def test_an_english_body_gets_english_captions_and_an_english_pointer():
    out = report.caption_figures_and_tables(_minutes())
    first, second = out[0]["content"], out[1]["content"]
    assert first.startswith(DECISIONS[:-1] + " (Table 1).")
    assert "**Table 1. Decisions**" in first
    assert second.startswith("**Table 2. Action Items**")
    assert not _hangul(out)


def test_an_english_table_the_text_never_names_is_led_into_in_english():
    mended = report_stages.mend_shapes(_minutes(), [{"code": "table_without_caption"}])
    assert mended[1]["content"].startswith(LED)
    assert not _hangul(mended)
    assert quality_gate.report_findings(mended) == []


def test_an_english_sentence_about_a_table_is_not_taken_for_its_caption():
    sections = [{"heading": "Action Items", "content": (
        "The team agreed on owners and deadlines for every open task in the meeting.\n\n"
        "Table 1 lists the action items.\n\n" + ACTIONS)}]
    body = report.caption_figures_and_tables(sections)[0]["content"]
    assert "Table 1 lists the action items.\n\n**Table 1. Action Items**\n\n| Task" in body


def test_cite_writes_an_english_lead_or_bracket():
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert table_mend.cite(f"**Table 3**\n\n{table}", "Table 3") \
        == f"See Table 3.\n\n**Table 3**\n\n{table}"
    assert table_mend.cite(f"The rows are below.\n\n**Table 3. Costs**\n\n{table}", "Table 3") \
        == f"The rows are below (Table 3).\n\n**Table 3. Costs**\n\n{table}"


def test_the_writer_s_references_and_figures_are_numbered_in_english():
    sections = [{"heading": "Method", "content": (
        "The pipeline has three stages that hand data to each other, as 〔그림〕 shows in full.\n\n"
        "![Pipeline](x.png)\n\n*그림: Pipeline*\n\n"
        "Each stage is drawn again below with its inputs and outputs for the review.\n\n"
        "```mermaid\nflowchart LR\nA-->B\n```\n\n"
        "The costs of each stage are in 〔표〕.\n\n| Stage | Cost |\n|---|---|\n| A | 3 |")}]
    body = report.caption_figures_and_tables(sections)[0]["content"]
    assert "as Figure 1 shows in full." in body
    assert "![Figure 1. Pipeline](x.png)" in body
    assert "*Figure 2. Diagram*" in body
    assert "The costs of each stage are in Table 1." in body
    assert "**Table 1. Method**" in body
    assert "〔" not in body and not re.search(r"[가-힣]", body)


def test_a_dangling_english_reference_goes_with_its_bracket():
    sections = [{"heading": "Scope", "content": (
        "The scope covers the settings page and the onboarding flow only (see 〔그림〕). "
        "Nothing else changes in this release of the product.")}]
    body = report.caption_figures_and_tables(sections)[0]["content"]
    assert body == ("The scope covers the settings page and the onboarding flow only. "
                    "Nothing else changes in this release of the product.")


def test_korean_references_code_wrote_into_an_english_document_become_english():
    sections = [
        {"heading": "Decisions", "content": f"{DECISIONS[:-1]}(표 1).\n\n**표 1. Decisions**\n\n"
                                            "| Decision | Owner |\n|---|---|\n| Ship v2 | Arjun |"},
        {"heading": "Action Items", "content": f"Action Items은(는) 표 2와 같습니다.\n\n"
                                               f"**표 2. Action Items**\n\n{ACTIONS}"},
    ]
    out = report.caption_figures_and_tables(sections)
    assert out[0]["content"].startswith(DECISIONS[:-1] + " (Table 1).\n\n**Table 1. Decisions**")
    assert out[1]["content"].startswith(LED)
    assert not _hangul(out)


def test_the_quality_read_reads_english_captions():
    out = report_stages.mend_shapes(_minutes(), [{"code": "table_without_caption"}])
    assert quality_gate.report_findings(out) == []
    swapped = [out[1], out[0]]
    codes = {x["code"] for x in quality_gate.report_findings(swapped)}
    assert "table_numbering" in codes
    bare = [{"heading": "Action Items", "content": "**Table 1. Action Items**\n\n" + ACTIONS}]
    assert [x for x in quality_gate.report_findings(bare)] \
        == [{"code": "unmentioned", "where": "Table 1", "detail": ""}]


def test_english_removal_asks_name_their_tables_and_figures():
    assert revise.numbered_removals("Remove table 2.") == [("표", 2)]
    assert revise.numbered_removals("Please delete tables 2 and 3 and Figure 1") \
        == [("표", 2), ("표", 3), ("그림", 1)]
    assert revise.numbered_removals("Remove table 2 and rewrite the introduction") == []
    assert revise.numbered_removals("표 2와 그림 1은 빼 주세요") == [("표", 2), ("그림", 1)]


def test_removing_a_table_from_an_english_document_renumbers_the_rest():
    doc = report_stages.mend_shapes(_minutes(), [{"code": "table_without_caption"}])
    doc[1]["content"] += "\n\nThe owners confirm their deadlines by Friday, as listed in Table 2."
    parts, taken, absent = revise.drop_numbered(doc, revise.numbered_removals("remove Table 1"))
    assert (taken, absent) == (["1절의 표 1"], [])
    assert parts[0]["content"] == DECISIONS
    again = report.caption_figures_and_tables(parts)
    body = again[1]["content"]
    assert body.startswith("See Table 1 for Action Items.\n\n**Table 1. Action Items**")
    assert body.endswith("by Friday, as listed in Table 1.")
    assert quality_gate.report_findings(again) == []


def test_a_sentence_about_a_removed_english_table_goes_and_a_phrase_leading_to_it_too():
    text = ("See Table 2 for the costs. The budget rises by 10%, as shown in Table 2, next year. "
            "Costs stay flat (Table 2).")
    assert revise._drop_references(text, "표 2") == \
        "The budget rises by 10% next year. Costs stay flat."


def test_revised_tables_drift_and_memo_pointers_read_english_captions():
    assert revised_tables.CAPTION.match("**Table 2. Action Items**")
    assert revise.drift_faults("Costs are in Table 9 here.", "Costs are in Table 9 here.", []) \
        == ["It cites Table 9, which the document does not have"]
    assert revise.drift_faults(f"See Table 1.\n\n**Table 1. X**\n\n{ACTIONS}",
                               f"See Table 1.\n\n**Table 1. X**\n\n{ACTIONS}", []) == []
    content = ("We met on Monday to plan the launch week for the product.\n"
               "The venue was not decided at the meeting (Table 1).\n\n**Table 1. Venues**\n\n"
               "| Venue | Status |\n|---|---|\n| Room 3A | open |")
    dropped = meeting_memo.drop(content, "The venue was not decided at the meeting (Table 1).")
    assert dropped.startswith("We met on Monday to plan the launch week for the product (Table 1).")


def test_a_korean_document_is_numbered_as_before():
    sections = [
        {"heading": "1. 추진 배경", "content": (
            "사업의 배경은 다음과 같습니다.\n\n| 항목 | 내용 |\n|---|---|\n"
            "| 예산 | 3억 |\n\n〔그림〕과 같이 세 단계로 나뉩니다.\n\n![구조](x.png)")},
        {"heading": "2. 할 일", "content": "| 할 일 | 담당 |\n|---|---|\n| 검수 | 김도윤 |"},
    ]
    out = report.caption_figures_and_tables(sections)
    assert out[0]["content"] == (
        "사업의 배경은 다음과 같습니다(표 1).\n\n**표 1. 추진 배경**\n\n"
        "| 항목 | 내용 |\n|---|---|\n"
        "| 예산 | 3억 |\n\n그림 1과 같이 세 단계로 나뉩니다.\n\n![그림 1. 구조](x.png)")
    assert out[1]["content"] == "**표 2. 할 일**\n\n| 할 일 | 담당 |\n|---|---|\n| 검수 | 김도윤 |"
    assert table_mend.cite(out[1]["content"], "표 2", "2. 할 일") \
        .startswith("할 일은 표 2와 같습니다.")
    assert captions.of_sections(out) == captions.KO


def test_caption_readers_finish_on_fifty_thousand_spaces():
    blank = " " * 50_000
    started = time.perf_counter()
    for text in (f"Table{blank}1", f"표 1{blank}과 같습니다", f"a{blank}(표 1)",
                 f"({blank}〔그림〕)", f"x. Table 1:{blank}", f"- Table{blank}"):
        report._english_references(text)
        report._DANGLING_BRACKET.sub("", text)
        report._TRAILING_TABLE_CAPTION.search(text)
        report._table_title(text)
        quality_gate._STRAY_CAPTION.search(text)
        revised_tables.CAPTION.match(text)
        meeting_memo._POINTER.findall(text)
    report._renumber_references([{"heading": "A", "content": f"Table{blank}1 Figure{blank}2"}],
                                captions.EN)
    revise.numbered_removals(f"remove table 2{blank}and")
    revise.numbered_removals(f"delete{blank}tables 2,{blank}")
    revise._drop_references(f"See Table 2{blank}x, as shown in{blank}Table 2", "표 2")
    table_mend.cite(f"**Table 1**{blank}\n\n| a |\n|---|\n| 1 |", "Table 1")
    meeting_memo.drop(f"a (Table 1){blank}", "a (Table 1)")
    assert time.perf_counter() - started < 0.5
