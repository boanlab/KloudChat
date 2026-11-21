"""A bullet that begins with a decimal (「1.63년 내 투자비 회수」) keeps it: the list-number strip
takes 「1. 」 or 「2) 」, never the 「1.」 of a number.

Round-3 r3d05 (11-21, KC-SA): the takeaways slide read 「63년 내 투자비 회수」."""

from __future__ import annotations

from app.services import deck


def test_a_leading_decimal_is_not_a_list_number():
    out = deck._clean_bullets(
        ["1.63년 내 투자비 회수", "2. 1년 무상 유지보수", "3) 연 5,130만 원 절감"])
    assert out == ["1.63년 내 투자비 회수", "1년 무상 유지보수", "연 5,130만 원 절감"]
