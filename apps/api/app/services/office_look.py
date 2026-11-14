"""How an HWP or Word file looks when KloudChat writes it: the project's theme (design tokens)
and the report template the conversation or the project chose, so 웹 보고서, 한글 and Word
read as the same template.

Word takes the template's own `template.docx` as its base (styles, page, fonts) and the
theme's accent on headings (`report_export.to_docx`). 한글 has no template file of its own, so
the template's Word base is read for what HWPX can carry: its page margins, its body face
(serif or gothic) and its heading colour where the theme gives none.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.services import design_templates

log = logging.getLogger(__name__)

#: an East Asian or Latin face name read as a serif (명조·바탕) face
_SERIF = ("바탕", "명조", "batang", "myeongjo", "serif", "times", "georgia", "garamond")


@dataclass(frozen=True)
class Look:
    """A theme and a report template; empty for the house look."""

    tokens: dict[str, str] | None = None
    template_id: str = ""
    #: page settings a report already carries (`pageSettings`): they win over the template's
    page_settings: dict[str, Any] = field(default_factory=dict)

    @property
    def template(self):
        return design_templates.rendering(self.template_id) if self.template_id else None


@lru_cache(maxsize=32)
def _from_word(path: str) -> tuple[tuple[tuple[str, float], ...], str, str]:
    """(margins in mm, body face `serif`/`gothic`, heading colour) of a template.docx."""
    from docx import Document
    from docx.oxml.ns import qn

    try:
        document = Document(path)
    except Exception as exc:  # noqa: BLE001 — a template we cannot read lends nothing
        log.warning("template unreadable for its look (%s): %s", path, exc)
        return (), "", ""
    section = document.sections[0]
    margins = []
    for side in ("top", "bottom", "left", "right"):
        value = getattr(section, f"{side}_margin", None)
        if value is not None:
            margins.append((side, round(value / 36000, 1)))
    faces = []
    styles = document.styles
    for name in ("Normal", "Body Text"):
        try:
            style = styles[name]
        except KeyError:
            continue
        faces.append(style.font.name or "")
        fonts = style.element.find(f"{qn('w:rPr')}/{qn('w:rFonts')}")
        if fonts is not None:
            faces.append(fonts.get(qn("w:eastAsia")) or "")
    defaults = document.styles.element.find(f"{qn('w:docDefaults')}//{qn('w:rFonts')}")
    if defaults is not None:
        faces.append(defaults.get(qn("w:eastAsia")) or "")
    seen = " ".join(f for f in faces if f).lower()
    family = "serif" if any(word in seen for word in _SERIF) else ("gothic" if seen else "")
    colour = ""
    try:
        heading = styles["Heading 1"].font.color
        if heading is not None and heading.rgb is not None:
            colour = f"#{heading.rgb}"
    except (KeyError, ValueError, AttributeError):
        colour = ""
    return tuple(margins), family, colour


def hwpx(look: Look | None) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    """`(tokens, page_settings)` for `report_export.to_hwpx`: the theme over the template's
    face and colour, the template's margins under the document's own page settings."""
    if look is None:
        return None, None
    tokens = dict(look.tokens or {})
    page = dict(look.page_settings or {})
    chosen = look.template
    if chosen is not None and chosen.docx_template:
        margins, family, colour = _from_word(chosen.docx_template)
        if family and not chosen.body_face_from_design:
            # the template's body face, as its Word base sets it
            tokens["font"] = family
        if colour and "accent" not in (look.tokens or {}):
            tokens["accent"] = colour
        if margins and "margins" not in page:
            page["margins"] = dict(margins)
    return (tokens or None), (page or None)


def docx(look: Look | None) -> dict[str, Any]:
    """Keyword arguments for `report_export.to_docx`: the theme, the template's Word base."""
    if look is None:
        return {}
    chosen = look.template
    return {
        "tokens": look.tokens or None,
        "template": chosen.docx_template if chosen is not None else "",
        "body_from_design": bool(chosen is not None and chosen.body_face_from_design),
        **({"page_settings": look.page_settings} if look.page_settings else {}),
    }


def of(ctx_look: dict[str, Any] | None) -> Look | None:
    """The `Look` a turn carries (`ToolContext.look`: `tokens`, `templateId`)."""
    if not ctx_look:
        return None
    return Look(tokens=ctx_look.get("tokens") or None,
                template_id=str(ctx_look.get("templateId") or ""))


async def for_project(db: Any, user: Any, project: Any, template_id: str = "") -> Look | None:
    """The look of a project's documents: its theme (design system) and its default report
    template, unless a conversation chose its own (`template_id`); None outside a project
    with neither."""
    from app.models.chat import SessionKind
    from app.services import design as design_service
    from app.services import workspace_context

    design = await workspace_context._load_design_system(db, user, project) \
        if project is not None else None
    tokens = design_service.tokens_of(design) if design is not None else None
    chosen = template_id if design_templates.rendering(template_id) is not None else ""
    if not chosen and project is not None:
        chosen = design_templates.default_for(project.render_templates,
                                              SessionKind.report) or ""
    if not tokens and not chosen:
        return None
    return Look(tokens=tokens, template_id=chosen)
