"""A visitor's 「내 대화」 on a published bot: past the first 50, the rest come a page at a time."""

from __future__ import annotations

from datetime import timedelta

import test_publishing
from test_publishing import _publish

from app.models.chat import ChatSession
from app.models.user import utcnow

world = test_publishing.world


async def test_the_list_pages_with_offset_and_keeps_the_first_page_as_before(world):
    pub = await _publish(world)
    slug = pub["slug"]
    world.as_("member")
    page = (await world.client.get(f"/b/{slug}")).json()
    await world.client.post(f"/b/{slug}/consent", json={"version": page["consentVersion"]})
    made = [(await world.client.post(f"/b/{slug}/conversations")).json()["id"] for _ in range(55)]
    # Newest last made: a distinct time each, so the order is known.
    start = utcnow()
    async with world.local() as db:
        for n, sid in enumerate(made):
            row = await db.get(ChatSession, sid)
            row.updated_at = start + timedelta(minutes=n)
            db.add(row)
        await db.commit()
    newest = list(reversed(made))

    first = (await world.client.get(f"/b/{slug}/conversations")).json()
    assert [row["id"] for row in first] == newest[:50]
    rest = await world.client.get(f"/b/{slug}/conversations", params={"offset": 50})
    assert [row["id"] for row in rest.json()] == newest[50:]
    small = await world.client.get(f"/b/{slug}/conversations",
                                   params={"offset": 10, "limit": 5})
    assert [row["id"] for row in small.json()] == newest[10:15]
    too_many = await world.client.get(f"/b/{slug}/conversations", params={"limit": 1000})
    assert too_many.status_code == 422
