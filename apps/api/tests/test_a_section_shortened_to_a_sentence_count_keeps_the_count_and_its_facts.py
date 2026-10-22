"""「2절을 두 문장으로 줄여줘」: a rewrite with another sentence count or without the section's
figures and names is sent back to the writer once with the reason; what still misses is said
in the reply."""

from __future__ import annotations

import time

import pytest

from app.pipeline import revise_stages, save_stages
from app.pipeline.stage import Context
from app.services import deck, revise

BEFORE = ("대성유통 협상은 견적서 발송(10/16 목)을 완료했으며, 상대 측의 5% 단가 인하 요구와 자사 "
          "허용 한도인 4% 사이의 폭이 남아 있습니다. 경쟁사 A사의 동일 품목 4% 낮은 단가 제시는 "
          "확정되지 않은 정보입니다.\n\n한솔마트 클레임은 2건을 모두 처리해 반환금 420만원 정산을 "
          "마쳤습니다.")
#: one sentence, the negotiation facts gone (reverify-m)
ONE = ("2026년 10월 3주차에는 대성유통 협상, 한솔마트 클레임 처리, CRM 데이터 정리가 함께 "
       "진행됩니다.")
TWO = ("대성유통은 10/16 견적서를 보냈고 5% 인하 요구와 4% 한도 사이의 폭, A사의 4% 낮은 단가 "
       "제시(확정 아님)가 남았습니다. 한솔마트 클레임 2건은 420만원 정산으로 마쳤습니다.")
ASK = "2절을 두 문장으로 줄여줘"


@pytest.mark.parametrize("words, asked", [
    (ASK, (2, False)), ("2절을 2문장으로 요약해줘", (2, False)),
    ("세 문장 이내로 줄여줘", (3, True)), ("Shorten section 2 to two sentences", (2, False)),
    ("3줄 요약을 넣어줘", (0, False)), ("두 문장을 하나로 합쳐줘", (0, False)),
])
def test_the_sentence_count_an_ask_names_is_read(words, asked):
    assert revise.sentences_asked(words) == asked


def test_a_shortened_section_is_checked_for_its_count_and_facts():
    assert revise.sentence_count(BEFORE) == 3 and revise.sentence_count(ONE) == 1
    # a list row is not a sentence
    assert revise.sentence_count("5.5% 올랐다. 「확정」이다.」\n- 남은 일") == 2
    assert revise.lost_facts(BEFORE, ONE) == ["10/16", "5%", "4%", "2건", "420만",
                                              "A사(확정 아님)"]
    assert revise.shortening_faults(BEFORE, TWO, ASK) == []
    assert revise.shortening_faults(BEFORE, BEFORE, "2절 표현을 다듬어줘") == []
    faults = revise.shortening_faults(BEFORE, ONE, ASK)
    assert faults[0] == ("The section must be exactly 2 sentences of its own content; the last "
                         "version had 1")
    assert "5%, 4%" in faults[1]
    started = time.monotonic()
    revise.sentences_asked("두" + " " * 50000 + "문장")
    revise.lost_facts("A" + " " * 50000, "1" + " " * 50000)
    assert time.monotonic() - started < 0.5


PARTS = [{"id": "s0", "heading": "개요", "content": "주간 보고입니다.", "format": "markdown"},
         {"id": "s1", "heading": "금주 진행 현황", "content": BEFORE, "format": "markdown"}]


def _bundle(ask: str = ASK):
    return revise_stages.bundle_for(
        request="주간 업무 보고서를 만들어줘.", instruction=ask, model={"id": "m"},
        planner=None, api_key="k", is_deck=False, title="주간 업무 보고", data={},
        parts=[dict(p) for p in PARTS], material=[],
        plan=revise.Plan(scope="parts", targets=[1], note=ask, clauses={1: [ask]}),
        format_id="", usage={"inputTokens": 0, "outputTokens": 0})


async def _rewrite(monkeypatch, answers, ask: str = ASK):
    notes: list[str] = []
    given = iter(answers)

    async def rewrite_section(**kwargs):
        notes.append(kwargs["note"])
        return next(given), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages._report(), "rewrite_section", rewrite_section)
    bundle = _bundle(ask)
    bundle.plan.order = [1]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    return bundle.document, notes


