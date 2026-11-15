"""지식 서가 studio: one-click outputs grounded in the sources the person picked.

`start` checks the request, writes a `running` row and returns; `_run` reads the sources
(numbered, within the writer's window), asks the model (`shelf_calls.route`: strict-local by
default and always for internal data while the administrator says so), turns the answer
into its kind (`studio_kinds`) and saves it: documents as an artifact in a conversation of
their own (a web report, or an HWP/Word file through the report path) so they open in the
document panel, quizzes and flashcards as item sets the player reads.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any

from sqlmodel import col, select

from app.core import db as core_db
from app.core import logs
from app.models.chat import ChatSession, SessionKind
from app.models.shelf import StudioItem
from app.models.user import User, utcnow
from app.models.workspace import Artifact, ArtifactKind, Project, StoredFile
from app.services import (
    shelf_calls,
    shelf_checks,
    shelf_jobs,
    shelf_sources,
    studio_kinds,
)
from app.services.shelf_names import kind_name

log = logging.getLogger(__name__)

REASON = "knowledge.studio"
FORMATS = ("web", "hwp", "docx")
#: the writer's window share the sources may take, in chars (~2.5 a token)
_WINDOW_SHARE = 0.55
_CHARS_PER_TOKEN = 2.5
_MAX_SOURCE_CHARS = 160_000
_MIN_SOURCE_CHARS = 24_000


def source_budget(model: dict) -> int:
    from app.services.context import effective_window

    window = effective_window(model)
    return max(_MIN_SOURCE_CHARS,
               min(int(window * _WINDOW_SHARE * _CHARS_PER_TOKEN), _MAX_SOURCE_CHARS))


async def sources_for(db, user: User, project_id: str, ids: list[str] | None) -> list[StoredFile]:
    """The project's readable sources among `ids` (all when none named), oldest first."""
    query = select(StoredFile).where(
        StoredFile.project_id == project_id, StoredFile.user_id == user.id,
        col(StoredFile.agent_id).is_(None)).order_by(col(StoredFile.created_at))
    rows = [f for f in (await db.exec(query)).all() if (f.text or "").strip()]
    if ids:
        wanted = set(ids)
        rows = [f for f in rows if f.id in wanted]
    return rows


def _labels(f: StoredFile) -> tuple[str, ...]:
    """What names a source's place in a course: its file name, its digest title, its
    first line (「# 3강 논리 시계」)."""
    digest = shelf_sources.digest_of(f) or {}
    first = next((line.strip(" #\t") for line in (f.text or "").splitlines()[:5]
                  if line.strip()), "")
    return (f.name or "", str(digest.get("title") or ""), first[:120])


def scoped(files: list[StoredFile], options: dict[str, Any]
           ) -> tuple[list[StoredFile], str]:
    """The sources a numbered range in the request (「1~7강」) names, and the range as written;
    all of them and "" when it names none or no source carries such a number."""
    scope = studio_kinds.scope_of(str(options.get("focus") or ""))
    if scope is None:
        return files, ""
    kept = [f for f in files if studio_kinds.in_scope(scope, *_labels(f))]
    if not kept:
        return files, ""
    return kept, scope.label


