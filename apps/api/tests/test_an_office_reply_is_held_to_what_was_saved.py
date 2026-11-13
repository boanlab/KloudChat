"""A chat turn's reply about an office document is held to the versions its tools saved."""

from __future__ import annotations

from app.services import office_claims
from app.services.tools.base import ToolContext


def test_a_claim_with_nothing_saved_is_corrected():
    ctx = ToolContext(user_id="u", session_id="s", pptx={"id": "a", "version": 1})
    said = office_claims.note("6장을 작성했습니다. 확인해 보세요.", ctx, {"pptx"})
    assert "저장되지 않았습니다" in said and "v1 그대로" in said


def test_a_failure_said_while_a_version_was_saved_names_it():
    ctx = ToolContext(user_id="u", session_id="s",
                      hwp={"saved": [{"version": 10, "changes": ["p3: 「2장」 → 「3장」"]}]})
    said = office_claims.note("장 순서를 바꾸지 못했습니다.", ctx, {"hwp"})
    assert "v10" in said and "p3" in said


def test_a_true_reply_or_a_plain_answer_gets_nothing():
    saved = ToolContext(user_id="u", session_id="s",
                        docx={"saved": [{"version": 3, "changes": ["p2 고침"]}]})
    assert office_claims.note("2절을 고쳤습니다.", saved, {"docx"}) == ""
    idle = ToolContext(user_id="u", session_id="s")
    assert office_claims.note("회의는 3시에 시작합니다.", idle, {"docx"}) == ""
    assert office_claims.note("문서를 고쳤습니다.", idle, set()) == ""
