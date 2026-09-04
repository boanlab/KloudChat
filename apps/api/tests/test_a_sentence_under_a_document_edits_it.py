"""`revise` routing: a sentence typed under a document revises parts, the whole, or nothing."""

from __future__ import annotations

from app.services import revise

PARTS = ["서론", "현행 시스템의 문제", "대안 비교", "권고와 다음 행동"]


def _plan(raw: str, parts=PARTS, message: str = "고쳐줘") -> revise.Plan:
    return revise._parse(raw, len(parts), message)


# ── where an instruction lands ─────────────────────────────────────────


def test_one_named_part_is_rewritten_alone():
    plan = _plan('{"scope": "parts", "targets": [3], "note": "분량을 절반으로"}')
    assert plan.scope == "parts"
    # Targets are one-based on the way in (outline numbering), zero-based out.
    assert plan.targets == [2]
    assert plan.revises


def test_a_document_wide_instruction_touches_every_part():
    plan = _plan('{"scope": "whole", "note": "말투를 더 간결하게"}')
    assert plan.targets == [0, 1, 2, 3]
    assert plan.revises


def test_asking_for_a_different_document_does_not_revise_this_one():
    plan = _plan('{"scope": "new"}')
    assert not plan.revises


def test_a_part_that_does_not_exist_is_dropped():
    plan = _plan('{"scope": "parts", "targets": [9, 2]}')
    assert plan.targets == [1]


def test_naming_only_parts_that_do_not_exist_falls_back_rather_than_guessing():
    assert not _plan('{"scope": "parts", "targets": [9, 12]}').revises


def test_too_many_parts_becomes_a_whole_document_pass():
    plan = _plan('{"scope": "parts", "targets": [1, 2, 3, 4]}')
    assert plan.scope == "whole"
    assert plan.targets == [0, 1, 2, 3]


def test_a_judgement_that_is_not_json_falls_back_to_the_old_behaviour():
    assert not _plan("무엇을 고쳐야 할지 잘 모르겠습니다.").revises


def test_the_note_carries_the_typed_sentence_when_the_model_writes_none():
    plan = _plan('{"scope": "parts", "targets": [1]}', message="표를 하나 넣어줘")
    assert plan.note == "표를 하나 넣어줘"


# ── the sentences that are plainly not a revision ──────────────────────


def test_starting_over_is_recognised_without_spending_a_call():
    for sentence in (
        "새로 써 줘",
        "처음부터 다시 해줘",
        "다른 주제로 다시 작성해줘",
        "이건 버리고 다시 써줘",
    ):
        assert revise.obviously_new(sentence), sentence


def test_an_ordinary_edit_is_not_mistaken_for_starting_over():
    for sentence in (
        "3절을 더 짧게",
        "표를 하나 추가해줘",
        "결론에 다음 행동을 적어줘",
        "말투를 조금 더 정중하게 바꿔줘",
    ):
        assert not revise.obviously_new(sentence), sentence


def test_an_empty_document_is_never_revised():
    assert not revise._parse('{"scope": "whole"}', 0, "고쳐줘").revises


# ── what the step on screen says ───────────────────────────────────────


def test_the_step_names_the_parts_being_worked_on():
    plan = _plan('{"scope": "parts", "targets": [2, 3]}')
    label = revise.label(plan, PARTS)
    assert "현행 시스템의 문제" in label and "대안 비교" in label


def test_a_whole_document_pass_says_so():
    assert revise.label(_plan('{"scope": "whole"}'), PARTS) == "문서 전체 고치는 중"
