"""Usage endpoints against a real SQLite database: every credit on the ledger lands in a bar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import CreditLedger, User
from app.routers import usage as usage_router
from app.services.credits import cycle_start, next_cycle_reset, refill_if_due, settle

_DDL = (
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        email TEXT,
        password_hash TEXT,
        name TEXT,
        role TEXT,
        status TEXT,
        monthly_credits INTEGER DEFAULT 0,
        credits_used INTEGER DEFAULT 0,
        cycle_resets_at DATETIME,
        litellm_user_id TEXT,
        litellm_key TEXT,
        litellm_key_preview TEXT,
        litellm_key_issued_at DATETIME,
        avatar_color TEXT,
        allowed_models TEXT,
        preferences TEXT,
        created_at DATETIME,
        last_active_at DATETIME,
        email_verified_at DATETIME
    )
    """,
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        kind TEXT,
        model TEXT DEFAULT ''
    )
    """,
    """
    CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        role TEXT,
        model TEXT,
        created_at DATETIME
    )
    """,
    """
    CREATE TABLE credit_ledger (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        delta INTEGER,
        reason TEXT,
        session_id TEXT,
        job_id TEXT,
        model TEXT,
        surface TEXT,
        units INTEGER,
        unit TEXT,
        created_at DATETIME
    )
    """,
    """
    CREATE TABLE api_keys (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        name TEXT,
        secret TEXT,
        preview TEXT,
        created_at DATETIME,
        last_used_at DATETIME,
        revoked_at DATETIME
    )
    """,
)


def _day_floor(_unit: str, value):
    """`date_trunc('day', …)` for SQLite."""
    return None if value is None else f"{str(value)[:10]} 00:00:00.000000"


@pytest.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @sa.event.listens_for(engine.sync_engine, "connect")
    def _register(dbapi_connection, _record):
        # aiosqlite's own `create_function` is a coroutine; this hook is sync.
        raw = dbapi_connection.driver_connection._conn
        raw.create_function("date_trunc", 2, _day_floor)

    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)
    # As the app builds it.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


def _at(days_ago: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days_ago)


async def _account(db, *, allowance: int = 1_000_000) -> User:
    user = User(
        email="e2e-personas@example.test",
        password_hash="x",
        name="사용자",
        monthly_credits=allowance,
    )
    db.add(user)
    await db.commit()
    return user


async def _session_row(db, user: User, *, kind: str, model: str) -> str:
    row = (
        await db.exec(
            sa.text(
                "INSERT INTO sessions (id, user_id, kind, model)"
                " VALUES (:i, :u, :k, :m) RETURNING id"
            ).bindparams(i=f"s-{kind}-{model}", u=user.id, k=kind, m=model)
        )
    ).one()
    await db.commit()
    return row[0]


async def _turn(db, session_id: str, *, model: str, when: datetime) -> None:
    await db.exec(
        sa.text(
            "INSERT INTO messages (id, session_id, role, model, created_at)"
            " VALUES (lower(hex(randomblob(16))), :s, 'assistant', :m, :t)"
        ).bindparams(s=session_id, m=model, t=when)
    )
    await db.commit()


async def _spend(
    db,
    user: User,
    *,
    credits: int,
    reason: str,
    session_id: str | None = None,
    model: str | None = None,
    when: datetime | None = None,
) -> None:
    row = CreditLedger(
        user_id=user.id,
        delta=-credits,
        reason=reason,
        session_id=session_id,
        model=model,
        created_at=when or _at(0),
    )
    db.add(row)
    await db.commit()


async def test_pictures_and_clips_are_where_the_money_went_not_other(db) -> None:
    """Media spend shows under image/av, not 기타."""
    user = await _account(db)
    pictures = await _session_row(db, user, kind="image", model="openai/gpt-5-image-mini")
    clips = await _session_row(db, user, kind="av", model="google/veo-3.1-lite")

    await _spend(
        db,
        user,
        credits=12_612,
        reason="image.generate",
        session_id=pictures,
        model="openai/gpt-5-image-mini",
        when=_at(2),
    )
    await _spend(
        db,
        user,
        credits=84_000,
        reason="video.generate",
        session_id=clips,
        model="google/veo-3.1-lite",
        when=_at(1),
    )

    report = await usage_router.my_usage(user, db, days=30)

    assert report["totals"]["credits"] == 96_612
    assert report["totals"]["otherCredits"] == 0
    # Three pictures on one call are one request.
    assert report["totals"]["requests"] == 2

    assert {row["model"]: row["credits"] for row in report["byModel"]} == {
        "openai/gpt-5-image-mini": 12_612,
        "google/veo-3.1-lite": 84_000,
    }
    assert {row["kind"]: row["credits"] for row in report["bySurface"]} == {
        "image": 12_612,
        "av": 84_000,
    }
    assert sorted(row["credits"] for row in report["daily"] if row["credits"]) == [12_612, 84_000]


async def test_the_parts_add_up_to_the_total(db) -> None:
    """The bars sum to the ledger total."""
    user = await _account(db)
    chat = await _session_row(db, user, kind="chat", model="vendor/quality")
    deck = await _session_row(db, user, kind="slides", model="vendor/quality")

    await _turn(db, chat, model="vendor/quality", when=_at(1))
    await _spend(
        db,
        user,
        credits=900,
        reason="chat.completion",
        session_id=chat,
        model="vendor/quality",
        when=_at(1),
    )
    await _spend(
        db,
        user,
        credits=40,
        reason="chat.title",
        session_id=chat,
        model="local/gemma-4-26b-a4b",
        when=_at(1),
    )
    await _spend(
        db,
        user,
        credits=310,
        reason="deck.factcheck",
        session_id=deck,
        model="vendor/cheap",
        when=_at(1),
    )

    report = await usage_router.my_usage(user, db, days=30)

    total = report["totals"]["credits"]
    assert total == 1_250
    assert (
        sum(row["credits"] for row in report["byModel"]) + report["totals"]["otherCredits"] == total
    )
    assert sum(row["credits"] for row in report["bySurface"]) == total
    assert sum(row["credits"] for row in report["daily"]) == total
    # The fact-check is billed to the model that ran it, not the deck's.
    assert {row["model"]: row["credits"] for row in report["byModel"]}["vendor/cheap"] == 310


async def test_other_is_only_what_belongs_to_nothing(db) -> None:
    """기타 holds only charges with no single owner."""
    user = await _account(db)
    chat = await _session_row(db, user, kind="chat", model="vendor/quality")

    await _spend(
        db,
        user,
        credits=500,
        reason="chat.completion",
        session_id=chat,
        model="vendor/quality",
        when=_at(1),
    )
    await _spend(db, user, credits=700, reason="chat.compare", session_id=chat, when=_at(1))
    await _spend(db, user, credits=120, reason="design.extract", when=_at(1))

    report = await usage_router.my_usage(user, db, days=30)

    assert report["totals"]["credits"] == 1_320
    assert report["totals"]["otherCredits"] == 820
    assert [row["model"] for row in report["byModel"]] == ["vendor/quality"]
    assert {row["kind"]: row["credits"] for row in report["bySurface"]} == {
        "chat": 1_200,
        "other": 120,
    }


async def test_a_media_row_written_before_the_column_still_finds_its_model(db) -> None:
    """A media row without a model column falls back to the session's generator."""
    user = await _account(db)
    pictures = await _session_row(db, user, kind="image", model="google/gemini-2.5-flash-image")

    await _spend(db, user, credits=3_924, reason="image.generate", session_id=pictures, when=_at(1))

    report = await usage_router.my_usage(user, db, days=30)

    assert report["byModel"] == [
        {
            "model": "google/gemini-2.5-flash-image",
            "credits": 3_924,
            "requests": 1,
            "units": 0,
            "unit": "",
        }
    ]
    assert report["totals"]["otherCredits"] == 0


