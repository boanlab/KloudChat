"""A verify repair keeps the document's 합니다체."""

from app.services import verify


def test_a_plain_ending_repair_is_read_back_in_the_polite_register():
    old = "평점 2.5 이상이면 신청 화면에 입력해야 합니다."
    assert verify.keep_register(old, "직전 학기 평점 2.5 이상인 자를 대상으로 한다.") == (
        "직전 학기 평점 2.5 이상인 자를 대상으로 합니다.")
    assert verify.keep_register("자료에는 없습니다.", "자료에 명시되어 있지 않다.") == (
        "자료에 명시되어 있지 않습니다.")
    # a 한다체 document is left as written; an ending with no polite form is refused
    assert verify.keep_register("대상으로 한다.", "대상으로 삼는다.") == "대상으로 삼는다."
    assert verify.keep_register("확인합니다.", "확인 바란다.") is None