@pytest.mark.asyncio
async def test_a_one_sentence_rewrite_is_sent_back_with_the_reason(monkeypatch):
    doc, notes = await _rewrite(monkeypatch, [ONE, TWO])
    assert len(notes) == 2
    assert "exactly 2 sentences of its own content; the last version had 1" in notes[1]
    assert "A사" in notes[1]
    assert doc.sections[1]["content"].startswith("대성유통은 10/16")
    assert doc.counted == [] and doc.dropped == []
    assert save_stages.revision_reply(doc, None, is_deck=False) == "2절을 고쳤습니다."


@pytest.mark.asyncio
async def test_what_still_misses_after_the_second_try_is_said(monkeypatch):
    doc, _ = await _rewrite(monkeypatch, [ONE, ONE])
    assert doc.counted == [(2, 2, 1)]
    assert save_stages.revision_reply(doc, None, is_deck=False) == (
        "2절을 고쳤습니다. 2절은 요청한 2문장이 아니라 1문장입니다. "
        "2절에 원래 없던 3주차가 들어갔습니다. 확인해 주세요. "
        "2절에서 10/16, 5%, 4%, 2건, 420만, A사(확정 아님)가 빠졌습니다. "
        "필요하면 다시 넣어 달라고 해 주세요.")
    assert save_stages.revision_reply(doc, None, is_deck=False, english=True) == (
        "Revised section 2. Section 2 has 1 sentence, not the 2 asked. Section 2 now states "
        "3주차, which the section and the material did not; please check. On section 2, 10/16, 5%, "
        "4%, 2건, 420만, A사(확정 아님) dropped out of the rewrite; ask again to put them back "
        "if they belong there.")


@pytest.mark.asyncio
async def test_a_plain_shortening_is_not_sent_back_and_its_dropped_facts_are_said(monkeypatch):
    doc, notes = await _rewrite(monkeypatch, [ONE], ask="2절을 줄여줘")
    assert len(notes) == 1 and doc.sections[1]["content"] == ONE
    assert doc.counted == [] and doc.dropped == [
        "2절 10/16, 5%, 4%, 2건, 420만, A사(확정 아님)"]


TABLED = ("목요일에는 대성유통에게 견적서를 발송했으며, 경쟁사 A사가 4% 낮은 단가를 "
          "제시했습니다.\n\n"
          "**표 1. 일별 업무 추진 현황**\n\n| 일정 | 내용 |\n|---|---|\n"
          "| 목 | 대성유통 견적서 발송, 경쟁사 A사 4% 낮음 |")
CUT = ("대성유통 견적서를 보냈습니다(표 1).\n\n**표 1. 일별 업무 추진 현황**\n\n| 일정 | 내용 |\n"
       "|---|---|\n| 목 | 대성유통 견적서 발송, 경쟁사 A사 4% 낮음 |")
#: the retry that wrote the reason into the section and a date from nowhere (reverify-o)
ECHOED = "2절을 두 문장만 유지해야 합니다.\n\n10월 13일 기준 " + TWO


def test_facts_still_in_the_sections_table_are_not_called_dropped():
    assert revise.lost_facts(TABLED, CUT) == []
    assert revise.lost_facts(TABLED, CUT.split("\n\n")[0]) == ["4%", "A사", "목요일"]


def test_the_ask_restated_in_the_section_is_taken_out_before_counting():
    kept = revise.without_ask_echo(ECHOED)
    assert not kept.startswith("2절을") and revise.sentence_count(kept) == 2
    # a section that names another section by number is left alone
    assert revise.without_ask_echo("자세한 일정은 3절에 정리했습니다.") == (
        "자세한 일정은 3절에 정리했습니다.")
    started = time.monotonic()
    revise.without_ask_echo("2절 " + " " * 50000 + "문장")
    revise.added_facts("1" + " " * 50000, "2" + " " * 50000, "")
    assert time.monotonic() - started < 0.5


def test_a_figure_or_date_from_nowhere_is_a_fault():
    # 10 is in the section's 10/16: only the day comes from nowhere
    assert revise.added_facts(BEFORE, "10월 13일 기준 " + TWO, "") == ["13일"]
    assert revise.added_facts(BEFORE, TWO, "") == []
    faults = revise.shortening_faults(BEFORE, "10월 13일 기준 " + TWO, ASK)
    assert any("Add no figure or date" in f and "13일" in f for f in faults)
    assert faults[-1].startswith("Write none of these remarks")


