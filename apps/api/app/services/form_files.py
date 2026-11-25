"""Which attached or saved file this turn's document follows as its form (양식).

Order (`design.md` §2.0): a form attached to this turn, the agent's 기본 양식 파일 for the output
kind, the project's. An attachment's role is the person's chip choice, else the request's words and
the file's profile (`form_profile`). A `.hwp` is never a form: the person is told to save it as
HWPX, and the file is read as material. An attached Excel file is judged like the others: a template
(`.xltx`, empty input cells, guidance) is a form, a table of numbers is the data a sheet works from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlmodel import select

from app.models.workspace import StoredFile
from app.services import form_profile

#: output kinds a form file shapes
KINDS = ("pptx", "docx", "hwpx", "xlsx")
#: kinds whose writer follows a form so far; another kind's file stays material
FOLLOWED = ("pptx", "docx", "hwpx", "xlsx")

Role = Literal["form", "material"]

#: the request treats an attachment as the form: 「이 양식에」, 「서식대로」, 「이 틀로」
_AS_FORM = re.compile(
    r"양식\s{0,2}(?:에|대로|으로|을\s{0,2}(?:따라|채워|써|사용))|서식\s{0,2}(?:에|대로|으로|을\s{0,2}따라)|"
    r"틀\s{0,2}(?:에|대로|로)|(?:작년|지난번|예전)\s{0,4}(?:것|보고서|자료|발표)\s{0,2}처럼|"
    r"(?:템플릿|template)\s{0,2}(?:에|대로|으로)|빈\s{0,2}칸\s{0,2}(?:을\s{0,2})?채워", re.I)
#: the request treats it as material: 「이 자료로」, 「참고해서」, 「요약해」
_AS_MATERIAL = re.compile(
    r"자료\s{0,2}(?:로|를\s{0,2}(?:바탕|참고))|참고\s{0,2}해|요약|내용\s{0,2}으로|"
    r"바탕\s{0,2}으로|정리해", re.I)

#: how much the request's words move a file's score
_WORDS_WEIGHT = 0.3


@dataclass
class FormChoice:
    kind: str
    file_id: str
    name: str
    #: `attached`, `agent` or `project`
    source: str
    profile: dict[str, Any] = field(default_factory=dict)

    def wire(self) -> dict[str, Any]:
        return {"kind": self.kind, "fileId": self.file_id, "name": self.name,
                "source": self.source, "profile": form_profile.summary(self.profile)}


@dataclass
class TurnForms:
    """This turn's forms by output kind, and what is left as material."""

    forms: dict[str, FormChoice] = field(default_factory=dict)
    #: attachment ids read as material (forms are not material)
    material: list[str] = field(default_factory=list)
    #: attachments whose role is unclear: asked about before writing
    unsure: list[str] = field(default_factory=list)
    #: what the person is told about a file (a `.hwp` asked to be a form, a second form of
    #: one kind): file id → note
    notes: dict[str, str] = field(default_factory=dict)
    #: attachment id → the role taken
    roles: dict[str, Role] = field(default_factory=dict)

    def form_for(self, kind: str | None) -> FormChoice | None:
        return self.forms.get(kind or "")


def _profile_of(row: StoredFile) -> dict[str, Any] | None:
    found = row.form_profile
    if found and found.get("version") == form_profile.VERSION:
        return found
    return None


def role_of(row: StoredFile, request: str, chosen: Role | None) -> tuple[Role | Literal[
        "unsure"], str]:
    """(`form`, `material` or `unsure`, note) for one attachment."""
    found = _profile_of(row)
    if found is None:
        return "material", ""
    kind = found.get("kind")
    if kind == "hwp":
        asked = chosen == "form" or (chosen is None and (
            found.get("form_like") or _AS_FORM.search(request or "")))
        return "material", form_profile.HWP_FORM_ADVICE if asked else ""
    if found.get("error"):
        note = f"{row.name}: {found['error']}" if chosen == "form" else ""
        return "material", note
    if kind not in FOLLOWED:
        return "material", ""
    if kind == "xlsx" and found.get("macro"):
        # a workbook with macros is never copied as a form (the copy would carry the macro)
        note = (f"{row.name}: 매크로가 든 통합 문서는 양식으로 쓸 수 없어 자료로 읽습니다."
                if chosen == "form" else "")
        return "material", note
    if chosen is not None:
        return chosen, ""
    score = float(found.get("score") or 0.0)
    if _AS_FORM.search(request or ""):
        score += _WORDS_WEIGHT
    if _AS_MATERIAL.search(request or "") and not _AS_FORM.search(request or ""):
        score -= _WORDS_WEIGHT
    if score >= form_profile.FORM_AT:
        return "form", ""
    if score <= form_profile.MATERIAL_AT:
        return "material", ""
    return "unsure", ""


