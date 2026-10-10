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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import nh3

from app.models.chat import SessionKind
from app.services import (
    deck_preview,
    design,
    doc_formats,
    doc_type,
    format_css,
    pictures,
    table_figures,
)

_ROOT = Path(__file__).resolve().parent.parent / "design_templates"
#: A person's template id (`UserTemplate.id` behind it), so a session names it like a shipped one.
USER_PREFIX = "ut-"

#: Surface each template kind is offered on.
SURFACE: dict[str, SessionKind] = {
    "deck": SessionKind.slides,
    "document": SessionKind.report,
}

#: Kinds that produce an HTML artifact: every kind the catalogue still has.
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

#: Removed with their contents. `h1`/`h2` in a body would duplicate the
#: wrapper's heading.
_REMOVED_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "link", "meta", "h1", "h2"}
#: Attributes a block may carry, by tag. `class` reaches the seed's own names;
#: `style` is filtered per declaration (person-edited blocks only) and `src`
#: survives only as an embedded `data:` picture — both in `_attribute`.
_ALLOWED_ATTRIBUTES = {
    "*": {"class", "style"},
    "img": {"src", "alt"},
    # A chart's numbers or a diagram's source; the file exporters read it.
    "figure": {"data-source"},
    # A page break a person inserted; `richtext` turns it into the exporters' break.
    "div": {"data-page-break"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

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
#: `<code>` contents are text, escaped before the tag rules run. Closing tag
#: required.
_CODE = re.compile(r"(<code\b[^>]*>)(.*?)</code\s*>", re.S | re.I)
#: An ampersand not already opening an entity.
_BARE_AMP = re.compile(r"&(?!#?\w{1,32};)")


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
    #: Path to `template.docx`, which `report_export` opens and appends to; empty for Word defaults.
    docx_template: str
    #: Path to `template.pptx`, which `deck_export` builds on; empty for PowerPoint defaults.
    pptx_template: str
    #: Whether the cover owns a sheet in paged exports.
    cover_page: bool
    #: Path to the blank `form.docx` / `form.pptx` for download; empty when none.
    form_file: str

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
    #: `skills = []` written out: the 서식 brings no skills on purpose (its rules live in its
    #: instructions and the agent's prompt), not by omission.
    no_skills: bool = False
    #: Check skills (`stage` in their SKILL.md) the server runs around writing in this 서식,
    #: by catalogue key; `check_skills` because `checks` is the checklist below.
    check_skills: tuple[str, ...] = ()
    #: `body_face = "design"`: the body face follows the design's `font` token in every
    #: export (its `design.css` reads `--font-body`); otherwise the 서식 fixes its faces.
    body_face_from_design: bool = False
    #: The document format it fixes (`doc_format` in `template.toml`), "" for none.
    doc_format: str = ""
    #: The writer's genre rule for that format (`genre.md`), given to the outline and the
    #: draft; "" for none.
    genre: str = ""
    #: 빠진 정보 질문 its format needs (`[[questions]]`, `services/gap_questions.py`), checked
    #: the way a person's agent's are.
    questions: tuple[dict, ...] = ()
    #: 진행 과정 설정 its format runs with (`[process]`, `services/process_settings.py`).
    process: dict[str, Any] = field(default_factory=dict)
    #: How strict the trace is in this 서식 (`check_skills.CHECK_OPTIONS` names).
    check_options: tuple[str, ...] = ()
    #: A person's template (`UserTemplate`): its row id, owner and sharing; "" for shipped.
    owner_id: str = ""
    visibility: str = ""
    #: A person's template's own document format (`doc_formats.DocFormat`); None for shipped.
    doc_format_def: Any = None
    #: A person's template's form, as text for the writer to read (never an export base).
    form_reference: str = ""
    #: Shown in the pickers (`listed = false` in `template.toml` hides it there): an unlisted
    #: 서식 is still chosen by id, drawn and exported — old conversations and agents keep it.
    listed: bool = True

    @property
    def user(self) -> bool:
        return self.id.startswith(USER_PREFIX)

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
    if template.form_reference:
        return template.form_reference
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
    # A deck seed reads its sizes from the deck's one type scale.
    seed = seed.replace("{{DECK_TYPE}}", deck_preview.type_declarations())
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


def _template(folder: Path, meta: dict[str, Any]) -> DesignTemplate:
    """One 서식 from its folder and its `template.toml`."""
    kind = str(meta.get("kind") or "")
    wrap: dict[str, Any] = meta.get("wrap") or {}
    return DesignTemplate(
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
        dark=bool(meta.get("dark")),
        look=_look(meta, kind),
        docx_template=str(folder / "template.docx") if (folder / "template.docx").is_file() else "",
        pptx_template=str(folder / "template.pptx") if (folder / "template.pptx").is_file() else "",
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
        no_skills=meta.get("skills") == [],
        check_skills=tuple(str(k) for k in (meta.get("check_skills") or [])),
        body_face_from_design=meta.get("body_face") == "design",
        sample=_read(folder, "sample.html"),
        doc_format=str(meta.get("doc_format") or ""),
        genre=_read(folder, "genre.md").strip(),
        questions=_questions(folder, meta),
        process=_process(folder, meta),
        check_options=_check_options(folder, meta),
        listed=meta.get("listed", True) is not False,
    )


def _process(folder: Path, meta: dict[str, Any]) -> dict[str, Any]:
    from app.services import process_settings

    try:
        return process_settings.clean(meta.get("process") or {})
    except process_settings.ProcessError as exc:
        raise ValueError(f"{folder.name}: {exc}") from exc


def _check_options(folder: Path, meta: dict[str, Any]) -> tuple[str, ...]:
    from app.services import check_skills

    names = tuple(str(k) for k in meta.get("check_options") or [])
    if unknown := [n for n in names if n not in check_skills.CHECK_OPTIONS]:
        raise ValueError(f"{folder.name}: unknown check option {unknown[0]!r}")
    return names


def _questions(folder: Path, meta: dict[str, Any]) -> tuple[dict, ...]:
    from app.services import gap_questions

    try:
        return tuple(gap_questions.clean(meta.get("questions") or []))
    except gap_questions.QuestionError as exc:
        raise ValueError(f"{folder.name}: {exc}") from exc


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
        if str(meta.get("kind") or "") not in SURFACE:
            continue
        found[folder.name] = _template(folder, meta)
    return found


#: Read once; the catalogue ships inside the image.
_TEMPLATES = _load()


def all_templates() -> list[DesignTemplate]:
    return list(_TEMPLATES.values())


def for_format(format_id: str | None) -> DesignTemplate | None:
    """The shipped 서식 bound to a format: its settings and questions are the format's."""
    if not format_id:
        return None
    return next((t for t in _TEMPLATES.values() if t.doc_format == format_id), None)


def format_genres() -> dict[str, str]:
    """Each bound format's genre rule, as its 서식 carries it (`genre.md`)."""
    return {t.doc_format: t.genre for t in _TEMPLATES.values() if t.doc_format and t.genre}


def for_surface(kind: SessionKind) -> list[DesignTemplate]:
    return [t for t in _TEMPLATES.values() if t.surface is kind]


#: People's templates this process has loaded (`services/user_templates.ensure`), by id.
_USER: dict[str, DesignTemplate] = {}


def get(template_id: str | None) -> DesignTemplate | None:
    key = template_id or ""
    return _USER.get(key) if key.startswith(USER_PREFIX) else _TEMPLATES.get(key)


def remember(template: DesignTemplate) -> None:
    """Hold a person's template (built by `user_templates.build`) for `get`."""
    if template.id.startswith(USER_PREFIX):
        _USER[template.id] = template


def forget(template_id: str) -> None:
    _USER.pop(template_id, None)


#: The shared seed as a 서식 of its own, outside the catalogue.
GENERIC_ID = "_document"

#: Document 서식 taken out of the catalogue. Documents and sessions may still name them;
#: `rendering` draws those in the shared seed, and nothing new can choose them.
RETIRED = frozenset(
    {
        "doc-brief",
        "doc-case",
        "doc-incident",
        "doc-lab",
        "doc-notice",
        "doc-project-brief",
        "doc-proposal",
        "doc-report",
        "doc-survey",
        "doc-term-paper",
    }
)

#: Image, audio and video 서식, all taken out. Nothing ever drew a stored document in one:
#: an image 서식 only shaped the prompt of the turn that picked it, an audio or video one only
#: set the composer's chips. A request or a saved choice that still names one is read as
#: naming none (`is_retired_media`).
RETIRED_MEDIA = frozenset(
    {
        "audio-bed",
        "audio-narration",
        "image-architecture",
        "image-banner",
        "image-cover",
        "image-diagram",
        "image-infographic",
        "image-mascot",
        "image-method",
        "image-mockup",
        "image-persona",
        "image-pipeline",
        "image-poster",
        "image-poster-bg",
        "image-scene",
        "image-teaser",
        "video-opening",
        "video-product",
    }
)


#: Document 서식 taken out of the catalogue but kept in the image: nothing new chooses them,
#: and the documents and conversations that name one are still drawn and exported in it.
#: Retired with their agents (자기소개서·경력기술서, 고객 맞춤 제안서); migration 0060 clears
#: the saved choices.
RETIRED_KEPT = frozenset({"doc-career-history", "doc-client-proposal"})


def offered() -> list[DesignTemplate]:
    """The 서식 a person may choose: the catalogue without the retired ones it still draws.
    Unlisted ones are in it (chosen by id); the pickers leave them out (`listed`)."""
    return [t for t in _TEMPLATES.values() if t.id not in RETIRED_KEPT]


def listed() -> list[DesignTemplate]:
    """The 서식 the pickers show: `offered` without the unlisted ones."""
    return [t for t in offered() if t.listed]


def choosable(template_id: str | None) -> DesignTemplate | None:
    """The 서식 when a person may pick it now; None for an unknown or retired id."""
    return None if (template_id or "") in RETIRED_KEPT else get(template_id)


def is_retired_media(template_id: str | None) -> bool:
    """Whether the id names a retired image, audio or video 서식."""
    return (template_id or "") in RETIRED_MEDIA


_GENERIC = _template(
    _ROOT / GENERIC_ID,
    {
        "kind": "document",
        "name": "기본 템플릿",
        "name_en": "Standard",
        "category": "업무",
        "category_en": "Work",
        "layouts": ["cover", "section"],
        "wrap": {
            # The shared seed's cover owns its page (`seed.html` cover page).
            "cover": '<div class="cover cover-page"><h1>{title}</h1>{body}</div>',
            "block": "<section><h2>{title}</h2>{body}</section>",
        },
    },
)


def generic_with(meta: dict[str, Any]) -> DesignTemplate:
    """A 서식 on the shared document seed with `meta` as its `template.toml` (a person's)."""
    return _template(_ROOT / GENERIC_ID, meta)


def rendering(template_id: str | None) -> DesignTemplate | None:
    """The 서식 a stored document is drawn and exported in: its own, or the shared seed when
    it names a retired one (or the seed itself). None for any other unknown id."""
    if found := get(template_id):
        return found
    if template_id in RETIRED or template_id == GENERIC_ID:
        return _GENERIC
    if (template_id or "").startswith(USER_PREFIX):
        # A person's template not loaded here (or deleted): it is drawn on this seed anyway.
        return _GENERIC
    return None


def default_for(defaults: Any, kind: SessionKind) -> str | None:
    """The project's default template id for this surface, or None when unset or no longer in the
    catalogue.
    """
    if not isinstance(defaults, dict):
        return None
    chosen = choosable(defaults.get(kind.value))
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


def _editable_style(raw: str) -> str:
    """A `style=` value reduced to `_EDITABLE_STYLE` declarations matching `_DECLARATION`; empty
    when nothing qualifies.
    """
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
    return "; ".join(kept)


def sanitise(fragment: str, layouts: Sequence[str] = (), *, editable_styles: bool = False) -> str:
    """One block of authored HTML reduced to what the seed styles; artifacts are also opened outside
    the sandbox.

    Parsed as a browser would (nh3) and written back from the tree, so unknown attributes
    or unclosed quotes cannot slip past the allow lists. Tags outside `_ALLOWED_TAGS` keep
    their words only; `_REMOVED_WITH_CONTENT` go whole.

    `layouts`: the template's layout names to strip from the front.
    `editable_styles`: keep `_EDITABLE_STYLE` declarations (person-edited
    blocks); model-written blocks get no inline style.
    """
    text = _CODE.sub(_quoted_code, _without_layout_preamble(_unwrapped(fragment), layouts))

    def attribute(element: str, name: str, value: str) -> str | None:
        if name == "style":
            return (_editable_style(value) if editable_styles else "") or None
        if name == "src":
            # `href` is not admitted at all; `src` only as a picture already in the file.
            return value if pictures.is_embedded(value) else None
        if name == "class" and value.strip().lower() == "cover":
            # Only `wrap_cover` may produce the cover frame.
            return None
        return value

    return nh3.clean(
        text,
        tags=_ALLOWED_TAGS,
        clean_content_tags=_REMOVED_WITH_CONTENT,
        attributes=_ALLOWED_ATTRIBUTES,
        attribute_filter=attribute,
        url_schemes={"data"},
        strip_comments=True,
    ).strip()


def _token_declarations(tokens: dict[str, str]) -> str:
    """Design tokens as CSS custom properties, shared by `render` and `stylesheet`.

    Includes `doc_type.CSS_VARIABLES`, the type scale the exporters also read.

    """
    return "\n".join(
        f"      --{name}: {value};"
        for name, value in (
            *doc_type.CSS_VARIABLES,
            ("accent", tokens.get("accent", "#5b5bd6")),
            ("ink", tokens.get("ink", "#1a1a1a")),
            ("muted", tokens.get("muted", "#666666")),
            (
                "font-body",
                # Linux fontconfig names the face `NanumMyeongjo`, Windows and macOS
                # `Nanum Myeongjo`; with only the second the print sidecar fell back.
                "'Nanum Myeongjo', 'NanumMyeongjo', 'Batang', serif"
                if tokens.get("font") == "serif"
                else "'Pretendard', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif",
            ),
            # Slots a 서식's `design.css` may override; default to the body face.
            ("font-head", "var(--font-body)"),
            ("font-num", "var(--font-head)"),
        )
    )


def render(template: DesignTemplate, *, title: str, tokens: dict[str, str], body: str) -> str:
    """The finished single file: seed with `{{TOKENS}}`, `{{TITLE}}` and `{{BODY}}` filled.

    Table cells in a column of figures are marked `.fig` (and a total row `.total`), so a
    seed sets figures flush right and leaves a column of words left."""
    return (
        template.seed.replace("{{TOKENS}}", _token_declarations(tokens))
        .replace("{{TITLE}}", escape(title))
        .replace("{{BODY}}", table_figures.mark_html(body))
    )


def preview(template: DesignTemplate, tokens: dict[str, str]) -> str:
    """The gallery card's page: the seed around `sample.html`, drawn as a finished document.

    A 서식 bound to a document format (`doc_format` in its `template.toml`) gets that format's
    rules as the web draws them — the head block, heading numbers, 개조식 — so the sample's
    `header.tb` reads as it will in a real document."""
    if template.kind == "deck":
        page = render(template, title=template.name, tokens=tokens, body=template.sample)
        # Each slide at the body step the deck would settle on.
        style = deck_preview.deck_style(sample_slides(template), template.seed)
        return _HEAD_END.sub(lambda _m: style + "</head>", page, count=1)
    if template.kind != "document":
        return render(template, title=template.name, tokens=tokens, body=template.sample)
    fmt = doc_formats.BY_ID.get(template.doc_format)
    if fmt is None:
        # No format: a title and the sections, its blanks still marked as the page view does.
        css = f"{format_css.PLACEHOLDER_CSS}\n{format_css.PAGED_CSS}"
        body = format_css.mark_placeholders(template.sample)
    else:
        css, body = format_css.preview(fmt, template.sample)
    page = render(template, title=template.name, tokens=tokens, body=body)
    return page.replace("</head>", f"<style>\n{css}\n</style>\n</head>", 1)


#: One slide of a deck sample; slides never nest, so the first `</section>` closes it.
_SLIDE = re.compile(r"<section\b[^>]*\bclass=\"slide\b[^\"]*\"[^>]*>.*?</section\s*>", re.S | re.I)

#: A single slide fills the frame and the page does not scroll past it.
_ONE_SLIDE = "<style>html, body { overflow: hidden; } .slide { height: 100vh; }</style>\n"


def sample_slides(template: DesignTemplate) -> list[str]:
    """The deck sample's slides in order, one `<section class="slide …">` each; empty for
    any other kind.
    """
    if template.kind != "deck":
        return []
    return _SLIDE.findall(template.sample)


def render_slide(template: DesignTemplate, *, tokens: dict[str, str], n: int) -> str | None:
    """The seed around slide `n` (1-based) of the sample alone, or None when out of range."""
    slides = sample_slides(template)
    if not 1 <= n <= len(slides):
        return None
    page = render(template, title=template.name, tokens=tokens, body=slides[n - 1])
    style = _ONE_SLIDE + deck_preview.slide_style(slides, template.seed, n)
    return _HEAD_END.sub(lambda _m: style + "</head>", page, count=1)


_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.S | re.I)


def stylesheet(template: DesignTemplate, tokens: dict[str, str]) -> str:
    """The seed's `<style>` blocks alone with `{{TOKENS}}` resolved, for the editor's shadow
    root.
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
    "GENERIC_ID",
    "RETIRED",
    "RETIRED_KEPT",
    "RETIRED_MEDIA",
    "is_retired_media",
    "form_text",
    "HTML_KINDS",
    "SURFACE",
    "DesignTemplate",
    "all_templates",
    "choosable",
    "assemble",
    "default_for",
    "escape",
    "figure",
    "pictures_in",
    "preview",
    "for_surface",
    "offered",
    "get",
    "render",
    "rendering",
    "sanitise",
    "stylesheet",
]
