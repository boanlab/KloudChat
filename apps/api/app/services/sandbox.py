"""The code sandbox (LibreCodeInterpreter): uploads, runs, and the files a run leaves behind.

Every upload opens a sandbox session of its own and its refs live 24 hours. A turn
keeps its state on `ToolContext.sandbox`: the exec session the first run opened
(later runs continue it, so `outputs/` and variables carry over) and the refs of
the chat files it uploaded. Skill packages are uploaded read-only once per process
and package version, and uploaded again once their refs near expiry.

Outputs are what a run writes under `outputs/`; they are downloaded and stored as
the user's files in the chat session, and the tool result lists their ids.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import httpx
from sqlmodel import col, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.workspace import StoredFile
from app.services import file_schema, settings_store, tools_gateway
from app.services import files as file_service
from app.services.skill_packages import SkillPackage

log = logging.getLogger(__name__)

OUTPUT_DIR = "outputs/"
#: The sandbox returns at most this many new files per run and drops the rest silently.
MAX_OUTPUTS = 10
MAX_INPUT_FILES = 20
MAX_INPUT_BYTES = 80 * 1024 * 1024
MAX_OUTPUT_BYTES = 60 * 1024 * 1024
#: Refs live 24 h on the sandbox; renewed well before.
SKILL_REF_TTL_SEC = 20 * 3600
TIMEOUT_MS = 30_000

MEMORY_LIMIT = (
    "실행 중 메모리 한도(1GB)를 넘어 중단됐습니다. 파일을 나누거나 필요한 열만 읽어 "
    "다시 시도하세요."
)
TIME_LIMIT = "실행 시간 한도(30초)를 넘어 중단됐습니다. 처리할 양을 줄이거나 나눠서 실행하세요."
#: What the sandbox says when the kernel killed a run over its memory cgroup.
_OOM_MARKER = "delimiter not found"
_TIMEOUT_MARKER = "Execution exceeded"


class SandboxError(RuntimeError):
    """The sandbox could not be reached or refused the request; the message is user-facing."""


@dataclass(slots=True)
class RunResult:
    stdout: str
    stderr: str
    session_id: str
    files: list[dict[str, Any]] = field(default_factory=list)
    memory_exceeded: bool = False
    timed_out: bool = False

    @property
    def generated(self) -> list[dict[str, Any]]:
        return [f for f in self.files if not f.get("inherited")]

    @property
    def crashed(self) -> bool:
        """An exception, a kill or a timeout, as opposed to warnings on stderr."""
        return (
            self.memory_exceeded
            or self.timed_out
            or "Traceback (most recent call last)" in self.stderr
        )


def interpret(payload: dict[str, Any]) -> RunResult:
    stderr = str(payload.get("stderr") or "")
    memory = _OOM_MARKER in stderr
    timed_out = _TIMEOUT_MARKER in stderr
    if memory:
        stderr = MEMORY_LIMIT
    elif timed_out:
        stderr = f"{TIME_LIMIT}\n{stderr.strip()[-500:]}"
    return RunResult(
        stdout=str(payload.get("stdout") or ""),
        stderr=stderr,
        session_id=str(payload.get("session_id") or ""),
        files=[f for f in payload.get("files") or [] if isinstance(f, dict)],
        memory_exceeded=memory,
        timed_out=timed_out,
    )


class Client:
    """HTTP client for one sandbox base URL (the gateway path injects the key itself)."""

    def __init__(self, base: str, api_key: str = "", timeout: float = 120.0) -> None:
        self.base = base.rstrip("/")
        self.headers = {"x-api-key": api_key} if api_key else {}
        self.timeout = httpx.Timeout(timeout, connect=10.0)

    async def upload(
        self, items: list[tuple[str, bytes]], *, kind: str | None = None, read_only: bool = False
    ) -> list[dict[str, str]]:
        """One new sandbox session holding `items`; returns the refs `exec` takes."""
        multipart = [
            ("file", (name, data, mimetypes.guess_type(name)[0] or "application/octet-stream"))
            for name, data in items
        ]
        form = {}
        if kind:
            form["kind"] = kind
        if read_only:
            form["read_only"] = "true"
        try:
            async with tools_gateway.client(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base}/upload/batch", headers=self.headers, files=multipart, data=form
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SandboxError(
                f"샌드박스에 파일을 올리지 못했습니다 ({type(exc).__name__})."
            ) from exc
        session = str(payload.get("session_id") or "")
        refs = [
            {"id": str(f["fileId"]), "name": str(f["filename"]), "session_id": session}
            for f in payload.get("files") or []
            if f.get("status", "success") == "success" and f.get("fileId")
        ]
        failed = sum(1 for f in payload.get("files") or [] if f.get("status") == "error")
        if failed:
            log.warning("sandbox refused %d upload(s)", failed)
        return refs

    async def exec(
        self,
        code: str,
        *,
        lang: str = "py",
        files: list[dict[str, str]] | None = None,
        session_id: str | None = None,
        timeout_ms: int = TIMEOUT_MS,
    ) -> RunResult:
        body: dict[str, Any] = {"code": code, "lang": lang, "timeout": timeout_ms}
        if files:
            body["files"] = files
        if session_id:
            body["session_id"] = session_id
        try:
            async with tools_gateway.client(timeout=self.timeout) as client:
                response = await client.post(f"{self.base}/exec", headers=self.headers, json=body)
                response.raise_for_status()
                # A long run is preceded by keep-alive spaces; json.loads skips them.
                payload = json.loads(response.text)
        except (httpx.HTTPError, ValueError) as exc:
            raise SandboxError(f"코드 실행에 실패했습니다 ({type(exc).__name__}).") from exc
        return interpret(payload)

    async def list(self, session_id: str) -> list[dict[str, Any]]:
        try:
            async with tools_gateway.client(timeout=self.timeout) as client:
                response = await client.get(f"{self.base}/files/{session_id}", headers=self.headers)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SandboxError(
                f"샌드박스 파일 목록을 읽지 못했습니다 ({type(exc).__name__})."
            ) from exc
        return payload if isinstance(payload, list) else []

    async def download(self, session_id: str, file_id: str) -> bytes:
        try:
            async with tools_gateway.client(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base}/download/{session_id}/{file_id}", headers=self.headers
                )
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            raise SandboxError(f"결과 파일을 내려받지 못했습니다 ({type(exc).__name__}).") from exc


async def client() -> Client | None:
    """The configured sandbox, or None when code execution is off."""
    backends = await settings_store.tools_config()
    if not backends.exec:
        return None
    return Client(backends.exec, settings.code_interpreter_api_key)


# ── the chat's files ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InputFile:
    id: str
    name: str
    size: int
    storage_key: str


async def session_files(user_id: str, session_id: str) -> list[InputFile]:
    """This chat's files for this user, newest first: uploads and earlier outputs."""
    if not session_id:
        return []
    async with SessionLocal() as db:
        rows = (
            await db.exec(
                select(StoredFile)
                .where(StoredFile.user_id == user_id, StoredFile.session_id == session_id)
                .order_by(col(StoredFile.created_at).desc())
            )
        ).all()
    return [InputFile(r.id, r.name, r.size, r.storage_key) for r in rows if r.storage_key]


