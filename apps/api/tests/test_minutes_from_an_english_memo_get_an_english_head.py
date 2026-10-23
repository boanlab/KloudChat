"""Minutes written in English get their head in English (「Date」, 「Location」, 「[Location]」),
and the checks that find a head field by its name read the English labels as the Korean ones."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from app.services import doc_formats, meeting_memo, placeholders, report_checks

#: the round-16 H2 memo, its own room added
MEMO = ("Oct 19 Product Design weekly sync. Location: Room 3A. Attendees: Maya Chen (lead), "
        "Arjun Patel, Lena Fischer, Tom Okafor. Absent: Sofia Reyes (sick). 1) Onboarding "
        "redesign: we moved the launch to Nov 11. Arjun will finish the event tracking spec by "
        "Oct 28. Next meeting: Oct 26 (Mon) 3pm, Room 4B.")
TODAY = dt.date(2026, 10, 20)
_WEB = Path(__file__).resolve().parents[3] / "apps/web/src"


def _fields(block: dict) -> dict[str, str]:
    return dict(map(tuple, block["fields"]))


def _english_head(memo: str, answer: dict | None = None) -> dict:
    minutes = doc_formats.BY_ID["minutes"]
    block = doc_formats.title_block(minutes, answer or {}, memo,
                                    doc_formats.head_values(minutes, memo))
    return doc_formats.in_english(block)


def test_an_english_memo_gives_an_english_head_with_its_place_and_day():
    pre = report_checks.Prepass(format_id="minutes", base_date="2026-10-19", base_source="near")
    block = _english_head(MEMO, {"fields": {"회의명": "Product Design weekly sync"}})
    assert [name for name, _ in block["fields"]] == [
        "Meeting", "Date", "Location", "Attendees", "Prepared by"]
    assert block["language"] == "en" and block["label"] == "Minutes"
    fields = _fields(block)
    # this meeting's room, not the next one's; the attendees end at their sentence
    assert fields["Location"] == "Room 3A"
    assert fields["Attendees"] == "Maya Chen (lead), Arjun Patel, Lena Fischer, Tom Okafor"
    dated = report_checks.minutes_head(block, pre, TODAY)
    assert _fields(dated)["Date"] == "2026-10-19 (Mon)"
    # every field fill_head fills is given; 「Prepared by」 stays the person's, as in Korean
    assert meeting_memo.fill_head(dated, MEMO) is None


def test_the_next_meetings_room_is_no_place_for_this_one_in_english():
    memo = MEMO.replace(" Location: Room 3A.", "", 1)
    block = _english_head(memo, {"fields": {"장소": "Room 4B"}})
    assert _fields(block)["Location"] == ""
    filled = meeting_memo.fill_head(block, memo)
    # the blank is the English field's own, and counts as a blank
    assert _fields(filled)["Location"] == "[Location]"
    assert placeholders.is_blank("[Location]") and placeholders.is_blank("[Prepared by]")
    assert "[Location]" in placeholders.head_blanks(filled)


def test_an_unknown_meeting_day_is_an_english_blank_and_a_written_day_gets_its_year():
    block = {"format": "minutes", "language": "en", "fields": [["Meeting", "x"], ["Date", ""]]}
    unknown = report_checks.Prepass(format_id="minutes")
    assert _fields(report_checks.minutes_head(block, unknown, TODAY))["Date"] == "[Date]"
    written = {**block, "fields": [["Meeting", "x"], ["Date", "10/14 Tue 10am"]]}
    assert _fields(report_checks.minutes_head(written, unknown, TODAY))["Date"] == (
        "2026-10-14 (Wed) (needs checking: the calendar gives Wed) 10am")
    far = {**block, "fields": [["Meeting", "x"], ["Date", "10/14"]]}
    assert _fields(report_checks.minutes_head(far, unknown, dt.date(2026, 4, 14)))["Date"] == (
        "[Year] 10/14")


def test_the_body_checks_read_the_english_head_place_and_day():
    head = {"format": "minutes", "language": "en",
            "fields": [["Meeting", "Sync"], ["Date", "2026-10-19 (Mon)"], ["Location", "Room 3A"]]}
    pre = report_checks.Prepass(format_id="minutes", base_date="2026-10-19", base_source="near")
    sections = [{"heading": "Overview",
                 "content": "| Item | Detail |\n|---|---|\n| Date | [Date] |\n| Location | TBD |"},
                {"heading": "Next meeting", "content": "- Location: TBD"}]
    out = report_checks.minutes_head_day(sections, head, pre)
    assert "| Date | 2026-10-19 (Mon) |" in out[0]["content"]
    assert "| Location | Room 3A |" in out[0]["content"]
    assert out[1]["content"] == "- Location: TBD"  # the next meeting's place is another one
    # a blank head place takes the body's 「TBD」 line out, as 「미정」 in Korean
    blank_head = {**head, "fields": [["Location", "[Location]"]]}
    gone = report_checks.minutes_head_place(sections, blank_head)
    assert "Location" not in gone[0]["content"]


def test_a_head_of_a_korean_document_stays_korean():
    memo = ("10/14 교육혁신팀 주간회의. 장소: 본관 2층 회의실. 참석 김지훈(팀장), 이수연. "
            "다음 회의 10/21 화 10시 3층 회의실.")
    minutes = doc_formats.BY_ID["minutes"]
    block = doc_formats.title_block(minutes, {}, memo, doc_formats.head_values(minutes, memo))
    assert [name for name, _ in block["fields"]] == ["회의명", "일시", "장소", "참석자", "작성자"]
    assert "language" not in block and block["label"] == "회의록"
    pre = report_checks.Prepass(format_id="minutes")
    blank_day = {**block, "fields": [["일시", ""]]}
    assert _fields(report_checks.minutes_head(blank_day, pre, TODAY))["일시"] == "[회의 일자]"
    filled = meeting_memo.fill_head({**block, "fields": [["장소", ""], ["회의명", ""]]}, "메모")
    assert _fields(filled) == {"장소": "[장소]", "회의명": "[회의명]"}
    # a format with a field that has no English label is left in Korean
    report = doc_formats.title_block(doc_formats.BY_ID["status"], {}, "")
    assert doc_formats.in_english(report) is None


def test_the_web_draws_the_english_head_as_the_exports_do():
    marks = (_WEB / "lib/placeholders.ts").read_text(encoding="utf-8")
    assert f"/^{placeholders.EN_HEAD_BLANK.pattern}$/" in marks
    formats = (_WEB / "components/report/docFormats.ts").read_text(encoding="utf-8")
    assert "block.language === 'en' ? 'Title' : '제목'" in formats
    from app.services import report_export

    assert report_export._title_key({"language": "en"}) == "Title"
    assert report_export._title_key({}) == "제목"
    assert doc_formats.blank("Location") == "[Location]" and doc_formats.blank("장소") == "[장소]"


def test_the_english_memo_patterns_finish_on_a_long_run_of_spaces():
    text = "Next meeting" + " " * 50_000 + "x"
    start = time.monotonic()
    doc_formats._NEXT_MEETING.search(text)
    doc_formats.head_values(doc_formats.BY_ID["minutes"], "Location:" + " " * 50_000)
    placeholders.is_blank("[" + "A" + "a" * 50_000)
    assert time.monotonic() - start < 0.5


async def _head_stage(monkeypatch, memo: str, body: str) -> dict:
    from app.pipeline.bundle import Bundle, Material, Request, Spec
    from app.pipeline.report_stages import TitleBlock
    from app.pipeline.stage import Context
    from app.services import process_settings, report

    async def complete(*_args, **_kw):
        return '{"fields": {}}', {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(report, "_complete", complete)
    bundle = Bundle(request=Request(text=memo, model="local/m", api_key="k"),
                    spec=Spec(format=doc_formats.BY_ID["minutes"], format_id="minutes",
                              process=process_settings.for_format("minutes")),
                    material=Material(given=""))
    bundle.plan.title = "Weekly sync"
    bundle.usage = {"inputTokens": 0, "outputTokens": 0}
    bundle.document.sections = [{"heading": "Overview", "content": body}]
    bundle.computed.prepass = report_checks.Prepass(
        format_id="minutes", base_date="2026-10-19", base_source="near")
    async for _ in TitleBlock().run(bundle, Context()):
        pass
    return bundle.document.head_block


async def test_the_head_stage_sets_an_english_bodys_head_in_english(monkeypatch):
    body = ("The Product Design team met for its weekly sync. The launch of the new onboarding "
            "flow moved to Nov 11 because the analytics events are not ready.")
    block = await _head_stage(monkeypatch, MEMO.replace(" Location: Room 3A.", "", 1), body)
    assert block["language"] == "en"
    fields = _fields(block)
    assert fields["Date"] == "2026-10-19 (Mon)" and fields["Location"] == "[Location]"
    # a Korean body from the same memo keeps the Korean head
    korean = await _head_stage(monkeypatch, MEMO, "제품 디자인 팀은 주간 회의를 열었습니다. " * 3)
    assert "language" not in korean and _fields(korean)["일시"] == "2026-10-19(월)"


def test_a_korean_dotted_date_keeps_its_last_dot():
    minutes = doc_formats.BY_ID["minutes"]
    read = doc_formats.head_values(minutes, "일시: 2026. 10. 14.\n장소: 본관 302호")
    assert read["일시"] == "2026. 10. 14." and read["장소"] == "본관 302호"


def test_a_due_date_is_no_meeting_date_but_a_meeting_date_label_is():
    from app.services import doc_formats

    fmt = doc_formats.BY_ID["minutes"]
    due = doc_formats.head_values(fmt, "Weekly sync. Location: Room 3A. Due Date: 2026-10-30.")
    assert not any("10-30" in v for v in due.values())
    said = doc_formats.head_values(fmt, "Meeting date: 2026-10-19. Location: Room 3A.")
    assert any("10-19" in v for v in said.values())


ENGLISH_MEMO = (
    "Oct 19 Product Design weekly sync. Attendees: Maya Chen (lead), Arjun Patel, Tom Okafor. "
    "Absent: Sofia Reyes (sick). 1) Onboarding: we moved the launch to Nov 11. "
    "Next meeting: Oct 26 (Mon) 3pm, Room 4B.")


def test_an_english_memo_gives_its_meeting_name_and_keeps_the_next_meetings_room_apart():
    from app.services import meeting_memo

    memo = meeting_memo.read(ENGLISH_MEMO)
    assert memo.this.name == "Product Design weekly sync"
    assert memo.this.day == "Oct 19" and memo.this.place == ""
    assert memo.this.attendees == "Maya Chen (lead), Arjun Patel, Tom Okafor"
    assert (memo.next.day, memo.next.clock, memo.next.place) == ("Oct 26 (Mon)", "3pm", "Room 4B")
    head = {"head": "memo", "language": "en", "fields": [
        ["Meeting", ""], ["Date", "Oct 19"], ["Location", ""], ["Attendees", ""]]}
    filled = dict(meeting_memo.fill_head(head, ENGLISH_MEMO)["fields"])
    assert filled["Meeting"] == "Product Design weekly sync"
    assert filled["Location"] == "[Location]"


def test_english_memo_patterns_stay_linear_on_long_spaces():
    import time

    from app.services import meeting_memo

    started = time.monotonic()
    meeting_memo.read("Oct" + " " * 50_000 + "19 sync Room" + " " * 50_000 + "4B 3" + " " * 50_000)
    assert time.monotonic() - started < 0.5


def test_a_due_day_an_english_memo_gives_stays_in_the_table():
    # audit-x2 H2/A: 「2026-10-28」 for 「by Oct 28」 was blanked to 「[기한]」
    from app.services import report

    memo = ("Arjun will finish the event tracking spec by Oct 28. "
            "Lena will deliver 40 icons by October 30th.")
    table = ("| Task | Owner | Due |\n| --- | --- | --- |\n"
             "| Event tracking spec | Arjun | 2026-10-28 |\n| 40 icons | Lena | 10/30 |\n"
             "| Mockups | Tom | 2026-10-26 |")
    out = report._unsourced_owner_dates(table, memo)
    assert "2026-10-28" in out and "10/30" in out
    # a day the memo never gave is still blanked
    assert "2026-10-26" not in out


def test_a_due_written_in_english_words_is_read_as_its_day():
    # audit-x2 H2/C: 「October 28th」 in the cell was blanked
    from app.services import report

    memo = "Arjun will finish the spec by Oct 28. Lena delivers icons by 30 Oct."
    table = ("| Task | Owner | Due |\n| --- | --- | --- |\n"
             "| Spec | Arjun | October 28th |\n| Icons | Lena | Oct. 30 |\n"
             "| Mockups | Lena | Nov 2 |")
    out = report._unsourced_owner_dates(table, memo)
    assert "October 28th" in out and "Oct. 30" in out and "Nov 2" not in out
    assert report._english_days("by 28th October and Sept 3") == [(10, 28), (9, 3)]


def test_a_caption_split_at_its_number_is_no_sentence_said_twice():
    # audit-x2 H2/A: 「**Table 1. Decisions Made**」 lost 「Decisions Made**」
    from app.services import meeting_memo

    sections = [{"heading": "Decisions Made", "content": (
        "The launch moved to Nov 11 (Table 1).\n\n**Table 1. Decisions Made**\n\n"
        "| Item | Decision |\n| --- | --- |\n| Launch | Nov 11 |")},
        {"heading": "Notes", "content": "Decisions Made. Decisions Made."}]
    out, _ = meeting_memo.settle_copies(sections, ENGLISH_MEMO, strong=False)
    assert "**Table 1. Decisions Made**" in out[0]["content"]
