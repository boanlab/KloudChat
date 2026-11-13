"""지식 서가 studio: one-click outputs grounded in the sources the person picked.

`start` checks the request, writes a `running` row and returns; `_run` reads the sources
(numbered, within the writer's window), asks the model (`shelf_calls.route`: strict-local by
default and always for internal data while the administrator says so), turns the answer
into its kind (`studio_kinds`) and saves it: documents as an artifact in a conversation of
their own (a web report, or an HWP/Word file through the report path) so they open in the
document panel, quizzes and flashcards as item sets the player reads, an audio overview as a
two-speaker script that speech may be made from (`start_speech`).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from sqlmodel import col, select

from app.core import db as core_db
from app.core import logs
from app.core.config import settings
from app.models.chat import ChatSession, SessionKind
from app.models.shelf import StudioItem
from app.models.user import User, utcnow
from app.models.workspace import Artifact, ArtifactKind, Project, StoredFile
from app.services import audiogen, shelf_calls, shelf_jobs, shelf_sources, studio_kinds
from app.services import models as model_service
from app.services.credits import charge_for_tokens, settle
from app.services.shelf_names import kind_name

log = logging.getLogger(__name__)

REASON = "knowledge.studio"
SPEECH_REASON = "knowledge.speech"
FORMATS = ("web", "hwp", "docx")
#: the writer's window share the sources may take, in chars (~2.5 a token)
_WINDOW_SHARE = 0.55
_CHARS_PER_TOKEN = 2.5
_MAX_SOURCE_CHARS = 160_000
_MIN_SOURCE_CHARS = 24_000
#: pause between two speakers' lines (seconds)
_GAP = 0.35
#: voices: host, explainer
VOICES = {"A": "nova", "B": "onyx"}


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
    if kind == "audio":
        length = str(raw.get("length") or "standard")
        out["length"] = length if length in studio_kinds.AUDIO_LENGTH else "standard"
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
    how = await shelf_calls.route(user, asked=options.get("model"))
    made = shelf_sources.bundle(files, source_budget(how.model))
    system = studio_kinds.system_prompt(kind, "ko", options)
    input_tokens = shelf_calls.tokens_of(system) + shelf_calls.tokens_of(made.text)
    output_tokens = int(studio_kinds.MAX_TOKENS[kind] * studio_kinds.TYPICAL_SHARE)
    out = {
        "kind": kind,
        "sources": len(files),
        "partial": made.partial,
        "model": how.model["id"],
        "modelLabel": how.model.get("label") or how.model["id"],
        "strict": how.strict,
        "required": how.required,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "credits": shelf_calls.estimate(how.model, input_tokens, output_tokens),
        "maxCredits": shelf_calls.estimate(how.model, input_tokens,
                                           studio_kinds.MAX_TOKENS[kind]),
    }
    if kind == "audio":
        out["speech"] = await speech_plan(user, options)
    return out


async def start(db, user: User, project: Project, kind: str, ids: list[str] | None,
                options: dict[str, Any], *, title: str = "") -> StudioItem:
    files = await sources_for(db, user, project.id, ids)
    if not files:
        raise shelf_calls.ShelfError("no_sources", 422)
    how = await shelf_calls.route(user, asked=options.get("model"))
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


async def _complete_json(user: User, how: shelf_calls.Route, system: str, prompt: str,
                         max_tokens: int, db) -> tuple[dict[str, Any] | None, int, int, int]:
    """Ask once, and once more when the answer is not readable JSON; both billed."""
    credits = spent_in = spent_out = 0
    for attempt in range(2):
        extra = "" if attempt == 0 else (
            "\n\nYour previous answer was not a readable JSON object. Answer again with the "
            "JSON object only.")
        answer, usage = await shelf_calls.complete(user, how.model, system + extra, prompt,
                                                   max_tokens)
        c, i, o = shelf_calls.bill(db, user, how.model, usage, REASON)
        credits, spent_in, spent_out = credits + c, spent_in + i, spent_out + o
        data = shelf_jobs.parse_json(answer)
        if data is not None:
            return data, credits, spent_in, spent_out
    return None, credits, spent_in, spent_out


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
    how = await shelf_calls.route(user, asked=options.get("model"))
    made = shelf_sources.bundle(files, source_budget(how.model))
    language = studio_kinds.language_of(made.text, str(options.get("language") or "auto"))
    system = studio_kinds.system_prompt(item.kind, language, options)
    prompt = shelf_calls.outgoing(
        studio_kinds.user_prompt(made.text, [s.name for s in made.sources]), how)
    data, credits, spent_in, spent_out = await _complete_json(
        user, how, system, prompt, studio_kinds.MAX_TOKENS[item.kind], db)
    item.model = str(how.model["id"])
    item.credits, item.input_tokens, item.output_tokens = credits, spent_in, spent_out
    if data is None:
        raise ValueError("answer unreadable")
    count = len(made.sources)
    sources = made.wire()
    title = shelf_jobs.clip(data.get("title"), 160) or kind_name(item.kind, language)
    item.title = title[:200]
    content: dict[str, Any] = {"language": language, "sources": sources,
                               "partial": made.partial, "strict": how.strict}
    if item.kind in studio_kinds.DOCUMENTS:
        doc = studio_kinds.document(item.kind, data, count, language)
        if doc is not None and studio_kinds.cites_nothing(item.kind, doc):
            # the writer left out the source numbers: asked once more, the better one kept
            again, c, i, o = await _complete_json(
                user, how, system + _CITE_AGAIN, prompt, studio_kinds.MAX_TOKENS[item.kind], db)
            item.credits += c
            item.input_tokens += i
            item.output_tokens += o
            redo = studio_kinds.document(item.kind, again, count, language) if again else None
            if redo is not None and not studio_kinds.cites_nothing(item.kind, redo):
                doc = redo
        if doc is None:
            raise ValueError("empty document")
        content.update(doc)
        if language == "ko":
            content = await _korean_only(user, how, content, db, item)
            doc = {"title": content["title"], "sections": content["sections"]}
        item.content = content
        await _save_document(db, user, item, doc, sources, str(options.get("format") or "web"))
        return
    elif item.kind == "quiz":
        questions = studio_kinds.quiz(data, count, random.Random())
        if len(questions) < 3:
            raise ValueError("too few usable questions")
        content["questions"] = questions
        item.content = content
    elif item.kind == "flashcards":
        found = studio_kinds.cards(data, count)
        if len(found) < 3:
            raise ValueError("too few usable cards")
        content["cards"] = found
        item.content = content
    elif item.kind == "audio":
        lines = studio_kinds.script(data)
        if len(lines) < 4:
            raise ValueError("script too short")
        content["lines"] = lines
        item.content = content
        item.speech_state = "idle"
        item.speech_total = len(lines)
    if language == "ko" and item.content:
        item.content = await _korean_only(user, how, item.content, db, item)


_CITE_AGAIN = ("\n\nYour previous answer cited no sources. Every statement taken from a source "
               "must end with its source number in square brackets, like [2].")

#: joins the texts of one mend call; a line of its own, never inside a sentence
_SEP = "\n\u241e\n"
#: the fields of an output that are prose a reader sees
_PROSE = ("title", "content", "question", "explanation", "front", "back", "text", "answer")


def _prose_slots(content: dict[str, Any]) -> list[tuple[dict, str]]:
    slots: list[tuple[dict, str]] = []
    if isinstance(content.get("title"), str):
        slots.append((content, "title"))
    for group in ("sections", "questions", "cards", "lines"):
        for row in content.get(group) or []:
            for key in _PROSE:
                if isinstance(row, dict) and isinstance(row.get(key), str):
                    slots.append((row, key))
            for i, choice in enumerate(row.get("choices") or [] if isinstance(row, dict) else []):
                if isinstance(choice, str):
                    slots.append((row["choices"], i))  # type: ignore[arg-type]
    return slots


async def _korean_only(user: User, how: shelf_calls.Route, content: dict[str, Any], db,
                       item: StudioItem) -> dict[str, Any]:
    """Chinese words slipped into Korean output (a self-hosted writer's habit) said in Korean:
    the sentences holding them rewritten in one call, else read in Hangul (`stray_han`)."""
    from app.services import stray_han

    slots = _prose_slots(content)
    joined = _SEP.join(str(holder[key]) for holder, key in slots)
    if not stray_han.strays(joined):
        return content
    spent: dict = {}

    async def complete(model, messages, api_key, max_tokens):
        return await shelf_calls.complete(user, how.model, "You correct Korean text.",
                                          messages[0]["content"], max_tokens)

    mended, _ = await stray_han.mend(joined, complete=complete, model=str(how.model["id"]),
                                     usage=spent)
    if spent:
        c, i, o = shelf_calls.bill(db, user, how.model, spent, REASON)
        item.credits += c
        item.input_tokens += i
        item.output_tokens += o
    parts = mended.split(_SEP)
    if len(parts) == len(slots):
        for (holder, key), text in zip(slots, parts, strict=True):
            holder[key] = text
    return content


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


# ── speech ────────────────────────────────────────────────────────────


def _tts_models(catalogue: list[dict]) -> tuple[dict | None, dict | None]:
    """`(local, outside)` speech models: a strict-local one named for speech, and the outside
    narration model the audio surface uses (`gpt-audio`)."""
    speech = [m for m in catalogue if "av" in (m.get("kinds") or [])]
    local = next((m for m in speech if shelf_calls.is_strict(m) and any(
        w in str(m.get("id")).lower() for w in ("tts", "speech", "voice"))), None)
    outside = next((m for m in speech if "gpt-audio" in str(m.get("id"))
                    and not shelf_calls.is_strict(m)), None)
    return local, outside


async def speech_plan(user: User, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Whether speech can be made for this account and where: `local` (strict-local TTS),
    `outside` (an outside model; needs consent, off while internal data must stay inside),
    or `none` with the reason."""
    rules = await shelf_calls.policy()
    catalogue = (await model_service.list_models_for_egress()).get("models") or []
    allowed = set(user.allowed_models or [])
    catalogue = [m for m in catalogue if not allowed or m.get("id") in allowed]
    local, outside = _tts_models(catalogue)
    chars = studio_kinds.AUDIO_LENGTH.get(str((options or {}).get("length") or "standard"),
                                          studio_kinds.AUDIO_LENGTH["standard"])[0]
    if local is not None:
        return {"mode": "local", "model": local["id"], "credits": 0}
    if outside is None:
        return {"mode": "none", "reason": "no_speech_model"}
    if rules.internal_data_strict_local:
        return {"mode": "none", "reason": "strict_local_no_tts", "outsideModel": outside["id"]}
    return {"mode": "outside", "model": outside["id"],
            "credits": _speech_credits(outside, chars)}


def _speech_credits(model: dict, chars: int) -> int:
    # spoken Korean ~5 chars a second; audio output ~ 25 tokens a second, the text read in
    seconds = chars / 5
    return charge_for_tokens(model, int(chars / 2), int(seconds * 25)) or int(
        model.get("creditPerCall") or 0)


async def start_speech(db, user: User, item: StudioItem, *, consent: bool) -> StudioItem:
    lines = list((item.content or {}).get("lines") or [])
    if item.kind != "audio" or not lines:
        raise shelf_calls.ShelfError("not_an_audio_script", 422)
    if item.speech_state == "running" and not shelf_jobs.stale(
            _SpeechView(item.speech_state, item.updated_at)):
        return item
    plan = await speech_plan(user, item.options)
    if plan["mode"] == "none":
        raise shelf_calls.ShelfError(plan["reason"])
    if plan["mode"] == "outside" and not consent:
        raise shelf_calls.ShelfError("speech_consent_required")
    item.speech_state = "running"
    item.speech_done = 0
    item.speech_total = len(lines)
    item.updated_at = utcnow()
    db.add(item)
    await db.commit()
    await db.refresh(item)
    shelf_jobs.spawn(_speak(item.id, plan["model"]))
    return item


class _SpeechView:
    """A row view `shelf_jobs.stale` reads for the speech job."""

    def __init__(self, state: str, updated_at) -> None:
        self.state = state
        self.updated_at = updated_at


def speech_state(item: StudioItem) -> str:
    if item.speech_state == "running" and shelf_jobs.stale(
            _SpeechView(item.speech_state, item.updated_at)):
        return "failed"
    return item.speech_state


def _pcm(wav: bytes) -> bytes:
    return wav[44:] if wav[:4] == b"RIFF" else wav


async def _speak(item_id: str, model_id: str) -> None:
    async with core_db.SessionLocal() as db:
        item = await db.get(StudioItem, item_id)
        user = await db.get(User, item.user_id) if item else None
        if item is None or user is None:
            return
        try:
            catalogue = (await model_service.list_models_for_egress()).get("models") or []
            model = model_service.find(catalogue, model_id)
            if model is None:
                raise shelf_calls.ShelfError("no_speech_model", 503)
            how = shelf_calls.Route(model, shelf_calls.is_strict(model), False,
                                    not shelf_calls.is_strict(model))
            from app.services import litellm as litellm_service

            await litellm_service.ensure_key(user)
            base_url, api_key = await litellm_service.credentials_for(user)
            lines = list((item.content or {}).get("lines") or [])
            pieces: list[bytes] = []
            spent_in = spent_out = 0
            gap = b"\x00\x00" * int(24_000 * _GAP)
            for n, line in enumerate(lines):
                text = shelf_calls.outgoing(line["text"], how)
                clip = await audiogen.generate(
                    base_url=base_url, api_key=api_key, model=model["id"],
                    prompt=("Read the following text aloud exactly as written, in a natural "
                            f"conversational voice, adding nothing:\n{text}"),
                    speech=True, voice=VOICES.get(line["speaker"], "alloy"))
                pieces.append(_pcm(clip.data))
                pieces.append(gap)
                spent_in += clip.input_tokens
                spent_out += clip.output_tokens
                item.speech_done = n + 1
                item.updated_at = utcnow()
                db.add(item)
                await db.commit()
                await asyncio.sleep(0)
            wav = audiogen._wav(b"".join(pieces))
            generated = audiogen.GeneratedAudio(wav, "audio/wav", "wav", spent_in, spent_out,
                                                studio_kinds.script_text(lines))
            file_id, key = audiogen.store(user.id, generated)
            db.add(StoredFile(id=file_id, user_id=user.id, origin="generated",
                              name=f"{(item.title or 'audio')[:40]}.wav", mime="audio/wav",
                              size=len(wav), storage_key=key, tokens=0))
            artifact = Artifact(
                user_id=user.id, project_id=item.project_id, kind=ArtifactKind.audio,
                title=(item.title or kind_name("audio"))[:200],
                data={"kind": "audio", "jobId": None, "prompt": item.title,
                      "audioKind": "narration", "voice": "two", "requestedSec": 0,
                      "durationSec": audiogen.duration_seconds(generated),
                      "model": model["id"], "transcript": generated.transcript, "waveform": [],
                      "src": f"{settings.api_prefix}/files/{file_id}/content",
                      "studio": {"itemId": item.id, "kind": "audio"}})
            db.add(artifact)
            await db.flush()
            credits = charge_for_tokens(model, spent_in, spent_out)
            if credits:
                settle(db, user, credits, reason=SPEECH_REASON, model=model["id"])
            item.credits = int(item.credits or 0) + credits
            item.audio_file_id = file_id
            item.artifact_id = artifact.id
            item.speech_state = "done"
        except Exception as exc:  # noqa: BLE001 — the script stays; speech can be asked again
            await db.rollback()
            item = await db.get(StudioItem, item_id)
            if item is None:
                return
            log.warning("speech for %s failed: %s", item_id, logs.safe(exc))
            item.speech_state = "failed"
            item.error = (exc.code if isinstance(exc, shelf_calls.ShelfError)
                          else "speech_failed")[:200]
        item.updated_at = utcnow()
        db.add(item)
        await db.commit()


def wire(item: StudioItem) -> dict[str, Any]:
    state = "failed" if shelf_jobs.stale(item) else item.state
    return {
        "id": item.id, "projectId": item.project_id, "kind": item.kind, "title": item.title,
        "state": state, "error": item.error if state == "failed" or
        item.speech_state == "failed" else "",
        "sourceIds": list(item.source_ids or []), "options": dict(item.options or {}),
        "content": item.content if state == "done" else None,
        "artifactId": item.artifact_id, "sessionId": item.session_id,
        "audioFileId": item.audio_file_id,
        "speech": {"state": speech_state(item), "done": item.speech_done,
                   "total": item.speech_total} if item.kind == "audio" else None,
        "model": item.model, "credits": item.credits,
        "inputTokens": item.input_tokens, "outputTokens": item.output_tokens,
        "createdAt": item.created_at.isoformat(), "updatedAt": item.updated_at.isoformat(),
    }


__all__ = ["FORMATS", "REASON", "estimate", "options_of", "regenerate", "save_sheet",
           "source_budget", "sources_for", "speech_plan", "start", "start_speech", "wire"]
