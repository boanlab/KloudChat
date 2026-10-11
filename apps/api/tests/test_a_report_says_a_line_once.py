"""A line a later section repeats word for word goes from it before the document ships."""


def test_a_line_repeated_in_a_later_section_goes_from_it():
    # Lint flags the repeat (P1); the finish also removes it.
    from app.services import lint

    said = "- 사실 일관성 평가는 요약의 신뢰도를 좌우하는 핵심 과제이다."
    sections = [
        {"id": "a", "heading": "필요성", "content": f"{said}\n- 기존 지표는 한계가 있다."},
        {"id": "b", "heading": "기대효과", "content": f"새 지표를 공개한다.\n\n{said}\n\n"
                                                  "| 항목 | 값 |\n|---|---|\n| 가 | 1 |"},
        {"id": "c", "heading": "요약", "content": said},
    ]
    assert any(f.rule == "repeat" for f in lint.check(lint.from_sections(sections)))
    out, dropped = lint.drop_repeats(sections)
    assert dropped == [said]
    assert out[1]["content"] == "새 지표를 공개한다.\n\n| 항목 | 값 |\n|---|---|\n| 가 | 1 |"
    # A section is never emptied: the one-line 요약 keeps its line.
    assert out[2]["content"] == said
    assert out[0] is sections[0]
    assert not any(f.rule == "repeat" for f in lint.check(lint.from_sections(out[:2])))
