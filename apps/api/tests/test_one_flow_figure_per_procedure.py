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


ASK = ("학부연구생 신청 절차 6장, 흐름도 넣어서.\n"
       "절차: ① 학생이 지도교수 연구실에 지원 메일 → ② 교수 면담 → ③ 승인 시 학생이 학과 사무실에 "
       "신청서 제출 → ④ 학과 사무실 서류 확인(미비 시 ②로 돌아가 보완) → ⑤ 학과장 승인 → "
       "⑥ 연구실 배정 → ⑦ 학기 말 활동보고서 제출 → ⑧ 학점(1학점) 인정 또는 연구장려금 중 택1 지급")


def test_the_numbered_steps_are_read_and_drawn_whole():
    steps = diagrams.given_steps(ASK)
    assert len(steps) == 8 and steps[1] == "교수 면담"
    assert steps[7].endswith("택1 지급")
    # drawn with five of eight and an invented 「최종 순위」: not covering
    short = ("flowchart LR\n a[메일 전송] --> b[지도교수 면담] --> c[서류 제출] --> d[서류 확인]"
             " --> e[최종 순위]")
    assert not diagrams.covers_steps(short, steps)
    # a figure about one step only is another subject
    assert diagrams.covers_steps("flowchart LR\n a[학과 사무실] --> b{미비?} -->|예| c(보완 요청)",
                                 steps)
    whole = diagrams.steps_flowchart(steps)
    from app.services import diagram_shapes

    graph = diagram_shapes.parse(whole)
    assert len(graph.nodes) == 8
    assert graph.nodes["s7"].label == "⑧ 학점 인정 또는 연구장려금 중 택1 지급"
    assert ("s3", "s1", "미비") in [(e.a, e.b, e.label) for e in graph.edges]
    assert diagrams.covers_steps(whole, steps)
    assert diagrams.given_steps("① 하나 ③ 셋 ④ 넷") == ()
