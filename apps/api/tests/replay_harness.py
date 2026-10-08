"""Replays a report or chat turn from recorded model calls, without a live model.

A case under `tests/fixtures/replay/<name>/` holds `input.json` (what the turn was given),
`calls.json` (every model call the turn made: its key, the pipeline stage that made it, a
short head of the prompt and the reply) and `golden.json` (the events the code produced
when the calls were recorded).

Every POST to `/chat/completions` goes through `Gateway`: recording, a responder answers and
the call is kept; replaying, the reply comes from `calls.json` by the call's key (model,
messages, max_tokens). A key that is not there is a prompt the code no longer makes the same
way — in a refactor that must not change behaviour, a regression — and the test fails on it.
Any other HTTP call fails as offline, recorded or not, so both runs see the same failure.
"""

from __future__ import annotations

import collections
import datetime as _datetime
import hashlib
import json
import sys
import types
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "replay"

Patch = Callable[[Any, str, Any], None]


def call_key(body: dict) -> str:
    kept = {k: body.get(k) for k in ("model", "messages", "max_tokens", "stream")}
    raw = json.dumps(kept, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def prompt_of(body: dict) -> str:
    """The last user message as text."""
    for message in reversed(body.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            return "\n".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        return str(content or "")
    return ""


def whole_prompt(body: dict) -> str:
    return "\n".join(
        str(m.get("content") or "") if not isinstance(m.get("content"), list)
        else "\n".join(str(p.get("text") or "") for p in m["content"] if isinstance(p, dict))
        for m in body.get("messages") or []
    )


def _completion(reply: str, usage: dict) -> dict:
    return {
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": reply}}],
        "usage": usage,
    }


class Gateway:
    """Answers model calls from a responder (recording) or from recorded calls (replay)."""

    def __init__(self, calls: list[dict] | None = None,
                 responder: Callable[[dict], tuple[str, str]] | None = None) -> None:
        self.responder = responder
        self.replies: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        for call in calls or []:
            self.replies[call["key"]].append(call)
        #: Calls made this run, in order: what `calls.json` holds after recording.
        self.made: list[dict] = []
        #: Replay only: prompts with no recorded reply.
        self.misses: list[str] = []

    async def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.endswith("/chat/completions"):
            self.made.append({"key": f"offline {request.method} {path}", "kind": "offline"})
            raise httpx.ConnectError("replay: offline", request=request)
        from app.pipeline.runner import CURRENT_STAGE

        body = json.loads(request.content or b"{}")
        key = call_key(body)
        prompt = prompt_of(body)
        stage = CURRENT_STAGE.get()
        if self.responder is not None:
            kind, reply = self.responder(body)
            usage = {"prompt_tokens": len(whole_prompt(body)) // 4 + 1,
                     "completion_tokens": len(reply) // 4 + 1}
            self.made.append({"key": key, "stage": stage, "kind": kind, "head": prompt[:160],
                              "reply": reply, "usage": usage})
        else:
            queue = self.replies.get(key)
            if not queue:
                self.misses.append(prompt[:300])
                raise httpx.ConnectError("replay: no recorded reply", request=request)
            call = queue.popleft()
            reply, usage = call["reply"], call["usage"]
            self.made.append({"key": key, "stage": stage, "kind": call.get("kind", "")})
        return httpx.Response(200, json=_completion(reply, usage), request=request)

    def install(self, patch: Patch) -> None:
        gateway = self

        async def send(client, request, **_kwargs):
            return await gateway.answer(request)

        patch(httpx.AsyncClient, "send", send)


# ── a fixed clock and fixed ids ─────────────────────────────────────────


def freeze(patch: Patch, now_iso: str) -> None:
    """Every `app` module sees `now_iso` as the current time and gets counted uuids."""
    # Loaded first, so their module globals are the ones patched.
    import app.routers.sessions  # noqa: F401
    import app.services.report  # noqa: F401

    now = _datetime.datetime.fromisoformat(now_iso)

    class FrozenDateTime(_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return now.astimezone(_datetime.UTC).replace(tzinfo=None)

        @classmethod
        def today(cls):
            return now.replace(tzinfo=None)

    class FrozenDate(_datetime.date):
        @classmethod
        def today(cls):
            return now.date()

    shim = types.ModuleType("datetime")
    shim.__dict__.update(_datetime.__dict__)
    shim.datetime = FrozenDateTime
    shim.date = FrozenDate
    for name, module in list(sys.modules.items()):
        if module is None or not (name.startswith("app.") or "/app/skills/" in str(
                getattr(module, "__file__", "") or "")):
            continue
        for attr, value in list(vars(module).items()):
            if value is _datetime:
                patch(module, attr, shim)
            elif value is _datetime.datetime:
                patch(module, attr, FrozenDateTime)
            elif value is _datetime.date:
                patch(module, attr, FrozenDate)

    counter = iter(range(1, 1 << 30))
    patch(uuid, "uuid4", lambda: uuid.UUID(int=next(counter)))


# ── fixtures ────────────────────────────────────────────────────────────


def load(case: str) -> tuple[dict, list[dict], Any]:
    root = FIXTURES / case
    return (json.loads((root / "input.json").read_text()),
            json.loads((root / "calls.json").read_text()),
            json.loads((root / "golden.json").read_text()))


def cases() -> list[str]:
    return sorted(p.name for p in FIXTURES.iterdir() if (p / "golden.json").exists())


# ── running a case ──────────────────────────────────────────────────────


async def run_report(case: dict) -> list[dict]:
    """Both passes of `report.write`, as `conftest.both_passes` drives them."""
    from app.services import report

    events: list[dict] = []
    kwargs = dict(case["write"])
    async for event in report.write(**kwargs):
        events.append(event)
    plan = next((e["plan"] for e in events if e["type"] == "proposal"), None)
    if plan is None:
        return events
    # The person approves the plan as shown; figures were declined on the card.
    approved = case.get("approved_plan") or plan
    async for event in report.write(**kwargs, approved_plan=approved, figures_plan=[]):
        events.append(event)
    return events


def sse_events(chunks: list[str]) -> list[dict]:
    out = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                try:
                    out.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    out.append({"raw": line[6:]})
    return out


async def run_chat(case: dict, patch: Patch) -> dict:
    """One chat turn through `sessions._run_turn`: the agent loop is the recorded answer (its
    tool outputs and the text it streamed); everything after it is the code under test."""
    from app.models.chat import ChatSession, Message
    from app.models.user import User
    from app.routers import sessions
    from app.services import agent

    user = User(id="replay-user", email="replay@example.test", password_hash="hash")
    session = ChatSession(id="replay-session", user_id=user.id, title="replay")
    added: list = []

    class Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            return session if model is ChatSession else user if model is User else None

        def add(self, row):
            added.append(row)

        async def delete(self, row):
            return None

        async def commit(self):
            return None

    turn = case["turn"]

    async def run_turn(model, messages, tools, ctx, **_kwargs):
        ctx.outputs.extend(turn.get("tool_outputs") or [])
        for step in turn.get("steps") or []:
            yield {"type": "step", **step}
        yield {"type": "delta", "text": turn["answer"]}
        yield {"type": "usage", "inputTokens": 100, "outputTokens": 50}

    async def nothing(*_args, **_kwargs):
        return None

    patch(sessions, "SessionLocal", Db)
    patch(agent, "run_turn", run_turn)
    patch(sessions.agent_service, "run_turn", run_turn)
    patch(sessions, "_store_artifacts", nothing)
    patch(sessions, "_enrich_memory", nothing)
    patch(sessions, "record_searches", lambda *_a, **_k: None)
    patch(sessions, "settle", lambda *_a, **_k: None)
    model = {"id": turn["model"], "label": turn["model"], "dataBoundary": "self_hosted",
             "strictLocal": True, "kinds": ["chat"], "creditCost": 0}
    chunks = [chunk async for chunk in sessions._run_turn(
        user_id=user.id, api_key="k", auto_memory=False, session_id=session.id, model=model,
        messages=turn["messages"], tools=[], first_user_message=turn["first_user_message"],
        is_first_turn=False, check_keys=tuple(turn.get("check_keys") or ()),
        check_facts=turn.get("check_facts") or "", check_material=turn.get("check_material") or "",
        judge_model=turn.get("judge_model") or "", agent_key=turn.get("agent_key") or "",
    )]
    stored = next((row for row in added if isinstance(row, Message)), None)
    events = [e for e in sse_events(chunks) if e.get("type") != "done"]
    return {
        "events": events,
        "stored": {
            "content": stored.content if stored else None,
            "steps": stored.steps if stored else None,
            "lengthCheck": (stored.routing or {}).get("lengthCheck") if stored else None,
        },
    }


async def run_revise(case: dict, patch: Patch) -> list[dict]:
    """One revision turn through `sessions._revise_document` on the recorded document."""
    from app.models.chat import ChatSession, Message, Role
    from app.models.user import User
    from app.models.workspace import Artifact, ArtifactKind
    from app.routers import sessions

    turn = case["revise"]
    user = User(id="replay-user", email="replay@example.test", password_hash="hash")
    session = ChatSession(id="replay-session", user_id=user.id, title="replay",
                          artifact_id="replay-artifact")
    artifact = Artifact(id="replay-artifact", user_id=user.id, kind=ArtifactKind.report,
                        title=turn["title"], data=turn["data"])
    asked = [Message(session_id=session.id, role=Role.user, content=turn["request"])]
    stored: list[dict] = []

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            return {ChatSession: session, User: user, Artifact: artifact}.get(model)

        async def exec(self, statement):
            return Rows(asked if "message" in str(statement).lower() else [])

        def add(self, row):
            if isinstance(row, Message):
                stored.append({"content": row.content, "steps": row.steps})

        async def flush(self):
            return None

        async def commit(self):
            return None

    async def store_document(db, session, **kwargs):
        stored.append({"sections": kwargs["data"].get("sections")})
        return "replay-artifact-2"

    patch(sessions, "SessionLocal", Db)
    patch(sessions, "_store_document", store_document)
    patch(sessions, "settle", lambda *_a, **_k: None)
    model = {"id": turn["model"], "label": turn["model"], "dataBoundary": "self_hosted",
             "strictLocal": True, "kinds": ["report"], "creditCost": 0}
    chunks = [chunk async for chunk in sessions._revise_document(
        user_id=user.id, api_key="k", session_id=session.id, model=model,
        instruction=turn["instruction"],
        planner={"id": turn["planner"]} if turn.get("planner") else None,
    )]
    return [*sse_events(chunks), {"type": "stored", "rows": stored}]
