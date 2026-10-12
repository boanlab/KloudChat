"""Design template (서식) catalogue: seed HTML, rules, files in `app/design_templates/<id>/`."""

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
#: User template id prefix (`UserTemplate.id`).
USER_PREFIX = "ut-"

SURFACE: dict[str, SessionKind] = {
    "deck": SessionKind.slides,
    "document": SessionKind.report,
}

#: Kinds producing an HTML artifact.
HTML_KINDS = ("deck", "document")

#: Allowed block tags (others unwrapped); no h1/h2 (wrapper's), no <pre> (exporters read md).
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
    "sup",  # footnote ref; `<small>` holds the note
}

#: Removed with contents; h1/h2 would duplicate the wrapper heading.
_REMOVED_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "link", "meta", "h1", "h2"}
#: Allowed attrs by tag; `style` and `src` (data: only) filtered in `_attribute`.
_ALLOWED_ATTRIBUTES = {
    "*": {"class", "style"},
    "img": {"src", "alt"},
    # chart data / diagram source for exporters
    "figure": {"data-source"},
    # user page break → exporters' break
    "div": {"data-page-break"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

#: User-editable declarations; no layout properties (seed owns layout).
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
#: No parentheses in value: blocks `url(`, `expression(`, `calc(`. Security invariant.
_DECLARATION = re.compile(r"^\s*([a-z-]{2,20})\s*:\s*([A-Za-z0-9 ,.%#'\"_-]{1,120})\s*$")
#: `<code>` contents escaped before tag rules; closing tag required.
_CODE = re.compile(r"(<code\b[^>]*>)(.*?)</code\s*>", re.S | re.I)
_BARE_AMP = re.compile(r"&(?!#?\w{1,32};)")


@dataclass(frozen=True, slots=True)
class DesignTemplate:
    id: str
    kind: str
    name: str
    description: str
    category: str
    #: English variants; empty → Korean.
    name_en: str
    description_en: str
    category_en: str
    fills_en: tuple[str, ...]
    example_prompt_en: str
    #: What the user must bring (card).
    fills: tuple[str, ...]
    #: Composer starter sentence.
    example_prompt: str
    #: Appended to the generation prompt; never shown.
    instructions: str
    #: Seed element vocabulary (`markup.md`); 서식 instructions win.
    markup: str
    checklist: str
    #: Dark slides; `.pptx` only, not `.pdf`.
    dark: bool
    #: Deck only: one of `design.VISUAL_STYLES`.
    look: str
    #: `template.docx` base for `report_export`; empty → Word defaults.
    docx_template: str
    #: `template.pptx` base for `deck_export`; empty → PowerPoint defaults.
    pptx_template: str
    #: Cover owns a sheet in paged exports.
    cover_page: bool
    #: Blank `form.docx` / `form.pptx` download; empty when none.
    form_file: str

    seed: str
    #: Layout names; first is the cover.
    layouts: tuple[str, ...]
    #: Block → markup wrappers from `template.toml`.
    wrap_cover: str
    wrap_block: str
    #: Optional wrapper around non-cover blocks (`{blocks}`).
    wrap_group: str
    #: Checker bounds; empty → general bounds.
    limits: dict[str, int]
    sample: str
    #: Catalogue skills auto-applied, by `catalog_key`.
    skills: tuple[str, ...]
    #: Explicit `skills = []` (intentional, not omitted).
    no_skills: bool = False
    #: Server-run check skills by catalogue key (`checks` is the checklist).
    check_skills: tuple[str, ...] = ()
    #: `body_face = "design"`: body face follows the design `font` token.
    body_face_from_design: bool = False
    #: Fixed document format, "" for none.
    doc_format: str = ""
    #: Writer genre rule (`genre.md`), "" for none.
    genre: str = ""
    #: 빠진 정보 질문 (`services/gap_questions.py`).
    questions: tuple[dict, ...] = ()
    #: 진행 과정 설정 (`services/process_settings.py`).
    process: dict[str, Any] = field(default_factory=dict)
    #: Trace strictness (`check_skills.CHECK_OPTIONS`).
    check_options: tuple[str, ...] = ()
    #: User template row id, owner, sharing; "" for shipped.
    owner_id: str = ""
    visibility: str = ""
    #: User template's own `doc_formats.DocFormat`; None for shipped.
    doc_format_def: Any = None
    #: User template form as text for the writer (never an export base).
    form_reference: str = ""
    #: Shown in pickers; unlisted still usable by id (old sessions, agents).
    listed: bool = True

    @property
    def user(self) -> bool:
        return self.id.startswith(USER_PREFIX)

    @property
    def surface(self) -> SessionKind:
        return SURFACE[self.kind]

    @property
    def checks(self) -> tuple[str, ...]:
        """Checklist bullet lines without markers, for the gallery card."""
        return tuple(
            line.lstrip("-").strip()
            for line in self.checklist.splitlines()
            if line.lstrip().startswith("-")
        )


#: Extracted form text by (path, mtime).
_FORM_TEXT: dict[tuple[str, float], str] = {}


def form_text(template: DesignTemplate) -> str:
    """Blank form text (headings, column names) for the model."""
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
        except Exception:  # noqa: BLE001 — unreadable form → empty
            _FORM_TEXT[key] = ""
    return _FORM_TEXT[key]


#: 서식 stylesheet spliced last in <head> (wins ties).
_HEAD_END = re.compile(r"</head\s*>", re.I)


def _seed(folder: Path, meta: dict) -> str:
    """Seed HTML (own or `seed_from`) with `design.css` spliced in."""
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
    # deck seed sizes from the deck type scale
    seed = seed.replace("{{DECK_TYPE}}", deck_preview.type_declarations())
    face = _read(folder, "design.css").strip()
    if not face:
        return seed
    # extra <style> block; `stylesheet()` concatenates all
    block = f"<style>\n/* {folder.name} */\n{face}\n</style>\n"
    return _HEAD_END.sub(lambda _m: block + "</head>", seed, count=1)


def _look(meta: dict[str, Any], kind: str) -> str:
    """Declared deck `look`, else "editorial"; empty for other kinds."""
    if kind != "deck":
        return ""
    look = str(meta.get("look") or "").strip()
    if look and look not in design.VISUAL_STYLES:
        raise ValueError(f"unknown look {look!r}; one of {', '.join(design.VISUAL_STYLES)}")
    return look or "editorial"


def _seed_markup(folder: Path, meta: dict[str, Any]) -> str:
    """`markup.md` from the folder or its `seed_from`."""
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
    """One 서식 from its folder and `template.toml`."""
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
    # `_` folders: shared seeds
    for folder in sorted(p for p in _ROOT.iterdir() if p.is_dir() and not p.name.startswith("_")):
        manifest = folder / "template.toml"
        if not manifest.is_file():
            continue
        meta: dict[str, Any] = tomllib.loads(manifest.read_text(encoding="utf-8"))
        if str(meta.get("kind") or "") not in SURFACE:
            continue
        found[folder.name] = _template(folder, meta)
    return found


#: Loaded once; ships inside the image.
_TEMPLATES = _load()


def all_templates() -> list[DesignTemplate]:
    return list(_TEMPLATES.values())


def for_format(format_id: str | None) -> DesignTemplate | None:
    """Shipped 서식 bound to a format (its settings and questions)."""
    if not format_id:
        return None
    return next((t for t in _TEMPLATES.values() if t.doc_format == format_id), None)


def format_genres() -> dict[str, str]:
    """Each bound format's genre rule (`genre.md`)."""
    return {t.doc_format: t.genre for t in _TEMPLATES.values() if t.doc_format and t.genre}


def for_surface(kind: SessionKind) -> list[DesignTemplate]:
    return [t for t in _TEMPLATES.values() if t.surface is kind]


#: Loaded user templates by id (`user_templates.ensure`).
_USER: dict[str, DesignTemplate] = {}


def get(template_id: str | None) -> DesignTemplate | None:
    key = template_id or ""
    return _USER.get(key) if key.startswith(USER_PREFIX) else _TEMPLATES.get(key)


def remember(template: DesignTemplate) -> None:
    """Cache a user template (`user_templates.build`) for `get`."""
    if template.id.startswith(USER_PREFIX):
        _USER[template.id] = template


def forget(template_id: str) -> None:
    _USER.pop(template_id, None)


#: Shared seed as its own 서식, outside the catalogue.
GENERIC_ID = "_document"

#: Retired document 서식: still drawn via the shared seed, not choosable.
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

#: Retired image/audio/video 서식: read as none (`is_retired_media`).
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


#: Retired but kept in the image: still drawn/exported, not choosable (migration 0060).
RETIRED_KEPT = frozenset({"doc-career-history", "doc-client-proposal"})


def offered() -> list[DesignTemplate]:
    """Choosable 서식: catalogue minus retired (unlisted included)."""
    return [t for t in _TEMPLATES.values() if t.id not in RETIRED_KEPT]


def listed() -> list[DesignTemplate]:
    """Picker 서식: `offered` minus unlisted."""
    return [t for t in offered() if t.listed]


def choosable(template_id: str | None) -> DesignTemplate | None:
    """The 서식 if pickable now; None for unknown/retired ids."""
    return None if (template_id or "") in RETIRED_KEPT else get(template_id)


def is_retired_media(template_id: str | None) -> bool:
    """Whether the id is a retired image/audio/video 서식."""
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
            # shared seed cover owns its page
            "cover": '<div class="cover cover-page"><h1>{title}</h1>{body}</div>',
            "block": "<section><h2>{title}</h2>{body}</section>",
        },
    },
)


