"""Design template (서식) catalogue: the seed HTML, rules and files a document or deck is written
into.

Each entry is a folder under `app/design_templates/<id>/`:

    template.toml     metadata
    instructions.md   appended to the surface prompt
    checklist.md      critique rubric
    markup.md         the seed's element vocabulary (own or via `seed_from`)
    seed.html         shell with `{{TITLE}}` / `{{TOKENS}}` / `{{BODY}}`
    design.css        this 서식's own stylesheet, spliced into the seed
    sample.html       body fragment the gallery previews
    template.docx / template.pptx / form.docx / form.pptx   optional

Seeds carry no script (artifacts render in a `sandbox=""` iframe) and carry
`@media print` rules, since PDF export is the reader's print dialogue.
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
from app.services import design, pictures

_ROOT = Path(__file__).resolve().parent.parent / "design_templates"

#: Surface each template kind is offered on.
SURFACE: dict[str, SessionKind] = {
    "deck": SessionKind.slides,
    "document": SessionKind.report,
    "image": SessionKind.image,
    "video": SessionKind.av,
    "audio": SessionKind.av,
}

#: Kinds that produce an HTML artifact; `image` templates only shape a prompt.
HTML_KINDS = ("deck", "document")

#: Tags a block may contain; others are unwrapped. No `h1`/`h2`: the wrapper
#: writes the block heading. No `<pre>`: the file exporters read markdown lines.
_ALLOWED_TAGS = {
    "p",
    "h3",
    "h4",
    "ul",
    "ol",
    "li",
    "strong",
    "em",
    "blockquote",
    "code",
    "figure",
    "figcaption",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "div",
    "span",
    "section",
    "br",
    "hr",
    "small",
    "dl",
    "dt",
    "dd",
    "sup",  # footnote reference; `<small>` carries the note
}

_TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")
#: Removed with their contents. `h1`/`h2` in a body would duplicate the
#: wrapper's heading.
_SCRIPTISH = re.compile(
    r"<(script|style|iframe|object|embed|link|meta|h1|h2)\b.*?(</\1\s*>|$)", re.S | re.I
)
#: Single leading `\s` and possessive quantifiers: `\s+` is quadratic on
#: whitespace-padded input, and these fragments are model-generated.
_EVENT_ATTR = re.compile(r"\son[a-z]++\s*+=\s*+(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
#: `class` survives (it reaches the seed's own names); `style` is filtered.
_STYLE_ATTR = re.compile(r"\sstyle\s*+=\s*+(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)

#: Declarations a person may set in the editor. No layout properties: the
#: seed owns position, display, spacing and background.
_EDITABLE_STYLE = {
    "font-size",
    "font-family",
    "font-weight",
    "font-style",
    "text-align",
    "text-decoration",
    "color",
    "background-color",
    "line-height",
}
#: Property/value pair with no parentheses in the value, so `url(`,
#: `expression(` and `calc(` never match. Security invariant.
_DECLARATION = re.compile(r"^\s*([a-z-]{2,20})\s*:\s*([A-Za-z0-9 ,.%#'\"_-]{1,120})\s*$")
_URL_ATTR = re.compile(r"\s(href|src)\s*+=\s*+(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
#: `<code>` contents are text, escaped before the tag rules run. Closing tag
#: required.
_CODE = re.compile(r"(<code\b[^>]*>)(.*?)</code\s*>", re.S | re.I)
#: An ampersand not already opening an entity.
_BARE_AMP = re.compile(r"&(?!#?\w{1,32};)")


@dataclass(frozen=True, slots=True)
class Argument:
    """One blank in a media template's prompt, filled in the composer."""

    name: str
    label: str
    label_en: str
    default: str
    default_en: str
    #: A closed list renders as a picker; empty renders as a text field.
    options: tuple[str, ...]
    options_en: tuple[str, ...]
    #: Multi-line field.
    long: bool = False