def options_of(kind: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    out: dict[str, Any] = {"language": str(raw.get("language") or "auto")}
    if out["language"] not in ("auto", "ko", "en"):
        out["language"] = "auto"
    if kind in studio_kinds.DOCUMENTS:
        fmt = str(raw.get("format") or "web")
        out["format"] = fmt if fmt in FORMATS and kind != "mindmap" else "web"
    if kind in studio_kinds.SETS:
        default, low, high = (10, 3, 30) if kind == "quiz" else (15, 5, 40)
        try:
            count = int(raw.get("count") or default)
        except (TypeError, ValueError):
            count = default
        out["count"] = max(low, min(high, count))
    if raw.get("model"):
        out["model"] = str(raw["model"])[:120]
    if str(raw.get("focus") or "").strip():
        out["focus"] = shelf_jobs.clip(raw["focus"], 300)
    return out


async def estimate(db, user: User, project: Project, kind: str, ids: list[str] | None,
                   options: dict[str, Any]) -> dict[str, Any]:
    """Length and cost before generating: what is read, the model, tokens and credits."""
    files = await sources_for(db, user, project.id, ids)
    if not files:
        raise shelf_calls.ShelfError("no_sources", 422)
    files, scope = scoped(files, options)
    options = {**options, "scope": scope} if scope else options
    how = await shelf_calls.route(user, kind=kind, asked=options.get("model"))
    made = shelf_sources.bundle(files, source_budget(how.model))
    system = studio_kinds.system_prompt(kind, "ko", options)
    input_tokens = shelf_calls.tokens_of(system) + shelf_calls.tokens_of(made.text)
    read_tokens = input_tokens
    output_tokens = int(studio_kinds.MAX_TOKENS[kind] * studio_kinds.TYPICAL_SHARE)
    # the checks' re-ask: what failed, with its passages, rewritten once
    input_tokens += output_tokens
    output_tokens += output_tokens // 3
    if how.effort in ("low", "high"):
        # thinking before each answer
        output_tokens += thinking_tokens(kind)
    out = {
        "kind": kind,
        "sources": len(files),
        "partial": made.partial,
        "model": how.model["id"],
        "modelLabel": how.model.get("label") or how.model["id"],
        "strict": how.strict,
        "required": how.required,
        "thinking": how.effort in ("low", "high"),
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "credits": shelf_calls.estimate(how.model, input_tokens, output_tokens),
        "maxCredits": _most_credits(how, read_tokens, kind),
    }
    return out


def _most_credits(how: Any, read_tokens: int, kind: str) -> int:
    """The ceiling: both calls written to the kind's limit (the answer, then the checks' re-ask
    reading it back and rewriting a third), with the thinking when reasoning is on."""
    ceiling = studio_kinds.MAX_TOKENS[kind]
    written = ceiling + ceiling // 3
    if how.effort in ("low", "high"):
        written += thinking_tokens(kind)
    return shelf_calls.estimate(how.model, read_tokens + ceiling, written)


def thinking_tokens(kind: str) -> int:
    """Typical thinking a writer with reasoning on spends on an output, both its calls (the
    answer and the checks' re-ask)."""
    return 6000 * 2


async def start(db, user: User, project: Project, kind: str, ids: list[str] | None,
                options: dict[str, Any], *, title: str = "") -> StudioItem:
    files = await sources_for(db, user, project.id, ids)
    if not files:
        raise shelf_calls.ShelfError("no_sources", 422)
    # a range the request names (「1~7강」) is what is read: the item lists only those
    files, scope = scoped(files, options)
    options = {**options, "scope": scope} if scope else \
        {k: v for k, v in options.items() if k != "scope"}
    how = await shelf_calls.route(user, kind=kind, asked=options.get("model"))
    if not shelf_calls.headroom(user, shelf_calls.estimate(how.model, 1000, 1000)):
        raise shelf_calls.ShelfError("insufficient_credits", 422)
    item = StudioItem(user_id=user.id, project_id=project.id, kind=kind,
                      title=title or kind_name(kind), state="running",
                      source_ids=[f.id for f in files], options=options,
                      model=str(how.model["id"]))
    db.add(item)
    await db.commit()
    await db.refresh(item)
    shelf_jobs.spawn(_run(item.id))
    return item


async def regenerate(db, user: User, item: StudioItem) -> StudioItem:
    """The same request again, as a new output (the earlier one stays until deleted)."""
    project = await db.get(Project, item.project_id)
    if project is None:
        raise shelf_calls.ShelfError("not_found", 404)
    return await start(db, user, project, item.kind, list(item.source_ids or []),
                       dict(item.options or {}))


#: the most output a re-ask after a cut-off answer may take (tokens)
_MOST_TOKENS = 12_000
_JSON_ONLY = ("\n\nYour previous answer was not a readable JSON object. Answer again with the "
              "JSON object only.")
_CONCISE = ("\n\nYour previous answer ran out of room and was cut off before the JSON object "
            "closed. Answer again, more concisely: shorter sentences and explanations, the "
            "same JSON shape, closed properly.")


def cut_off(answer: str, usage: dict | None, max_tokens: int) -> bool:
    """Whether an unreadable answer ran out of room: it used (nearly) all its output tokens,
    or it opened a JSON object it never closed."""
    spent = int((usage or {}).get("outputTokens") or (usage or {}).get("completion_tokens") or 0)
    if max_tokens and spent >= max_tokens * 0.9:
        return True
    text = (answer or "").strip()
    start = text.find("{")
    return start >= 0 and text[start:].count("{") > text[start:].count("}")


async def _complete_json(user: User, how: shelf_calls.Route, system: str, prompt: str,
                         max_tokens: int, db, item: StudioItem) -> dict[str, Any] | None:
    """The answer as JSON. An unreadable answer is asked again once (JSON only); one cut off
    for room is asked again with twice the room and a nudge to be brief, up to twice (the
    same room would cut it the same way). Every call billed on `item`."""
    extra, most, tries = "", max_tokens, 2
    for attempt in range(3):
        if attempt >= tries:
            break
        answer, usage = await shelf_calls.complete(user, how.model, system + extra, prompt,
                                                   most, effort=how.effort)
        _add_usage(item, shelf_calls.bill(db, user, how.model, usage, REASON))
        data = shelf_jobs.parse_json(answer)
        if data is not None:
            return data
        if cut_off(answer, usage, most):
            log.info("studio %s (%s): answer cut off at %d tokens; asked again with more room",
                     item.id, item.kind, most)
            extra, most, tries = _CONCISE, min(_MOST_TOKENS, most * 2), 3
        else:
            log.info("studio %s (%s): answer not readable JSON", item.id, item.kind)
            extra = _JSON_ONLY
    return None


async def _run(item_id: str) -> None:
    async with core_db.SessionLocal() as db:
        item = await db.get(StudioItem, item_id)
        if item is None:
            return
        user = await db.get(User, item.user_id)
        if user is None:
            return
        try:
            await _write(db, user, item)
            item.state = "done"
            item.error = ""
        except Exception as exc:  # noqa: BLE001 — the row says why; the person asks again
            await db.rollback()
            item = await db.get(StudioItem, item_id)
            if item is None:
                return
            log.warning("studio %s (%s) failed: %s", item_id, item.kind, logs.safe(exc))
            item.state = "failed"
            item.error = (exc.code if isinstance(exc, shelf_calls.ShelfError)
                          else "studio_failed")[:200]
        item.updated_at = utcnow()
        db.add(item)
        await db.commit()


async def _write(db, user: User, item: StudioItem) -> None:
    options = dict(item.options or {})
    files = await sources_for(db, user, item.project_id, list(item.source_ids or []))
    if not files:
        raise shelf_calls.ShelfError("no_sources", 422)
    how = await shelf_calls.route(user, kind=item.kind, asked=options.get("model"))
    made = shelf_sources.bundle(files, source_budget(how.model))
    language = studio_kinds.language_of(made.text, str(options.get("language") or "auto"))
    unspaced = language == "ko" and any(shelf_checks.unspaced(f.text or "") for f in files)
    system = _system(item.kind, language, options, unspaced)
    prompt = shelf_calls.outgoing(
        studio_kinds.user_prompt(made.text, [s.name for s in made.sources]), how)
    item.model = str(how.model["id"])
    item.credits = item.input_tokens = item.output_tokens = 0
    count = len(made.sources)
    data = await _complete_json(user, how, system, prompt,
                                studio_kinds.MAX_TOKENS[item.kind], db, item)
    if data is None:
        raise ValueError("answer unreadable")
    if item.kind in studio_kinds.DOCUMENTS:
        doc = studio_kinds.document(item.kind, data, count, language, made.text)
        if doc is not None and studio_kinds.cites_nothing(item.kind, doc):
            # the writer left out the source numbers: asked once more, the better one kept
            again = await _complete_json(user, how, system + _CITE_AGAIN, prompt,
                                         studio_kinds.MAX_TOKENS[item.kind], db, item)
            redo = (studio_kinds.document(item.kind, again, count, language, made.text)
                    if again else None)
            if redo is not None and not studio_kinds.cites_nothing(item.kind, redo):
                data = again
    checked = await _checked(db, user, how, item, data, made, language)
    if item.kind in studio_kinds.SETS:
        await _top_up(db, user, how, item, data, made, language, prompt, checked,
                      options, unspaced)
    item.content = {"language": language, "sources": made.wire(), "partial": made.partial,
                    "strict": how.strict, "checks": checked}
    title = shelf_jobs.clip(data.get("title"), 160) or kind_name(item.kind, language)
    if language == "ko":
        title = shelf_checks.mend_script(title, made.text) or kind_name(item.kind, language)
    item.title = title[:200]
    content = item.content
    if item.kind in studio_kinds.DOCUMENTS:
        doc = studio_kinds.document(item.kind, data, count, language, made.text)
        if doc is None:
            raise ValueError("empty document")
        doc["title"] = item.title
        content.update(doc)
        item.content = content
        await _save_document(db, user, item, doc, made.wire(),
                             str(options.get("format") or "web"))
    elif item.kind == "quiz":
        questions = studio_kinds.quiz(data, count, random.Random())
        if len(questions) < 3:
            raise ValueError("too few usable questions")
        wanted = int(options.get("count") or len(questions))
        item.content = {**content, "questions": questions[:wanted]}
    elif item.kind == "flashcards":
        found = studio_kinds.cards(data, count)
        if len(found) < 3:
            raise ValueError("too few usable cards")
        wanted = int(options.get("count") or len(found))
        item.content = {**content, "cards": found[:wanted]}


_SET_KEY = {"quiz": ("questions", "question"), "flashcards": ("cards", "front")}


def _usable(kind: str, data: dict[str, Any], count: int) -> list[dict]:
    return (studio_kinds.quiz(data, count, random.Random()) if kind == "quiz"
            else studio_kinds.cards(data, count))


def _system(kind: str, language: str, options: dict[str, Any], unspaced: bool) -> str:
    system = studio_kinds.system_prompt(kind, language, options)
    if unspaced:
        # stored text extracted before word spacing was kept: the writer must not copy it
        system = system.replace("Answer with one JSON object only",
                                studio_kinds.SPACING_RULE + "Answer with one JSON object only",
                                1)
    return system


async def _top_up(db, user: User, how: shelf_calls.Route, item: StudioItem,
                  data: dict[str, Any], made: shelf_sources.Bundle, language: str,
                  prompt: str, checked: dict[str, Any], options: dict[str, Any],
                  unspaced: bool) -> None:
    """A set the checks left short of the count asked for gets the missing items once: the
    writer asked for exactly that many new ones (none like those kept, from the sources not
    used yet), checked the same way, appended to `data` (in place). The count is trimmed to
    after, by the caller."""
    group, key = _SET_KEY[item.kind]
    wanted = int(options.get("count") or 0)
    count = len(made.sources)
    have = _usable(item.kind, data, count)
    missing = wanted - len(have)
    if missing <= 0 or not have:
        return
    used = {n for row in have for n in row.get("cite") or []}
    unused = [s.number for s in made.sources if s.number not in used]
    asked = [str(row[key]) for row in have]
    # room for the missing share of the set, and some to spare
    most = max(1500, studio_kinds.MAX_TOKENS[item.kind] * (missing + 2) // max(wanted, 1))
    try:
        system = _system(item.kind, language, {**options, "count": missing}, unspaced)
        extra = await _complete_json(
            user, how, system + studio_kinds.more_prompt(item.kind, missing, asked, unused),
            prompt, most, db, item)
        if not extra or not isinstance(extra.get(group), list):
            return
        report = await _checked(db, user, how, item, extra, made, language)
    except Exception as exc:  # noqa: BLE001 — the set stays as it is
        log.info("studio %s top-up failed: %s", item.id, logs.safe(exc))
        return
    for name in ("script", "unbacked", "garbled", "fixed", "mended", "dropped"):
        checked[name] = int(checked.get(name) or 0) + int(report.get(name) or 0)
    checked["details"] = (list(checked.get("details") or [])
                          + list(report.get("details") or []))[:30]
    seen = {re.sub(r"\s+", "", a) for a in asked}
    fresh = [row for row in extra[group] if isinstance(row, dict)
             and re.sub(r"\s+", "", str(row.get(key) or "")) not in seen]
    # all of them: a malformed one is left out when the set is read, the rest trimmed after
    data[group] = [*(data.get(group) or []), *fresh]


def _add_usage(item: StudioItem, spent: tuple[int, int, int]) -> None:
    item.credits += spent[0]
    item.input_tokens += spent[1]
    item.output_tokens += spent[2]


async def _checked(db, user: User, how: shelf_calls.Route, item: StudioItem,
                   data: dict[str, Any], made: shelf_sources.Bundle,
                   language: str) -> dict[str, Any]:
    """`shelf_checks` over the writer's JSON (in place): script, grounding and fluency, one
    re-ask of what failed by the writer, the rest mended or dropped. Both calls billed."""
    texts = {f.id: f.text for f in await sources_for(db, user, item.project_id,
                                                     [s.file_id for s in made.sources])}
    by_number = {s.number: texts.get(s.file_id, "") for s in made.sources}
    names = {s.number: s.name for s in made.sources}
    try:
        reader = await shelf_calls.route(user, role="proofread")
    except shelf_calls.ShelfError:
        reader = None

    async def write(system: str, prompt: str, most: int) -> tuple[str, dict]:
        # an edit of a few sentences needs no thinking
        return await shelf_calls.complete(user, how.model, system,
                                          shelf_calls.outgoing(prompt, how), most)

    async def proofread(system: str, prompt: str, most: int) -> tuple[str, dict]:
        assert reader is not None
        return await shelf_calls.complete(user, reader.model, system,
                                          shelf_calls.outgoing(prompt, reader), most,
                                          effort=reader.effort)

    # the writer and the guide model proofread side by side: each misses what the other sees
    readers = [write] + ([proofread] if reader is not None
                         and reader.model["id"] != how.model["id"] else [])
    report = await shelf_checks.check(
        item.kind, data, language=language, by_number=by_number, names=names, write=write,
        proofread=readers)
    for role, usage in report.usage:
        model = reader.model if role == "proofread:1" and reader is not None else how.model
        _add_usage(item, shelf_calls.bill(db, user, model, usage, REASON))
    return {"script": report.script, "unbacked": report.unbacked, "garbled": report.garbled,
            "fixed": report.fixed, "mended": report.mended, "dropped": report.dropped,
            "details": report.details}


_CITE_AGAIN = ("\n\nYour previous answer cited no sources. Every statement taken from a source "
               "must end with its source number in square brackets, like [2].")

async def _save_document(db, user: User, item: StudioItem, doc: dict[str, Any],
                         sources: list[dict], fmt: str) -> None:
    """The output as an artifact in a conversation of its own, so it opens, edits and exports
    in the document panel like any report."""
    session = ChatSession(user_id=user.id, project_id=item.project_id, kind=SessionKind.report,
                          title=doc["title"][:200])
    db.add(session)
    await db.flush()
    data = studio_kinds.report_data(doc, sources, {"itemId": item.id, "kind": item.kind})
    artifact_id = await _store(db, user, session, item.project_id, doc["title"], data, fmt)
    session.artifact_id = artifact_id
    db.add(session)
    item.session_id = session.id
    item.artifact_id = artifact_id


async def _store(db, user: User, session: ChatSession, project_id: str, title: str,
                 data: dict[str, Any], fmt: str) -> str:
    if fmt in ("hwp", "docx"):
        from app.services import docx_doc, hwp_doc, report_office

        kind = ArtifactKind.hwp if fmt == "hwp" else ArtifactKind.docx
        blob = await report_office.write(kind, title, data)
        service = hwp_doc if fmt == "hwp" else docx_doc
        made = await service.create(db, user_id=user.id, title=title, data=blob,
                                    session_id=session.id, project_id=project_id)
        return made.id
    artifact = Artifact(user_id=user.id, session_id=session.id, project_id=project_id,
                        kind=ArtifactKind.report, title=title[:200], data=data)
    db.add(artifact)
    await db.flush()
    return artifact.id


async def save_sheet(db, user: User, item: StudioItem, fmt: str) -> tuple[str, str]:
    """A quiz as a printable sheet (questions, then the answer key): `(session, artifact)`."""
    questions = list((item.content or {}).get("questions") or [])
    if item.kind != "quiz" or not questions:
        raise shelf_calls.ShelfError("not_a_quiz", 422)
    language = str((item.content or {}).get("language") or "ko")
    title = item.title or kind_name("quiz", language)
    sections = studio_kinds.sheet_sections(title, questions, language)
    session = ChatSession(user_id=user.id, project_id=item.project_id, kind=SessionKind.report,
                          title=title[:200])
    db.add(session)
    await db.flush()
    data = studio_kinds.report_data({"title": title, "sections": sections},
                                    list((item.content or {}).get("sources") or []),
                                    {"itemId": item.id, "kind": "quiz-sheet"})
    artifact_id = await _store(db, user, session, item.project_id, title, data,
                               fmt if fmt in FORMATS else "web")
    session.artifact_id = artifact_id
    db.add(session)
    await db.commit()
    return session.id, artifact_id


def wire(item: StudioItem) -> dict[str, Any]:
    state = "failed" if shelf_jobs.stale(item) else item.state
    return {
        "id": item.id, "projectId": item.project_id, "kind": item.kind, "title": item.title,
        "state": state, "error": item.error if state == "failed" else "",
        "sourceIds": list(item.source_ids or []), "options": dict(item.options or {}),
        "content": item.content if state == "done" else None,
        "artifactId": item.artifact_id, "sessionId": item.session_id,
        "model": item.model, "credits": item.credits,
        "inputTokens": item.input_tokens, "outputTokens": item.output_tokens,
        "createdAt": item.created_at.isoformat(), "updatedAt": item.updated_at.isoformat(),
    }


__all__ = ["FORMATS", "REASON", "estimate", "options_of", "regenerate", "save_sheet",
           "source_budget", "sources_for", "start", "wire"]