def generic_with(meta: dict[str, Any]) -> DesignTemplate:
    """서식 on the shared document seed with `meta` as its `template.toml`."""
    return _template(_ROOT / GENERIC_ID, meta)


def rendering(template_id: str | None) -> DesignTemplate | None:
    """서식 a stored document renders in; shared seed for retired ids, None if unknown."""
    if found := get(template_id):
        return found
    if template_id in RETIRED or template_id == GENERIC_ID:
        return _GENERIC
    if (template_id or "").startswith(USER_PREFIX):
        # unloaded/deleted user template: shared seed
        return _GENERIC
    return None


def default_for(defaults: Any, kind: SessionKind) -> str | None:
    """Project default template id for the surface, or None."""
    if not isinstance(defaults, dict):
        return None
    chosen = choosable(defaults.get(kind.value))
    if chosen is None or chosen.kind not in HTML_KINDS or chosen.surface is not kind:
        return None
    return chosen.id


def _quoted_code(match: re.Match[str]) -> str:
    """One `<code>` element with contents unescaped."""
    inner = _BARE_AMP.sub("&amp;", match.group(2))
    return f"{match.group(1)}{inner.replace('<', '&lt;').replace('>', '&gt;')}</code>"


#: Leading `layout: …` line (fragment start only).
_LAYOUT_DIRECTIVE = re.compile(r"^[ \t]*layout[ \t]*[:=][^\n]*\n", re.I)


