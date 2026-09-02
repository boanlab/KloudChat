"""논문에 넣을 도식은 이름표가 있다.

The image path draws shapes and cannot spell; the diagram path writes the
figure as mermaid, which is nothing but names and arrows. What is asserted
here is the part that does not need a model: the answer is read correctly,
colour the model was told not to write is stripped, and a starting point
says what it needs before anybody commits.
"""

from __future__ import annotations

from app.services import diagram, prompt_templates


def test_the_source_and_the_caption_come_apart() -> None:
    text = (
        "```mermaid\n"
        "flowchart LR\n"
        '  subgraph enc["인코더"]\n'
        "    x[입력] --> h(어텐션)\n"
        "  end\n"
        "  h --> out[결과]:::hot\n"
        "  out -.->|손실| h\n"
        "```\n"
        "그림: 입력이 인코더를 거쳐 결과가 되는 흐름.\n"
    )
    source, caption = diagram._parse(text)
    assert source.startswith("flowchart LR")
    assert ":::hot" in source
    assert caption == "입력이 인코더를 거쳐 결과가 되는 흐름."


def test_colour_the_model_was_told_not_to_write_is_stripped() -> None:
    text = (
        "```mermaid\nflowchart LR\n  a --> b\n"
        "  style a fill:#f00\n  classDef hot fill:#0f0\n  linkStyle 0 stroke:red\n```\n"
    )
    source, _ = diagram._parse(text)
    assert "style" not in source
    assert "classDef" not in source
    assert "linkStyle" not in source
    assert "a --> b" in source


def test_every_built_in_says_how_to_fill_each_blank() -> None:
    # 「기간·언어」 alone is a noun. The example beside it is the instruction.
    for row in prompt_templates.all_templates():
        assert len(row.examples) == len(row.fills), row.id


def test_a_survey_needs_the_web_and_a_reading_needs_the_file() -> None:
    by_id = {row.id: row for row in prompt_templates.all_templates()}
    assert "web" in by_id["t_report_literature"].needs
    assert "file" in by_id["t_translate"].needs
    assert "인용 형식 맞추기" in by_id["t_report_literature"].skills


def test_an_edit_recomputes_the_findings() -> None:
    """검사 결과는 지금 있는 절만 가리킨다.

    Findings were computed once, at generation, and a report edited by hand
    kept naming sections it no longer had — so 모두 고치기 found nothing to
    rewrite and reported success. Recomputed from the text on every write.
    """
    from app.models.workspace import ArtifactKind
    from app.routers.workspace import _relint

    stale = {
        "sections": [
            {"id": "s1", "heading": "배경", "content": "여기에 내용을 입력하세요."},
        ],
        "lint": [{"severity": "P0", "code": "cjk", "message": "옛 지적", "where": "추진 계획"}],
    }
    fresh = _relint(ArtifactKind.report, stale)
    wheres = {row["where"] for row in fresh["lint"]}
    assert "추진 계획" not in wheres
    assert wheres <= {"배경", ""}


def test_a_source_without_a_publisher_does_not_fail_the_rewrite() -> None:
    """A hand-typed source has a title and a url. One missing `publisher`
    used to be a KeyError that failed every rewrite of the document."""
    from app.services.report import _refs_block

    block = _refs_block([{"title": "출처 하나", "url": "https://example.org"}])
    assert "출처 하나" in block
