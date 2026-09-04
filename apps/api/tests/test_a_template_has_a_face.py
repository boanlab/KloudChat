"""A 서식 is the shared `seed.html` plus its own `design.css`, spliced after it."""

from __future__ import annotations

import re

import pytest

from app.services import design_templates

#: Base shapes whose design is the shared file itself; they have no face.
BASES = {"doc-report": "_document"}

PAPER = [t for t in design_templates.all_templates() if t.kind in ("document", "deck")]


def _face(template) -> str:
    path = design_templates._ROOT / template.id / "design.css"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _rendered(template) -> str:
    return design_templates.render(
        template, title="제목", tokens={}, body="<section><h2>절</h2><p>본문</p></section>"
    )


@pytest.mark.parametrize("template", PAPER, ids=lambda t: t.id)
def test_a_face_reaches_the_file_and_the_editor_both(template) -> None:
    """`render` and `stylesheet` both carry the face."""
    marker = f"/* {template.id} */"
    has_face = bool(_face(template))
    assert (marker in _rendered(template)) is has_face
    assert (marker in design_templates.stylesheet(template, {})) is has_face


@pytest.mark.parametrize("template", PAPER, ids=lambda t: t.id)
def test_the_face_is_read_after_the_shared_typesetting(template) -> None:
    """The face is spliced after the shared sheet, inside the head."""
    face = _face(template)
    if not face:
        pytest.skip("이 서식은 공통 조판이 곧 얼굴이다")
    html = _rendered(template)
    # After the shared sheet's `--paper`, still inside the head.
    assert html.index(f"/* {template.id} */") > html.index("--paper:")
    assert html.index(f"/* {template.id} */") < html.index("</head>")


@pytest.mark.parametrize("name,base", BASES.items(), ids=list(BASES))
def test_the_base_shape_has_no_face_of_its_own(name, base) -> None:
    """The base document shape has no `design.css`."""
    template = design_templates.get(name)
    assert template is not None
    assert _face(template) == ""


#: Only the bare form; `var(--x, fallback)` declares its own answer.
_BARE_VAR = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*\)", re.I)
_DECLARED = re.compile(r"(--[a-z0-9-]+)\s*:", re.I)


@pytest.mark.parametrize("template", PAPER, ids=lambda t: t.id)
def test_every_property_a_seed_uses_is_declared(template) -> None:
    """Every bare `var(--x)` in a stylesheet is declared, `{{TOKENS}}` resolved first."""
    css = design_templates.stylesheet(template, {})
    declared = set(_DECLARED.findall(css))
    used = set(_BARE_VAR.findall(css))
    missing = sorted(used - declared)
    assert not missing, f"{template.id}: 선언되지 않은 속성 {missing}"


def test_the_dark_deck_keeps_its_face_on_screen_and_paper() -> None:
    """The dark deck's face is not restricted to `@media screen`."""
    template = design_templates.get("deck-signal")
    assert template is not None and template.dark
    face = _face(template)
    assert "--paper: color-mix" in face
    assert "body { background: var(--paper)" in face
    assert "@media screen" not in face


@pytest.mark.parametrize(
    "kind,page",
    [("document", "A4 portrait"), ("deck", "960pt 540pt")],
    ids=["문서는 A4 세로", "덱은 16:9"],
)
def test_each_kind_prints_at_the_shape_it_is(kind, page) -> None:
    """Seeds declare `@page` A4 portrait for documents and 960pt x 540pt for decks."""
    for template in PAPER:
        if template.kind != kind:
            continue
        assert f"@page {{ size: {page}" in template.seed, template.id
