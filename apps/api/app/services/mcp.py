"""Minimal MCP client; stdio spawned per call so credentials never cross users."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
from typing import Any

from app.core.config import settings
from app.services import tools_gateway

log = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "kchat", "version": "0.1.0"}


class McpError(RuntimeError):
    pass


_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def substitute(env: dict[str, str] | None, *, user_id: str, user_email: str) -> dict[str, str]:
    """Connector env for one caller: identity placeholders, `${VAR}` from API env."""
    values = {
        "{{USER_ID}}": user_id,
        "{{USER_EMAIL}}": user_email,
    }
    out: dict[str, str] = {}
    for key, raw in (env or {}).items():
        text = str(raw)
        for token, value in values.items():
            text = text.replace(token, value)
        # unset → empty, not literal
        text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
        out[key] = text
    return out


#: env keys a stdio server inherits; never DB URL, master key, …
_INHERITED_ENV = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "PYTHONIOENCODING"})


class _StdioSession:
    """One server process per `with` block."""

    def __init__(self, command: str, env: dict[str, str]):
        self.command = command
        # never the API's own secrets; server's vars win
        self.env = {**{k: v for k, v in os.environ.items() if k in _INHERITED_ENV}, **env}
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
                # banners before JSON-RPC
                continue
            if message.get("id") != request_id:
                continue  # notification or out-of-band response
            if "error" in message:
                raise McpError(str(message["error"].get("message") or message["error"]))
            return message.get("result")


async def _http_request(url: str, method: str, params: dict[str, Any] | None = None) -> Any:
    """One MCP call over streamable HTTP, handshake included; keeps `Mcp-Session-Id`."""
    # gateway key only to the deep-research address
    async with tools_gateway.client(
        timeout=settings.tool_timeout_sec, features=("research",)
    ) as client:
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

        # notification: no id/response; required by some servers
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
    """Streamable-http may answer as SSE even for one request."""
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


def _flatten(result: Any) -> str:
    """MCP content array → text."""
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
            # non-text content named, not dropped
            parts.append(f"[{item.get('type')} 콘텐츠]")
    return "\n".join(p for p in parts if p).strip()


def expand(endpoint: str, env: dict[str, str] | None) -> str:
    """`${VAR}` substitution in the command line (argv credentials)."""
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