@dataclass(frozen=True, slots=True)
class DesignTemplate:
    id: str
    kind: str
    name: str
    description: str
    #: Gallery filter chip.
    category: str
    #: English halves; empty falls back to the Korean. The client picks by language.
    name_en: str
    description_en: str
    category_en: str
    fills_en: tuple[str, ...]
    example_prompt_en: str
    #: What the person has to bring, shown on the card.
    fills: tuple[str, ...]
    #: Starting sentence the card's button puts in the composer.
    example_prompt: str
    #: Appended to the generation prompt; never shown to the reader.
    instructions: str
    #: The seed's element vocabulary (`markup.md`); the 서식's own instructions win.
    markup: str
    #: Critique rubric, kept apart from `instructions`.
    checklist: str
    #: Slides on a dark ground; carried into the `.pptx`, not the `.pdf`.
    dark: bool
    #: `deck` only: one of `design.VISUAL_STYLES`. Empty for documents.
    look: str
    #: Blanks in `example_prompt`, written `{name}`. Media templates only.
    arguments: tuple[Argument, ...]
    #: Composer settings this template implies (aspect, duration, voice).
    defaults: dict[str, Any]
    #: `image` only: English clause folded into the picture prompt.
    prompt_suffix: str
    #: `image` only: `method` / `flow` / `concept` sends the request to the
    #: mermaid diagram path instead of the image model.
    figure: str
    #: Path to `template.docx`, which `report_export` opens and appends to; empty for Word defaults.
    docx_template: str
    #: Path to `template.pptx`, which `deck_export` builds on; empty for PowerPoint defaults.
    pptx_template: str
    #: Whether the cover owns a sheet in paged exports.
    cover_page: bool
    #: Path to the blank `form.docx` / `form.pptx` for download; empty when none.
    form_file: str

    @property
    def wordless(self) -> bool:
        """An image 서식 whose suffix forbids lettering: the planner must not write any
        text into the picture either, or the two halves of the prompt contradict."""
        return "no text" in self.prompt_suffix.lower()
    #: The shell every slide or section is placed into.
    seed: str
    #: Layout names this template describes; the first is the cover.
    layouts: tuple[str, ...]
    #: Block → markup wrappers from `template.toml`.
    wrap_cover: str
    wrap_block: str
    #: Optional wrapper around every non-cover block, `{blocks}` inside.
    wrap_group: str
    #: `max_bullets` / `max_bullet_chars` the checker enforces; empty means the general bounds.
    limits: dict[str, int]
    #: Body fragment the gallery card renders inside the seed.
    sample: str
    #: Catalogue skills applied automatically in this 서식, by `catalog_key`.
    skills: tuple[str, ...]

    @property
    def surface(self) -> SessionKind:
        return SURFACE[self.kind]

    @property
    def checks(self) -> tuple[str, ...]:
        """The checklist's bullet lines without their markers, for the gallery card."""
        return tuple(
            line.lstrip("-").strip()
            for line in self.checklist.splitlines()
            if line.lstrip().startswith("-")
        )


#: Extracted form text, keyed by path and mtime.
_FORM_TEXT: dict[tuple[str, float], str] = {}


def form_text(template: DesignTemplate) -> str:
    """The blank form's text (headings, column names), for the model to write into."""
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
        except Exception:  # noqa: BLE001 — an unreadable form is written without
            _FORM_TEXT[key] = ""
    return _FORM_TEXT[key]


#: The 서식's own stylesheet is spliced in last in `<head>`, so it wins ties.
_HEAD_END = re.compile(r"</head\s*>", re.I)


