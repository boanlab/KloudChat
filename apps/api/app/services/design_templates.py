"""The rendering catalogue: shapes the model writes into, rather than prompts.

A **prompt template** (`models.workspace.Template`) is a sentence someone
starts from. A **design template** is the other half of that idea: the shape
the answer comes out in. It carries a seed document with the CSS already
decided and a small vocabulary of blocks the model is allowed to fill, so the
model writes content and never layout.

Each entry is a folder under `app/design_templates/<id>/`:

    template.toml     metadata — read with `tomllib`, no new dependency
    instructions.md   what the model is told, appended to the surface prompt
    checklist.md      the rubric a critique scores this shape against
    seed.html         the shell, with `{{TITLE}}` / `{{TOKENS}}` / `{{BODY}}`
    sample.html       a body fragment the gallery previews

The gallery preview is the seed rendered around `sample.html` rather than a
second baked file. A preview that can drift from the shape it advertises is
worse than no preview, and two files saying the same thing drift the first
time one of them is edited.

Two constraints shape every seed, and both are load-bearing:

**No script.** Artifacts render in a `sandbox=""` iframe, so nothing in here
executes. A deck navigates by CSS scroll-snap rather than a keyboard handler.
That is a smaller deck than one with a runtime, and it is one that cannot run
model-written JavaScript in somebody's browser.

**Print is a first-class output.** There is no headless browser in this image
— `report_export` explicitly chose reportlab over an HTML engine — so the way
an HTML artifact becomes a PDF is the reader's own print dialogue. Every seed
therefore carries `@media print` rules that put one slide or section per page.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models.chat import SessionKind
from app.services import pictures

_ROOT = Path(__file__).resolve().parent.parent / "design_templates"

#: Which surface each kind is offered on. `document` is the report surface's
#: rendering track, `deck` the slides surface's.
SURFACE: dict[str, SessionKind] = {
    "deck": SessionKind.slides,
    "document": SessionKind.report,
    "image": SessionKind.image,
    # Audio and video share one surface, as they do everywhere else here.
    "video": SessionKind.av,
    "audio": SessionKind.av,
}

#: Kinds that produce an HTML artifact. `image` templates only shape a prompt.
HTML_KINDS = ("deck", "document")

#: Blocks a fragment may contain. Everything else is dropped — the seed owns
#: the layout, so an invented `<div class="grid-4">` gets a slide with no
#: styling rather than a broken one.
#: `h2` is absent: the wrapper writes the block's heading from the outline.
#: Sub-headings use `h3`.
#: `code` is inline only. `<pre>` is absent because its contents are
#: whitespace-significant and the file exporters read markdown lines — a
#: stack trace would arrive re-indented and half read back as a list.
_ALLOWED_TAGS = {
    "p", "h3", "ul", "ol", "li", "strong", "em", "blockquote", "code",
    "figure", "figcaption", "img", "table", "thead", "tbody", "tr", "th", "td",
    "div", "span", "section", "br", "hr", "small", "dl", "dt", "dd",
    # The footnote reference. `<small>` carries the note itself and always
    # could; without a marker in the sentence there was no way to say *which*
    # sentence a note belonged to, which is the whole difference between a
    # footnote and a paragraph in smaller type.
    "sup",
}

_TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")
#: Removed with their contents rather than unwrapped.
#:
#: Script and friends have no place in a document assembled from model output.
#: `h1` and `h2` are here because the wrapper writes the block's heading, so
#: one in the body is a title printed twice.
_SCRIPTISH = re.compile(
    r"<(script|style|iframe|object|embed|link|meta|h1|h2)\b.*?(</\1\s*>|$)", re.S | re.I
)
#: The leading `\s` is one character rather than `\s+`, and the rest is
#: possessive. `\s+` here is quadratic: at every position in a run of
#: whitespace the engine consumes the whole run and then fails on the next
#: literal, so a fragment padded with sixty thousand spaces took twenty-one
#: seconds to sanitise — and these fragments are model-generated. One space is
#: all the rule needs to know it is between attributes; the whitespace it
#: leaves behind sits inside a tag, where it means nothing. Same attributes
#: removed, 0.001s.
_EVENT_ATTR = re.compile(r"\son[a-z]++\s*+=\s*+(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
#: Inline presentation. The seed owns every colour, size and space in the
#: document; a `style=` is the one thing that can actually win against it, and
#: the model's own never agrees with the template around it. `class` survives —
#: that is how a block reaches the names its own seed styles, such as `lead`
#: and `cols`.
_STYLE_ATTR = re.compile(r"\sstyle\s*+=\s*+(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)

#: Declarations a person may set by hand, and nothing else.
#:
#: Dropping `style` wholesale was right while the only author was the model: a
#: writer that invents its own type scale produces a document that disagrees
#: with itself on every page. It is wrong once somebody is editing the document
#: in a word processor, because the four things they reach for first — size,
#: face, alignment, emphasis colour — have no other way to be expressed, and a
#: silent strip means their edit vanishes on save.
#:
#: So the rule became an allowlist rather than a switch. What is *not* here is
#: the point of it: no `position`, no `display`, no margin or padding, no
#: `background` — layout stays the seed's, which is what keeps a template a
#: template after somebody has typed in it. Values are bounded too; `url()`,
#: `expression(` and anything with a semicolon-smuggled second declaration
#: cannot survive `_declaration`.
_EDITABLE_STYLE = {
    "font-size",
    "font-family",
    "font-weight",
    "font-style",
    "text-align",
    "text-decoration",
    "color",
    "line-height",
}
#: A property/value pair with nothing exotic in the value. Deliberately narrow:
#: letters, digits, spaces, and the punctuation a font stack or a length needs.
#:
#: **No parentheses.** None of `_EDITABLE_STYLE` needs them — a font stack is
#: quoted names and commas, a length is a number and a unit, and a colour from
#: a picker is a hex triple. Allowing them let `font-size: expression(alert(1))`
#: through on the first pass, which is legacy-IE only and still not something to
#: write into a file that gets downloaded and opened outside the sandbox. With
#: them gone, `url(`, `expression(` and `calc(` all fail to match and the whole
#: declaration is dropped.
_DECLARATION = re.compile(
    r"^\s*([a-z-]{2,20})\s*:\s*([A-Za-z0-9 ,.%#'\"_-]{1,120})\s*$"
)
_URL_ATTR = re.compile(r"\s(href|src)\s*+=\s*+(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
#: What is inside a `<code>` is text. Every other rule below reads the
#: fragment as markup, so left alone `<code><div></code>` becomes a real
#: division. The closing tag is required, unlike `_SCRIPTISH`'s: an unclosed
#: one is a typo, and escaping to the end would turn the rest of the block
#: into visible tag soup.
_CODE = re.compile(r"(<code\b[^>]*>)(.*?)</code\s*>", re.S | re.I)
#: An ampersand that is not already opening an entity. A model writes a sample
#: both ways — `&lt;div&gt;` and a bare `<div>` — and both have to arrive as
#: the four characters somebody can copy, so neither may be escaped twice.
_BARE_AMP = re.compile(r"&(?!#?\w{1,32};)")

@dataclass(frozen=True, slots=True)
class Argument:
    """One blank in a media template's prompt.

    The prompt these fill is the *whole* input on an image or video surface, so
    it is filled in the composer where the person can still read and edit it —
    a template that sent something they never saw would be a template they
    could not correct.
    """

    name: str
    label: str
    label_en: str
    default: str
    default_en: str
    #: A closed list renders as a picker; empty renders as a text field.
    options: tuple[str, ...]
    options_en: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DesignTemplate:
    id: str
    kind: str
    name: str
    description: str
    #: Gallery filter chip, and the scenario line under the card.
    category: str
    #: The English half. Both sides go on the wire and the client picks by
    #: language — the server has no business knowing which one is looking.
    #: Empty falls back to the Korean, which leaves a card readable rather
    #: than blank.
    name_en: str
    description_en: str
    category_en: str
    fills_en: tuple[str, ...]
    example_prompt_en: str
    #: What the person has to bring, shown before they commit — same idea as
    #: `Template.fills`, so the two galleries read alike.
    fills: tuple[str, ...]
    #: The starting sentence the card's button puts in the composer.
    example_prompt: str
    #: Appended to the generation prompt. Never shown to the reader.
    instructions: str
    #: The seed's own markup vocabulary — the elements its stylesheet already
    #: stands up. Shared, and loaded from the same folder the seed comes from,
    #: because it describes the seed rather than the 서식: eight of seventeen
    #: 서식 named their layouts and never said what to build them out of, so a
    #: model handed `layout: split` and no vocabulary wrote a bulleted list and
    #: the two-column design nothing had asked it to use went unused. A 서식's
    #: own instructions still come first and still win — this is the floor.
    markup: str
        #: What a critique reads the finished thing against. Separate from the
        #: instructions: a rubric folded into the brief becomes a checklist the
        #: model writes *to* rather than one it is measured by. Shown on the
        #: gallery card through `checks`.
    checklist: str
    #: Whether this template's slides are laid on a dark ground. Carried into
    #: the `.pptx`, which is for presenting; the `.pdf` stays light because it
    #: is for paper, exactly as the seed's own print rules decide.
    dark: bool
    #: Blanks in `example_prompt`, written `{name}`. Media templates only:
    #: a deck's brief is a sentence somebody writes, not a form they fill.
    arguments: tuple[Argument, ...]
    #: Composer settings this template implies — aspect, duration, voice. The
    #: catalogue entry knows the shape it is for; making the person set them
    #: again after picking it is asking twice.
    defaults: dict[str, Any]
    #: `image` templates only: the English clause folded into the picture
    #: prompt. Separate from `instructions`, which is Korean prose for the
    #: writing surfaces — a picture model reads neither well nor at that
    #: length.
    prompt_suffix: str
    #: The 서식's Word half — a real `.docx` whose styles, page setup and
    #: theme the exporter opens and writes into. Empty when the folder has
    #: none, and then `report_export` falls back to Word's own defaults.
    #:
    #: A path rather than bytes: `python-docx` opens a file, the templates are
    #: 37KB each, and holding five of them in memory for the life of the
    #: process buys nothing.
    docx_template: str
    #: The 서식's PowerPoint half, for the deck surfaces — a real `.pptx`
    #: whose master, layouts and theme `deck_export` opens and builds on.
    #: Empty when the folder has none, and then PowerPoint's own defaults.
    pptx_template: str
    #: The blank form somebody downloads — the 서식 as a file to fill in by
    #: hand, in the same styles the export comes out in.
    #:
    #: A second file rather than the one above, because the writer *appends*
    #: to `template.docx`: a heading left in it would arrive at the top of
    #: every report written from it. `.docx` on the document surfaces and
    #: `.pptx` on the deck ones; empty where a 서식 has no form yet.
    form_file: str
    #: The shell every slide or section is placed into.
    seed: str
    #: Layout names this template's instructions actually describe. The first
    #: is always the cover, and the outline call is offered only these.
    layouts: tuple[str, ...]
    #: How a written block becomes markup. Declarative rather than a branch in
    #: the assembler, because "sections sit in a grid" is a property of the
    #: one-pager, not of documents.
    wrap_cover: str
    wrap_block: str
    #: Optional wrapper around every non-cover block, `{blocks}` inside.
    wrap_group: str
    #: What this template's own instructions promise about a slide's shape,
    #: in the two numbers the checker can count: items per block, characters
    #: per item. The lecture deck asks for 25 characters and the checker's
    #: general bound is 45 — without this, a template's own rule is the one
    #: rule nothing enforces. Empty means the general bounds apply.
    limits: dict[str, int]
    #: Catalogue skills this 서식 works best with, by `catalog_key`.
    #:
    #: A 서식 is a shape and a skill is a procedure, and some shapes imply
    #: their procedure — a 공문 without the 공문 문체 rules is a notice-shaped
    #: essay. Applied automatically when a document is generated in this 서식,
    #: and announced in the same `skills_applied` event a hand-activated skill
    #: gets, so the person sees what joined the prompt and can turn it off by
    #: switching 서식.
    skills: tuple[str, ...]

    @property
    def surface(self) -> SessionKind:
        return SURFACE[self.kind]

    @property
    def checks(self) -> tuple[str, ...]:
        """The checklist as sentences, for somebody choosing rather than a model.

        `critique` hands the file over whole and the bullet marks do it no
        harm; a card renders each line as a list item of its own, and a stray
        dash in front of it reads as the template's punctuation. Split here so
        both readings come from the one file, which is what keeps them from
        drifting.
        """
        return tuple(
            line.lstrip("-").strip()
            for line in self.checklist.splitlines()
            if line.lstrip().startswith("-")
        )


#: Form text by path, so a turn does not re-open a 38KB zip to read 250 characters.
#: Keyed by path and mtime: rebuilding the forms changes the second, and the
#: next turn reads the new one.
_FORM_TEXT: dict[tuple[str, float], str] = {}


def form_text(template: DesignTemplate) -> str:
    """The blank form as words, for the model to write into.

    A 서식 told the model its rules and showed it nothing. The rules are prose
    — "결정마다 왜 그렇게 정했는지 한 줄이라도 남긴다" — and the form beside
    them is the same thing as a shape: the headings in order, the columns of
    each table, the line under each heading saying what belongs there. Handing
    over the file it already ships is cheaper than describing it again in
    another file that then drifts from it.

    Short by construction. A form is headings and column names, so 회의록 comes
    to 257 characters and 사내 브리핑 to 231 — this is not a document being
    stuffed into the context, it is a table of contents with the blanks named.
    """
    if not template.form_file:
        return ""
    path = Path(template.form_file)
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return ""
    if key not in _FORM_TEXT:
        from app.services import files as file_service

        try:
            _FORM_TEXT[key] = file_service.extract_text(path.name, "", path.read_bytes()).strip()
        except Exception:  # noqa: BLE001
            # A form that cannot be read is a form the model writes without.
            # The instructions still stand, and they are the half that says why.
            _FORM_TEXT[key] = ""
    return _FORM_TEXT[key]


#: Where a 서식's own stylesheet is spliced in — last in `<head>`, so it is
#: read after everything the shared seed declares and wins a tie without
#: needing `!important` or a specificity contest.
_HEAD_END = re.compile(r"</head\s*>", re.I)


def _seed(folder: Path, meta: dict) -> str:
    """The typesetting this 서식 is drawn in: a shared shape, then its own face.

    Two files, because they answer two different questions.

    `seed.html` — the shared one, named by `seed_from` — is *correctness*. It
    declares the tokens, styles the whole closed vocabulary `assemble` can
    emit, carries the `@page` rule and the print rules, and gets the shadow-root
    `:host` right. Every 서식 needs all of that and none of it is what makes one
    서식 different from another. Ten copies of it drifted once already: one got
    a fix and the other nine did not.

    `design.css` — this 서식's own — is *identity*. 안내문·공지 centres its
    title under a double rule; 한 장 요약 runs two columns; 신호 presents white
    on black. Those are forty lines each, not four hundred, and they are the
    forty lines the catalogue already promises in its own descriptions.

    Splitting them this way was worth doing only once a browser drew the PDF.
    Before that the export was redrawn by reportlab, which reads no CSS, so ten
    designs made one file and merging them lost nothing anybody could see.
    """
    seed = ""
    own = folder / "seed.html"
    if own.is_file():
        seed = own.read_text(encoding="utf-8")
    else:
        borrowed = str(meta.get("seed_from") or "")
        if borrowed:
            shared = folder.parent / borrowed / "seed.html"
            if shared.is_file():
                seed = shared.read_text(encoding="utf-8")
    if not seed:
        return ""
    face = _read(folder, "design.css").strip()
    if not face:
        return seed
    # A second `<style>` rather than an edit to the first: `stylesheet()`
    # concatenates every style block in order, so the editor's shadow root
    # picks this up by the same route the exported file does. One insertion,
    # both surfaces.
    block = f"<style>\n/* {folder.name} */\n{face}\n</style>\n"
    return _HEAD_END.sub(lambda _m: block + "</head>", seed, count=1)


def _seed_markup(folder: Path, meta: dict[str, Any]) -> str:
    """The vocabulary of whichever seed this 서식 is drawn on.

    Follows `seed_from` exactly as `_seed` does, so a 서식 that borrows the
    deck seed is told about the deck seed's elements and not the document's.
    """
    own = _read(folder, "markup.md")
    if own:
        return own
    borrowed = str(meta.get("seed_from") or "")
    if not borrowed:
        return ""
    shared = folder.parent / borrowed / "markup.md"
    return shared.read_text(encoding="utf-8") if shared.is_file() else ""


def _read(folder: Path, name: str) -> str:
    path = folder / name
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _load() -> dict[str, DesignTemplate]:
    found: dict[str, DesignTemplate] = {}
    if not _ROOT.is_dir():
        return found
    # `_` folders hold the shared typesettings rather than a 서식. A 서식 is a
    # name, a set of rules and a file to fill in; the paper it is drawn on is
    # one of two, and neither of those is something anybody picks.
    for folder in sorted(
        p for p in _ROOT.iterdir() if p.is_dir() and not p.name.startswith("_")
    ):
        manifest = folder / "template.toml"
        if not manifest.is_file():
            continue
        meta: dict[str, Any] = tomllib.loads(manifest.read_text(encoding="utf-8"))
        kind = str(meta.get("kind") or "")
        if kind not in SURFACE:
            continue
        wrap: dict[str, Any] = meta.get("wrap") or {}
        found[folder.name] = DesignTemplate(
            id=folder.name,
            kind=kind,
            name=str(meta.get("name") or folder.name),
            description=str(meta.get("description") or ""),
            category=str(meta.get("category") or "일반"),
            fills=tuple(str(f) for f in (meta.get("fills") or [])),
            example_prompt=str(meta.get("example_prompt") or ""),
            name_en=str(meta.get("name_en") or ""),
            description_en=str(meta.get("description_en") or ""),
            category_en=str(meta.get("category_en") or ""),
            fills_en=tuple(str(f) for f in (meta.get("fills_en") or [])),
            example_prompt_en=str(meta.get("example_prompt_en") or ""),
            instructions=_read(folder, "instructions.md"),
            markup=_seed_markup(folder, meta),
            checklist=_read(folder, "checklist.md"),
            prompt_suffix=str(meta.get("prompt_suffix") or ""),
            dark=bool(meta.get("dark")),
            arguments=tuple(
                Argument(
                    name=str(arg.get("name") or ""),
                    label=str(arg.get("label") or ""),
                    label_en=str(arg.get("label_en") or ""),
                    default=str(arg.get("default") or ""),
                    default_en=str(arg.get("default_en") or ""),
                    options=tuple(str(o) for o in (arg.get("options") or [])),
                    options_en=tuple(str(o) for o in (arg.get("options_en") or [])),
                )
                for arg in (meta.get("arguments") or [])
                if arg.get("name")
            ),
            defaults=dict(meta.get("defaults") or {}),
            docx_template=str(folder / "template.docx")
            if (folder / "template.docx").is_file()
            else "",
            pptx_template=str(folder / "template.pptx")
            if (folder / "template.pptx").is_file()
            else "",
            form_file=next(
                (
                    str(folder / name)
                    for name in ("form.docx", "form.pptx")
                    if (folder / name).is_file()
                ),
                "",
            ),
            seed=_seed(folder, meta),
            layouts=tuple(str(x) for x in (meta.get("layouts") or ["cover", "section"])),
            wrap_cover=str(wrap.get("cover") or "{body}"),
            wrap_block=str(wrap.get("block") or "{body}"),
            wrap_group=str(wrap.get("group") or ""),
            limits={
                key: int(value)
                for key, value in (meta.get("limits") or {}).items()
                if key in ("max_bullets", "max_bullet_chars") and int(value) > 0
            },
            skills=tuple(str(k) for k in (meta.get("skills") or [])),
        )
    return found


#: Read once. The catalogue ships inside the image, so a rescan per request
#: would be disk reads for a directory that cannot have changed.
_TEMPLATES = _load()


def all_templates() -> list[DesignTemplate]:
    return list(_TEMPLATES.values())


def for_surface(kind: SessionKind) -> list[DesignTemplate]:
    return [t for t in _TEMPLATES.values() if t.surface is kind]


def get(template_id: str | None) -> DesignTemplate | None:
    return _TEMPLATES.get(template_id or "")


def default_for(defaults: Any, kind: SessionKind) -> str | None:
    """The format a project hands a new session on this surface, if any.

    Lenient where the write path is strict, deliberately. A project's map is
    written through a router that refuses an id this catalogue cannot place,
    so what is left for the read to meet is the case validation cannot reach:
    an id that stopped existing between two versions of this image. That
    degrades to the built-in track, the way a session's own stale id does — a
    project nobody can start work in would be the worse failure.
    """
    if not isinstance(defaults, dict):
        return None
    chosen = get(defaults.get(kind.value))
    if chosen is None or chosen.kind not in HTML_KINDS or chosen.surface is not kind:
        return None
    return chosen.id


def _quoted_code(match: re.Match[str]) -> str:
    """One `<code>` element with its contents turned back into characters."""
    inner = _BARE_AMP.sub("&amp;", match.group(2))
    return f"{match.group(1)}{inner.replace('<', '&lt;').replace('>', '&gt;')}</code>"


#: A first line that restates the brief rather than answering it —
#: `layout: "bullets"`, `layout = quote`. Anchored at the start of the
#: fragment, not per line: past the content it is the model's own word.
_LAYOUT_DIRECTIVE = re.compile(r"^[ \t]*layout[ \t]*[:=][^\n]*\n", re.I)


#: Fields of an envelope that describe the block rather than fill it. What is
#: left after these is the answer, whatever the model decided to call it.
_ENVELOPE_META = frozenset({"layout", "title", "heading", "n", "index", "id"})


def _unwrapped(fragment: str) -> str:
    """The prose out of an answer that came back as its own envelope.

    The block prompt asks for `HTML 조각만` and shows no object at all — but the
    outline call one step earlier answers in JSON, and a small model carries the
    habit into the next call. The slide then opens with
    `{"layout":"cover","content":"…` behind a speaker.

    Read structurally rather than by key name, because there is no key to know:
    nothing was ever specified, and the same model called it `body` in one run
    and `content` in the next. So the metadata fields are dropped and what is
    left has to be exactly one string — one object, one answer. Two payloads is
    ambiguous and stays as it arrived.

    Salvaged rather than refused, which is the trade `page._salvaged` already
    makes one level up. What will not parse — a block cut off at the token
    limit — is handed on whole, because guessing where it ended would put words
    on a slide nobody wrote. `lint` names that one instead.
    """
    text = fragment.strip()
    if not text.startswith("{"):
        return fragment
    try:
        parsed = json.loads(text)
    except ValueError:
        return fragment
    if not isinstance(parsed, dict):
        return fragment
    payload = [
        value
        for key, value in parsed.items()
        if key.lower() not in _ENVELOPE_META and isinstance(value, str) and value.strip()
    ]
    return payload[0] if len(payload) == 1 else fragment


def _without_layout_preamble(fragment: str, layouts: Sequence[str]) -> str:
    """The fragment with the layout it was handed taken off the front.

    A small model answers the brief before it answers the request: asked for
    the inside of a `bullets` block it writes `bullets`, then the list — and
    the wrapper puts that word straight after the heading, so the screen behind
    a speaker reads `측정 환경과 방법` and then `bullets`.

    Whole lines only, and only before any content. A deck about presentation
    software may well say `bullets` in a sentence, and reaching into prose to
    delete a word would be a worse slide than the one this fixes.
    """
    names = {name.strip().lower() for name in layouts if name and name.strip()}
    text = fragment.lstrip("\n")
    while True:
        without = _LAYOUT_DIRECTIVE.sub("", text, count=1)
        if without != text:
            text = without.lstrip("\n")
            continue
        head, newline, rest = text.partition("\n")
        # `newline` is required: a fragment that is *only* a layout name is an
        # empty block, and `lint` says so better than a silent deletion would.
        if newline and head.strip().strip("\"'`").lower() in names:
            text = rest.lstrip("\n")
            continue
        return text


def _kept_style(match: re.Match[str]) -> str:
    """A `style=` reduced to the declarations a person is allowed to set.

    Everything outside `_EDITABLE_STYLE` goes, and so does any value the narrow
    `_DECLARATION` pattern will not match — which is what keeps `url(...)`,
    `expression(...)` and a second declaration smuggled past a semicolon out of
    a document that is also opened outside the sandbox.

    An empty result drops the attribute rather than leaving `style=""` behind.
    """
    raw = match.group(1).strip().strip("\"'")
    kept = []
    for part in raw.split(";"):
        if not part.strip():
            continue
        found = _DECLARATION.match(part)
        if found and found.group(1).lower() in _EDITABLE_STYLE:
            kept.append(f"{found.group(1).lower()}: {found.group(2).strip()}")
    return f' style="{"; ".join(kept)}"' if kept else ""


#: The cover class, wherever a block claims it.
#:
#: The cover is the 서식's own frame — `wrap_cover` puts it round the title the
#: outline already decided, and the page view draws it *around* the sections
#: rather than as one of them. A body block can never legitimately carry it, and
#: a model that has just seen one written sometimes writes another: the document
#: then has two covers, one of them in the middle, and the report track stores
#: it as a section because the filter there reads the block's `layout` and not
#: its markup.
#:
#: Stripped after the tag filter so the element itself survives — losing the
#: class is losing a frame nobody asked for, and losing the `<div>` would lose
#: the words inside it.
#: Bounded on purpose. `\s*` on both sides of `=` and again round the value
#: gives the engine several ways to split one run of whitespace, and
#: `test_an_attribute_stripper_cannot_be_stalled_by_whitespace` measured 21
#: seconds on the first version of this line. One optional space either side is
#: every shape a model actually writes and is linear.
_COVER_CLASS = re.compile(r'\sclass\s?=\s?(["\'])cover\1', re.I)


def sanitise(
    fragment: str, layouts: Sequence[str] = (), *, editable_styles: bool = False
) -> str:
    """One block of authored HTML, reduced to what the seed styles.

    `sandbox=""` already stops a script from running, so this is the second
    lock rather than the only one. It exists because an artifact is also
    downloaded, opened outside the sandbox, and shared by link.

    `layouts` is the template's own vocabulary. Empty — which is every caller
    that has no template in hand — leaves the front of the fragment alone.

    `editable_styles` is the difference between the model writing this block and
    a person writing it. The model gets no inline style at all — one that
    invents its own type scale produces a document that disagrees with itself
    on every page. A person editing in the document editor keeps the narrow set
    in `_EDITABLE_STYLE`, because size, face, alignment and emphasis have no
    other way to be expressed and a silent strip means their edit disappears
    when they press save. Layout is not in that set either way.
    """
    text = _CODE.sub(_quoted_code, _without_layout_preamble(_unwrapped(fragment), layouts))
    text = _SCRIPTISH.sub("", text)
    text = _EVENT_ATTR.sub("", text)
    text = _STYLE_ATTR.sub(_kept_style if editable_styles else "", text)

    def address(match: re.Match[str]) -> str:
        """A link goes nowhere in a printed document; a picture may stay."""
        if match.group(1).lower() != "src":
            return ""
        value = match.group(2).strip().strip("\"'")
        return match.group(0) if pictures.is_embedded(value) else ""

    text = _URL_ATTR.sub(address, text)

    def keep(match: re.Match[str]) -> str:
        return match.group(0) if match.group(1).lower() in _ALLOWED_TAGS else ""

    return _COVER_CLASS.sub("", _TAG.sub(keep, text)).strip()


def _token_declarations(tokens: dict[str, str]) -> str:
    """The design system as CSS custom properties, in the seed's indentation.

    One implementation, because `render` writes the exported file and
    `stylesheet` writes what the editor draws in — and a document that looks
    one way while it is typed and another when it is downloaded is worse than
    one that is plain in both.
    """
    return "\n".join(
        f"      --{name}: {value};"
        for name, value in (
            ("accent", tokens.get("accent", "#5b5bd6")),
            ("ink", tokens.get("ink", "#1a1a1a")),
            ("muted", tokens.get("muted", "#666666")),
            (
                "font-body",
                "'Nanum Myeongjo', 'Batang', serif"
                if tokens.get("font") == "serif"
                else "'Pretendard', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif",
            ),
            # The heading face, which the document seed already asks for by
            # name — the step number and the KPI figure are both set in
            # `var(--font-head)` — and which nothing declared. `font-family` is
            # inherited, so an undeclared name there is not an error and not a
            # visible fallback either: the declaration is thrown away and the
            # element keeps the body face, which is what those two had been
            # doing all along.
            #
            # It resolves to the body face, so declaring it changes nothing on
            # its own. What it gives is a slot: a 서식 that wants its figures in
            # a different face sets one property in its `design.css` instead of
            # restating `font-family` at every site that uses one.
            ("font-head", "var(--font-body)"),
        )
    )


def render(template: DesignTemplate, *, title: str, tokens: dict[str, str], body: str) -> str:
    """The finished single file.

    `tokens` reaches the document as CSS custom properties, which is the whole
    reason the design system and this catalogue compose: the seed decides the
    layout, the design system decides what colour it is.
    """
    return (
        template.seed.replace("{{TOKENS}}", _token_declarations(tokens))
        .replace("{{TITLE}}", escape(title))
        .replace("{{BODY}}", body)
    )


_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.S | re.I)


def stylesheet(template: DesignTemplate, tokens: dict[str, str]) -> str:
    """The seed's CSS alone, with `{{TOKENS}}` resolved.

    For the document editor, which is the one consumer that cannot use
    `render`. Everything else wants the finished file and shows it in a
    `sandbox=""` frame, where a sandbox is exactly right — nothing in there is
    meant to be clicked. An editor has to be clicked, so the document lives in
    the page, inside a shadow root, and what the shadow root needs is this.

    Same substitution `render` performs, so the editor and the exported file
    are looking at one stylesheet rather than two that agree today.
    """
    seed = template.seed.replace("{{TOKENS}}", _token_declarations(tokens))
    return "\n\n".join(match.group(1) for match in _STYLE_BLOCK.finditer(seed)).strip()


def assemble(template: DesignTemplate, blocks: list[dict[str, str]]) -> str:
    """Written blocks → the seed's body.

    The wrappers come from `template.toml`, so a model that answered with a
    heading of its own or a stray `<div>` still lands inside the structure the
    seed styles. What the model contributes is the inside of one block.
    """
    cover = ""
    rest: list[str] = []
    for index, block in enumerate(blocks, start=1):
        body = block.get("html") or ""
        # A body block that came back empty is left out — the document shows a
        # gap where a section failed, which is what the reader can act on. The
        # cover is not a gap: its wrapper carries the title the outline already
        # decided, so an empty one still makes the title page it promised, and
        # dropping it would leave a deck whose first slide is a body slide.
        if not body and block.get("layout") != "cover":
            continue
        markup = (template.wrap_cover if block.get("layout") == "cover" else template.wrap_block)
        markup = (
            markup.replace("{title}", escape(block.get("title") or ""))
            .replace("{layout}", block.get("layout") or "")
            .replace("{n}", str(index))
            .replace("{body}", body)
        )
        if block.get("layout") == "cover" and not cover:
            cover = markup
        else:
            rest.append(markup)
    grouped = (
        template.wrap_group.replace("{blocks}", "\n".join(rest))
        if template.wrap_group and rest
        else "\n".join(rest)
    )
    return "\n".join(part for part in (cover, grouped) if part)


def figure(*, mime: str, data_b64: str, alt: str = "", caption: str = "") -> str:
    """A picture, already encoded, as a block of markup this seed styles.

    Built here rather than in the router because it is the same vocabulary
    question as everything else in this file: `<figure>`, `<img>` and
    `<figcaption>` are what the seeds have rules for, and the `data:` URI is
    the only address `sanitise` lets through.
    """
    body = f'<figure><img src="{pictures.data_uri(mime, data_b64)}" alt="{escape(alt)}" />'
    if caption:
        body += f"<figcaption>{escape(caption)}</figcaption>"
    return body + "</figure>"


#: A `<figure>` that carries an embedded picture rather than a described one.
#: Non-greedy, and anchored on the whole element, because what is being kept is
#: the picture *and* its caption.
_PICTURE = re.compile(
    r"<figure\b[^>]*>(?:(?!</figure>).)*?src=\"data:image/.*?</figure>", re.S | re.I
)


def pictures_in(fragment: str) -> str:
    """The embedded pictures of a block, as markup, in the order they appear.

    A rewrite replaces a block with what the model wrote, and the model cannot
    write a picture — so without this, asking for better wording on a block
    silently deletes the illustration somebody put there. A figure the model
    wrote *in words* is not kept: that one it can write again, and keeping it
    would leave the document saying the same thing twice.
    """
    return "".join(_PICTURE.findall(fragment))


def escape(text: str) -> str:
    """Text into markup. Public because the router builds a `<figcaption>`."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


__all__ = [
    "form_text",
    "HTML_KINDS",
    "SURFACE",
    "Argument",
    "DesignTemplate",
    "all_templates",
    "assemble",
    "default_for",
    "escape",
    "figure",
    "pictures_in",
    "for_surface",
    "get",
    "render",
    "sanitise",
    "stylesheet",
]