async def test_a_free_month_still_says_what_ran(db) -> None:
    """Zero-credit turns still produce models and a chart."""
    user = await _account(db)
    chat = await _session_row(db, user, kind="chat", model="local/gemma-4-26b-a4b")
    for day in (1, 1, 2):
        await _turn(db, chat, model="local/gemma-4-26b-a4b", when=_at(day))

    report = await usage_router.my_usage(user, db, days=30)

    assert report["totals"] == {"credits": 0, "requests": 3, "otherCredits": 0}
    assert report["byModel"] == [
        {"model": "local/gemma-4-26b-a4b", "credits": 0, "requests": 3, "units": 0, "unit": ""}
    ]
    assert [row["requests"] for row in report["daily"] if row["requests"]] == [1, 2]


async def test_a_priced_turn_is_counted_once(db) -> None:
    """Request count comes from the message and money from the ledger, never both from both."""
    user = await _account(db)
    chat = await _session_row(db, user, kind="chat", model="vendor/quality")
    await _turn(db, chat, model="vendor/quality", when=_at(1))
    await _spend(
        db,
        user,
        credits=900,
        reason="chat.completion",
        session_id=chat,
        model="vendor/quality",
        when=_at(1),
    )

    report = await usage_router.my_usage(user, db, days=30)

    assert report["totals"]["requests"] == 1
    assert report["byModel"] == [
        {"model": "vendor/quality", "credits": 900, "requests": 1, "units": 0, "unit": ""}
    ]


async def test_the_admin_view_tells_the_same_story(db) -> None:
    """The admin view reads the same rows as the user view."""
    user = await _account(db)
    pictures = await _session_row(db, user, kind="image", model="openai/gpt-5-image-mini")
    await _spend(
        db,
        user,
        credits=12_612,
        reason="image.generate",
        session_id=pictures,
        model="openai/gpt-5-image-mini",
        when=_at(1),
    )

    mine = await usage_router.my_usage(user, db, days=7)
    everyone = await usage_router.usage(user, db, days=7)

    assert everyone["totals"]["credits"] == mine["totals"]["credits"] == 12_612
    assert everyone["totals"]["activeUsers"] == 1
    assert everyone["byModel"] == [
        {
            "model": "openai/gpt-5-image-mini",
            "credits": 12_612,
            "requests": 1,
            "users": 1,
            "units": 0,
            "unit": "",
        }
    ]
    assert everyone["topUsers"][0]["credits"] == 12_612


