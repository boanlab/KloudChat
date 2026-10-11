"""A meeting memo without an attendance line still names its people.

「포스터 → 지민」, 「… 하자 (회장)」, 「수아가 하기로」: the head's 참석자 lists them, marked as
read from the material, and the gap card does not ask who attended. A figure caption in
brackets is no blank.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.services import check_skills, doc_formats, gap_questions, placeholders

#: 빠진 정보 질문 as the 서식 and the agent files carry them.
_MINUTES_Q = gap_questions.for_format("minutes")


_MEMO = """축제 준비 회의 때 휴대폰에 막 적은 거야. 회의록으로 만들어 줘.

축제 부스 회의
- 부스 위치 A동 앞으로 (회장)
- 메뉴: 닭꼬치 + 음료. 단가는 다음에
- 포스터 → 지민, 다음 주 금요일까지
- 재료 업체 견적 → 도윤, 모레까지
- 위생교육 이수증 → 학생처 제출 필요, 누가?
- 지민 중간고사 겹쳐서 포스터는 수아가 하기로
"""

_GAPS = Path(__file__).resolve().parents[1] / "app/skills/gap-check/scripts/gaps.py"


def _gaps():
    spec = importlib.util.spec_from_file_location("gap_check_gaps", _GAPS)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_people_a_memo_names_fill_the_attendees():
    assert doc_formats.named_people(_MEMO) == ["회장", "지민", "도윤", "수아"]
    fmt = doc_formats.BY_ID["minutes"]
    head = doc_formats.head_values(fmt, _MEMO)
    assert head["참석자"] == "회장, 지민, 도윤, 수아 (자료에 나온 이름 기준)"
    # The head keeps it: the note is the code's, the names are the material's.
    block = doc_formats.title_block(fmt, {}, _MEMO, head)
    assert ["참석자", "회장, 지민, 도윤, 수아 (자료에 나온 이름 기준)"] in block["fields"]


def test_an_attendance_line_wins_over_the_names():
    memo = ("[메모 — 교육과정위원회, 2026-10-12(월) 15:00]\n참석: 김교수, 박교수\n"
            "- 설문 → 최○○, 금요일까지\n")
    assert doc_formats.head_values(doc_formats.BY_ID["minutes"], memo)["참석자"] == "김교수, 박교수"


def test_the_gap_card_does_not_ask_who_attended_a_memo_that_names_them():
    gaps = _gaps()
    assert gaps.named_people(_MEMO) == doc_formats.named_people(_MEMO)
    assert gaps._attendees(_MEMO, "", {}) == (True, "")
    found = check_skills.gaps(("gap-check",), questions=_MINUTES_Q, request=_MEMO, material="")
    asked = [f["fact"] for f in found]
    assert "attendees" not in asked and "meeting_date" in asked
    # One owner is not a list of attendees: still asked.
    assert gaps._attendees("- 포스터 → 지민, 금요일까지", "", {})[0] is False


def test_a_figure_caption_in_brackets_is_no_blank():
    for caption in ("[그림 부스 위치 비교]", "[표 실행 항목]", "[사진 부스 전경]"):
        assert not placeholders.PLACEHOLDER.search(caption), caption
    assert placeholders.PLACEHOLDER.search("[참석자]")
    assert placeholders.PLACEHOLDER.search("[그림]") is None