def choose(
    attachments: list[StoredFile], roles: dict[str, Role] | None, request: str,
    *, agent_forms: dict[str, StoredFile] | None = None,
    project_forms: dict[str, StoredFile] | None = None,
    skip_defaults: bool = False,
) -> TurnForms:
    """This turn's forms. `skip_defaults`: the person took the saved form off for this turn."""
    out = TurnForms()
    roles = roles or {}
    for row in attachments:
        role, note = role_of(row, request, roles.get(row.id))
        if note:
            out.notes[row.id] = note
        if role == "form":
            found = _profile_of(row) or {}
            kind = str(found.get("kind") or "")
            if kind in out.forms:
                out.notes[row.id] = (
                    f"같은 종류의 양식이 둘이라 「{out.forms[kind].name}」을 따르고 이 파일은 "
                    "자료로 읽었습니다.")
                out.material.append(row.id)
                out.roles[row.id] = "material"
                continue
            out.forms[kind] = FormChoice(kind, row.id, row.name, "attached", found)
            out.roles[row.id] = "form"
            continue
        if role == "unsure":
            out.unsure.append(row.id)
        out.material.append(row.id)
        out.roles[row.id] = "material"
    if skip_defaults:
        return out
    for source, saved in (("agent", agent_forms or {}), ("project", project_forms or {})):
        for kind, row in saved.items():
            found = _profile_of(row)
            if kind in out.forms or kind not in FOLLOWED or not found or found.get("error"):
                continue
            out.forms[kind] = FormChoice(kind, row.id, row.name, source, found)
    return out


# ── saved forms (기본 양식 파일) ────────────────────────────────────────────


class SlotError(ValueError):
    """A refused form slot; `code` is the API detail."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


#: file names a saved Excel form is not: macro workbooks and the old binary format
_NOT_XLSX = {".xlsm": "form_xlsm_refused", ".xlsb": "form_xlsm_refused",
             ".xls": "form_xls_needs_xlsx"}


def refusal(row: StoredFile) -> str | None:
    """Why `row` cannot be saved as a form, as an API detail, or None."""
    from pathlib import Path

    if code := _NOT_XLSX.get(Path(row.name).suffix.lower()):
        return code
    found = _profile_of(row)
    if found is not None and found.get("kind") == "xlsx" and found.get("macro"):
        return "form_xlsm_refused"
    if found is None or found.get("kind") not in (*KINDS, "hwp"):
        return "form_kind_unknown"
    if found.get("kind") == "hwp":
        return "form_hwp_needs_hwpx"
    if found.get("error"):
        return "form_unreadable"
    return None


def new_form_copy(user_id: str, source: StoredFile) -> StoredFile:
    """A `role=form` copy of an own file (blob copied), owned by nothing but the person."""
    from app.services import files as file_service

    copy = StoredFile(
        user_id=user_id, name=source.name, size=source.size, mime=source.mime,
        role="form", form_profile=source.form_profile, text=source.text,
        tokens=source.tokens, origin="upload",
    )
    copy.storage_key = file_service.copy_blob(user_id, copy.id, copy.name, source.storage_key)
    return copy


async def checked_slots(db, user_id: str, current: dict | None,
                        changes: dict[str, str | None]) -> dict[str, str]:
    """`current` with `changes` laid over (a null clears a kind); every id must be the person's
    own saved form copy of that kind."""
    out = {k: v for k, v in (current or {}).items() if k in KINDS and v}
    for kind, file_id in changes.items():
        if kind not in KINDS:
            raise SlotError("form_kind_unknown")
        if not file_id:
            out.pop(kind, None)
            continue
        row = await db.get(StoredFile, file_id)
        if row is None or row.user_id != user_id or row.role != "form":
            raise SlotError("form_not_found")
        if (found := _profile_of(row)) is None or found.get("kind") != kind:
            raise SlotError("form_kind_mismatch")
        out[kind] = file_id
    return out


async def drop_unused(db, user_id: str, file_ids: set[str]) -> int:
    """Saved form copies no project or agent of the person names any more, deleted (uncommitted);
    returns how many."""
    from app.models.workspace import Agent, Project
    from app.services import files as file_service

    if not file_ids:
        return 0
    used: set[str] = set()
    for model, owner in ((Project, Project.user_id), (Agent, Agent.owner_id)):
        for slots in (await db.exec(select(model.form_files).where(owner == user_id))).all():
            used.update(v for v in (slots or {}).values() if isinstance(v, str))
    dropped = 0
    for file_id in file_ids - used:
        row = await db.get(StoredFile, file_id)
        if row is not None and row.user_id == user_id and row.role == "form":
            file_service.delete_blob(row.storage_key)
            await db.delete(row)
            dropped += 1
    return dropped


# ── this conversation's form ───────────────────────────────────────────────


@dataclass
class Resolved:
    """A form the conversation's document follows: its choice, bytes and profile."""

    choice: FormChoice
    data: bytes

    @property
    def profile(self) -> dict[str, Any]:
        return self.choice.profile