def read_input(file: InputFile) -> bytes:
    return file_service.read_blob(file.storage_key)


async def store_output(user_id: str, session_id: str, name: str, data: bytes) -> dict[str, Any]:
    """Keeps a produced file as the user's own, in the chat it came from."""
    stored = StoredFile(
        user_id=user_id,
        session_id=session_id or None,
        name=file_service.safe_name(name),
        size=len(data),
        mime=file_service.detected_mime(name, None, data),
    )
    stored.storage_key = file_service.write_blob(user_id, stored.id, stored.name, data)
    try:
        stored.text = await file_service.text_of(stored.name, stored.mime, data)
        stored.tokens = file_service.estimate_tokens(stored.text)
    except Exception as exc:  # noqa: BLE001 — the file is kept even when unreadable
        stored.error = str(exc)[:500]
    async with SessionLocal() as db:
        db.add(stored)
        await db.commit()
    return {"id": stored.id, "name": stored.name, "size": stored.size}


def pick_inputs(
    files: list[InputFile], wanted: list[str] | None
) -> tuple[list[InputFile], list[str]]:
    """The files a run gets, one per name (newest wins), and the wanted names not found."""
    by_name: dict[str, InputFile] = {}
    for file in files:
        by_name.setdefault(file.name, file)
    if wanted:
        chosen, missing = [], []
        for name in wanted:
            key = PurePosixPath(str(name)).name
            if key in by_name:
                chosen.append(by_name[key])
            else:
                missing.append(str(name))
        return list(dict.fromkeys(chosen)), missing
    chosen, total = [], 0
    for file in by_name.values():
        if len(chosen) >= MAX_INPUT_FILES or total + file.size > MAX_INPUT_BYTES:
            break
        chosen.append(file)
        total += file.size
    return chosen, []


async def input_refs(sandbox: Client, state: dict[str, Any], files: list[InputFile]) -> list[dict]:
    """Refs for `files`, uploading the ones this turn has not uploaded yet."""
    known: dict[str, dict] = state.setdefault("inputs", {})
    fresh = [f for f in files if f.id not in known]
    if fresh:
        refs = await sandbox.upload([(f.name, read_input(f)) for f in fresh])
        by_name = {ref["name"]: ref for ref in refs}
        for file in fresh:
            if file.name in by_name:
                known[file.id] = by_name[file.name]
    return [known[f.id] for f in files if f.id in known]


_SKILL_REFS: dict[str, tuple[str, float, list[dict]]] = {}


