"""Minimal MCP client — JSON-RPC over stdio or streamable HTTP.

Hand-written rather than taken from the SDK: only `initialize`, `tools/list`
and `tools/call` are needed, plus per-caller process isolation that the SDK's
session model does not express directly.

**Tenancy.** A stdio server is spawned per call with the caller's substituted
environment (`{{USER_ID}}`, `{{USER_EMAIL}}`). A shared long-lived process
would answer one person's question with another's credentials, so the spawn
cost is accepted.

**Trust.** Server output is data. It reaches the agent loop as a `tool` message,
never as an instruction — see `services/agent.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
from typing import Any

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "kchat", "version": "0.1.0"}


class McpError(RuntimeError):
    pass


_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def substitute(env: dict[str, str] | None, *, user_id: str, user_email: str) -> dict[str, str]:
    """Resolves a connector's environment for one caller.

    Two substitutions of different kinds:

    * `{{USER_ID}}` / `{{USER_EMAIL}}` — the tenancy boundary. A server that
      reports "my usage" gets the caller's identity here and nowhere else.
    * `${LITELLM_MASTER_KEY}` — deployment secrets, read from the API's own
      environment at spawn time rather than stored on the row, so a rotation
      does not mean reinstalling every connector.
    """
    values = {
        "{{USER_ID}}": user_id,
        "{{USER_EMAIL}}": user_email,
    }
    out: dict[str, str] = {}
    for key, raw in (env or {}).items():
        text = str(raw)
        for token, value in values.items():
            text = text.replace(token, value)
        # Unset becomes empty, not a literal `${FOO}` a server would treat as
        # a real value.
        text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
        out[key] = text
    return out


# ── stdio ──────────────────────────────────────────────────────────────


class _StdioSession:
    """One server process for the life of a `with` block."""

    def __init__(self, command: str, env: dict[str, str]):
        self.command = command
        # Inherit PATH etc.; the server's own vars win.
        self.env = {**os.environ, **env}
        self.proc: asyncio.subprocess.Process | None = None
        self._id = 0

    async def __aenter__(self) -> _StdioSession:
        argv = shlex.split(self.command)
        if not argv:
            raise McpError("빈 실행 명령입니다.")
        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        await self._request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        )
        await self._notify("notifications/initialized")
        return self

    async def __aexit__(self, *_exc) -> None:
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except (ProcessLookupError, TimeoutError):
            # A server ignoring SIGTERM would leak for the life of the API.
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()

    async def _send(self, message: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        assert self.proc is not None and self.proc.stdout is not None
        self._id += 1
        request_id = self._id
        await self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )

        while True:
            line = await self.proc.stdout.readline()
            if not line:
                stderr = b""
                if self.proc.stderr is not None:
                    stderr = await self.proc.stderr.read()
                raise McpError(
                    f"서버가 응답 없이 종료했습니다: {stderr.decode(errors='replace')[:300]}"
                )
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                # Servers print banners to stdout before speaking JSON-RPC.
                continue
            if message.get("id") != request_id:
                continue  # a notification or an out-of-band response
            if "error" in message:
                raise McpError(str(message["error"].get("message") or message["error"]))
            return message.get("result")


# ── http / sse ─────────────────────────────────────────────────────────


async def _http_request(url: str, method: str, params: dict[str, Any] | None = None) -> Any:
    """One MCP call over streamable HTTP, handshake included.

    The transport is stateful: a server may hand back an `Mcp-Session-Id` on
    `initialize` and reject anything that arrives without it. Skipping the
    handshake and POSTing `tools/list` straight at the endpoint gets a 400 from
    every server that enforces it, which reads as a broken connector.
    """
    async with httpx.AsyncClient(timeout=settings.tool_timeout_sec) as client:
        headers = {"Accept": "application/json, text/event-stream"}

        init = await client.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "kchat", "version": "1"},
                },
            },
            headers=headers,
        )
        init.raise_for_status()
        _parse(init.text)

        session = init.headers.get("mcp-session-id")
        if session:
            headers["Mcp-Session-Id"] = session

        # Notification: no id, no response expected. Servers that require it
        # refuse real work until they have seen it.
        await client.post(
            url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
        )

        response = await client.post(
            url,
            json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}},
            headers=headers,
        )
        response.raise_for_status()
        return _parse(response.text)


def _parse(body: str) -> Any:
    """A streamable-http server may answer as SSE even for a single request."""
    if body.lstrip().startswith("event:") or body.lstrip().startswith("data:"):
        for line in body.splitlines():
            if line.startswith("data:"):
                body = line[5:].strip()
                break
    try:
        message = json.loads(body)
    except json.JSONDecodeError as exc:
        raise McpError(f"해석할 수 없는 응답: {body[:200]}") from exc
    if "error" in message:
        raise McpError(str(message["error"].get("message") or message["error"]))
    return message.get("result")


# ── public API ─────────────────────────────────────────────────────────


def _flatten(result: Any) -> str:
    """MCP returns a content array; the model wants text."""
    if result is None:
        return ""
    content = result.get("content") if isinstance(result, dict) else None
    if content is None:
        return json.dumps(result, ensure_ascii=False)[:20_000]

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            parts.append(str(item))
        elif item.get("type") == "text":
            parts.append(item.get("text") or "")
        elif item.get("type") == "resource":
            resource = item.get("resource") or {}
            parts.append(resource.get("text") or resource.get("uri") or "")
        else:
            # Images and other blobs cannot go into a text tool result; naming
            # the type beats silently dropping it.
            parts.append(f"[{item.get('type')} 콘텐츠]")
    return "\n".join(p for p in parts if p).strip()


def expand(endpoint: str, env: dict[str, str] | None) -> str:
    """Substitutes `${VAR}` inside the command line itself.

    Some servers take their credential as an argv element rather than an
    environment variable (`server-postgres <url>`), so the same resolution has to
    reach the command string — otherwise the process starts with a literal
    `${PG_URL}` and fails somewhere far less obvious.
    """
    merged = {**os.environ, **(env or {})}
    return _ENV_REF.sub(lambda m: merged.get(m.group(1), ""), endpoint)


async def list_tools(
    transport: str, endpoint: str, env: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    if transport == "stdio":
        async with _StdioSession(expand(endpoint, env), env or {}) as session:
            result = await session._request("tools/list")
    else:
        result = await _http_request(endpoint, "tools/list")
    return list((result or {}).get("tools") or [])


async def call_tool(
    transport: str,
    endpoint: str,
    name: str,
    arguments: dict[str, Any],
    env: dict[str, str] | None = None,
) -> str:
    if transport == "stdio":
        async with _StdioSession(expand(endpoint, env), env or {}) as session:
            result = await session._request("tools/call", {"name": name, "arguments": arguments})
    else:
        result = await _http_request(endpoint, "tools/call", {"name": name, "arguments": arguments})
    if isinstance(result, dict) and result.get("isError"):
        raise McpError(_flatten(result) or "도구가 오류를 반환했습니다.")
    return _flatten(result)
