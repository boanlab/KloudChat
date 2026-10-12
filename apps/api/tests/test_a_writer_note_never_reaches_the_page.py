"""A `[[…]]` note the writer left itself goes whole, and the summary under it stays: the
「핵심 요약」 of a 장학금 공고 shipped 「… 한두 문장으로 압축.]]」 and lost its real sentence."""

from __future__ import annotations

from app.services import report

NOTE = (
    "[[확정 수치 검토: 시간당 근로장학금 11,000원, 가계곤란장학금 200만 원 — 본 섹션은 신청 "
    "기간과 대상의 요약이므로 지급액 수치는 본 섹션에서 다루지 않음. 신청 기간 계산은 규정 "
    "제7조에 따라 개강일 2027. 3. 2.(화)를 기준으로 30일 전부터 14일 전까지이므로, 2027. 2. "
    "1.(일)부터 2027. 2. 16.(월)까지. 「쓸 수 있는 수치」 목록에 2027.02.28이 포함되어 있으나, "
    "2027년 2월은 28일까지이므로 날짜 계산상 무방. 다만 본 섹션은 \"200자 내외\"로, 신청 기간과 "
    "대상의 핵심 요약만 담으므로, 기간과 대상(성적우수·가계곤란 재학생)을 한두 문장으로 압축.]]"
)
SUMMARY = (
    "2027학년도 1학기 교내 장학금(성적우수·가계곤란) 신청 기간은 2027. 1. 31.(일)부터 "
    "2027. 2. 16.(화)까지이며, 학생포털을 통해 접수합니다."
)


def test_the_note_goes_whole_and_the_summary_stays():
    sections = [
        {"id": "s0", "heading": "핵심 요약: 신청 기간과 대상", "content": f"{NOTE}\n\n{SUMMARY}\n"},
        {"id": "s1", "heading": "신청 기간 및 방법",
         "content": "신청 기간은 2027. 1. 31.부터 2027. 2. 16.까지입니다. 포털로 냅니다.\n"},
    ]
    out = report.normalize_document(sections, request="공고문", context="")[0]["content"]
    assert "[[" not in out and "]]" not in out
    for leak in ("본 섹션", "200자 내외", "압축", "확정 수치", "30일 전부터"):
        assert leak not in out, leak
    assert "학생포털을 통해 접수합니다" in out


def test_a_half_scrubbed_note_tail_and_an_open_note_go_too():
    tail = ("신청 기간 계산은 30일 전부터 14일 전까지이므로 2027. 2. 1.부터. 다만 본 섹션은 "
            "\"200자 내외\"로 한두 문장으로 압축.]]\n\n" + SUMMARY + "\n")
    assert report.drop_writer_notes(tail).strip() == SUMMARY
    opened = SUMMARY + "\n\n[[작성 메모: 이 절은 대상만 짧게 정리하고 수치는 뒤로 넘긴다\n"
    assert report.drop_writer_notes(opened).strip() == SUMMARY
    # A figure mark, a citation and a link stay.
    kept = "흐름은 아래와 같다 [[그림: 신청 흐름도]] 근거는 규정이다[[3]]. [규정](https://a.kr)\n"
    assert report.drop_writer_notes(kept) == kept


def test_a_sentence_talking_to_the_writer_is_dropped():
    body = f"{SUMMARY} 본 섹션은 200자 내외로 기간과 대상을 한두 문장으로 압축한다.\n"
    assert report.drop_prompt_talk(body).strip() == SUMMARY