async def test_settle_records_which_model_took_the_money(db) -> None:
    """`settle` records the model on the deduction."""
    user = await _account(db)
    settle(db, user, 900, reason="chat.completion", session_id="s-1", model="vendor/quality")
    await db.commit()

    row = (await db.exec(sa.select(CreditLedger))).one()[0]
    assert (row.model, row.delta, row.reason) == ("vendor/quality", -900, "chat.completion")


async def test_every_day_of_the_window_is_on_the_chart(db) -> None:
    """Days with no requests appear as zero rows."""
    user = await _account(db)
    chat = await _session_row(db, user, kind="chat", model="local/gemma-4-26b-a4b")
    for day in (0, 3):
        await _turn(db, chat, model="local/gemma-4-26b-a4b", when=_at(day))

    report = await usage_router.my_usage(user, db, days=7)

    days = report["daily"]
    assert len(days) == 7
    dates = [row["date"] for row in days]
    assert dates == sorted(dates)
    assert dates[-1] == datetime.now(UTC).date().isoformat()
    assert [row["requests"] for row in days] == [0, 0, 0, 1, 0, 0, 1]


async def test_a_cycle_that_came_due_refills_itself(db) -> None:
    """A cycle past its reset refills on read, without a cron."""
    user = await _account(db, allowance=1_000_000)
    user.credits_used = 999_999
    user.cycle_resets_at = _at(0.5)  # came due half a day ago
    db.add(user)
    await db.commit()

    assert refill_if_due(db, user) is True
    await db.commit()

    assert user.credits_used == 0
    assert user.cycle_resets_at > datetime.now(UTC)
    # The refill is on the ledger.
    grants = (
        await db.exec(sa.select(CreditLedger).where(CreditLedger.reason == "allowance.refill"))
    ).all()
    assert [row[0].delta for row in grants] == [1_000_000]


async def test_a_cycle_still_running_is_left_alone(db) -> None:
    """A mid-cycle call does not reset `credits_used`."""
    user = await _account(db, allowance=1_000_000)
    user.credits_used = 400
    user.cycle_resets_at = datetime.now(UTC) + timedelta(days=9)
    db.add(user)
    await db.commit()

    assert refill_if_due(db, user) is False
    assert user.credits_used == 400


async def test_this_month_starts_when_the_allowance_actually_refills(db) -> None:
    """The usage window starts at the KST cycle reset, not the UTC month."""
    # 00:00 KST on the first: the refill.
    reset = datetime(2026, 8, 31, 15, 0, tzinfo=UTC)
    # An hour after it, the cycle in progress is September's.
    assert cycle_start(reset + timedelta(hours=1)) == reset
    # An hour before it, the cycle in progress is still August's.
    assert cycle_start(reset - timedelta(hours=1)) == datetime(2026, 7, 31, 15, 0, tzinfo=UTC)
    # Both definitions agree on the next boundary.
    assert next_cycle_reset(reset + timedelta(hours=1)) == datetime(2026, 9, 30, 15, 0, tzinfo=UTC)


async def test_every_charge_says_which_surface_it_came_from(db) -> None:
    """`settle` derives `surface` from the reason when the caller omits it."""
    from app.services.credits import surface_for

    assert surface_for("chat.completion") == "chat"
    assert surface_for("chat.title") == "chat"
    assert surface_for("report.generate") == "report"
    assert surface_for("report.factcheck") == "report"
    assert surface_for("page.generate") == "report"
    assert surface_for("deck.generate") == "slides"
    assert surface_for("deck.rewrite") == "slides"
    assert surface_for("image.generate") == "image"
    assert surface_for("audio.generate") == "av"
    assert surface_for("video.generate") == "av"
    # 유도할 수 없으면 비워 둔다.
    assert surface_for("document.plan") is None
    assert surface_for("document.revise") is None
    assert surface_for("design.extract") is None

    # 원장에 적힌다.
    user = await _account(db)
    settle(db, user, 900, reason="deck.factcheck", session_id="s-1", model="vendor/quality")
    await db.commit()
    row = (await db.exec(sa.select(CreditLedger))).one()[0]
    assert row.surface == "slides"


async def test_a_caller_that_knows_better_still_wins(db) -> None:
    """An explicit `surface` wins over the derived one."""
    user = await _account(db)
    settle(
        db,
        user,
        100,
        reason="document.plan",
        session_id="s-2",
        model="vendor/quality",
        surface="slides",
    )
    await db.commit()
    row = (await db.exec(sa.select(CreditLedger))).one()[0]
    assert row.surface == "slides"
