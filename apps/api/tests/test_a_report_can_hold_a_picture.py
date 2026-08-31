"""A picture put into a report section leaves in the file somebody submits.

The page track has had a way to put a picture in a document since it shipped
and the report track — the surface most of this product's writing happens on —
had none at all. `POST /artifacts/{id}/sections/image` is that way, and it adds
no field and no exporter code: a report is Markdown, a Markdown picture line is
what `report_export._IMAGE` already reads, and what it hands to the renderers is
the same shape a figure the writer proposed arrives in.

So what has to hold is the contract the route leans on. If a picture line in a
section body ever stopped reaching the file, the route would keep answering 200
and the picture would be on the screen and missing from the download — the exact
failure `richtext._picture` was written against once already.
"""

from __future__ import annotations

import io
import zipfile

import pytest

# A sibling module, imported the way pytest makes siblings importable: it puts
# the test file's own directory on the path. `tests.test_export_pictures` needs
# `apps/api` on the path instead, which `python -m pytest` provides by inserting
# the working directory and the `pytest` console script does not — so this
# passed every local run and failed in CI, which runs the script.
from test_export_pictures import png

from app.routers import workspace as router
from app.services import report_export, richtext

TOKENS = {"accent": "#5b5bd6", "ink": "#111111", "muted": "#666666", "font": "gothic"}


def _section(body: str, fmt: str = "markdown") -> dict:
    return {
        "id": "s1",
        "heading": "현황",
        "level": 1,
        "status": "done",
        "content": body,
        "format": fmt,
    }


def _with_picture(fmt: str = "markdown") -> list[dict]:
    """A section the route has appended a picture to, in that body's own shape."""
    src = f"data:image/png;base64,{png()}"
    if fmt == "html":
        body = (
            f'<p>본문</p><figure><img src="{src}" alt="추이" />'
            "<figcaption>추이</figcaption></figure>"
        )
    else:
        body = f"본문\n\n![추이]({src})\n"
    return richtext.normalise([_section(body, fmt)])


@pytest.mark.parametrize("fmt", ["markdown", "html"])
def test_the_docx_carries_a_picture_put_into_a_section(fmt) -> None:
    """Both body shapes, because a section somebody formatted by hand is HTML.

    A Markdown line dropped into an HTML body prints as literal text — the
    reason the route builds a `<figure>` there instead — and this is what says
    the HTML half actually converts back rather than being dropped.
    """
    archive = zipfile.ZipFile(
        io.BytesIO(report_export.to_docx("보고서", _with_picture(fmt), tokens=TOKENS))
    )
    media = [name for name in archive.namelist() if name.startswith("word/media/")]
    assert media, f"{fmt}: 그림이 .docx 에 들어가지 않았습니다"
    assert archive.read(media[0])[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("fmt", ["markdown", "html"])
def test_the_pdf_carries_it_too(fmt) -> None:
    with_picture = report_export.to_pdf("보고서", _with_picture(fmt), tokens=TOKENS)
    without = report_export.to_pdf("보고서", richtext.normalise([_section("본문")]), tokens=TOKENS)
    assert with_picture.count(b"/Image") > without.count(b"/Image")


def test_the_caption_is_what_the_reader_sees_under_it() -> None:
    """The alt text carries the caption because that is what the exporters print.

    Not a description of the picture and not the prompt: a prompt is a request,
    and a request printed under a figure reads as a mistake.
    """
    sections = _with_picture()
    assert "![추이](data:image/png;base64," in sections[0]["content"]


def test_the_route_is_mounted_and_only_takes_a_report() -> None:
    """A deck and a page have their own doors; this one is the report's.

    Checked on the source rather than through the app because the body of the
    route is three lines of markup around `_picture_bytes`, and what could
    regress is which artifacts it accepts.
    """
    routes = {
        route.path: set(route.methods)
        for route in router.router.routes
        if getattr(route, "methods", None)
    }
    assert "POST" in routes["/artifacts/{artifact_id}/sections/image"]

    import inspect

    source = inspect.getsource(router.add_section_image)
    assert "ArtifactKind.report" in source and "not_a_report" in source
    # Snapshotted like a rewrite, or a picture in the wrong section is
    # unrecoverable.
    assert "ArtifactVersion(" in source
    assert "artifact.version += 1" in source
    # And a hand-formatted body goes through the same sanitiser a PATCH uses.
    assert "editable_styles=True" in source


def test_a_picture_for_a_document_is_told_not_to_be_one() -> None:
    """The first picture anybody made came back as a whole slide.

    Asked only for "a picture for 시장 전망", an image model draws the page: a
    title across the top, a chart, three labelled cards down the side. Put into
    a slide that already had a title and bullets, that is a slide inside a
    slide — which is what it looked like.

    The clause goes in for the two pickers inside a document and stays out of
    the image surface, where a picture *is* the whole output and a poster with
    a title on it is a reasonable thing to ask for.
    """
    from app.services import imagegen

    inside = imagegen.compose_prompt("시장 전망", aspect="16:9", style="", figure=True)
    assert imagegen._FIGURE_CLAUSE in inside
    # The load-bearing half: whatever an image model writes it writes badly, and
    # in Korean it writes glyphs that are not words.
    assert "no text" in inside

    alone = imagegen.compose_prompt("시장 전망", aspect="16:9", style="")
    assert imagegen._FIGURE_CLAUSE not in alone


def test_what_the_person_typed_stays_first() -> None:
    """Ordered from the particular to the standing, as the docstring says.

    A model honours the later phrase where two disagree, so the clause must not
    displace the request — and the request must still be the thing being asked
    for rather than a footnote to a list of constraints.
    """
    from app.services import imagegen

    made = imagegen.compose_prompt(
        "서버실 사진", aspect="4:3", style="사진", figure=True
    )
    assert made.startswith("서버실 사진")
    assert made.index(imagegen._FIGURE_CLAUSE) < made.index("photorealistic")