#: JSON envelope keys describing the block, not content.
_ENVELOPE_META = frozenset({"layout", "title", "heading", "n", "index", "id"})


def _unwrapped(fragment: str) -> str:
    """Single string payload of a JSON-envelope answer; else unchanged."""
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
    """Strips leading layout-name-only lines."""
    names = {name.strip().lower() for name in layouts if name and name.strip()}
    text = fragment.lstrip("\n")
    while True:
        without = _LAYOUT_DIRECTIVE.sub("", text, count=1)
        if without != text:
            text = without.lstrip("\n")
            continue
        head, newline, rest = text.partition("\n")
        # bare layout name: left for `lint`
        if newline and head.strip().strip("\"'`").lower() in names:
            text = rest.lstrip("\n")
            continue
        return text


def _editable_style(raw: str) -> str:
    """`style=` reduced to allowed declarations; empty when none qualify."""
    kept = []
    for part in raw.split(";"):
        if not part.strip():
            continue
        # picked colours `rgb(r, g, b)` → hex
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
    """Authored HTML reduced to what the seed styles (nh3, allow lists); safe outside sandbox."""
    text = _CODE.sub(_quoted_code, _without_layout_preamble(_unwrapped(fragment), layouts))

    def attribute(element: str, name: str, value: str) -> str | None:
        if name == "style":
            return (_editable_style(value) if editable_styles else "") or None
        if name == "src":
            # no `href`; `src` only for embedded pictures
            return value if pictures.is_embedded(value) else None
        if name == "class" and value.strip().lower() == "cover":
            # cover frame only via `wrap_cover`
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
    """Design tokens (incl. `doc_type.CSS_VARIABLES`) as CSS custom properties."""
    return "\n".join(
        f"      --{name}: {value};"
        for name, value in (
            *doc_type.CSS_VARIABLES,
            ("accent", tokens.get("accent", "#5b5bd6")),
            ("ink", tokens.get("ink", "#1a1a1a")),
            ("muted", tokens.get("muted", "#666666")),
            (
                "font-body",
                # fontconfig `NanumMyeongjo` vs Windows/macOS `Nanum Myeongjo`
                "'Nanum Myeongjo', 'NanumMyeongjo', 'Batang', serif"
                if tokens.get("font") == "serif"
                else "'Pretendard', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif",
            ),
            # overridable by `design.css`; default body face
            ("font-head", "var(--font-body)"),
            ("font-num", "var(--font-head)"),
        )
    )


