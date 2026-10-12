"""A report draws one flow figure per procedure: a second one of the same steps disagrees."""

from app.services import diagrams

DRAWN = """flowchart LR
  a[지원 메일] --> b[교수 면담] --> c[신청서 제출] --> d{서류 확인}
  d -->|미비| b
  d --> e[학과장 승인] --> f[연구실 배정] --> g[활동보고서 제출] --> h[학점 또는 장려금 지급]"""


def _row(index, figure, description):
    return diagrams.Planned(index, figure, description, "")


def test_a_second_flow_of_the_same_procedure_is_not_planned():
    planned = [
        _row(2, "flow", "지원 메일 → 교수 면담 → 신청서 제출 → 서류 확인 → 학과장 승인"),
        _row(3, "concept", "학점 인정과 연구장려금 두 혜택의 관계"),
        _row(4, "flow", "학기 말 성적 입력 → 이의 신청 → 정정 확정"),
    ]
    kept = diagrams.drop_repeated_flows(planned, [DRAWN])
    assert [row.index for row in kept] == [3, 4]
    # two planned flows of one procedure: the first stays
    twice = diagrams.drop_repeated_flows([planned[0], _row(5, "flow", planned[0].description)], [])
    assert [row.index for row in twice] == [2]
