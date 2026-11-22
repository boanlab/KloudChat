"""Slack with each person's own user token: own channels only, sends only from the card, a
published agent answers @mentions; signature checked, Slack mocked."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import integration_fixtures
import pytest
from integration_fixtures import form, request_stub, settings_for, world  # noqa: F401
from sqlmodel import select

from app.models.integrations import IntegrationAccount, SlackBotChannel, SlackWorkspace
from app.models.publishing import Publication
from app.models.user import AuditEvent
from app.routers import integrations as router
from app.services.integrations import accounts, slack, slack_bot, slack_notify, tools
from app.services.tools.base import ToolContext

integration_fixtures.EXTRA_TABLES.update({
    "slack_bot_channels", "slack_threads", "publications", "publication_versions",
    "publication_visitors", "publication_counters", "agents"})

API = "POST slack.com/api/"
CHANNELS = {"ok": True, "channels": [
    {"id": "C111111", "name": "운영위", "is_private": False},
    {"id": "G222222", "name": "예산-비공개", "is_private": True},
    {"id": "C333333", "name": "협력사", "is_ext_shared": True},
]}


async def _connected(world, scopes=("channels:read", "groups:read", "channels:history",  # noqa: F811
                                    "groups:history", "users:read", "chat:write")):
    world.values.update(settings_for(google=False, slack=True))
    row = IntegrationAccount(user_id=world.member.id, provider="slack", status="connected",
                             label="단국 · @jn", external_id="U1", team_id="T1",
                             scopes=sorted(scopes), access_token=accounts.seal("xoxp-mine"))
    world.db.add(row)
    await world.db.commit()
    world.provider.on(API + "users.conversations", CHANNELS)
    return row


def _tools() -> dict:
    return {t.name: t for t in slack.tools({"read", "post"})}


@pytest.mark.asyncio
async def test_reading_a_channel_names_people_and_stays_inside_my_channels(world):  # noqa: F811
    await _connected(world)
    world.provider.on(API + "conversations.history", {"ok": True, "messages": [
        {"type": "message", "user": "U2", "text": "<@U1> 예산안 금요일까지", "ts": "1763170000.0"},
        {"type": "message", "subtype": "channel_join", "user": "U3", "text": "joined",
         "ts": "1763170100.0"}]})
    world.provider.on(API + "users.info", lambda r: {"ok": True, "user": {
        "profile": {"display_name": {"U1": "jn", "U2": "김예산"}[form(r)["user"]]}}})
    ctx = ToolContext(user_id=world.member.id, session_id="s1")
    read = await _tools()[slack.READ].run({"channel": "#운영위", "days": 3}, ctx)
    assert not read.failed and "김예산: @jn 예산안 금요일까지" in read.content
    assert "joined" not in read.content
    assert form(world.provider.sent(API + "conversations.history")[0])["channel"] == "C111111"
    # the user's own token
    assert world.provider.sent(API + "conversations.history")[0].headers["authorization"] \
        == "Bearer xoxp-mine"
    # a channel they are not in is not read
    other = await _tools()[slack.READ].run({"channel": "#임원회의"}, ctx)
    assert other.failed and len(world.provider.sent(API + "conversations.history")) == 1


@pytest.mark.asyncio
async def test_a_message_goes_only_from_the_card_and_to_the_channel_picked_there(
        world):  # noqa: F811
    await _connected(world)
    ctx = ToolContext(user_id=world.member.id, session_id="s1")
    proposed = await _tools()[slack.POST].run({"channel": "운영위", "text": "회의록 올렸습니다"},
                                              ctx)
    assert not world.provider.sent(API + "chat.postMessage")
    action_id = proposed.trace["confirm"]["actionId"]
    card = await router.get_action(action_id, world.member, world.db)
    assert card["payload"] == {"channel": "C111111", "channelName": "운영위",
                               "text": "회의록 올렸습니다"}
    world.provider.on(API + "chat.postMessage", {"ok": True, "ts": "1.2"})
    world.provider.on(API + "chat.getPermalink", {"ok": True, "permalink": "https://x.slack.com/p1"})
    done = await router.confirm_action(
        action_id, request_stub(), world.member, world.db,
        router.ActionDecision(edits={"channel": "G222222", "channelName": "예산-비공개"}))
    assert done["status"] == "done" and done["result"]["url"] == "https://x.slack.com/p1"
    sent = form(world.provider.sent(API + "chat.postMessage")[0])
    assert sent == {"channel": "G222222", "text": "회의록 올렸습니다"}
    assert (await world.db.exec(select(AuditEvent).where(
        AuditEvent.action == "integration.write"))).one().event_metadata["kind"] \
        == "slack.post_message"


@pytest.mark.asyncio
async def test_a_card_cannot_send_to_a_channel_the_person_is_not_in(world):  # noqa: F811
    await _connected(world)
    ctx = ToolContext(user_id=world.member.id, session_id="s1")
    proposed = await _tools()[slack.POST].run({"text": "안녕하세요"}, ctx)
    action_id = proposed.trace["confirm"]["actionId"]
    with pytest.raises(Exception) as refused:
        await router.confirm_action(action_id, request_stub(), world.member, world.db)
    assert getattr(refused.value, "detail", "") == "channel_required"
    failed = await router.confirm_action(
        action_id, request_stub(), world.member, world.db,
        router.ActionDecision(edits={"channel": "C999999"}))
    assert failed["status"] == "failed" and failed["error"] == "not_found"
    assert not world.provider.sent(API + "chat.postMessage")


@pytest.mark.asyncio
async def test_a_revoked_token_asks_to_reconnect(world):  # noqa: F811
    row = await _connected(world)
    world.provider.on(API + "users.conversations", {"ok": False, "error": "token_revoked"})
    ctx = ToolContext(user_id=world.member.id, session_id="s1")
    result = await _tools()[slack.LIST].run({}, ctx)
    assert result.failed and "다시 연결" in result.content
    await world.db.refresh(row)
    assert row.status == "needs_auth"


@pytest.mark.asyncio
async def test_slack_tools_join_only_a_turn_about_slack(world):  # noqa: F811
    await _connected(world)
    names = [t.name for t in await tools.turn_tools(world.db, world.member,
                                                     "슬랙 운영위 채널 오늘 대화 요약해 줘")]
    assert names == sorted([slack.LIST, slack.POST, slack.READ])
    assert await tools.turn_tools(world.db, world.member, "보고서 목차") == []


@pytest.mark.asyncio
async def test_a_notice_goes_to_my_own_dms_only_when_i_turned_it_on(world):  # noqa: F811
    await _connected(world)
    assert await slack_notify.deliver(world.member.id, "보고서가 끝났습니다", "11월 운영위",
                                      "/s/abc") is False
    out = await router.slack_notify_choice(router.SlackNotify(on=True), world.member, world.db)
    assert out["notify"] == {"on": True, "channel": "", "name": ""}
    world.provider.on(API + "chat.postMessage", {"ok": True, "ts": "1"})
    assert await slack_notify.deliver(world.member.id, "보고서가 끝났습니다", "11월 운영위",
                                      "/s/abc", internal=True)
    sent = form(world.provider.sent(API + "chat.postMessage")[0])
    # no bot here: their own DM, as themselves; internal material keeps the title out
    assert sent["channel"] == "U1"
    assert sent["text"] == "보고서가 끝났습니다 <https://chat.example.ac.kr/s/abc|열기>"
    # with the app installed, the bot DMs them
    world.db.add(SlackWorkspace(team_id="T1", name="단국", bot_token=accounts.seal("xoxb-t1")))
    await world.db.commit()
    world.provider.on(API + "conversations.open", {"ok": True, "channel": {"id": "D1"}})
    assert await slack_notify.deliver(world.member.id, "끝났습니다", "결과", "/s/x")
    last = world.provider.sent(API + "chat.postMessage")[-1]
    assert form(last)["channel"] == "D1" and last.headers["authorization"] == "Bearer xoxb-t1"


def _signed(secret: str, body: bytes, at: int | None = None) -> dict[str, str]:
    at = at or int(time.time())
    digest = hmac.new(secret.encode(), f"v0:{at}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": str(at), "X-Slack-Signature": f"v0={digest}"}


def test_slack_events_are_checked_by_signature_and_age() -> None:
    body = b'{"type":"url_verification"}'
    headers = _signed("s", body)
    assert slack_bot.verify("s", headers["X-Slack-Request-Timestamp"], body,
                            headers["X-Slack-Signature"])
    assert not slack_bot.verify("other", headers["X-Slack-Request-Timestamp"], body,
                                headers["X-Slack-Signature"])
    old = _signed("s", body, int(time.time()) - 600)
    assert not slack_bot.verify("s", old["X-Slack-Request-Timestamp"], body,
                                old["X-Slack-Signature"])
    assert not slack_bot.verify("", headers["X-Slack-Request-Timestamp"], body,
                                headers["X-Slack-Signature"])
    assert slack_bot.first_time("Ev1") and not slack_bot.first_time("Ev1")


def test_a_bot_answers_inside_the_organisation_but_outside_only_when_approved() -> None:
    internal = Publication(owner_id="o", slug="b", visibility="internal", status="approved")
    assert slack_bot.allowed(internal, shared=False) is None
    assert slack_bot.allowed(internal, shared=True) == "outside_not_approved"
    outside = Publication(owner_id="o", slug="c", visibility="link", status="approved")
    assert slack_bot.allowed(outside, shared=True) is None
    paused = Publication(owner_id="o", slug="d", visibility="internal", status="paused")
    assert slack_bot.allowed(paused, shared=False) == "bot_paused"


@pytest.mark.asyncio
async def test_a_publisher_links_a_channel_they_are_in_and_it_is_audited(world):  # noqa: F811
    await _connected(world)
    pub = Publication(owner_id=world.member.id, slug="minutes-bot", visibility="internal",
                      status="approved")
    world.db.add(pub)
    await world.db.commit()
    with pytest.raises(Exception) as refused:
        await router.slack_bot_link(pub.id, router.SlackBotLink(channel="#운영위"),
                                    request_stub(), world.member, world.db)
    assert getattr(refused.value, "detail", "") == "app_not_installed"
    world.db.add(SlackWorkspace(team_id="T1", name="단국", bot_token=accounts.seal("xoxb-t1"),
                                internal=True))
    await world.db.commit()
    linked = await router.slack_bot_link(pub.id, router.SlackBotLink(channel="#협력사"),
                                         request_stub(), world.member, world.db)
    # shared with another organisation: an outside audience, not answered for an internal bot
    assert linked["shared"] is True and linked["blocked"] == "outside_not_approved"
    inside = await router.slack_bot_link(pub.id, router.SlackBotLink(channel="운영위"),
                                         request_stub(), world.member, world.db)
    assert inside["blocked"] is None
    # someone else's bot is not theirs to link
    with pytest.raises(Exception) as refused:
        await router.slack_bot_link(pub.id, router.SlackBotLink(channel="운영위"),
                                    request_stub(), world.admin, world.db)
    assert getattr(refused.value, "detail", "") == "not_found"
    assert len((await world.db.exec(select(AuditEvent).where(
        AuditEvent.action == "integration.slack_bot_channel"))).all()) == 2
    await router.slack_bot_unlink(pub.id, inside["id"], request_stub(), world.member, world.db)
    assert len((await world.db.exec(select(SlackBotChannel))).all()) == 1


@pytest.mark.asyncio
async def test_a_mention_in_an_unlinked_or_outside_channel_runs_no_turn(world,  # noqa: F811
                                                                         monkeypatch):
    await _connected(world)
    pub = Publication(owner_id=world.member.id, slug="minutes-bot", visibility="internal",
                      status="approved")
    world.db.add_all([pub, SlackWorkspace(team_id="T1", name="단국",
                                          bot_token=accounts.seal("xoxb-t1"))])
    await world.db.commit()
    world.db.add(SlackBotChannel(team_id="T1", channel_id="C333333", publication_id=pub.id,
                                 added_by=world.member.id, shared=True))
    await world.db.commit()

    async def never(*_args, **_kwargs):
        raise AssertionError("a turn ran")

    from app.routers import publications

    monkeypatch.setattr(publications, "run_published_turn", never)
    world.provider.on(API + "chat.postMessage", {"ok": True})
    event = {"team_id": "T1", "event_id": "E1", "event": {
        "type": "app_mention", "channel": "C111111", "user": "U9", "text": "<@UB> 안녕",
        "ts": "1.0"}}
    assert await slack_bot.answer(world.db, event) == "not_linked"
    event["event"]["channel"] = "C333333"
    assert await slack_bot.answer(world.db, event) == "outside_not_approved"
    said = form(world.provider.sent(API + "chat.postMessage")[0])
    assert said["thread_ts"] == "1.0" and "답할 수 없습니다" in said["text"]


@pytest.mark.asyncio
async def test_the_events_url_answers_slacks_check_and_refuses_unsigned_calls(
        world, monkeypatch):  # noqa: F811
    world.values.update(settings_for(google=False, slack=True))
    from starlette.requests import Request

    def req(body: bytes, headers: dict[str, str]) -> Request:
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        return Request({"type": "http", "method": "POST", "path": "/",
                        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]},
                       receive)

    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    assert await router.slack_events(req(body, _signed("signing-secret", body))) == {
        "challenge": "abc"}
    with pytest.raises(Exception) as refused:
        await router.slack_events(req(body, _signed("wrong", body)))
    assert getattr(refused.value, "detail", "") == "bad_signature"
    handled: list[dict] = []
    monkeypatch.setattr(slack_bot, "handle", handled.append)
    event = json.dumps({"type": "event_callback", "event": {"type": "app_mention"}}).encode()
    await router.slack_events(req(event, {**_signed("signing-secret", event),
                                          "X-Slack-Retry-Num": "1"}))
    assert handled == []
    await router.slack_events(req(event, _signed("signing-secret", event)))
    assert len(handled) == 1


@pytest.mark.asyncio
async def test_a_mention_inside_the_organisation_is_answered_in_its_thread(
        world, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from app.models.publishing import PublicationVersion
    from app.routers import bot_api, publications

    await _connected(world)
    pub = Publication(owner_id=world.member.id, slug="minutes-bot", visibility="internal",
                      status="approved", show_sources=True)
    world.db.add_all([pub, SlackWorkspace(team_id="T1", name="단국",
                                          bot_token=accounts.seal("xoxb-t1"), internal=True)])
    await world.db.commit()
    version = PublicationVersion(publication_id=pub.id, agent_snapshot_id="snap",
                                 model="strict-local/qwen3.8-flash")
    world.db.add(version)
    pub.active_version_id = version.id
    world.db.add(pub)
    world.db.add(SlackBotChannel(team_id="T1", channel_id="C111111", publication_id=pub.id,
                                 added_by=world.member.id))
    await world.db.commit()
    turns: list[dict] = []

    async def turn(db, pub_, visitor, session, **kwargs):
        turns.append({"user": kwargs["user"], "channel": kwargs["channel"],
                      "content": kwargs["content"], "session": session.id,
                      "visitor": visitor.anon_id})

        async def stream():
            yield 'data: {"type": "done"}\n\n'

        return SimpleNamespace(body_iterator=stream())

    async def final(_session, _events, *, show_sources):
        return {"answer": "예산 3건이 의결되었습니다 [1].",
                "citations": [{"index": 1, "source": "회의록", "url": "https://x/1"}]}

    monkeypatch.setattr(publications, "run_published_turn", turn)
    monkeypatch.setattr(bot_api, "_final", final)
    world.provider.on(API + "chat.postMessage", {"ok": True})
    event = {"team_id": "T1", "event": {"type": "app_mention", "channel": "C111111",
                                        "user": "U9", "text": "<@UB> 지난 회의 결정 사항?",
                                        "ts": "5.0"}}
    assert await slack_bot.answer(world.db, event) is None
    assert turns[0]["content"] == "지난 회의 결정 사항?" and turns[0]["channel"] == "slack"
    # the members' audience: the publisher's account stands in, as an internal bot's page does
    assert turns[0]["user"].id == world.member.id and turns[0]["visitor"] == "slack:T1:U9"
    notice, said = (form(r) for r in world.provider.sent(API + "chat.postMessage"))
    # whose account handles it is said before the first answer
    assert slack_bot.NOTICE in notice["text"] and notice["thread_ts"] == "5.0"
    assert said["thread_ts"] == "5.0" and said["channel"] == "C111111"
    assert "<https://x/1|회의록>" in said["text"]
    # the same thread continues the same conversation, and the notice is said once
    event["event"].update(ts="6.0", thread_ts="5.0")
    assert await slack_bot.answer(world.db, event) is None
    assert turns[1]["session"] == turns[0]["session"]
    assert len(world.provider.sent(API + "chat.postMessage")) == 3
    # someone from another organisation in the same channel is an outside audience
    event["event"].update(ts="7.0", user_team="T9")
    assert await slack_bot.answer(world.db, event) == "outside_not_approved"
    assert len(turns) == 2


def test_an_answer_cannot_ping_a_channel_or_forge_a_link() -> None:
    assert slack_bot.quiet("모두 <!channel> 보세요 <@U1> & <https://x|y>") == (
        "모두 &lt;!channel&gt; 보세요 &lt;@U1&gt; &amp; &lt;https://x|y&gt;")
