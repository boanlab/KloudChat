"""One `push.Notice` fans out to web push and to Slack (for people who turned Slack on), once
each; a Slack failure, or a slow one, never stops web push, and the other way round."""

from __future__ import annotations

import asyncio

import pytest

from app.services import push
from app.services.integrations import slack_notify


def _notice(event: str = "job_done", internal: bool = True) -> push.Notice:
    return push.Notice(event=event, title="보고서가 끝났습니다", label="11월 운영위",
                       url="/s/abc", internal=internal)


@pytest.fixture
def seen(monkeypatch):
    calls: dict[str, list] = {"push": [], "slack": []}

    async def web(user_id, notice):
        calls["push"].append((user_id, notice.title))

    async def slack(user_id, title, label, url, internal):
        calls["slack"].append((user_id, title, label, url, internal))
        return True

    monkeypatch.setattr(push, "deliver", web)
    monkeypatch.setattr(slack_notify, "deliver", slack)
    return calls


@pytest.mark.asyncio
async def test_a_notice_reaches_both_channels_once_with_internal_carried(seen) -> None:
    push.notify("u1", _notice())
    await push.drain()
    assert seen["push"] == [("u1", "보고서가 끝났습니다")]
    assert seen["slack"] == [("u1", "보고서가 끝났습니다", "11월 운영위", "/s/abc", True)]


@pytest.mark.asyncio
async def test_a_test_notice_goes_to_the_device_only(seen) -> None:
    push.notify("u1", _notice(event="test"))
    await push.drain()
    assert len(seen["push"]) == 1 and seen["slack"] == []


@pytest.mark.asyncio
async def test_a_failing_slack_never_breaks_web_push(seen, monkeypatch) -> None:
    async def broken(*_args):
        raise RuntimeError("slack is down")

    monkeypatch.setattr(slack_notify, "deliver", broken)
    push.notify("u1", _notice())
    await push.drain()
    assert len(seen["push"]) == 1


@pytest.mark.asyncio
async def test_a_slow_slack_does_not_hold_back_web_push(seen, monkeypatch) -> None:
    release = asyncio.Event()

    async def slow(*_args):
        await release.wait()
        return True

    monkeypatch.setattr(slack_notify, "deliver", slow)
    push.notify("u1", _notice())
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(seen["push"]) == 1
    release.set()
    await push.drain()


@pytest.mark.asyncio
async def test_a_failing_web_push_still_reaches_slack(seen, monkeypatch) -> None:
    async def broken(*_args):
        raise RuntimeError("push service is down")

    monkeypatch.setattr(push, "deliver", broken)
    push.notify("u1", _notice())
    await push.drain()
    assert len(seen["slack"]) == 1
