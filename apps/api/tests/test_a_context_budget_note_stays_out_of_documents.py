"""The context budget's record (how much of a file reached the writer) is server
bookkeeping: the writer is told not to copy it, and a document that still carries it loses
those sentences."""

from __future__ import annotations

from app.services import internal_words, workspace_context
from app.services.workspace_context import ContextFile


def test_the_file_report_tells_the_writer_not_to_copy_its_counts():
    report = workspace_context._file_report(
        (), (ContextFile("01강.md", "truncated", 1165, 8904, "f1", None),))
    assert "전체 8,904자 중 1,165자만" in report
    assert "never copy character counts" in report


def test_a_budget_sentence_leaves_a_document_and_the_content_stays():
    body = (
        "각 파일은 독립적인 주차 강의로 제공되며, 총 21개 강의 노트로 구성된다. 강의 노트에는 "
        "전체 8,904자에서 1,165자만 전달된 강부터 9,576자 중 1,215자만 전달된 강까지 있으며, "
        "전달되지 않은 부분의 내용은 확인 할 수 없다(표 1).\n\n"
        "| 주차(강) | 주제 |\n|---|---|\n| 1강 | 분산 시스템 개요 |\n\n"
        "2강은 메시지 전달 방식과 RPC를 다룬다. 원문은 9,576자 중 일부입니다."
    )
    kept = internal_words.strip_work_notes(body, request="강의계획서", document=True)
    assert "자만 전달" not in kept and "전달되지 않은 부분" not in kept and "자 중 일부" not in kept
    assert "총 21개 강의 노트로 구성된다." in kept
    assert "| 1강 | 분산 시스템 개요 |" in kept
    # 「전달」 as content is not bookkeeping
    assert "2강은 메시지 전달 방식과 RPC를 다룬다." in kept


def test_text_without_a_budget_note_is_untouched():
    text = "1강은 분산 시스템의 투명성과 확장성을 다룬다.\n\n- 핵심어: 투명성"
    assert internal_words.strip_budget_notes(text) == text
