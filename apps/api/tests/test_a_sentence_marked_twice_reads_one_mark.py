"""A sentence an earlier check marked, marked again for a stated rule, reads one mark."""

from __future__ import annotations

from app.pipeline import report_stages


class _Finding:
    code = "unsourced_policy"
    section = 0
    sentence = "반품 승인은 팀장 결재를 거쳐야 함 [2]"


def test_the_second_mark_is_folded_into_the_first():
    content = "- 반품 승인은 팀장 결재를 거쳐야 함 [2] (확인 필요)\n- 다음 항목"
    out = report_stages.mark_policy([{"content": content}], [_Finding()])
    assert out[0]["content"] == content
