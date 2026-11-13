"""A search-index delete that fails is kept and retried until it lands, and a vector hit from a
file that is gone (or since changed) is never used as evidence."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import utcnow
from app.models.workspace import IndexDeletion, StoredFile
from app.routers import workspace as ws
from app.services import files as file_service
from app.services import index_cleanup, index_client, knowledge
from app.services.tools import builtin

KEY = "k" * 32
DOC = "d" * 32


@pytest.fixture
async def index(monkeypatch, tmp_path):
    monkeypatch.setattr(file_service.settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: SQLModel.metadata.create_all(
            c, tables=[StoredFile.__table__, IndexDeletion.__table__]))

    state = type("Index", (), {})()
    state.up = False
    state.configured = True
    state.calls = []

    async def available():
        return state.configured

    async def forget_document(*, collection, doc_id):
        state.calls.append((collection, doc_id))
        return state.up

    async def forget_collection(*, collection):
        state.calls.append((collection, None))
        return state.up

    monkeypatch.setattr(index_client, "available", available)
    monkeypatch.setattr(index_client, "forget_document", forget_document)
    monkeypatch.setattr(index_client, "forget_collection", forget_collection)
    state.local = lambda: AsyncSession(engine, expire_on_commit=False)
    try:
        yield state
    finally:
        await engine.dispose()


async def _pending(index) -> list[IndexDeletion]:
    async with index.local() as db:
        return list((await db.exec(select(IndexDeletion))).all())


async def test_a_failed_delete_is_kept_once_and_retried_until_it_lands(index):
    async with index.local() as db:
        assert not await index_cleanup.forget_document(db, collection=KEY, doc_id=DOC)
        assert not await index_cleanup.forget_document(db, collection=KEY, doc_id=DOC,
                                                       commit=True)
        assert not await index_cleanup.forget_collection(db, collection=KEY, commit=True)
    rows = await _pending(index)
    assert sorted(((r.collection, r.doc_id) for r in rows), key=str) == [(KEY, DOC), (KEY, None)]

    # still down: tried again, kept, counted
    async with index.local() as db:
        assert await index_cleanup.retry(db) == 0
    assert {r.attempts for r in await _pending(index)} == {2}

    index.up = True
    index.calls.clear()
    async with index.local() as db:
        assert await index_cleanup.retry(db) == 2
    assert await _pending(index) == []
    assert sorted(index.calls, key=str) == sorted([(KEY, DOC), (KEY, None)], key=str)


async def test_nothing_is_kept_without_an_index_or_for_an_id_it_would_never_take(index):
    async with index.local() as db:
        await index_cleanup.forget_document(db, collection=KEY, doc_id="../../etc",
                                            commit=True)
        await index_cleanup.forget_collection(db, collection="bad key", commit=True)
        index.configured = False
        await index_cleanup.forget_document(db, collection=KEY, doc_id=DOC, commit=True)
        # nothing to retry against: the pass waits, the row (none here) stays
        assert await index_cleanup.retry(db) == 0
    assert await _pending(index) == []


async def test_an_entry_indexed_again_since_is_left_and_an_old_refusal_given_up(index):
    async with index.local() as db:
        db.add(StoredFile(id=DOC, user_id="me", name="a.txt", text="본문",
                          indexed_at=utcnow(), index_collection=KEY))
        db.add(IndexDeletion(collection=KEY, doc_id=DOC))
        db.add(IndexDeletion(collection=KEY, doc_id="e" * 32,
                             created_at=utcnow() - index_cleanup.GIVE_UP - timedelta(days=1)))
        await db.commit()
        assert await index_cleanup.retry(db) == 1
    # the live entry was not deleted; the stale one was tried, then dropped
    assert index.calls == [(KEY, "e" * 32)]
    assert await _pending(index) == []


async def test_deleting_a_file_while_the_index_is_down_leaves_a_retry(index):
    async with index.local() as db:
        stored = StoredFile(id=DOC, user_id="me", name="a.txt", text="본문", storage_key="me/a",
                            indexed_at=utcnow(), index_collection=KEY)
        db.add(stored)
        await db.commit()
        await ws._drop_file(db, stored)
        await db.commit()
        assert await db.get(StoredFile, DOC) is None
    assert [(r.collection, r.doc_id) for r in await _pending(index)] == [(KEY, DOC)]


def test_a_vector_hit_counts_only_while_its_file_still_holds_the_passage():
    documents = [("규정.pdf", "제1조 목적.\n\n\n제2조   휴학은 학기 전에 신청한다.", None),
                 ("규정.pdf", "다른 판의 같은 이름 파일", None)]
    hits = [
        {"document": "규정.pdf", "index": 2, "text": "제2조 휴학은 학기 전에\n신청한다."},
        # deleted file whose name a living one shares
        {"document": "규정.pdf", "index": 1, "text": "제2조 휴학은 아무 때나 신청한다."},
        # a deleted file's entry
        {"document": "지운 자료.pdf", "index": 1, "text": "제1조 목적."},
        {"document": "규정.pdf", "index": 3, "text": ""},
    ]
    assert [h["index"] for h in builtin._living_hits(hits, documents)] == [2]


async def test_the_knowledge_search_leaves_a_gone_files_passage_out(monkeypatch):
    monkeypatch.setattr(knowledge, "_WHOLE_SHELF", 10)
    living = "제12조(휴학) 휴학은 학기 개시일 전까지 신청한다. " * 3

    async def search(*, collection, query, limit=4):
        return [{"document": "학칙.pdf", "index": 9, "score": 0.99,
                 "text": "휴학은 언제든 전화로 신청한다."},
                {"document": "옛 학칙.pdf", "index": 1, "score": 0.99,
                 "text": "휴학은 언제든 전화로 신청한다."}]

    monkeypatch.setattr(index_client, "search", search)
    tool = builtin.knowledge_tool([("학칙.pdf", living, None)], collection=KEY)
    found = await tool.run({"query": "휴학 신청"})
    assert "전화로" not in found.content and "학기 개시일" in found.content