@pytest.mark.asyncio
async def test_an_echoed_ask_and_an_invented_date_are_sent_back_then_said(monkeypatch):
    doc, notes = await _rewrite(monkeypatch, [ECHOED, ECHOED])
    assert len(notes) == 2 and "13일" in notes[1]
    assert not doc.sections[1]["content"].startswith("2절을")
    assert doc.counted == [] and doc.added == ["2절 13일"]
    assert save_stages.revision_reply(doc, None, is_deck=False) == (
        "2절을 고쳤습니다. 2절에 원래 없던 13일이 들어갔습니다. 확인해 주세요.")


#: one sentence and the table (reverify-p): one sentence, not three
WITH_TABLE = ("3분기 팀 매출은 41.2억원으로 목표 40억 대비 103%를 달성해, 전분기 38.5억 대비 7% "
              "성장했습니다(표 2).\n\n**표 2. 진행 사항**\n\n| 일정 | 내용 |\n| --- | --- |\n"
              "| 월 | 대성유통 1차 미팅 (5% 인하 요구) |\n\n- 남은 일: CRM 병합")


def test_only_prose_sentences_are_counted():
    assert revise.sentence_count(WITH_TABLE) == 1
    faults = revise.shortening_faults(WITH_TABLE, WITH_TABLE, ASK)
    assert faults[0].endswith("the last version had 1")
    started = time.monotonic()
    revise.sentence_count("**" + "a" * 50000)
    revise.sentence_count("표 1" + " " * 50000)
    revise.sentence_count("-" + " " * 50000 + "x")
    assert time.monotonic() - started < 0.5


def test_a_section_written_as_a_list_only_counts_its_top_level_rows():
    """audit-g H6s: two 개조식 rows asked as 「두 문장」 were said to be 0문장."""
    rows = ("- 업무 만족도가 3.4점에서 4.1점으로 상승하는 등 성과가 확인됨.\n"
            "- 다만 개인 기기 접속 시도 3건이 차단된 점은 개선 과제로 남음\n"
            "  - VPN 라이선스 추가 검토\n\n**표 3. 문제점**\n\n"
            "| 문제 | 개선 |\n|---|---|\n| a | b |")
    assert revise.sentence_count(rows) == 2
    assert revise.count_met(rows, 2)


def test_figures_are_real_figures_and_money_is_compared_as_money():
    said = [s for *_, s in deck.figures_in("표 1의 항목, 2.7억원입니다, 3곳에서, 목표 40억 대비")]
    assert said == ["2.7억", "3곳", "40억"]
    assert revise.lost_facts("전분기 38.5억 원", "전분기 38억 5천만 원보다 늘었다") == []
    assert revise.lost_facts("전분기 38.5억 원", "전분기 3,850,000,000원") == []
    assert revise.lost_facts("전분기 38.5억원보다", "38.5 억 원") == []
    assert revise.lost_facts("전분기 38.5억 원", "전분기 38억 원") == ["38.5억"]
    started = time.monotonic()
    deck.amounts_in("1억" + " " * 50000 + "5")
    assert time.monotonic() - started < 0.5


#: reverify-u t1e 2절: prose, a table and a picture the writer is not shown
PICTURE = "![그림 2. 단가 인하 옵션 대비](data:image/png;base64,iVBORw0KGgo=)"
OPTIONS = ("**표 2. 진행 중 이슈 및 요청 사항**\n\n| 안 | 금액 |\n|----|------|\n"
           "| 5% 인하 (요구 수용) | 미정 |\n| 4% 인하 (최대 허용) | 미정 |")
ISSUES = ("단가 인하 폭에 대한 결재와 물류팀과의 SLA 협의가 필요합니다.\n\n대성유통의 5% 인하 "
          "요구에 대해 최대 허용 인하 폭은 4%이므로, 4%까지의 인하 여부에 대한 결재를 "
          "요청합니다.\n\n영남 출고 지연이 반복되지 않도록 물류팀과의 출고 SLA 협의 자리를 "
          "마련해 주십시오. CRM 중복 거래처 37건의 병합은 60% 진행되었습니다(표 2, 그림 2)."
          f"\n\n{OPTIONS}\n\n{PICTURE}")
#: two sentences: the first holds the table's figures and a clause of its own
ISSUES_TWO = ("대성유통 단가 인하(5% 요구)에 대해 최대 4% 허용 여부를 결재받아야 하며, 영남 "
              "출고 지연 재발 방지를 위해 물류팀과의 출고 SLA 협의 자리가 필요합니다.\nCRM "
              "중복 거래처 37건의 병합 작업은 현재 60% 진행되었습니다.")


