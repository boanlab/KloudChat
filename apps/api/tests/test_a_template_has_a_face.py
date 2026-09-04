"""A 서식 is a shared typesetting plus a face of its own.

Two files, two jobs. `seed.html` — shared, named by `seed_from` — is
correctness: the tokens, the whole closed vocabulary `assemble` can emit, the
`@page` rule, the print rules, the shadow-root `:host`. `design.css` is
identity: 안내문·공지 centres its title under a double rule, 한 장 요약 runs two
columns, 신호 presents white on black.

They were one file for a while, and the merge that made them one is what these
tests are written against. It cost three things, all of them silent:

  · the catalogue kept promising designs that no longer existed — "가운데 정렬
    제목에 이중선" on a 서식 that looked like every other one
  · `@media` wrappers were lost, so a narrow-screen override applied on a
    projector and a print rule applied on screen
  · three custom properties arrived without their declarations, and an
    undeclared property is not an error anywhere: `background: var(--tint)`
    just comes out white

The last one is why `test_every_property_a_seed_uses_is_declared` exists. It is
the only one of the three that no amount of looking at a screen would catch
reliably, because the failure is a thing not appearing.
"""

from __future__ import annotations

import re

import pytest

from app.services import design_templates

#: The document shape everything else borrows. Its own design *is* the shared
#: file, so it must not have a face of its own. The editorial deck now adds a
#: cover treatment while continuing to borrow the shared deck typesetting.
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
    """One insertion, two surfaces.

    The exported file comes from `render` and the document editor's shadow root
    from `stylesheet`, and a 서식 that looked one way while it was typed and
    another when it was downloaded would be worse than one that is plain in
    both. `stylesheet` concatenates every `<style>` block, so splicing a second
    one is enough — this is the test that it stays enough.
    """
    marker = f"/* {template.id} */"
    has_face = bool(_face(template))
    assert (marker in _rendered(template)) is has_face
    assert (marker in design_templates.stylesheet(template, {})) is has_face


@pytest.mark.parametrize("template", PAPER, ids=lambda t: t.id)
def test_the_face_is_read_after_the_shared_typesetting(template) -> None:
    """Order is the whole mechanism.

    A face overrides by coming later, not by being more specific, so nothing in
    it needs `!important` and nothing needs a contrived selector. If it were
    spliced before the shared sheet, every override of equal specificity would
    silently lose.
    """
    face = _face(template)
    if not face:
        pytest.skip("이 서식은 공통 조판이 곧 얼굴이다")
    html = _rendered(template)
    # The shared sheet declares `--paper`; the face is after that declaration
    # and still inside the head, which is the whole of the ordering promise.
    assert html.index(f"/* {template.id} */") > html.index("--paper:")
    assert html.index(f"/* {template.id} */") < html.index("</head>")


@pytest.mark.parametrize("name,base", BASES.items(), ids=list(BASES))
def test_the_base_shape_has_no_face_of_its_own(name, base) -> None:
    """Otherwise the base stops being the base.

    `_document/seed.html` says "document seed — report" in its own first line.
    Giving that base a `design.css` would mean the shared file is nobody's
    design and every document including it is layered on a shape that belongs
    to no one.
    """
    template = design_templates.get(name)
    assert template is not None
    assert _face(template) == ""


#: `var(--x, fallback)` declares its own answer, so an undeclared name there is
#: a decision rather than a hole. Only the bare form is checked.
_BARE_VAR = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*\)", re.I)
_DECLARED = re.compile(r"(--[a-z0-9-]+)\s*:", re.I)


@pytest.mark.parametrize("template", PAPER, ids=lambda t: t.id)
def test_every_property_a_seed_uses_is_declared(template) -> None:
    """The bug this is written against was three lines that did nothing.

    `--accent-ink`, `--tint` and `--receded` came in with the proposal deck's
    layouts when the typesetting was merged, and came in without the block that
    declared them. Nothing raised: an undeclared custom property makes the
    declaration invalid at computed-value time and the property falls back to
    its initial value. So `background: var(--tint)` was white, and
    `border-left: 2px solid var(--accent-ink)` was no border, and the compare
    slide quietly stopped saying which column was being argued for.

    `{{TOKENS}}` is resolved first, because half the palette is declared there.
    """
    css = design_templates.stylesheet(template, {})
    declared = set(_DECLARED.findall(css))
    used = set(_BARE_VAR.findall(css))
    missing = sorted(used - declared)
    assert not missing, f"{template.id}: 선언되지 않은 속성 {missing}"


def test_the_dark_deck_keeps_its_face_on_screen_and_paper() -> None:
    """신호 declares `dark = true`, and every exported surface must know.

    The flag reached `deck_export` and nothing else, so the deck presented light
    in the browser, printed light, and opened dark in PowerPoint — three answers
    to one question. The darkness lives in the face without a media restriction
    so the browser, PDF, and PowerPoint keep the same visual identity.
    """
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
    """A deck's PDF is a slide; a document's is a sheet of paper.

    The deck seed said `A4 landscape` for as long as printing it meant a person
    pressing Ctrl+P for a handout. Once the browser became the exporter that
    stopped being a handout and became the deck's own `.pdf` — the file somebody
    projects — and a four-line slide left two thirds of every page white. The
    numbers here are the ones `deck_export` drew with before the renderer
    changed underneath it, so switching engines did not change the deck's shape.

    Checked on the seed rather than on a rendered PDF because it is the seed
    that has to keep saying it: `prefer_css_page_size` means Chromium reads
    this and nothing else decides.
    """
    for template in PAPER:
        if template.kind != kind:
            continue
        assert f"@page {{ size: {page}" in template.seed, template.id