def _seed(folder: Path, meta: dict) -> str:
    """The seed HTML (own `seed.html`, or the shared one named by `seed_from`) with `design.css`
    spliced in.
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
    # A second `<style>` block; `stylesheet()` concatenates all of them in order.
    block = f"<style>\n/* {folder.name} */\n{face}\n</style>\n"
    return _HEAD_END.sub(lambda _m: block + "</head>", seed, count=1)


def _look(meta: dict[str, Any], kind: str) -> str:
    """Declared `look` for a deck template, else `"editorial"`; empty for other kinds."""
    if kind != "deck":
        return ""
    look = str(meta.get("look") or "").strip()
    if look and look not in design.VISUAL_STYLES:
        raise ValueError(f"unknown look {look!r}; one of {', '.join(design.VISUAL_STYLES)}")
    return look or "editorial"


def _seed_markup(folder: Path, meta: dict[str, Any]) -> str:
    """`markup.md` from the folder, else from the `seed_from` folder."""
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
    # `_` folders hold shared seeds, not templates.
    for folder in sorted(p for p in _ROOT.iterdir() if p.is_dir() and not p.name.startswith("_")):
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
            figure=str(meta.get("figure") or ""),
            dark=bool(meta.get("dark")),
            look=_look(meta, kind),
            arguments=tuple(
                Argument(
                    name=str(arg.get("name") or ""),
                    label=str(arg.get("label") or ""),
                    label_en=str(arg.get("label_en") or ""),
                    default=str(arg.get("default") or ""),
                    default_en=str(arg.get("default_en") or ""),
                    options=tuple(str(o) for o in (arg.get("options") or [])),
                    options_en=tuple(str(o) for o in (arg.get("options_en") or [])),
                    long=bool(arg.get("long")),
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
            cover_page=bool(meta.get("cover_page", True)),
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
            sample=_read(folder, "sample.html"),
        )
    return found


#: Read once; the catalogue ships inside the image.
_TEMPLATES = _load()


def all_templates() -> list[DesignTemplate]:
    return list(_TEMPLATES.values())


def for_surface(kind: SessionKind) -> list[DesignTemplate]:
    return [t for t in _TEMPLATES.values() if t.surface is kind]


def get(template_id: str | None) -> DesignTemplate | None:
    return _TEMPLATES.get(template_id or "")


def default_for(defaults: Any, kind: SessionKind) -> str | None:
    """The project's default template id for this surface, or None when unset or no longer in the
    catalogue.
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


#: A leading `layout: …` line, anchored at the start of the fragment only.
_LAYOUT_DIRECTIVE = re.compile(r"^[ \t]*layout[ \t]*[:=][^\n]*\n", re.I)


#: JSON envelope keys that describe the block rather than fill it.
_ENVELOPE_META = frozenset({"layout", "title", "heading", "n", "index", "id"})


def _unwrapped(fragment: str) -> str:
    """The single string payload of a JSON-envelope answer; anything else is returned as is."""
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
    """Strips leading whole lines that only name a layout (`layout: bullets`, `bullets`)."""
    names = {name.strip().lower() for name in layouts if name and name.strip()}
    text = fragment.lstrip("\n")
    while True:
        without = _LAYOUT_DIRECTIVE.sub("", text, count=1)
        if without != text:
            text = without.lstrip("\n")
            continue
        head, newline, rest = text.partition("\n")
        # A fragment that is only a layout name is left for `lint` to report.
        if newline and head.strip().strip("\"'`").lower() in names:
            text = rest.lstrip("\n")
            continue
        return text


def _kept_style(match: re.Match[str]) -> str:
    """A `style=` reduced to `_EDITABLE_STYLE` declarations matching `_DECLARATION`; dropped when
    empty.
    """
    raw = match.group(1).strip().strip("\"'")
    kept = []
    for part in raw.split(";"):
        if not part.strip():
            continue
        # Browsers serialise picked colours as `rgb(r, g, b)`; canonicalise to hex.
        colour = re.fullmatch(
            r"\s*(color|background-color)\s*:\s*rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)\s*",
            part,
            re.I,
        )
        if colour:
            channels = [int(colour.group(index)) for index in (2, 3, 4)]
            if any(channel > 255 for channel in channels):
                continue
            hexed = "".join(f"{channel:02x}" for channel in channels)
            part = f"{colour.group(1).lower()}: #{hexed}"
        found = _DECLARATION.match(part)
        if found and found.group(1).lower() in _EDITABLE_STYLE:
            kept.append(f"{found.group(1).lower()}: {found.group(2).strip()}")
    return f' style="{"; ".join(kept)}"' if kept else ""


