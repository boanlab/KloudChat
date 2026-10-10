"""A person's own document 템플릿, made in the editor and used like a shipped 서식.

A row (`UserTemplate`) holds every field a shipped document 서식 sets — what to bring, the
instructions and the checklist, the genre rule, the document format (head fields, numbering,
head layout, 개조식 lists, 「끝.」, a cover page), the check skills and how strict the trace is,
the 빠진 정보 질문, and the 진행 과정 설정 (calculations, 서식 보정 stages, checks, 검토 규칙,
structure rules, options). `build` makes the same `DesignTemplate` a shipped folder makes, on
the shared document seed (`_document`) with the design's tokens: a person's template carries
no HTML or CSS, so nothing it holds reaches a page as markup. Sessions and agents name it
`ut-<id>`, and every reader of a 서식 reads it through `design_templates.get` once `ensure`
has loaded it in this process.

A form file (DOCX/HWPX) it was made from is kept as its extracted text, given to the writer
as reference (the form's headings and columns) — never used as the export base.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from app.services import design_templates, doc_formats

PREFIX = design_templates.USER_PREFIX
NUMBERINGS = ("decimal", "roman", "korean", "official", "none")
HEADS = ("cover", "header", "memo", "press", "paper", "draft")
LISTS = ("", "gaejo")
VISIBILITIES = ("private", "org")
BODY_FACES = ("", "design")

MAX_FILLS = 6
MAX_FIELDS = 10
_LIMITS = {
    "name": 60, "description": 200, "category": 20, "example_prompt": 1000,
    "instructions": 8000, "checklist": 4000, "genre": 4000, "form_text": 20000,
    "form_name": 200, "label": 30, "field": 20, "fill": 30,
}
#: Control characters (but tab and newline), zero-width and direction marks: never kept.
_HIDDEN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁠-⁩﻿]")


class TemplateError(ValueError):
    """A field the editor may not store; `code` is the wire detail."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def plain(raw: Any, limit: int, *, lines: bool = False) -> str:
    """Text as stored: hidden characters dropped, bounded; one line unless `lines`."""
    text = _HIDDEN.sub("", str(raw or ""))
    if lines:
        # Kept as written (a shipped 서식's files end in a newline too), CRLF made LF.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    else:
        text = " ".join(text.split())
    return text[:limit]


