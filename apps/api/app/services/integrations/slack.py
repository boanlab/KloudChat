"""Slack with the person's own user token: their channels, recent messages, and a confirmed post
(`actions`).

Calls go through `http.request`; lists follow `next_cursor` up to a cap. A refused token marks
the connection for reconnecting."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.integrations import IntegrationAccount
from app.models.user import User, utcnow
from app.services.integrations import accounts, actions, common, config
from app.services.integrations.http import AuthExpired, ProviderError, request
from app.services.tools.base import Tool, ToolContext, ToolResult

API = "https://slack.com/api"
#: channels one listing returns at most
CHANNELS_MAX = 300
#: messages one read returns at most
HISTORY_MAX = 200
#: days a read reaches back at most
DAYS_MAX = 31
#: characters of one message the model reads
_MESSAGE_CHARS = 1200
#: characters of a message the card sends at most (Slack's own limit is 40,000)
POST_CHARS = 4000

LIST = "slack_list_channels"
READ = "slack_read_channel"
POST = "slack_post_message"

_REFUSED = {"invalid_auth", "token_revoked", "token_expired", "not_authed", "account_inactive"}
_SCOPE = {"missing_scope", "no_permission"}
_CHANNEL = re.compile(r"^[CGD][A-Z0-9]{6,20}$")


class Slack:
    """One person's Slack calls for one run."""

    def __init__(self, db: AsyncSession, account: IntegrationAccount) -> None:
        self.db = db
        self.account = account
        self._names: dict[str, str] = {}

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        token = await accounts.access_token(self.db, self.account)
        response = await request("POST", f"{API}/{method}", token=token, data=params)
        if response.status_code == 429:
            raise ProviderError("rate_limited", 429)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict) or not body.get("ok"):
            error = str((body or {}).get("error") or response.status_code)
            if error in _REFUSED:
                await accounts._needs_auth(self.db, self.account, error)
                raise AuthExpired(error)
            if error in _SCOPE:
                raise ProviderError("scope_missing", 403, error)
            if error in ("channel_not_found", "not_in_channel"):
                raise ProviderError("not_found", 404, error)
            raise ProviderError("slack_error", 502, error)
        await accounts.touch(self.db, self.account)
        return body

    async def channels(self) -> list[dict[str, Any]]:
        """Channels the person is in (public and private), archived ones left out."""
        found: list[dict[str, Any]] = []
        cursor = ""
        while True:
            body = await self.call("users.conversations", types="public_channel,private_channel",
                                   exclude_archived="true", limit=200,
                                   **({"cursor": cursor} if cursor else {}))
            found += [c for c in body.get("channels") or [] if isinstance(c, dict)]
            cursor = str((body.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor or len(found) >= CHANNELS_MAX:
                return sorted(found[:CHANNELS_MAX], key=lambda c: str(c.get("name") or ""))

    async def resolve(self, raw: str) -> dict[str, Any]:
        """A channel the person is in, by id or `#name`; `ProviderError("not_found")` else."""
        wanted = str(raw or "").strip().lstrip("#").lower()
        for channel in await self.channels():
            if wanted in (str(channel.get("id") or "").lower(),
                          str(channel.get("name") or "").lower()):
                return channel
        raise ProviderError("not_found", 404)

    async def history(self, channel_id: str, oldest: datetime,
                      limit: int = HISTORY_MAX) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        cursor = ""
        while True:
            body = await self.call("conversations.history", channel=channel_id,
                                   oldest=f"{oldest.timestamp():.6f}", limit=min(200, limit),
                                   **({"cursor": cursor} if cursor else {}))
            found += [m for m in body.get("messages") or [] if isinstance(m, dict)]
            cursor = str((body.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor or len(found) >= limit:
                return sorted(found[:limit], key=lambda m: float(m.get("ts") or 0))

    async def name(self, user_id: str) -> str:
        if not user_id:
            return "?"
        if user_id not in self._names:
            try:
                body = await self.call("users.info", user=user_id)
                person = body.get("user") or {}
                profile = person.get("profile") or {}
                self._names[user_id] = str(profile.get("display_name") or person.get("real_name")
                                           or person.get("name") or user_id)[:60]
            except ProviderError:
                self._names[user_id] = user_id
        return self._names[user_id]

    async def post(self, channel_id: str, text: str) -> dict[str, Any]:
        return await self.call("chat.postMessage", channel=channel_id, text=text)


def _channel_line(channel: dict[str, Any]) -> str:
    kind = "비공개" if channel.get("is_private") else "공개"
    shared = " · 외부 공유" if channel.get("is_ext_shared") or channel.get("is_shared") else ""
    return f"- #{channel.get('name', '')} ({kind}{shared}) [id: {channel.get('id', '')}]"


_MENTION = re.compile(r"<@([A-Z0-9]+)>")


async def _readable(slack: Slack, message: dict[str, Any]) -> str:
    text = str(message.get("text") or "")
    for user_id in set(_MENTION.findall(text)):
        text = text.replace(f"<@{user_id}>", "@" + await slack.name(user_id))
    who = await slack.name(str(message.get("user") or "")) if message.get("user") \
        else str(message.get("username") or "bot")
    at = datetime.fromtimestamp(float(message.get("ts") or 0), tz=common.zone())
    replies = f" (답글 {message['reply_count']})" if message.get("reply_count") else ""
    return f"- {common.say(at)} {who}: {' '.join(text.split())[:_MESSAGE_CHARS]}{replies}"


# ── agent tools ──────────────────────────────────────────────────────────


async def _list(_arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    async def work(db: AsyncSession, row: IntegrationAccount) -> ToolResult:
        found = await Slack(db, row).channels()
        if not found:
            return ToolResult(content="속한 채널이 없습니다.", detail="채널 없음", empty=True)
        return ToolResult(content="\n".join([f"내가 속한 채널 {len(found)}개",
                                             *(_channel_line(c) for c in found)]),
                          detail=f"채널 {len(found)}개")

    return await common.with_account(ctx, config.SLACK, "read", work)


async def _read(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    days = max(1, min(DAYS_MAX, int(arguments.get("days") or 1)))
    limit = max(1, min(HISTORY_MAX, int(arguments.get("limit") or HISTORY_MAX)))

    async def work(db: AsyncSession, row: IntegrationAccount) -> ToolResult:
        slack = Slack(db, row)
        channel = await slack.resolve(str(arguments.get("channel") or ""))
        messages = await slack.history(str(channel["id"]), utcnow() - timedelta(days=days),
                                       limit)
        head = f"#{channel.get('name', '')} 최근 {days}일 메시지 {len(messages)}개"
        if not messages:
            return ToolResult(content=head + ": 없음", detail="메시지 없음", empty=True)
        lines = [await _readable(slack, m) for m in messages
                 if m.get("type", "message") == "message" and not m.get("subtype")]
        return ToolResult(content="\n".join([head, *lines]), detail=f"메시지 {len(lines)}개")

    return await common.with_account(ctx, config.SLACK, "read", work)


async def _propose(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    text = str(arguments.get("text") or "").strip()[:POST_CHARS]
    if not text:
        return ToolResult(content="오류: 보낼 내용(text)이 없습니다.", failed=True)

    async def work(db: AsyncSession, row: IntegrationAccount) -> ToolResult:
        channel: dict[str, Any] = {}
        if arguments.get("channel"):
            try:
                channel = await Slack(db, row).resolve(str(arguments["channel"]))
            except ProviderError as exc:
                if exc.code != "not_found":
                    raise
        payload = {"channel": str(channel.get("id") or ""),
                   "channelName": str(channel.get("name") or ""), "text": text}
        where = f"#{payload['channelName']}" if payload["channel"] else "고를 채널"
        return await actions.propose(
            ctx, config.SLACK, "slack.post_message", payload,
            f"Slack 메시지 보내기 제안: {where}에 {len(text)}자. 채널은 카드에서 바꿀 수 있습니다.")

    return await common.with_account(ctx, config.SLACK, "post", work)


async def _run_post(db: AsyncSession, _user: User, row: IntegrationAccount,
                    payload: dict[str, Any]) -> dict[str, Any]:
    slack = Slack(db, row)
    # re-checked: the person must still be in the channel
    channel = await slack.resolve(str(payload.get("channel") or ""))
    sent = await slack.post(str(channel["id"]), str(payload.get("text") or "")[:POST_CHARS])
    link = ""
    try:
        found = await slack.call("chat.getPermalink", channel=channel["id"],
                                 message_ts=sent.get("ts"))
        link = str(found.get("permalink") or "")
    except ProviderError:
        pass
    return {"id": sent.get("ts"), "url": link, "label": f"#{channel.get('name', '')}"}


def _edit_post(payload: dict[str, Any], edits: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    if "channel" in edits:
        channel = str(edits.get("channel") or "")
        if not _CHANNEL.match(channel):
            raise ValueError("bad_channel")
        out["channel"] = channel
        out["channelName"] = str(edits.get("channelName") or "")[:200]
    if "text" in edits:
        text = str(edits.get("text") or "").strip()[:POST_CHARS]
        if not text:
            raise ValueError("empty_text")
        out["text"] = text
    if not out.get("channel"):
        raise ValueError("channel_required")
    return out


actions.register("slack.post_message", actions.Executor(
    provider=config.SLACK, feature="post", editable=frozenset({"channel", "channelName", "text"}),
    run=_run_post, edit=_edit_post))


def tools(features: set[str]) -> list[Tool]:
    """The Slack tools this person's grant and the administrator allow."""
    out: list[Tool] = []
    if "read" in features:
        out += [
            Tool(
                name=LIST,
                description="List the Slack channels the person is in, with their ids.",
                parameters={"type": "object", "properties": {}},
                run=_list, label="Slack 채널 보는 중", title="Slack 채널 보기",
                source=config.SLACK, wants_context=True,
            ),
            Tool(
                name=READ,
                description=(
                    "Read recent messages of a Slack channel the person is in (id or #name), "
                    "to summarise or find something. Only channels they are a member of."),
                parameters={"type": "object", "properties": {
                    "channel": {"type": "string", "description": "Channel id or #name"},
                    "days": {"type": "integer", "description": f"How far back, 1-{DAYS_MAX}; "
                             "default 1"},
                    "limit": {"type": "integer", "description": "Most messages, default 200"},
                }, "required": ["channel"]},
                run=_read, label="Slack 채널 읽는 중", title="Slack 채널 읽기",
                source=config.SLACK, wants_context=True,
            ),
        ]
    if "post" in features:
        out.append(Tool(
            name=POST,
            description=(
                "Propose a Slack message from the person to a channel they are in. Nothing is "
                "sent until they confirm the card this shows (they may pick another channel "
                "or edit the text there). Use only when they asked to post."),
            parameters={"type": "object", "properties": {
                "channel": {"type": "string", "description": "Channel id or #name, if known"},
                "text": {"type": "string", "description": "The message, as it will be sent"},
            }, "required": ["text"]},
            run=_propose, label="Slack 메시지 준비 중", title="Slack 메시지 제안",
            read_only=False, source=config.SLACK, wants_context=True,
        ))
    return out
