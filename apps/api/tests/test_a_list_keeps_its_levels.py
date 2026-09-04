"""Nested list levels survive HTML -> Markdown -> exporter."""

from __future__ import annotations

import io
import re
import zipfile

from app.services import report_export, richtext

_NESTED = (
    "<ul>"
    "<li>바깥<ul><li>안쪽</li><li>안쪽 둘</li></ul></li>"
    "<li>둘째<ul><li>속<ul><li>더 속</li></ul></li></ul></li>"
    "<li>셋째</li>"
    "</ul>"
)


def test_an_item_does_not_swallow_the_list_inside_it() -> None:
    """Nested `<ul>` items become two-space-indented Markdown items."""
    assert richtext.to_markdown(_NESTED).splitlines() == [
        "- 바깥",
        "  - 안쪽",
        "  - 안쪽 둘",
        "- 둘째",
        "  - 속",
        "    - 더 속",
        "- 셋째",
    ]


def test_nothing_after_a_sub_list_falls_out_of_the_list() -> None:
    """Items after a closed sub-list are still bullets."""
    lines = report_export._markdown_to_lines(richtext.to_markdown(_NESTED))
    assert [kind for kind, *_ in lines] == ["bullet"] * 7


def test_each_level_is_set_in_and_marked_differently() -> None:
    """Each depth gets its own marker and indent."""
    lines = report_export._markdown_to_lines(richtext.to_markdown(_NESTED))
    assert [(text, marker, depth) for _, text, marker, depth in lines] == [
        ("바깥", "•", 0),
        ("안쪽", "–", 1),
        ("안쪽 둘", "–", 1),
        ("둘째", "•", 0),
        ("속", "–", 1),
        ("더 속", "·", 2),
        ("셋째", "•", 0),
    ]


def test_an_ordered_sub_list_starts_at_its_own_first_number() -> None:
    """An ordered sub-list restarts its numbering."""
    markdown = richtext.to_markdown(
        "<ol><li>하나<ol><li>가</li><li>나</li></ol></li><li>둘</li></ol>"
    )
    lines = report_export._markdown_to_lines(markdown)
    assert [marker for *_rest, marker, _ in lines] == ["1.", "1.", "2.", "2."]


def test_the_hwpx_sets_each_level_in() -> None:
    """The .hwpx uses three paragraph shapes with increasing indents."""
    section = {"id": "1", "heading": "글머리", "level": 1, "format": "html", "content": _NESTED}
    written = report_export.to_hwpx("문서", richtext.normalise([section]))
    archive = zipfile.ZipFile(io.BytesIO(written))
    body = "".join(
        archive.read(name).decode("utf8", "replace")
        for name in archive.namelist()
        if name.startswith("Contents/section")
    )
    indents = dict(
        (pid, left) for pid, _align, left, _prev, _next in report_export._HWPX_PARA_SHAPES
    )
    assert indents[4] < indents[8] < indents[9]
    for shape in (4, 8, 9):
        assert any(shape == int(m) for m in re.findall(r'paraPrIDRef="(\d+)"', body))