def remember(session, turn: TurnForms, off: list[str] | None) -> bool:
    """The conversation keeps this turn's attached forms for its later turns; `off` lists the
    kinds whose saved (agent or project) form the person took off (None: unchanged). True when
    the conversation's forms changed."""
    slots = dict(session.form_files or {})
    if off is not None:
        for kind in KINDS:
            if kind in off and not slots.get(kind):
                slots[kind] = ""
            elif kind not in off and slots.get(kind) == "":
                slots.pop(kind)
    for kind, choice in turn.forms.items():
        if choice.source == "attached":
            slots[kind] = choice.file_id
    before = session.form_files or None
    session.form_files = slots or None
    return session.form_files != before


def attached_ids(session) -> set[str]:
    """Files this conversation follows as forms: never material in its later turns."""
    return {v for v in (session.form_files or {}).values() if isinstance(v, str) and v}


async def for_session(db, session, kind: str, *, agent=None, project=None) -> Resolved | None:
    """The form `kind` documents of this conversation follow: one attached here, else the
    agent's, else the project's; None when none (or the person took the saved one off)."""
    from app.models.workspace import Agent, Project
    from app.services import files as file_service

    if kind not in FOLLOWED:
        return None
    slots = session.form_files or {}
    candidates: list[tuple[str, str, str]] = []
    if slots.get(kind):
        candidates.append(("attached", slots[kind], session.user_id))
    elif slots.get(kind) != "":
        if agent is None and session.agent_id:
            agent = await db.get(Agent, session.agent_id)
        if agent is not None and (agent.form_files or {}).get(kind):
            candidates.append(("agent", agent.form_files[kind], agent.owner_id))
        if project is None and session.project_id:
            project = await db.get(Project, session.project_id)
        if project is not None and project.user_id == session.user_id and (
                project.form_files or {}).get(kind):
            candidates.append(("project", project.form_files[kind], project.user_id))
    for source, file_id, owner in candidates:
        row = await db.get(StoredFile, file_id)
        if row is None or row.user_id != owner:
            continue
        found = _profile_of(row)
        if not found or found.get("kind") != kind or found.get("error"):
            continue
        try:
            data = file_service.read_blob(row.storage_key)
        except OSError:
            continue
        return Resolved(FormChoice(kind, row.id, row.name, source, found), data)
    return None


def turn_meta(turn: TurnForms, meta: list[dict] | None) -> list[dict] | None:
    """The question's attachment list with the forms it follows: an attached form keeps its
    row (`role: form`), a saved one is added with its `source` (never re-sent on a retry), and
    a note the person is told (a `.hwp` asked to be a form) rides on that file."""
    out = [dict(item) for item in meta or []]
    by_id = {item["id"]: item for item in out}
    for kind, choice in turn.forms.items():
        if choice.file_id in by_id:
            by_id[choice.file_id].update(role="form", formKind=kind)
        else:
            out.append({"id": choice.file_id, "name": choice.name, "role": "form",
                        "formKind": kind, "source": choice.source})
    for file_id, role in turn.roles.items():
        if file_id in by_id and "role" not in by_id[file_id]:
            by_id[file_id]["role"] = role
    for file_id, note in turn.notes.items():
        if file_id in by_id:
            by_id[file_id]["formNote"] = note
    return out or None