def test_a_sentence_saying_more_than_its_tables_figures_is_no_echo():
    from app.services import report

    body = f"{ISSUES_TWO}\n\n{OPTIONS}"
    assert report.trim_table_echo(body) == body
    # a sentence that only reads the rows back still goes
    echo = f"첫 문장입니다. 5% 인하와 4% 인하 두 안이 있습니다.\n\n{OPTIONS}"
    assert "두 안이" not in report.trim_table_echo(echo)
    started = time.monotonic()
    report._says_more("5%, " * 20000)
    assert time.monotonic() - started < 0.5


def test_a_rewrite_keeps_the_parts_pictures_unless_their_removal_is_asked():
    kept = revise.keep_pictures(ISSUES, f"{ISSUES_TWO}\n\n{OPTIONS}", ASK)
    assert kept.endswith(PICTURE) and kept.count(PICTURE) == 1
    assert revise.keep_pictures(ISSUES, kept, ASK) == kept
    assert PICTURE not in revise.keep_pictures(ISSUES, ISSUES_TWO, "2절 그림을 빼줘")
    started = time.monotonic()
    revise.keep_pictures("![a](b)\n" + " " * 50000, " " * 50000, ASK)
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_a_section_with_a_table_and_a_picture_shortens_to_the_count_asked(monkeypatch):
    parts = [PARTS[0], {"id": "s1", "heading": "진행 중 이슈 및 요청 사항", "content": ISSUES,
                        "format": "markdown"}]
    notes: list[str] = []

    async def rewrite_section(**kwargs):
        notes.append(kwargs["note"])
        return f"{ISSUES_TWO}\n\n{OPTIONS}", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages._report(), "rewrite_section", rewrite_section)
    bundle = _bundle()
    bundle.document.sections = [dict(p) for p in parts]
    bundle.plan.order = [1]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    content = bundle.document.sections[1]["content"]
    assert len(notes) == 1 and revise.sentence_count(content) == 2
    # numbered anew with the document; the picture is the one the part had
    assert "영남 출고 지연" in content and "단가 인하 옵션 대비](data:image/png;base64," in content
    assert save_stages.revision_reply(bundle.document, None, is_deck=False) == "2절을 고쳤습니다."


@pytest.mark.asyncio
async def test_two_sentences_whose_figures_are_the_tables_are_not_cut_below_the_count(
        monkeypatch):
    # the first sentence's figures are all in the table: no echo cut under a count asked
    asked = ("대성유통의 5% 인하 요구에 대해 최대 4%까지의 인하 여부를 결재해 주십시오. 영남 출고 "
             "지연 재발 방지를 위한 물류팀 출고 SLA 협의 자리를 마련해 주십시오.")
    parts = [PARTS[0], {"id": "s1", "heading": "진행 중 이슈 및 요청 사항", "content": ISSUES,
                        "format": "markdown"}]

    async def rewrite_section(**_kwargs):
        return f"{asked}\n\n{OPTIONS}", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages._report(), "rewrite_section", rewrite_section)
    bundle = _bundle()
    bundle.document.sections = [dict(p) for p in parts]
    bundle.plan.order = [1]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    content = bundle.document.sections[1]["content"]
    assert content.startswith("대성유통의 5% 인하 요구") and revise.sentence_count(content) == 2


def test_the_shortening_that_meets_the_count_is_the_better_one():
    assert revise.better_shortening(BEFORE, TWO, ONE, ASK)
    assert not revise.better_shortening(BEFORE, ONE, TWO, ASK)
    # both off the count: the one missing less
    assert revise.better_shortening(BEFORE, BEFORE, ONE, ASK)


def test_vs_ends_no_sentence_and_a_picture_is_no_pointer_sentence():
    from app.services import report

    one = ("단가 인하 폭(5% 요구 vs. 최대 허용 4%)의 결재와 물류팀 SLA 협의 자리 마련이 "
           "요청 사항이며, CRM 병합은 60% 진행되었습니다.")
    assert revise.sentence_count(one) == 1
    assert report.trim_restatements(one, ["대성유통은 5% 인하를 요구했습니다."])[0] == one
    three = f"첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다.\n\n{OPTIONS}\n\n{PICTURE}"
    assert revise.fold_table_pointers(three, 2).endswith(PICTURE)


