"""Person-given amounts never scaled twice."""

from app.services import units

REQUEST = ("아래 발표문 정리해 줘.\n---\n"
           "감축 인원 1명당 연 1,000만원을 3년간 지원한다(대학당 연 최대 30억원).")


def test_a_won_amount_with_a_second_scale_goes_back_to_the_person_s():
    answer = "감축 인원 1명당 연 10,000,000만 원(대학당 연 최대 30억 원)을 지원한다."
    assert units.fix_double_scale(answer, REQUEST) == (
        "감축 인원 1명당 연 1,000만 원(대학당 연 최대 30억 원)을 지원한다.")


def test_right_amounts_and_unrelated_ones_stay():
    right = "연 1,000만 원, 최대 30억 원"
    assert units.fix_double_scale(right, REQUEST) == right
    # Digits that are no amount the person gave are not touched.
    assert units.fix_double_scale("총 12,000,000만 원", REQUEST) == "총 12,000,000만 원"