def outline(form: Resolved) -> list[str]:
    """A Word form's chapters (its heading-1 lines), their own numbers set aside: the 목차 a
    report on it is planned with."""
    from app.services.docx_form import _NUMBER

    out: list[str] = []
    for item in form.profile.get("skeleton") or []:
        if item.get("level") == 1:
            name = _NUMBER.sub("", str(item.get("text") or ""), count=1).strip()
            if name and name not in out:
                out.append(name)
    return out[:20]


def writer_note(form: Resolved) -> str:
    """What the writer is told about the form the document is written on."""
    chapters = outline(form)
    lines = [
        f"# 양식: {form.choice.name}",
        "The document is written on the person's own form file. The server keeps the form's "
        "cover, header, footer, styles and numbering, and puts each chapter you write under "
        "the form's heading of the same name.",
        "Write the chapters below, in this order, with these names. Do not number them and do "
        "not add a title or a cover: the form has its own. Never copy the form's sample text "
        "or guidance (「※ 작성요령」) into the document, and never take a fact from the form.",
    ]
    if chapters:
        lines.append("Chapters: " + " / ".join(chapters))
    for name, subs, heads in form_parts(form):
        parts = []
        if subs:
            parts.append("sub-headings " + " / ".join(subs) + " (write each as a level-2 "
                         "heading of that name)")
        for head in heads:
            parts.append("a table with the columns " + " | ".join(head) + " (write your "
                         "table for it with exactly these column names; amounts in the unit "
                         "the column names)")
        if parts:
            lines.append(f"Under 「{name}」 the form has " + "; ".join(parts) + ".")
    return "\n".join(lines)


def form_parts(form: Resolved) -> list[tuple[str, list[str], list[list[str]]]]:
    """(chapter, its sub-headings, the head rows of its blank tables) for a Word form."""
    import io

    from docx import Document

    from app.services import docx_form

    try:
        document = Document(io.BytesIO(form_profile.as_document(form.data)))
    except Exception:  # noqa: BLE001 — the note goes without the parts
        return []
    headings = form.profile.get("headings") or {}
    ones = {(headings.get("1") or {}).get("style"), "Heading1"} - {None, ""}
    twos = {(headings.get("2") or {}).get("style"), "Heading2"} - {None, ""}
    body = [e for e in document.element.body if e.tag != docx_form._q("sectPr")]
    _, chapters = docx_form._split(body, ones)
    out = []
    for heading, content in chapters:
        _, subs = docx_form._split(content, twos)
        heads = [docx_form.table_cells(e)[0] for e in content
                 if e.tag == docx_form._q("tbl") and docx_form.blank_table(e)]
        out.append((docx_form._NUMBER.sub("", docx_form._text(heading), count=1).strip(),
                    [docx_form._NUMBER.sub("", docx_form._text(h), count=1).strip()
                     for h, _ in subs], heads))
    return out


#: the request asks for the form to be filled or written on
_FILL_ASK = re.compile(r"채워|채우|작성해|써\s{0,2}줘|써\s{0,2}주|기입|맞춰|양식대로|서식대로")

HWP_REFUSAL = (
    "이 파일은 예전 한글 형식(.hwp)이라 양식으로 채울 수 없어 이번에는 아무것도 쓰지 않았습니다."
    "\n\n"
    "한글에서 「파일 › 다른 이름으로 저장 › 한글 표준 문서 (*.hwpx)」로 저장해 다시 올려 주세요. "
    "올리신 파일 옆의 「HWP로 열기」로 열어 저장해도 HWPX가 됩니다. 다시 올리면 서식의 칸마다 "
    "채워 드립니다.")


def hwp_refusal(turn: TurnForms, request: str) -> str:
    """The whole answer to a request to fill an old-format `.hwp` as a form, which nothing can
    be written on: the way to a form that can be (HWPX), not an essay about the file. Empty
    when the request is not that (a summary of the file, a form that can be followed)."""
    if turn.forms or form_profile.HWP_FORM_ADVICE not in turn.notes.values():
        return ""
    if not _FILL_ASK.search(request or "") or _AS_MATERIAL.search(request or ""):
        return ""
    return HWP_REFUSAL