@pytest.mark.asyncio
async def test_a_retry_off_the_count_the_first_version_met_does_not_replace_it(monkeypatch):
    # two sentences without A사; the retry puts it back but writes the section out again
    thin = ("대성유통은 10/16 견적서를 보냈고 5% 인하 요구와 4% 한도 사이의 폭이 남았습니다. "
            "한솔마트 클레임 2건은 420만원 정산으로 마쳤습니다.")
    doc, notes = await _rewrite(monkeypatch, [thin, BEFORE])
    assert len(notes) == 2 and "Keep it to 2 sentences" in notes[1] and "A사" in notes[1]
    assert doc.sections[1]["content"] == thin
    assert doc.counted == [] and doc.dropped == ["2절 A사(확정 아님)"]


def test_a_count_still_off_is_met_by_joining_or_splitting_clauses():
    three = ("진행 중 이슈는 단가 결재입니다. 물류팀 SLA 협의가 필요합니다. CRM 병합은 다음 주에 "
             "마칩니다.")
    assert revise.fit_count(three, 2) == (
        "진행 중 이슈는 단가 결재이며, 물류팀 SLA 협의가 필요합니다. CRM 병합은 다음 주에 "
        "마칩니다.")
    one = ("대성유통 단가 인하 폭에 대한 결재가 필요하며, 영남 출고 지연 재발 방지를 위해 물류팀 "
           "SLA 협의 자리를 마련해 주십시오.")
    assert revise.fit_count(f"{one}\n\n{OPTIONS}", 2) == (
        "대성유통 단가 인하 폭에 대한 결재가 필요합니다. 영남 출고 지연 재발 방지를 위해 물류팀 "
        f"SLA 협의 자리를 마련해 주십시오.\n\n{OPTIONS}")
    # across paragraphs, and nothing reworded where no joint is
    paragraphs = "가는 됩니다.\n\n나는 했습니다.\n\n다다."
    assert revise.sentence_count(revise.fit_count(paragraphs, 2)) == 2
    plain = "요청 드립니다. 확인 바랍니다. 끝."
    assert revise.fit_count(plain, 2) == plain
    started = time.monotonic()
    revise.fit_count("하며, " * 10000, 2)
    revise.fit_count("합니다. " * 2000, 1)
    assert time.monotonic() - started < 0.5


def test_a_day_the_shortening_took_out_is_named_with_the_figures():
    """reverify-v t1: 「병합 완료 목표 일자는 다음 주 수요일」 went with the shortening, unnamed."""
    before = ("CRM 중복 거래처 37건 병합을 60%까지 진행했습니다. 병합 완료 목표 일자는 다음 주 "
              "수요일입니다. 금요일에는 물류팀과 점검합니다.")
    after = "CRM 중복 거래처 37건 병합을 60%까지 진행했고, 금요일에 물류팀과 점검합니다."
    assert revise.lost_facts(before, after) == ["다음 주 수요일"]
    # the day kept, its week left unsaid, is not lost; nor is a day said another way
    assert revise.lost_facts(before, after + " 병합은 수요일에 마칩니다.") == []
    assert revise.lost_facts("차주 화요일 회의", "다음주 화요일 회의") == []
    assert revise.lost_facts("Due next Wednesday.", "Due soon.") == ["next Wednesday"]
    assert revise.lost_facts("Paid per week.", "Paid.") == []
    started = time.monotonic()
    revise.lost_facts("다음" + " " * 50000 + "주", "x")
    revise.lost_facts("next" + " " * 50000 + "week", "x")
    assert time.monotonic() - started < 0.5


#: reverify-w t1: the claims said done, gone from the two-sentence rewrite
DONE = ("기존 거래처 한솔마트의 재고 클레임 2건은 반품 420만원 건으로, 이번 주 처리를 "
        "완료했습니다. 경쟁사 A사 단가는 파악했으나 확정된 값은 아닙니다. CRM 병합은 수요일까지 "
        "마무리할 예정입니다.")


def test_a_thing_said_done_that_the_shortened_section_no_longer_says_is_named():
    assert revise.lost_facts(DONE, "한솔마트 클레임 2건(반품 420만원)과 A사 단가를 정리했습니다. "
                             "CRM은 수요일 예정입니다.") == ["이번 주", "처리 완료",
                                                            "A사의 「확정 아님」 표시"]
    # still said done in other words, or not done at all (확정된 값은 아닙니다, 마무리할 예정)
    assert revise.lost_facts(DONE, DONE.replace("처리를 완료했습니다", "처리했습니다")) == []
    assert revise.lost_facts(DONE, DONE.replace("처리를 완료했습니다", "모두 완료됐습니다")) == []
    started = time.monotonic()
    revise._lost_done("가나" + " " * 50000 + "완료했", "")
    assert time.monotonic() - started < 0.5
