"""A group's sum from one month column to the next (rows per site and measure, 「9월」 「10월」
columns) is a percentage the table works out, so it is not marked 「(확인 필요)」.

Round-3 r3r01 (11-21, KC-SL): a library CSV gave 도서관·지표·9월·10월 rows; the report's
「방문자 31,500명 → 33,195명, 5.4% 증가」 (the 지표 group's sums of the two columns) came out as
「5.4%(확인 필요)」 while each branch's own change stood unmarked."""

from __future__ import annotations

from app.services import report

CSV = ("센터,항목,1분기,2분기\n"
       "북부센터,상담건수,1200,1350\n북부센터,교육인원,300,360\n"
       "남부센터,상담건수,800,770\n남부센터,교육인원,200,190\n"
       "서부센터,상담건수,500,580\n서부센터,교육인원,100,150\n")


def test_the_groups_change_stays_unmarked():
    text = "세 센터 상담건수는 2,500건에서 2,700건으로 8.0% 늘었고, 교육인원은 600명에서 700명으로 16.7% 늘었습니다.\n"
    assert report._without_invented_percents(text, [CSV]) == text


def test_a_change_the_table_does_not_give_is_still_marked():
    out = report._without_invented_percents("상담건수는 23.4% 늘었습니다.\n", [CSV])
    assert "23.4%(확인 필요)" in out
