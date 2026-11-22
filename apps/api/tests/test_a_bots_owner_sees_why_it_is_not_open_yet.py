"""The owner's bot page carries the reason a link is not open outside yet; others learn nothing."""

from __future__ import annotations

import test_publishing
from test_publishing import _publish
from test_publishing_outside import _CAP, _anon, _approve

from app.models.user import User
from app.models.workspace import Agent
from app.routers import workspace

world = test_publishing.world


async def test_a_pending_link_shows_the_owner_the_request_and_no_one_else(world):
    pub = await _publish(world, visibility="link", **_CAP)
    world.as_("owner")
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    assert page["mine"] is True and page["publicationId"] == pub["id"]
    assert page["visibility"] == "internal"
    assert page["requestedVisibility"] == "link" and page["reviewState"] == "pending"
    # A member reads the bot but not that an approval is waiting.
    world.as_("member")
    member = (await world.client.get(f"/b/{pub['slug']}")).json()
    assert member["mine"] is False
    assert not {"publicationId", "requestedVisibility", "reviewState"} & set(member)
    # A visitor without an account gets the same answer as for an address that does not exist.
    _anon(world)
    known = await world.client.get(f"/b/{pub['slug']}")
    unknown = await world.client.get("/b/no-such-bot-1a2b")
    assert known.status_code == unknown.status_code == 404
    assert known.json() == unknown.json()


async def test_an_open_link_shows_the_owner_no_request(world):
    pub = await _publish(world, visibility="link", **_CAP)
    await _approve(world, pub["id"])
    world.as_("owner")
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    assert page["visibility"] == "link"
    assert page["requestedVisibility"] == "" and page["reviewState"] == ""


async def test_a_declined_request_shows_the_owner_that_it_was_declined(world):
    pub = await _publish(world, visibility="link", **_CAP)
    world.as_("admin")
    declined = await world.client.post(
        f"/admin/publications/{pub['id']}/review",
        json={"decision": "reject", "reason": "자료에 연락처가 있습니다"})
    assert declined.status_code == 200, declined.text
    world.as_("owner")
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    assert page["visibility"] == "internal"
    assert page["requestedVisibility"] == "link" and page["reviewState"] == "rejected"


async def test_an_agent_card_carries_where_it_is_shared_and_what_was_asked(world):
    pub = await _publish(world, visibility="link", **_CAP)
    async with world.local() as db:
        owner = await db.get(User, "owner")
        agent = await db.get(Agent, "agent-1")
        card = (await workspace._published(db, owner, [agent]))["agent-1"]
    assert card == {"id": pub["id"], "slug": pub["slug"], "status": "approved",
                    "visibility": "internal", "requestedVisibility": "link",
                    "reviewState": "pending"}