#: `class="cover"` on a body block; only `wrap_cover` may produce the cover
#: frame. `\s?` rather than `\s*`: the latter is quadratic on whitespace.
_COVER_CLASS = re.compile(r'\sclass\s?=\s?(["\'])cover\1', re.I)


def sanitise(fragment: str, layouts: Sequence[str] = (), *, editable_styles: bool = False) -> str:
    """One block of authored HTML reduced to what the seed styles; artifacts are also opened outside
    the sandbox.

    `layouts`: the template's layout names to strip from the front.
    `editable_styles`: keep `_EDITABLE_STYLE` declarations (person-edited
    blocks); model-written blocks get no inline style.
    """
    text = _CODE.sub(_quoted_code, _without_layout_preamble(_unwrapped(fragment), layouts))
    text = _SCRIPTISH.sub("", text)
    text = _EVENT_ATTR.sub("", text)
    text = _STYLE_ATTR.sub(_kept_style if editable_styles else "", text)

    def address(match: re.Match[str]) -> str:
        """`href` is dropped; `src` survives only as an embedded `data:` image."""
        if match.group(1).lower() != "src":
            return ""
        value = match.group(2).strip().strip("\"'")
        return match.group(0) if pictures.is_embedded(value) else ""

    text = _URL_ATTR.sub(address, text)

    def keep(match: re.Match[str]) -> str:
        return match.group(0) if match.group(1).lower() in _ALLOWED_TAGS else ""

    return _COVER_CLASS.sub("", _TAG.sub(keep, text)).strip()


def _token_declarations(tokens: dict[str, str]) -> str:
    """Design tokens as CSS custom properties, shared by `render` and `stylesheet`."""
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
            # Slots a 서식's `design.css` may override; default to the body face.
            ("font-head", "var(--font-body)"),
            ("font-num", "var(--font-head)"),
        )
    )


def render(template: DesignTemplate, *, title: str, tokens: dict[str, str], body: str) -> str:
    """The finished single file: seed with `{{TOKENS}}`, `{{TITLE}}` and `{{BODY}}` filled."""
    return (
        template.seed.replace("{{TOKENS}}", _token_declarations(tokens))
        .replace("{{TITLE}}", escape(title))
        .replace("{{BODY}}", body)
    )


_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.S | re.I)


def stylesheet(template: DesignTemplate, tokens: dict[str, str]) -> str:
    """The seed's `<style>` blocks alone with `{{TOKENS}}` resolved, for the editor's shadow root.
    """
    seed = template.seed.replace("{{TOKENS}}", _token_declarations(tokens))
    return "\n\n".join(match.group(1) for match in _STYLE_BLOCK.finditer(seed)).strip()


def assemble(template: DesignTemplate, blocks: list[dict[str, str]]) -> str:
    """Written blocks wrapped by `wrap_cover` / `wrap_block` / `wrap_group` into the seed's body."""
    cover = ""
    rest: list[str] = []
    for index, block in enumerate(blocks, start=1):
        body = block.get("html") or ""
        # An empty body block is left out; an empty cover still makes its title page.
        if not body and block.get("layout") != "cover":
            continue
        markup = template.wrap_cover if block.get("layout") == "cover" else template.wrap_block
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
    """`<figure><img src="data:…"><figcaption>` markup for an encoded picture."""
    body = f'<figure><img src="{pictures.data_uri(mime, data_b64)}" alt="{escape(alt)}" />'
    if caption:
        body += f"<figcaption>{escape(caption)}</figcaption>"
    return body + "</figure>"


#: A whole `<figure>` element carrying an embedded `data:image/` picture.
_PICTURE = re.compile(
    r"<figure\b[^>]*>(?:(?!</figure>).)*?src=\"data:image/.*?</figure>", re.S | re.I
)


def pictures_in(fragment: str) -> str:
    """The embedded `<figure>` elements of a block, in order; kept across a model rewrite."""
    return "".join(_PICTURE.findall(fragment))


def escape(text: str) -> str:
    """HTML-escapes text for markup."""
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
