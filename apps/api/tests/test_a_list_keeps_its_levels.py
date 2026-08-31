"""글머리의 위계가 파일까지 간다.

A Korean 공문 is three levels of 글머리 from top to bottom — ○ then • then - —
and the exporters read one. That was recorded as "nested lists flatten", which
would have been a limitation. What actually happened was worse:

    <ul><li>바깥<ul><li>안쪽</li></ul></li><li>둘째</li></ul>
      →  '- 바깥안쪽\n\n둘째'

`바깥안쪽` is not a word. `둘째` left the list altogether and came back as a
paragraph. Both because the patterns were non-greedy: `<li>(.*?)</li>` stopped
at the *inner* item's close, and `<ul>.*?</ul>` at the inner list's.

So the nesting is counted now rather than matched, Markdown carries it as its
own two-space indent, and each exporter reads the indent back and sets the item
in. What a document says is subordinate to what is not decoration.
"""

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
    """바깥안쪽 was the whole of the bug, in one word."""
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
    """`둘째` and `셋째` come after a sub-list closes. They used to arrive as
    prose, which reads as the list having ended two items early."""
    lines = report_export._markdown_to_lines(richtext.to_markdown(_NESTED))
    assert [kind for kind, *_ in lines] == ["bullet"] * 7


def test_each_level_is_set_in_and_marked_differently() -> None:
    """A sub-item at its parent's indent is not a sub-item."""
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
    """Numbering runs within one list. A sub-list is another list, so it starts
    again rather than carrying on from the one it sits inside."""
    markdown = richtext.to_markdown(
        "<ol><li>하나<ol><li>가</li><li>나</li></ol></li><li>둘</li></ol>"
    )
    lines = report_export._markdown_to_lines(markdown)
    assert [marker for *_rest, marker, _ in lines] == ["1.", "1.", "2.", "2."]


def test_the_hwpx_sets_each_level_in() -> None:
    """Three paragraph shapes, at three indents. One shape for all three is a
    flat list wearing three different markers."""
    section = {"id": "1", "heading": "글머리", "level": 1, "format": "html", "content": _NESTED}
    # Out of the archive, not off the zip: `.hwpx` is a zip and decoding the
    # container finds nothing but compressed bytes.
    written = report_export.to_hwpx("문서", richtext.normalise([section]))
    archive = zipfile.ZipFile(io.BytesIO(written))
    body = "".join(
        archive.read(name).decode("utf8", "replace")
        for name in archive.namelist()
        if name.startswith("Contents/section")
    )
    # The shapes the three levels use, and the indents declared for them.
    indents = dict(
        (pid, left) for pid, _align, left, _prev, _next in report_export._HWPX_PARA_SHAPES
    )
    assert indents[4] < indents[8] < indents[9]
    for shape in (4, 8, 9):
        assert any(shape == int(m) for m in re.findall(r'paraPrIDRef="(\d+)"', body))
