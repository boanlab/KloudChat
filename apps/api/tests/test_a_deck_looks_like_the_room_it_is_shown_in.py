"""The outline is shown a look and a colour chosen from the request, not one fixed example."""

from app.services import design
from app.services.deck import _THEMES, suggest_look


def test_the_room_decides_the_style() -> None:
    assert design.venue_style_for("연구실 세미나 발표 자료") == "minimal"
    assert design.venue_style_for("경영진 현황 보고") == "split"
    assert design.venue_style_for("신입 사원 교육 자료") == "warm"
    assert design.venue_style_for("제품 출시 데모") == "dark"
    assert design.venue_style_for("학과 설명회 홍보") == "poster"
    assert design.venue_style_for("주간 업무 정리") == ""


def test_a_style_word_outranks_the_room() -> None:
    # 「미니멀」 is said outright; the seminar it names does not get to argue.
    assert suggest_look("경영진 보고인데 미니멀하게")[1] == "미니멀"


def test_the_subject_decides_the_colour() -> None:
    theme, _ = suggest_look("사내 보안 정책 발표")
    assert theme == "남색"
    theme, _ = suggest_look("환경 동아리 교육 자료")
    assert theme == "초록"


def test_two_unnamed_subjects_do_not_share_a_colour_by_default() -> None:
    themes = {suggest_look(f"주제 {word} 발표")[0] for word in ("가", "나다", "라마바", "사아자차")}
    assert len(themes) > 1
    assert all(theme in _THEMES and theme != "보라" for theme in themes)


def test_the_suggestion_is_a_valid_prompt_label() -> None:
    theme, style = suggest_look("")
    assert theme in _THEMES
    assert style in ("편집형", "포스터형", "미니멀", "다크", "분할형", "따뜻한", "흑백")
