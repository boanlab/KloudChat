"""A weekly report built from minutes has its own head and finish line, and a minutes document
from one named file takes that file's meeting."""

import datetime as dt

from app.pipeline import report_stages, save_stages
from app.pipeline.bundle import Bundle, Material, Request, Spec
from app.services import doc_formats, report_checks, source_focus

C2 = ("11월 10일 회의록이랑 매출 실적표 보고 이번 주 주간 업무 보고서를 한글 파일로 만들어 줘. "
      "팀장님께 올릴 거야.")

KNOWLEDGE = """# 프로젝트 지식
아래는 이미 읽어 둔 자료 본문입니다.

## 주간회의록_20261027.docx
회의명: 영업팀 주간회의
일시: 2026-10-27(화) 10:00
장소: 3층 소회의실
참석자: 강민재, 최유나, 임하준
작성자: 최유나
- 신규 거래처 제안서 → 강민재, 11/3까지

## 주간회의록_20261110.docx
회의명: 영업팀 주간회의
일시: 2026-11-10(화) 10:00
장소: 2층 대회의실
참석자: 강민재, 최유나, 임하준, 박서준
작성자: 임하준
- 4분기 매출 전망 보고서 초안 → 임하준, 11/17까지
- 거래처 계약 갱신 협의 → 강민재, 11/20까지
- 연말 프로모션 기획안 → 최유나, 11/28까지

## 매출실적_2026Q4.xlsx
| 월 | 매출 | 목표 |
| 10월 | 4.2억 | 4.0억 |
"""


def _bundle(request: str, fmt: str, sources: list[str]) -> Bundle:
    req = Request(text=request, model="m", api_key="k", untrusted=[KNOWLEDGE],
                  project_sources=[{"name": n, "state": "included"} for n in sources])
    return Bundle(request=req, spec=Spec(format=doc_formats.BY_ID[fmt], format_id=fmt),
                  material=Material(given=KNOWLEDGE))


NAMES = ["주간회의록_20261027.docx", "주간회의록_20261110.docx", "매출실적_2026Q4.xlsx"]


def test_the_document_made_is_the_one_the_verb_governs():
    assert doc_formats.detect(C2).id == "status"
    assert doc_formats.detect("회의록 읽고 주간보고 작성해 줘").id == "status"
    assert doc_formats.detect("회의록을 바탕으로 결과 보고서 써 줘").id == "result_report"
    # still minutes when minutes are what is made, alone or beside another document
    assert doc_formats.detect("어제 회의 녹취 정리해서 회의록 만들어 줘").id == "minutes"
    assert doc_formats.detect("회의록과 안건지를 만들어 줘").id == "minutes"
    assert doc_formats.detect("주간 보고 써 줘").id == "status"


def test_the_finish_line_names_what_was_made():
    from app.models.workspace import ArtifactKind

    said = save_stages._written_note(5, {"format": "minutes"}, office=ArtifactKind.hwp,
                                     title="11월 2주차 주간 업무 보고서")
    assert "회의록" not in said and "보고서" in said
    # a title agreeing with the format keeps its label
    assert "회의록" in save_stages._written_note(4, {"format": "minutes"},
                                              title="영업팀 주간회의 회의록")
    assert save_stages._written_note(4, None) == "4개 섹션으로 보고서를 작성했습니다."


def test_a_weekly_report_head_is_never_a_project_file_head():
    bundle = _bundle(C2, "status", NAMES)
    head = report_stages._head_material(bundle)
    assert "작성자: 최유나" not in head and "회의명" not in head
    read = doc_formats.head_values(doc_formats.BY_ID["status"], head)
    assert "보고자" not in read


def test_the_named_meeting_file_is_found_among_many():
    part = source_focus.requested_part("11월 10일 회의록 정리해 줘", KNOWLEDGE, NAMES)
    assert part is not None and part.name == "주간회의록_20261110.docx"
    assert "11/17" in part.text and "11/3" not in part.text
    # by name, too
    named = source_focus.requested_part("주간회의록_20261027 내용으로 회의록", KNOWLEDGE, NAMES)
    assert named is not None and named.name == "주간회의록_20261027.docx"
    # nothing named: no guessing
    assert source_focus.requested_part("회의록 써 줘", KNOWLEDGE, NAMES) is None
    # a deadline is no source day
    assert source_focus.named_day("11월 20일까지 보고서 써 줘") is None


def test_a_minutes_head_and_day_come_from_the_named_file():
    bundle = _bundle("11월 10일 회의록 한글로 정리해 줘", "minutes", NAMES)
    head = report_stages._head_material(bundle)
    read = doc_formats.head_values(doc_formats.BY_ID["minutes"], head)
    assert read["일시"].startswith("2026-11-10")
    assert read["장소"] == "2층 대회의실"
    assert "박서준" in read["참석자"]
    # the meeting day: the named record's, not the first dated meeting among the files
    today = dt.date(2026, 11, 15)
    assert report_checks.meeting_date(C2, KNOWLEDGE, today) == dt.date(2026, 11, 10)
    assert report_checks.meeting_date("11월 10일 회의록", "", today) == dt.date(2026, 11, 10)


def test_a_yearless_record_day_resolves_to_the_day_passed():
    today = dt.date(2026, 1, 5)
    assert source_focus.resolve_day(None, 12, 28, "", today) == dt.date(2025, 12, 28)
    assert source_focus.resolve_day(None, 11, 10, "일시: 2026-11-10", today) == \
        dt.date(2026, 11, 10)
