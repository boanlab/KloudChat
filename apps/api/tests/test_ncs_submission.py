import pytest

from app.services.ncs_submission import submitted_choice_from_request


@pytest.mark.parametrize(
    "text,choice",
    [
        ("1", 1),
        (" 2번 ", 2),
        ("10번!", 10),
        ("제 답은 1번입니다. 채점과 해설만 해 주세요.", 1),
        ("매출이 80에서 100으로 늘었습니다. 제 답은 1번 20%입니다. 채점해 주세요.", 1),
        ("내 선택은 3번이야", 3),
        ("My answer is 4. Please grade it.", 4),
    ],
)
def test_only_explicit_supported_answer_syntax_is_recognized(text, choice):
    assert submitted_choice_from_request(text) == choice


@pytest.mark.parametrize(
    "text",
    [
        "A팀 9명의 평균은 60점, B팀 6명의 평균은 90점입니다. 1번 75점, 2번 70점, 3번 72점.",
        "0.1 + 0.2는? 1번 0.1, 2번 0.2, 3번 0.3.",
        "제 답은 1번이 아닙니다.",
        "제 답은 1번입니다. 취소합니다.",
        "제 답은 1번인지 모르겠습니다.",
        '예시: "제 답은 1번입니다."',
        "제 답은 1번입니다. 제 답은 2번입니다.",
        "1번 또는 2번",
        "11번",
        "0",
        "",
        "2.5",
        "정답은 3번입니다.",
        "My answer is not 1.",
        "My answer is 1. My answer is 2.",
        "x" * 8193,
    ],
)
def test_missing_quoted_uncertain_conflicting_or_unsupported_answers_are_not_guessed(text):
    assert submitted_choice_from_request(text) is None
