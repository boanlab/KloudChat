"""The rendering catalogue: shapes the model writes into, rather than prompts.

A **prompt template** (`models.workspace.Template`) is a sentence someone
starts from. A **design template** is the other half of that idea: the shape
the answer comes out in. It carries a seed document with the CSS already
decided and a small vocabulary of blocks the model is allowed to fill, so the
model writes content and never layout.

Each entry is a folder under `app/design_templates/<id>/`:

    template.toml     metadata — read with `tomllib`, no new dependency
    instructions.md   what the model is told, appended to the surface prompt
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

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models.chat import SessionKind

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
#: the layout, and a model that invents a `<div class="grid-4">` gets a slide
#: with no styling rather than a broken one.
#: `h2` is deliberately absent: the wrapper writes the block's heading from
#: the outline, and a model that repeats it inside the body renders the title
#: twice. Sub-headings inside a block use `h3`.
_ALLOWED_TAGS = {
    "p", "h3", "ul", "ol", "li", "strong", "em", "blockquote",
    "figure", "figcaption", "table", "thead", "tbody", "tr", "th", "td",
    "div", "span", "section", "br", "hr", "small", "dl", "dt", "dd",
}

_TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")
#: Removed with their contents rather than unwrapped.
#:
#: The first group is inert-by-removal: script and friends have no place in a
#: document assembled from model output. `h1` and `h2` are here for a different
#: reason — the wrapper writes the block's heading from the outline, so one in
#: the body is a title printed twice. Unwrapping it would leave the duplicate
#: words behind, which is the visible half of the problem.
_SCRIPTISH = re.compile(
    r"<(script|style|iframe|object|embed|link|meta|h1|h2)\b.*?(</\1\s*>|$)", re.S | re.I
)
_EVENT_ATTR = re.compile(r"\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_URL_ATTR = re.compile(r"\s+(href|src)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)


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
    #: The shell every slide or section is placed into.
    seed: str
    #: Body fragment the gallery previews, rendered inside the same seed.
    sample: str
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

    @property
    def surface(self) -> SessionKind:
        return SURFACE[self.kind]


def _read(folder: Path, name: str) -> str:
    path = folder / name
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _load() -> dict[str, DesignTemplate]:
    found: dict[str, DesignTemplate] = {}
    if not _ROOT.is_dir():
        return found
    for folder in sorted(p for p in _ROOT.iterdir() if p.is_dir()):
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
            seed=_read(folder, "seed.html"),
            sample=_read(folder, "sample.html"),
            layouts=tuple(str(x) for x in (meta.get("layouts") or ["cover", "section"])),
            wrap_cover=str(wrap.get("cover") or "{body}"),
            wrap_block=str(wrap.get("block") or "{body}"),
            wrap_group=str(wrap.get("group") or ""),
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


def sanitise(fragment: str) -> str:
    """One block of model-written HTML, reduced to what the seed styles.

    `sandbox=""` already stops a script from running, so this is the second
    lock rather than the only one. It exists because an artifact is also
    downloaded, opened outside the sandbox, and shared by link.
    """
    text = _SCRIPTISH.sub("", fragment)
    text = _EVENT_ATTR.sub("", text)
    # Links and images would fetch from wherever the model invented; the seed
    # has no room for either and a dead reference reads as a broken document.
    text = _URL_ATTR.sub("", text)

    def keep(match: re.Match[str]) -> str:
        return match.group(0) if match.group(1).lower() in _ALLOWED_TAGS else ""

    return _TAG.sub(keep, text).strip()


def render(template: DesignTemplate, *, title: str, tokens: dict[str, str], body: str) -> str:
    """The finished single file.

    `tokens` reaches the document as CSS custom properties, which is the whole
    reason the design system and this catalogue compose: the seed decides the
    layout, the design system decides what colour it is.
    """
    declarations = "\n".join(
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
        )
    )
    return (
        template.seed.replace("{{TOKENS}}", declarations)
        .replace("{{TITLE}}", _escape(title))
        .replace("{{BODY}}", body)
    )


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
        if not body:
            continue
        markup = (template.wrap_cover if block.get("layout") == "cover" else template.wrap_block)
        markup = (
            markup.replace("{title}", _escape(block.get("title") or ""))
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


def preview(template: DesignTemplate) -> str:
    """What the gallery card shows: this template's own shape, filled in."""
    from app.services.design import DEFAULT_TOKENS

    return render(template, title=template.name, tokens=DEFAULT_TOKENS, body=template.sample)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


__all__ = [
    "HTML_KINDS",
    "SURFACE",
    "Argument",
    "DesignTemplate",
    "all_templates",
    "assemble",
    "for_surface",
    "get",
    "preview",
    "render",
    "sanitise",
]
