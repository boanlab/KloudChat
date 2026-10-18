"""A report figure that repeats a table, or has too few boxes to say anything, is left out."""

from app.pipeline import report_stages
from app.services import diagrams, figure_kinds, report

TABLE = """| 구분 | 공사 전 | 공사 후 |
| :--- | :--- | :--- |
| 좌석 배치 | 긴 테이블 120석 | 세 구역 120석 |
| 조명 | 차가운 형광등 | 따뜻한 색 조명 |
| 쓰임새 | 식사 | 식사·회의·휴식 |
"""
SECTION = f"좌석·조명·쓰임새가 바뀝니다.\n\n**표: 공사 전후 비교**\n\n{TABLE}\n뒤 문단입니다."


def _compare(rows) -> dict:
    slots = {"kind": "compare", "lang": "ko", "sides": ["공사 전", "공사 후"],
             "rows": [{"aspect": a, "before": b, "after": c} for a, b, c in rows]}
    return {"figure": "compare", "source": figure_kinds.to_source(slots)}


SAME = _compare([("좌석", "긴 테이블 120석", "세 구역 120석"),
                 ("조명", "차가운 형광등", "따뜻한 색 조명")])
MORE = _compare([("좌석", "긴 테이블 120석", "세 구역 120석"),
                 ("소음", "점심 시간에 대화 어려움", "구역별로 분리")])


def test_a_comparison_of_the_table_s_rows_repeats_it():
    assert diagrams.repeats_table(SAME, TABLE)
    assert not diagrams.repeats_table(MORE, TABLE)


STEPS_TABLE = """| 항목 | 내용 |
| --- | --- |
| 총 기간 | 3주 |
| 단계 1 | 철거 |
| 단계 2 | 배선·조명 교체 |
| 단계 3 | 가구 설치 |
| 단계 4 | 시범 운영 |
"""
STEPS = {"figure": "flow", "source": figure_kinds.to_source({
    "kind": "steps", "lang": "ko", "steps": [{"name": n} for n in (
        "철거", "배선·조명 교체", "가구 설치", "시범 운영")]})}


def test_a_comparison_mostly_the_table_s_rows_repeats_it():
    # one row says a little more (「창가·중앙·안쪽」), the criteria and the rest are the table's
    richer = _compare([("좌석", "긴 테이블 120석", "창가·중앙·안쪽 세 구역 120석"),
                       ("조명", "차가운 형광등", "따뜻한 색 조명"),
                       ("쓰임새", "식사", "식사·회의·휴식")])
    assert diagrams.repeats_table(richer, TABLE)


def test_steps_listed_by_name_in_a_table_repeat_it():
    assert diagrams.repeats_table(STEPS, STEPS_TABLE)
    # a table that says more of each step (when, who) is not repeated by the names
    wide = ("| 단계 | 기간 | 담당 |\n| --- | --- | --- |\n| 철거 | 1주 | 시설팀 |\n"
            "| 배선·조명 교체 | 1주 | 전기 |\n| 가구 설치 | 4일 | 업체 |\n"
            "| 시범 운영 | 3일 | 총무 |\n")
    assert not diagrams.repeats_table(STEPS, wide)


def test_a_structure_of_three_boxes_is_too_thin():
    thin = {"figure": "method",
            "source": "flowchart LR\n  a[수요 조사] --> b[검토] --> c[투자 판단]"}
    full = {"figure": "method", "source": "flowchart LR\n  a[수요 조사] --> b[검토]\n"
                                            "  b --> c[투자 판단]\n  b --> d[보류]"}
    assert diagrams.too_thin(thin) and not diagrams.too_thin(full)
    assert not diagrams.too_thin(SAME)


def test_a_repeating_figure_is_left_out_unless_figures_were_asked_for():
    sections = [{"content": SECTION}]
    assert not report_stages._figure_says_more(SAME, sections, asked=False)
    assert sections[0]["content"] == SECTION
    # asked for as a figure: the figure stands, the table it repeats goes with its caption
    assert report_stages._figure_says_more(SAME, sections, asked=True)
    assert "| 조명 |" not in sections[0]["content"] and "**표:" not in sections[0]["content"]
    assert sections[0]["content"] == "좌석·조명·쓰임새가 바뀝니다.\n\n뒤 문단입니다."
    assert report_stages._figure_says_more(MORE, [{"content": SECTION}], asked=False)


def test_a_table_is_dropped_alone():
    assert report.drop_table("앞\n\n" + TABLE, "없는 표") == "앞\n\n" + TABLE
    assert report.drop_table("앞\n\n" + TABLE, TABLE) == "앞"
    # prose that pointed at the table points at the figure that replaces it
    assert report.drop_table("위 표에 따라 바뀝니다.\n\n" + TABLE, TABLE) == "그림에 따라 바뀝니다."
    assert report.drop_table("표지 다음 표준안\n\n" + TABLE, TABLE) == "표지 다음 표준안"