async def skill_refs(sandbox: Client, package: SkillPackage, *, fresh: bool = False) -> list[dict]:
    """The package's files mounted read-only under `skills/<key>/`."""
    fingerprint = package.fingerprint
    cached = _SKILL_REFS.get(package.key)
    if (
        not fresh
        and cached
        and cached[0] == fingerprint
        and time.monotonic() - cached[1] < SKILL_REF_TTL_SEC
    ):
        return cached[2]
    refs = await sandbox.upload(package.upload_files(), kind="skill", read_only=True)
    _SKILL_REFS[package.key] = (fingerprint, time.monotonic(), refs)
    return refs


async def own_refs(sandbox: Client, state: dict[str, Any], folder: str,
                   files: dict[str, str]) -> list[dict]:
    """A person's own skill scripts mounted read-only under `skills/<folder>/scripts/`, once
    per turn. They run here only, at `run_code`'s trust; the server never imports them."""
    cache = state.setdefault("own_refs", {})
    if folder not in cache:
        cache[folder] = await sandbox.upload(
            [(f"skills/{folder}/scripts/{name}.py", source.encode("utf-8"))
             for name, source in sorted(files.items())],
            kind="skill", read_only=True)
    return cache[folder]


async def run(
    sandbox: Client,
    state: dict[str, Any],
    code: str,
    *,
    lang: str = "py",
    refs: list[dict] | None = None,
) -> RunResult:
    """Runs in the turn's sandbox session, opening one on the first call."""
    session = state.get("session")
    try:
        result = await sandbox.exec(code, lang=lang, files=refs or None, session_id=session)
    except SandboxError:
        if not session:
            raise
        # An expired or reaped session: start a new one with the same files.
        state.pop("session", None)
        result = await sandbox.exec(code, lang=lang, files=refs or None)
    if result.session_id:
        state["session"] = result.session_id
    return result


async def collect_outputs(
    sandbox: Client, result: RunResult, *, user_id: str, session_id: str, state: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Downloads and stores what the run wrote under `outputs/`; also returns warnings."""
    notes: list[str] = []
    generated = result.generated
    if len(generated) >= MAX_OUTPUTS:
        notes.append(
            f"샌드박스는 한 번에 파일 {MAX_OUTPUTS}개까지만 돌려줍니다. 더 만들었다면 일부가 "
            "빠졌을 수 있습니다: zip 하나나 시트가 여러 개인 엑셀 하나로 묶으세요."
        )
    seen: dict[str, str] = state.setdefault("outputs", {})
    stored: list[dict[str, Any]] = []
    for item in generated:
        path = str(item.get("name") or "")
        if not path.startswith(OUTPUT_DIR):
            continue
        version = f"{item.get('session_id')}/{item.get('id')}"
        if seen.get(path) == version:
            continue
        owner = str(item.get("session_id") or result.session_id)
        data = await sandbox.download(owner, str(item["id"]))
        if len(data) > MAX_OUTPUT_BYTES:
            notes.append(f"{path} 은(는) 너무 커서 보관하지 않았습니다.")
            continue
        name = PurePosixPath(path).name
        row = await store_output(user_id, session_id, name, data)
        row["path"] = path
        check = file_schema.check_output(name, data)
        if check:
            row["check"] = check
        seen[path] = version
        stored.append(row)
    return stored, notes


def last_json(stdout: str) -> dict[str, Any] | None:
    """The JSON object a script printed on its last non-empty line."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        if line.startswith("{") and line.endswith("}"):
            try:
                value = json.loads(line)
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
        return None
    return None


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit // 2] + "\n…(중략)…\n" + text[-limit // 2 :]


def report(
    result: RunResult,
    outputs: list[dict[str, Any]],
    notes: list[str],
    *,
    summary: dict[str, Any] | None = None,
    stdout: str | None = None,
    head: str = "",
) -> str:
    """The tool result the model reads: outcome, output files with checks, printed text."""
    parts = [head] if head else []
    if outputs:
        listed = []
        for row in outputs:
            line = f"- {row['name']} ({row['size']:,} bytes, 파일 id {row['id']})"
            if row.get("check"):
                line += f" — 검사: {row['check']}"
            listed.append(line)
        parts.append(
            "만든 파일(사용자 화면에 내려받기 단추로 붙어 있습니다. 링크를 지어내지 마세요):\n"
            + "\n".join(listed)
        )
    if summary is not None:
        parts.append("요약(JSON):\n" + _clip(json.dumps(summary, ensure_ascii=False), 4000))
    text = (stdout if stdout is not None else result.stdout).strip()
    if text:
        parts.append("stdout:\n" + _clip(text, 4000))
    if result.stderr.strip():
        parts.append("stderr:\n" + _clip(result.stderr, 2000))
    parts += notes
    if len(parts) == (1 if head else 0):
        parts.append("실행되었지만 출력도 파일도 없습니다.")
    return "\n\n".join(parts)