def _format(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    label = plain(data.get("label"), _LIMITS["label"])
    fields = [plain(f, _LIMITS["field"]) for f in data.get("fields") or [] if plain(f, 1)]
    if len(fields) > MAX_FIELDS:
        raise TemplateError("template_fields", f"머리 항목은 {MAX_FIELDS}개까지입니다.")
    numbering = str(data.get("numbering") or "decimal")
    head = str(data.get("head") or "header")
    lists = str(data.get("lists") or "")
    if numbering not in NUMBERINGS:
        raise TemplateError("template_numbering", "번호 매기기를 고르세요.")
    if head not in HEADS:
        raise TemplateError("template_head", "머리 모양을 고르세요.")
    if lists not in LISTS:
        raise TemplateError("template_lists", "목록 모양을 고르세요.")
    return {"label": label, "fields": list(dict.fromkeys(fields)), "numbering": numbering,
            "head": head, "lists": lists, "closing": bool(data.get("closing")),
            "subtitle": bool(data.get("subtitle"))}


def clean(payload: dict[str, Any]) -> dict[str, Any]:
    """The row's fields from the editor's payload, each checked; raises `TemplateError`."""
    from app.services import (
        check_skills,
        gap_questions,
        process_settings,
        skill_packages,
    )

    name = plain(payload.get("name"), _LIMITS["name"])
    if not name:
        raise TemplateError("template_name", "템플릿 이름을 적으세요.")
    fills = [plain(f, _LIMITS["fill"]) for f in payload.get("fills") or [] if plain(f, 1)]
    if len(fills) > MAX_FILLS:
        raise TemplateError("template_fills", f"채울 것은 {MAX_FILLS}개까지입니다.")
    checks = [str(k) for k in payload.get("check_skills") or []]
    for key in checks:
        package = skill_packages.package_for(key)
        if package is None or not package.stage:
            raise TemplateError("template_check_skill", f"모르는 검사 스킬 「{key[:40]}」")
    options = [str(k) for k in payload.get("check_options") or []]
    if unknown := [k for k in options if k not in check_skills.CHECK_OPTIONS]:
        raise TemplateError("template_check_option", f"모르는 근거 확인 설정 「{unknown[0][:40]}」")
    face = str(payload.get("body_face") or "")
    if face not in BODY_FACES:
        raise TemplateError("template_body_face", "본문 서체 설정을 고르세요.")
    visibility = str(payload.get("visibility") or "private")
    if visibility not in VISIBILITIES:
        raise TemplateError("template_visibility", "공개 범위를 고르세요.")
    try:
        questions = gap_questions.clean(payload.get("questions") or [])
    except gap_questions.QuestionError as exc:
        raise TemplateError(exc.code, str(exc)) from exc
    try:
        process = process_settings.clean(payload.get("process") or {})
    except process_settings.ProcessError as exc:
        raise TemplateError(exc.code, str(exc)) from exc
    return {
        "name": name,
        "description": plain(payload.get("description"), _LIMITS["description"]),
        "category": plain(payload.get("category"), _LIMITS["category"]),
        "fills": list(dict.fromkeys(fills)),
        "example_prompt": plain(payload.get("example_prompt"), _LIMITS["example_prompt"],
                                lines=True),
        "instructions": plain(payload.get("instructions"), _LIMITS["instructions"], lines=True),
        "checklist": plain(payload.get("checklist"), _LIMITS["checklist"], lines=True),
        "genre": plain(payload.get("genre"), _LIMITS["genre"], lines=True),
        "doc_format": _format(payload.get("doc_format")),
        "cover_page": bool(payload.get("cover_page")),
        "body_face": face,
        "check_skills": list(dict.fromkeys(checks)),
        "check_options": list(dict.fromkeys(options)),
        "questions": questions,
        "process": process_settings.stored_form(process),
        "visibility": visibility,
    }


# ── the 서식 a row makes ────────────────────────────────────────────────────


def template_id(row) -> str:
    return f"{PREFIX}{row.id}"


def row_id(template: str | None) -> str:
    return (template or "").removeprefix(PREFIX) if is_user(template) else ""


def is_user(template: str | None) -> bool:
    return bool(template) and str(template).startswith(PREFIX)


def doc_format_of(row) -> doc_formats.DocFormat:
    data = _format(row.doc_format or {})
    return doc_formats.DocFormat(
        id=template_id(row), label=data["label"] or row.name, match="",
        fields=tuple(data["fields"]), numbering=data["numbering"], head=data["head"],
        lists=data["lists"], closing=data["closing"], subtitle=data["subtitle"])


def build(row) -> design_templates.DesignTemplate:
    """The `DesignTemplate` a row makes: the shared document seed, the row's own fields."""
    from app.services import gap_questions, process_settings

    cover = "cover cover-page" if row.cover_page else "cover"
    base = design_templates.generic_with({
        "kind": "document", "name": row.name, "description": row.description,
        "category": row.category or "내 템플릿", "fills": list(row.fills or []),
        "example_prompt": row.example_prompt, "layouts": ["cover", "section"],
        "wrap": {"cover": f'<div class="{cover}"><h1>{{title}}</h1>{{body}}</div>',
                 "block": "<section><h2>{title}</h2>{body}</section>"},
        "cover_page": bool(row.cover_page), "body_face": row.body_face or "",
        "skills": [], "check_skills": list(row.check_skills or []),
    })
    fmt = doc_format_of(row)
    try:
        questions = tuple(gap_questions.clean(row.questions or []))
    except gap_questions.QuestionError:
        questions = ()
    return dataclasses.replace(
        base, id=template_id(row), instructions=row.instructions or "",
        checklist=row.checklist or "", genre=row.genre or "", doc_format=fmt.id,
        doc_format_def=fmt, questions=questions,
        process=process_settings.stored(row.process or {}),
        check_options=tuple(str(k) for k in row.check_options or []),
        form_reference=row.form_text or "", owner_id=row.owner_id,
        visibility=row.visibility or "private")


#: `updated_at` of each row this process holds a template for.
_LOADED: dict[str, Any] = {}


async def ensure(db, ids) -> None:
    """Load (or reload, when edited since) the person's templates among `ids` into this
    process, so `design_templates.get` reads them; a deleted one is dropped."""
    from sqlmodel import col, select

    from app.models.workspace import UserTemplate

    wanted = sorted({row_id(i) for i in ids if is_user(i)} - {""})
    if not wanted:
        return
    rows = {r.id: r for r in (await db.exec(
        select(UserTemplate).where(col(UserTemplate.id).in_(wanted)))).all()}
    for rid in wanted:
        row = rows.get(rid)
        key = f"{PREFIX}{rid}"
        if row is None:
            design_templates.forget(key)
            _LOADED.pop(key, None)
        elif _LOADED.get(key) != row.updated_at or design_templates.get(key) is None:
            design_templates.remember(build(row))
            _LOADED[key] = row.updated_at


def readable(row, user_id: str) -> bool:
    return row is not None and (row.owner_id == user_id or row.visibility == "org")


async def accessible(db, user_id: str, template: str | None):
    """The row behind `template` when the person may use it (theirs or shared), else None."""
    from app.models.workspace import UserTemplate

    if not is_user(template):
        return None
    row = await db.get(UserTemplate, row_id(template))
    return row if readable(row, user_id) else None


async def usable(db, user_id: str, template: str | None) -> bool:
    """Whether the person may use the template `template` names (theirs or shared), loading
    it into this process when so. A shipped id is not this module's to judge: True."""
    if not is_user(template):
        return True
    row = await accessible(db, user_id, template)
    if row is None:
        return False
    await ensure(db, [template])
    return True


async def load(db, user_id: str, ids) -> None:
    """Load each person's template among `ids` the person may use."""
    for template in {i for i in ids if is_user(i)}:
        await usable(db, user_id, template)


def wire(row, *, viewer_id: str, owner_name: str = "") -> dict[str, Any]:
    from app.services import process_settings

    mine = row.owner_id == viewer_id
    return {
        "id": template_id(row), "name": row.name, "description": row.description,
        "category": row.category, "fills": list(row.fills or []),
        "examplePrompt": row.example_prompt,
        "instructions": row.instructions if mine else "",
        "checklist": row.checklist if mine else "", "genre": row.genre if mine else "",
        "docFormat": _format(row.doc_format or {}), "coverPage": row.cover_page,
        "bodyFace": row.body_face, "checkSkills": list(row.check_skills or []),
        "checkOptions": list(row.check_options or []),
        "questions": [dict(q) for q in row.questions or []],
        "process": process_settings.stored_form(process_settings.stored(row.process or {})),
        "formName": row.form_name, "hasForm": bool(row.form_text),
        "visibility": row.visibility, "mine": mine, "ownerName": owner_name,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def draft_of(template: design_templates.DesignTemplate) -> dict[str, Any]:
    """A shipped document 서식 as the editor's fields: every field it sets, so a person can
    start from it (and a test can check a person can set them all)."""
    from app.services import process_settings

    fmt = (doc_formats.BY_ID.get(template.doc_format) if template.doc_format else None)
    return {
        "name": template.name, "description": template.description,
        "category": template.category, "fills": list(template.fills),
        "example_prompt": template.example_prompt, "instructions": template.instructions,
        "checklist": template.checklist, "genre": template.genre,
        "doc_format": {
            "label": fmt.label, "fields": list(fmt.fields), "numbering": fmt.numbering,
            "head": fmt.head, "lists": fmt.lists, "closing": fmt.closing,
            "subtitle": fmt.subtitle} if fmt is not None else {},
        "cover_page": template.cover_page,
        "body_face": "design" if template.body_face_from_design else "",
        "check_skills": list(template.check_skills),
        "check_options": list(template.check_options),
        "questions": [dict(q) for q in template.questions],
        "process": process_settings.stored_form(dict(template.process)),
        "visibility": "private",
    }


def catalogue() -> dict[str, Any]:
    """What the editor offers: the format's choices, the check skills, the strictness, the
    question detectors and the 진행 과정 설정 names, and the shipped 서식 to start from."""
    from app.services import check_skills, gap_questions, process_settings, skill_packages

    checks = [{"key": p.key, "name": p.name, "stage": p.stage}
              for p in skill_packages.packages().values() if p.stage and not p.own_stage]
    return {
        "numberings": list(NUMBERINGS), "heads": list(HEADS), "lists": list(LISTS),
        "checkSkills": checks, "checkOptions": list(check_skills.CHECK_OPTIONS),
        "detectors": gap_questions.detector_catalogue(),
        "process": process_settings.catalogue(),
        "starts": [{"id": t.id, "name": t.name, "description": t.description}
                   for t in design_templates.offered() if t.kind == "document"],
        "limits": {k: v for k, v in _LIMITS.items()} | {"fills": MAX_FILLS,
                                                         "fields": MAX_FIELDS},
    }


__all__ = ["HEADS", "LISTS", "NUMBERINGS", "PREFIX", "TemplateError", "accessible", "build",
           "catalogue", "clean", "doc_format_of", "draft_of", "ensure", "is_user", "plain",
           "load", "readable", "row_id", "template_id", "usable", "wire"]
