"""A table header the model wrote on the sentence's line is moved to its own line."""

from app.services import report_export, richtext

GLUED = (
    "비교한 결과는 다음과 같습니다. | 주파수 (Hz) | 측정 이득 | 오차 |\n"
    "| :--- | :--- | :--- |\n"
    "| 100 | 0.99 | -0.09 |\n"
    "| 1590 | 0.70 | -0.08 |"
)


def test_the_header_moves_to_its_own_line():
    lines = richtext.detach_tables(GLUED).split("\n")
    assert lines[:3] == ["비교한 결과는 다음과 같습니다.", "", "| 주파수 (Hz) | 측정 이득 | 오차 |"]
    assert lines[3] == "| :--- | :--- | :--- |"


def test_prose_with_a_bar_and_no_table_is_left_alone():
    text = "선택지는 A | B | C 셋이다.\n다음 줄"
    assert richtext.detach_tables(text) == text
    assert richtext.detach_tables("| a | b |\n| --- | --- |") == "| a | b |\n| --- | --- |"


def test_the_exporter_reads_the_glued_table_as_a_table():
    kinds = [kind for kind, *_ in report_export._markdown_to_lines(GLUED)]
    assert "table" in kinds and kinds[0] == "body"


def test_the_web_mirror_uses_the_same_patterns():
    import pathlib
    import re

    web = pathlib.Path(__file__).parents[2] / "web/src/lib/markdownTables.ts"
    source = web.read_text()
    glued = re.search(r"const GLUED_HEADER = /(.+)/\n", source).group(1)
    separator = re.search(r"const SEPARATOR_ROW = /(.+)/\n", source).group(1)
    # Same shapes; the server bounds the prose part for its regex engine.
    assert glued.replace("[^|\\n]*", "[^|\\n]{0,4000}") == richtext._GLUED_HEADER.pattern.replace(
        "(?P<prose>", "("
    ).replace("(?P<head>", "(")
    assert separator == richtext._SEPARATOR_ROW.pattern