def render(template: DesignTemplate, *, title: str, tokens: dict[str, str], body: str) -> str:
    """Finished single file; figure columns marked `.fig` (totals `.total`)."""
    return (
        template.seed.replace("{{TOKENS}}", _token_declarations(tokens))
        .replace("{{TITLE}}", escape(title))
        .replace("{{BODY}}", table_figures.mark_html(body))
    )


def preview(template: DesignTemplate, tokens: dict[str, str]) -> str:
    """Gallery card page: seed around `sample.html`, with the bound format's rules."""
    if template.kind == "deck":
        page = render(template, title=template.name, tokens=tokens, body=template.sample)
        # body step the deck would settle on
        style = deck_preview.deck_style(sample_slides(template), template.seed)
        return _HEAD_END.sub(lambda _m: style + "</head>", page, count=1)
    if template.kind != "document":
        return render(template, title=template.name, tokens=tokens, body=template.sample)
    fmt = doc_formats.BY_ID.get(template.doc_format)
    if fmt is None:
        # no format: title, sections, blanks marked
        css = f"{format_css.PLACEHOLDER_CSS}\n{format_css.PAGED_CSS}"
        body = format_css.mark_placeholders(template.sample)
    else:
        css, body = format_css.preview(fmt, template.sample)
    page = render(template, title=template.name, tokens=tokens, body=body)
    return page.replace("</head>", f"<style>\n{css}\n</style>\n</head>", 1)


#: One deck sample slide (slides never nest).
_SLIDE = re.compile(r"<section\b[^>]*\bclass=\"slide\b[^\"]*\"[^>]*>.*?</section\s*>", re.S | re.I)

#: Single slide fills the frame, no scroll.
_ONE_SLIDE = "<style>html, body { overflow: hidden; } .slide { height: 100vh; }</style>\n"


def sample_slides(template: DesignTemplate) -> list[str]:
    """Deck sample slides in order; empty for other kinds."""
    if template.kind != "deck":
        return []
    return _SLIDE.findall(template.sample)


def render_slide(template: DesignTemplate, *, tokens: dict[str, str], n: int) -> str | None:
    """Seed around sample slide `n` (1-based), or None."""
    slides = sample_slides(template)
    if not 1 <= n <= len(slides):
        return None
    page = render(template, title=template.name, tokens=tokens, body=slides[n - 1])
    style = _ONE_SLIDE + deck_preview.slide_style(slides, template.seed, n)
    return _HEAD_END.sub(lambda _m: style + "</head>", page, count=1)


_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.S | re.I)


def stylesheet(template: DesignTemplate, tokens: dict[str, str]) -> str:
    """Seed `<style>` blocks with tokens resolved, for the editor shadow root."""
    seed = template.seed.replace("{{TOKENS}}", _token_declarations(tokens))
    return "\n\n".join(match.group(1) for match in _STYLE_BLOCK.finditer(seed)).strip()


def assemble(template: DesignTemplate, blocks: list[dict[str, str]]) -> str:
    """Blocks wrapped by `wrap_cover` / `wrap_block` / `wrap_group` into the body."""
    cover = ""
    rest: list[str] = []
    for index, block in enumerate(blocks, start=1):
        body = block.get("html") or ""
        # empty blocks dropped; empty cover still makes its page
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
    """`<figure>` markup for an encoded picture."""
    body = f'<figure><img src="{pictures.data_uri(mime, data_b64)}" alt="{escape(alt)}" />'
    if caption:
        body += f"<figcaption>{escape(caption)}</figcaption>"
    return body + "</figure>"


#: Whole `<figure>` with an embedded `data:image/` picture.
_PICTURE = re.compile(
    r"<figure\b[^>]*>(?:(?!</figure>).)*?src=\"data:image/.*?</figure>", re.S | re.I
)


def pictures_in(fragment: str) -> str:
    """Embedded `<figure>`s of a block, kept across rewrites."""
    return "".join(_PICTURE.findall(fragment))


def escape(text: str) -> str:
    """HTML-escapes text."""
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
