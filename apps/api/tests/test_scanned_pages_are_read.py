"""Scanned pages and document photos are read in the background, by the engine first and a
strict-local vision model for the pages the engine is unsure of.

The readers are replaced by fakes here; what is checked is which pages are read, by
whom, what is kept, when a page is marked for review, that a page never reaches a model
that is not strict-local with vision, and that the job survives replicas, failures and
deletion.
"""

from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from reportlab.pdfgen import canvas
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import CreditLedger, User, utcnow
from app.models.workspace import FilePage, StoredFile
from app.routers import workspace
from app.services import files as file_service
from app.services import (
    index_client,
    ocr,
    ocr_jobs,
    settings_store,
    tools_gateway,
    workspace_context,
)
from app.services import litellm as litellm_service
from app.services import models as model_service


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    return "JSON"


VISION = "strict-local/vision-27b"
CATALOGUE = {
    "litellmAvailable": True,
    "models": [
        {"id": VISION, "strictLocal": True, "supportsVision": True, "kinds": ["chat"]},
        {
            "id": "strict-local/text-only",
            "strictLocal": True,
            "supportsVision": False,
            "kinds": ["chat"],
        },
        {
            "id": "openai/outside-vision",
            "strictLocal": False,
            "supportsVision": True,
            "kinds": ["chat"],
        },
    ],
}


CONTAINED = {"kchat_data_boundary": "self_hosted", "kchat_strict_local": True}
#: What LiteLLM holds under each name; the vision alias has one contained deployment.
DEPLOYMENTS = [{"model_name": VISION, "model_info": CONTAINED}]


# ── documents ───────────────────────────────────────────────────────────


def _pdf(pages: list[str | None]) -> bytes:
    """A PDF whose pages carry the given text layer; None makes a scan (a picture only)."""
    buffer = io.BytesIO()
    sheet = canvas.Canvas(buffer)
    for text in pages:
        if text is None:
            sheet.rect(100, 400, 300, 200, fill=1)
        else:
            sheet.drawString(72, 720, text)
        sheet.showPage()
    sheet.save()
    return buffer.getvalue()


def _picture(kind: str = "PNG", frames: int = 1) -> bytes:
    buffer = io.BytesIO()
    images = [Image.new("RGB", (64, 64), (255, 255, 255)) for _ in range(frames)]
    images[0].save(buffer, format=kind, save_all=frames > 1, append_images=images[1:])
    return buffer.getvalue()


LAYER = "This page has its own text layer with plenty of characters on it."


# ── fakes ───────────────────────────────────────────────────────────────


class Readers:
    """The engine and the vision model, recorded."""

    def __init__(self):
        self.engine_pages: list[int] = []
        self.vision_calls: list[str] = []
        self.engine: dict[int, dict] = {}
        self.vision: dict[int, str] = {}
        self.fail_engine = 0
        self.refuse = False

    async def engine_read(self, cfg, data, page, *, want_image):
        if self.refuse:
            raise ocr.OcrRefused("쪽 수가 너무 많아 글자를 읽지 못했습니다.")
        if self.fail_engine:
            self.fail_engine -= 1
            raise ocr.OcrError("engine down")
        self.engine_pages.append(page)
        found = {
            "text": f"엔진 {page}쪽 금액 4,200,000원",
            "confidence": 0.97,
            "tables": [],
            "lines": [{"text": "x", "conf": 0.97, "box": [0, 0, 1, 1]}],
            "dpi": 200,
            "width": 100,
            "height": 100,
        }
        found.update(self.engine.get(page, {}))
        if want_image:
            found["image"] = "aGVsbG8="
            found["image_mime"] = "image/jpeg"
        return found

    async def vision_read(self, cfg, picture, mime, api_key):
        page = len(self.vision_calls) + 1
        self.vision_calls.append(cfg.vision_model)
        return self.vision.get(page, f"엔진 {self.engine_pages[-1]}쪽 금액 4,200,000원")


@pytest.fixture
def readers(monkeypatch):
    fake = Readers()
    monkeypatch.setattr(ocr, "engine_read", fake.engine_read)
    monkeypatch.setattr(ocr, "vision_read", fake.vision_read)
    return fake


@pytest.fixture
def configured(monkeypatch):
    """An engine address and the catalogue above; settings as stored in `values`."""
    values: dict[str, str] = {}
    real_tools = settings_store.tools_config

    async def tools():
        import dataclasses

        return dataclasses.replace(await real_tools(), ocr="http://gateway.test/tools/ocr")

    async def all_values(force: bool = False):
        return dict(values)

    async def catalogue(force: bool = False):
        return CATALOGUE

    async def egress():
        return CATALOGUE

    async def deployments():
        return list(DEPLOYMENTS)

    monkeypatch.setattr(settings_store, "tools_config", tools)
    monkeypatch.setattr(settings_store, "all_values", all_values)
    monkeypatch.setattr(litellm_service, "model_info", deployments)
    monkeypatch.setattr(model_service, "list_models", catalogue)
    monkeypatch.setattr(model_service, "list_models_for_egress", egress)
    return values


_USERS_DDL = """
CREATE TABLE users (
    id TEXT PRIMARY KEY, email TEXT, password_hash TEXT, name TEXT, role TEXT, status TEXT,
    monthly_credits INTEGER DEFAULT 0, credits_used INTEGER DEFAULT 0, cycle_resets_at DATETIME,
    litellm_user_id TEXT, litellm_key TEXT, litellm_key_preview TEXT,
    litellm_key_issued_at DATETIME, avatar_color TEXT, allowed_models TEXT, preferences TEXT,
    created_at DATETIME, last_active_at DATETIME, email_verified_at DATETIME
)
"""


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(file_service.settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [m.__table__ for m in (StoredFile, FilePage, CreditLedger)]
    async with engine.begin() as connection:
        # Hand-written: the users model has JSONB defaults SQLite cannot compile.
        await connection.execute(sa.text(_USERS_DDL))
        await connection.run_sync(lambda c: SQLModel.metadata.create_all(c, tables=tables))

    def session():
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(ocr_jobs.core_db, "SessionLocal", session)
    async with session() as s:
        s.add(User(id="me", email="me@example.test", password_hash="x", name="Me"))
        await s.commit()
    yield session


async def _stored(session, name: str, mime: str, data: bytes, **extra) -> StoredFile:
    async with session() as s:
        row = StoredFile(user_id="me", name=name, size=len(data), mime=mime, **extra)
        row.storage_key = file_service.write_blob("me", row.id, name, data)
        try:
            row.text = await file_service.text_of(name, mime, data)
        except RuntimeError as exc:
            row.error = str(exc)
        await ocr_jobs.queue_if_scanned(row, data)
        s.add(row)
        await s.commit()
        return row


async def _reload(session, file_id: str) -> tuple[StoredFile, list[FilePage]]:
    async with session() as s:
        row = await s.get(StoredFile, file_id)
        pages = (
            await s.exec(
                select(FilePage).where(FilePage.file_id == file_id).order_by(FilePage.page)
            )
        ).all()
        return row, list(pages)


# ── which pages need reading ───────────────────────────────────────────


def test_only_the_pages_without_a_text_layer_are_planned():
    found = ocr.plan("mixed.pdf", "application/pdf", _pdf([LAYER, None, LAYER, None]), 300)
    assert found is not None and found.kind == "pdf"
    assert found.pages == (2, 4) and found.total == 4
    assert set(found.layer) == {1, 3}


def test_a_pdf_with_text_everywhere_needs_no_reading():
    assert ocr.plan("text.pdf", "application/pdf", _pdf([LAYER, LAYER]), 300) is None


def test_pages_past_the_limit_are_counted_but_not_read():
    found = ocr.plan("long.pdf", "application/pdf", _pdf([None] * 5), 3)
    assert found.pages == (1, 2, 3) and found.skipped == 2


def test_a_picture_is_one_page_and_a_tiff_one_page_per_frame():
    assert ocr.plan("a.png", "image/png", _picture(), 300).pages == (1,)
    assert ocr.plan("a.tif", "image/tiff", _picture("TIFF", 3), 300).pages == (1, 2, 3)
    assert ocr.plan("a.docx", "application/zip", b"PK\x03\x04", 300) is None


# ── comparing two readings ─────────────────────────────────────────────


def test_spacing_and_table_marks_do_not_count_as_disagreement():
    engine = "제12조(휴학)①휴학기간은통산4년을\n| 구분 | 금액 |\n| 1학기 | 4,200,000원 |"
    vision = (
        "제12조(휴학) ① 휴학 기간은 통산 4년을\n| 구분 | 금액 |\n|---|---|\n| 1학기 | 4,200,000원 |"
    )
    assert ocr.agreement(engine, vision) > 0.99
    assert not ocr.disagree(engine, vision)


def test_any_differing_number_is_a_disagreement():
    assert ocr.disagree("등록금 4,200,000원 반환", "등록금 4,800,000원 반환")
    assert ocr.disagree("제12조", "제13조")


def test_words_that_differ_a_lot_are_a_disagreement():
    assert ocr.disagree(
        "휴학 기간은 통산 4년을 초과할 수 없다", "복학 신청은 학기 시작 전에 한다 4"
    )


# ── what is in force ───────────────────────────────────────────────────


async def test_both_readers_are_used_when_both_are_there(configured):
    cfg = await ocr.config()
    assert cfg.mode == "both" and cfg.vision_model == VISION


async def test_only_a_strict_local_vision_model_can_be_chosen(configured):
    configured[settings_store.OCR_VISION_MODEL] = "openai/outside-vision"
    cfg = await ocr.config()
    assert cfg.vision_model == "" and cfg.mode == "engine"
    configured[settings_store.OCR_VISION_MODEL] = "strict-local/text-only"
    assert (await ocr.config()).vision_model == ""


async def test_turned_off_means_no_reader(configured):
    configured[settings_store.OCR_ENABLED] = "0"
    assert (await ocr.config()).mode is None


async def test_the_engine_gets_the_tools_key_and_nothing_else_does(configured, monkeypatch):
    monkeypatch.setattr(tools_gateway.settings, "tools_gateway_key", "k" * 40)
    assert await tools_gateway.key_for("http://gateway.test/tools/ocr/ocr") == "k" * 40
    assert await tools_gateway.key_for("http://elsewhere.test/ocr") == ""


# ── one page ────────────────────────────────────────────────────────────


async def test_a_confident_page_is_kept_from_the_engine_alone(configured, readers):
    cfg = await ocr.config()
    read = await ocr.read_page(cfg, b"%PDF", "pdf", "application/pdf", 1, "key")
    assert read.source == "engine" and not read.vision_used and not readers.vision_calls
    assert read.layout["lines"] and not read.needs_review


async def test_an_unsure_page_is_read_again_and_the_vision_text_kept(configured, readers):
    readers.engine[1] = {"confidence": 0.62, "text": "흐릿한 엔진 4,200,000원"}
    readers.vision[1] = "또렷한 비전 4,200,000원"
    cfg = await ocr.config()
    read = await ocr.read_page(cfg, b"%PDF", "pdf", "application/pdf", 1, "key")
    assert read.source == "vision" and read.text == "또렷한 비전 4,200,000원"
    assert readers.vision_calls == [VISION]
    assert read.layout["engine_text"] == "흐릿한 엔진 4,200,000원"


async def test_a_page_with_a_table_goes_to_the_vision_model(configured, readers):
    readers.engine[1] = {"tables": [[0, 0, 10, 10]]}
    read = await ocr.read_page(await ocr.config(), b"%PDF", "pdf", "application/pdf", 1, "key")
    assert read.vision_used and read.has_table


async def test_readings_that_disagree_on_a_number_are_marked_for_review(configured, readers):
    readers.engine[1] = {"confidence": 0.7, "text": "반환액 2,800,000원"}
    readers.vision[1] = "반환액 2,300,000원"
    read = await ocr.read_page(await ocr.config(), b"%PDF", "pdf", "application/pdf", 1, "key")
    assert read.needs_review


async def test_engine_only_marks_a_low_score_for_review(configured, readers):
    configured[settings_store.OCR_MODE] = "engine"
    readers.engine[1] = {"confidence": 0.6}
    read = await ocr.read_page(await ocr.config(), b"%PDF", "pdf", "application/pdf", 1, "key")
    assert read.source == "engine" and read.needs_review and not readers.vision_calls


# ── the page never leaves for a model that is not strict-local ─────────


async def test_the_vision_call_is_refused_when_the_model_stopped_being_strict_local(
    configured, monkeypatch
):
    cfg = await ocr.config()

    async def now_outside():
        return {
            "litellmAvailable": True,
            "models": [
                {"id": VISION, "strictLocal": False, "supportsVision": True, "kinds": ["chat"]}
            ],
        }

    monkeypatch.setattr(model_service, "list_models_for_egress", now_outside)
    sent: list[httpx.Request] = []

    async def record(self, request):  # pragma: no cover — must not be reached
        sent.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", record)
    with pytest.raises(ocr.OcrError):
        await ocr.vision_read(cfg, b"jpeg", "image/jpeg", "key")
    assert sent == []


async def test_the_vision_call_names_the_strict_local_model_without_fallbacks(
    configured, monkeypatch
):
    cfg = await ocr.config()
    sent: list[dict] = []

    async def gateway():
        return "http://litellm.test", "master"

    async def record(self, request):
        import json

        sent.append(
            {
                "url": str(request.url),
                "body": json.loads(request.content),
                "auth": request.headers.get("authorization"),
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": "```\n제1조\n```"}}]})

    monkeypatch.setattr(settings_store, "litellm_config", gateway)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", record)
    text = await ocr.vision_read(cfg, b"jpeg", "image/jpeg", "user-key")
    assert text == "제1조"
    body = sent[0]["body"]
    assert body["model"] == VISION and body["disable_fallbacks"] is True
    assert sent[0]["auth"] == "Bearer user-key"
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )


# ── the background job ─────────────────────────────────────────────────


async def test_a_scanned_upload_is_queued_with_its_text_layer_kept(db, configured):
    row = await _stored(db, "mixed.pdf", "application/pdf", _pdf([LAYER, None]))
    assert row.ocr_state == "queued" and row.ocr_total == 1
    assert "[페이지 1]" in row.text and row.error is None


async def test_a_fully_scanned_pdf_is_queued_instead_of_failing(db, configured):
    row = await _stored(db, "scan.pdf", "application/pdf", _pdf([None, None]))
    assert row.ocr_state == "queued" and row.text == "" and row.error is None


async def test_without_any_reader_the_old_failure_stands(db, configured):
    configured[settings_store.OCR_ENABLED] = "0"
    row = await _stored(db, "scan.pdf", "application/pdf", _pdf([None]))
    assert row.ocr_state is None and "OCR" in (row.error or "")


async def test_the_job_reads_the_pages_and_joins_them_in_order(db, configured, readers):
    row = await _stored(db, "mixed.pdf", "application/pdf", _pdf([LAYER, None, LAYER, None]))
    readers.engine[4] = {"confidence": 0.5, "text": "흐릿 4쪽"}
    readers.vision[1] = "비전 4쪽"
    assert await ocr_jobs.run_one()
    assert not await ocr_jobs.run_one()
    done, pages = await _reload(db, row.id)
    assert done.ocr_state == "done" and done.ocr_done == 2 and done.ocr_total == 2
    assert [p.source for p in pages] == ["text", "engine", "text", "vision"]
    text = done.text
    assert text.index("[페이지 1]") < text.index("[페이지 2]") < text.index("[페이지 4]")
    assert "엔진 2쪽" in text and "비전 4쪽" in text
    async with db() as s:
        ledger = (await s.exec(select(CreditLedger))).all()
    reasons = sorted((r.reason, r.units, r.unit, r.delta) for r in ledger)
    assert reasons == [
        ("ocr.engine", 1, "pages", 0),
        ("ocr.engine", 1, "pages", 0),
        ("ocr.vision", 1, "pages", 0),
    ]


async def test_a_reader_failure_is_retried_then_given_up(db, configured, readers):
    row = await _stored(db, "scan.pdf", "application/pdf", _pdf([None]))
    readers.fail_engine = 99
    for _ in range(ocr_jobs.MAX_ATTEMPTS):
        async with db() as s:
            stuck = await s.get(StoredFile, row.id)
            stuck.ocr_claimed_at = None  # skip the pause between attempts
            s.add(stuck)
            await s.commit()
        assert await ocr_jobs.run_one()
    failed, _ = await _reload(db, row.id)
    assert failed.ocr_state == "failed" and failed.error == ocr_jobs.GAVE_UP


async def test_a_failure_waits_before_the_next_attempt(db, configured, readers):
    row = await _stored(db, "scan.pdf", "application/pdf", _pdf([None]))
    readers.fail_engine = 1
    assert await ocr_jobs.run_one()
    waiting, _ = await _reload(db, row.id)
    assert waiting.ocr_state == "queued"
    assert not await ocr_jobs.run_one()  # still within the pause


async def test_a_document_the_reader_refuses_fails_at_once(db, configured, readers):
    row = await _stored(db, "huge.pdf", "application/pdf", _pdf([None]))
    readers.refuse = True
    assert await ocr_jobs.run_one()
    failed, _ = await _reload(db, row.id)
    assert failed.ocr_state == "failed" and "쪽 수" in failed.error and failed.ocr_attempts == 1


async def test_a_fresh_claim_is_left_alone_and_a_stale_one_taken_over(db, configured, readers):
    row = await _stored(db, "scan.pdf", "application/pdf", _pdf([None, None]))
    async with db() as s:
        held = await s.get(StoredFile, row.id)
        held.ocr_state, held.ocr_claimed_at = "running", utcnow()
        s.add(held)
        await s.commit()
    assert not await ocr_jobs.run_one()
    async with db() as s:
        held = await s.get(StoredFile, row.id)
        held.ocr_claimed_at = utcnow() - ocr_jobs.LEASE - timedelta(seconds=1)
        # The replica that died had read page 1 already.
        s.add(FilePage(file_id=row.id, page=1, source="engine", text="이미 읽은 1쪽"))
        s.add(held)
        await s.commit()
    assert await ocr_jobs.run_one()
    done, pages = await _reload(db, row.id)
    assert done.ocr_state == "done" and readers.engine_pages == [2]
    assert [p.text for p in pages][0] == "이미 읽은 1쪽"


async def test_a_file_deleted_while_being_read_stops_the_job(db, configured, readers, monkeypatch):
    row = await _stored(db, "scan.pdf", "application/pdf", _pdf([None, None, None]))
    real = readers.engine_read

    async def delete_then_read(cfg, data, page, *, want_image):
        if page == 2:
            async with db() as s:
                await s.delete(await s.get(StoredFile, row.id))
                await s.commit()
        return await real(cfg, data, page, want_image=want_image)

    monkeypatch.setattr(ocr, "engine_read", delete_then_read)
    assert await ocr_jobs.run_one()
    gone, _ = await _reload(db, row.id)
    assert gone is None and readers.engine_pages == [1, 2]


async def test_a_photo_with_a_stray_word_stays_a_picture(db, configured, readers):
    readers.engine[1] = {"text": "STOP", "confidence": 0.99}
    row = await _stored(db, "sign.png", "image/png", _picture())
    assert row.ocr_state == "queued"
    assert await ocr_jobs.run_one()
    done, _ = await _reload(db, row.id)
    assert done.ocr_state == "done" and done.text == ""


async def test_a_document_photo_gets_its_text(db, configured, readers):
    readers.engine[1] = {
        "text": "제12조(휴학) 휴학 기간은 통산 4년을 초과할 수 없다.",
        "confidence": 0.98,
    }
    row = await _stored(db, "page.png", "image/png", _picture())
    assert await ocr_jobs.run_one()
    done, _ = await _reload(db, row.id)
    assert done.text.startswith("제12조") and "[페이지" not in done.text


# ── what the model is told meanwhile ───────────────────────────────────


def test_a_file_still_being_read_is_named_as_such():
    from app.services.workspace_context import ContextFile, _file_report

    report = _file_report(
        (ContextFile("scan.pdf", "reading", 0, 0, pages_done=3, pages_total=10),), ()
    )
    assert "3/10쪽" in report and "지어내지 말고" in report


def test_the_reading_state_is_known_to_the_step_detail():
    from app.routers.sessions import _FILE_NOTE, _file_context_step
    from app.services.workspace_context import ContextFile

    assert "reading" in _FILE_NOTE
    step = _file_context_step(
        "context-attachments", "첨부 파일", (ContextFile("scan.pdf", "reading", 0, 0),)
    )
    assert "글자 읽는 중" in step["label"] and "빠짐" not in step["label"]
    assert workspace_context.ContextFile is ContextFile


# ── the routes ─────────────────────────────────────────────────────────


@pytest.fixture
async def client(db, configured, readers):
    app = FastAPI()
    app.include_router(workspace.router)

    async def session():
        async with db() as s:
            yield s

    me = User(id="me", email="me@example.test", password_hash="x", name="Me")
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: me
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
        yield http


async def test_an_uploaded_scan_reports_its_progress_and_is_readable_after(client, db):
    made = (
        await client.post("/files", files={"file": ("scan.pdf", _pdf([None]), "application/pdf")})
    ).json()
    assert made["ocrState"] == "queued" and made["ocrTotal"] == 1 and made["error"] is None
    assert await ocr_jobs.run_one()
    after = (await client.get(f"/files/{made['id']}")).json()
    assert after["ocrState"] == "done" and after["ocrDone"] == 1 and "엔진 1쪽" in after["preview"]


async def test_someone_elses_file_is_not_shown(client, db):
    async with db() as s:
        s.add(User(id="other", email="o@example.test", password_hash="x", name="O"))
        theirs = StoredFile(user_id="other", name="x.pdf", mime="application/pdf")
        s.add(theirs)
        await s.commit()
    assert (await client.get(f"/files/{theirs.id}")).status_code == 404


# ── the migration ──────────────────────────────────────────────────────


def test_the_migration_adds_and_drops_what_ocr_needs_on_sqlite():
    import importlib.util

    path = Path(__file__).parents[1] / "alembic" / "versions" / "0067_file_ocr.py"
    spec = importlib.util.spec_from_file_location("m0067", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("0067", "0066")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE files (id VARCHAR PRIMARY KEY, name VARCHAR)"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        inspector = sa.inspect(conn)
        assert {c.name for c in FilePage.__table__.columns} == {
            c["name"] for c in inspector.get_columns("file_pages")
        }
        added = {c.name for c in StoredFile.__table__.columns if c.name.startswith("ocr_")}
        assert added <= {c["name"] for c in inspector.get_columns("files")}
        foreign = inspector.get_foreign_keys("file_pages")[0]
        assert (
            foreign["referred_table"] == "files" and foreign["options"].get("ondelete") == "CASCADE"
        )
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        inspector = sa.inspect(conn)
        assert "file_pages" not in inspector.get_table_names()
        assert not {c["name"] for c in inspector.get_columns("files")} & added


# ── the administrator's settings ───────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value", "detail"),
    [
        ("ocr_vision_model", "openai/outside-vision", "ocr_vision_model_not_strict_local"),
        ("ocr_vision_model", "strict-local/text-only", "ocr_vision_model_not_strict_local"),
        ("ocr_mode", "cloud", "ocr_mode_invalid"),
        ("ocr_max_pages", "0", "ocr_max_pages_invalid"),
        ("ocr_max_pages", "99999", "ocr_max_pages_invalid"),
        ("ocr_enabled", "yes", "ocr_enabled_invalid"),
    ],
)
async def test_settings_that_would_send_pages_out_or_mean_nothing_are_refused(
    configured, field, value, detail
):
    from fastapi import HTTPException

    from app.routers.admin import _check_ocr
    from app.schemas.admin import SystemSettingsIn

    with pytest.raises(HTTPException) as refused:
        await _check_ocr(SystemSettingsIn(**{field: value}))
    assert refused.value.status_code == 422 and refused.value.detail == detail


async def test_a_strict_local_vision_model_is_accepted(configured):
    from app.routers.admin import _check_ocr, _ocr_settings
    from app.schemas.admin import SystemSettingsIn

    await _check_ocr(
        SystemSettingsIn(ocr_vision_model=VISION, ocr_mode="both", ocr_max_pages="500")
    )
    shown = await _ocr_settings({})
    assert [choice["id"] for choice in shown["visionChoices"]] == [VISION]
    assert shown["modeInForce"] == "both" and shown["engineConfigured"]


async def test_without_the_engine_a_pdf_is_not_queued_but_a_photo_is(db, configured, monkeypatch):
    real_tools = settings_store.tools_config

    async def no_engine():
        import dataclasses

        return dataclasses.replace(await real_tools(), ocr="")

    monkeypatch.setattr(settings_store, "tools_config", no_engine)
    assert (await ocr.config()).mode == "vision"
    scan = await _stored(db, "scan.pdf", "application/pdf", _pdf([None]))
    photo = await _stored(db, "page.png", "image/png", _picture())
    assert scan.ocr_state is None and scan.error
    assert photo.ocr_state == "queued"


def test_pages_read_count_as_free_work_in_usage():
    from app.routers.usage import UNIT_REASONS, _unit_of

    assert {"ocr.engine", "ocr.vision"} <= set(UNIT_REASONS)
    assert _unit_of("ocr-engine") == _unit_of("ocr-vision") == "pages"
    assert _unit_of("strict-local/qwen3.8-27b") == ""


# ── after review: limits, every deployment, the claim, the index ──────


async def test_an_alias_with_an_outside_deployment_is_never_shown_a_page(configured, monkeypatch):
    cfg = await ocr.config()
    twin = {"model_name": VISION, "model_info": {"kchat_data_boundary": "external"}}
    monkeypatch.setattr(ocr_test_module(), "DEPLOYMENTS", [*DEPLOYMENTS, twin])
    sent: list[object] = []

    async def record(self, request):  # pragma: no cover — must not be reached
        sent.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", record)
    with pytest.raises(ocr.OcrError):
        await ocr.vision_read(cfg, b"jpeg", "image/jpeg", "key")
    assert sent == []


def ocr_test_module():
    import sys

    return sys.modules[__name__]


def test_a_picture_claiming_too_many_pixels_is_not_planned(monkeypatch):
    monkeypatch.setattr(ocr, "MAX_PIXELS", 1000)
    assert ocr.plan("big.png", "image/png", _picture(), 300) is None


def test_frames_are_counted_only_up_to_the_page_limit():
    found = ocr.plan("a.tif", "image/tiff", _picture("TIFF", 5), 2)
    # One frame past the limit is counted, so the file shows it was cut short.
    assert found.pages == (1, 2) and found.total == 3 and found.skipped == 1


def test_every_picture_is_re_encoded_under_the_cap_before_a_model_sees_it(monkeypatch):
    import asyncio

    data, mime = asyncio.run(ocr._picture_for_model(_picture(), "image/png", 1))
    assert mime == "image/jpeg" and data[:3] == b"\xff\xd8\xff"
    monkeypatch.setattr(ocr, "MAX_PIXELS", 1000)
    with pytest.raises(ocr.OcrRefused):
        asyncio.run(ocr._picture_for_model(_picture(), "image/png", 1))


async def test_a_worker_whose_claim_was_taken_over_writes_nothing(
    db, configured, readers, monkeypatch
):
    row = await _stored(db, "scan.pdf", "application/pdf", _pdf([None, None]))
    real = readers.engine_read

    async def taken_over_then_read(cfg, data, page, *, want_image):
        if page == 1:
            # Another replica took the job over while this one was reading.
            async with db() as s:
                held = await s.get(StoredFile, row.id)
                held.ocr_claim = "someone-else"
                s.add(held)
                await s.commit()
        return await real(cfg, data, page, want_image=want_image)

    monkeypatch.setattr(ocr, "engine_read", taken_over_then_read)
    assert await ocr_jobs.run_one()
    after, pages = await _reload(db, row.id)
    assert after.ocr_claim == "someone-else" and after.ocr_state == "running"
    assert [p for p in pages if p.source != "text"] == []


async def test_a_file_deleted_while_its_vectors_were_written_has_them_taken_out(
    db, configured, monkeypatch
):
    from app.routers import workspace as ws

    row = await _stored(db, "scan.pdf", "application/pdf", _pdf([None]))
    forgotten: list[tuple[str, str]] = []

    async def shelf(db_, stored):
        return "shelf-key"

    async def delete_while_indexing(db_, stored, collection):
        async with db() as s:
            await s.delete(await s.get(StoredFile, stored.id))
            await s.commit()
        return True

    async def forget(*, collection, doc_id):
        forgotten.append((collection, doc_id))
        return True

    monkeypatch.setattr(ws, "shelf_of", shelf)
    monkeypatch.setattr(ws, "reindex_file", delete_while_indexing)
    monkeypatch.setattr(index_client, "forget_document", forget)
    async with db() as s:
        stored = await s.get(StoredFile, row.id)
        stored.text = "읽은 글"
        s.add(stored)
        await s.commit()
        await ocr_jobs._reindex(s, stored)
    assert forgotten == [("shelf-key", row.id)]


async def test_deleting_a_file_being_read_clears_its_shelf_even_before_indexing(
    client, db, monkeypatch
):
    from app.routers import workspace as ws

    made = (
        await client.post("/files", files={"file": ("scan.pdf", _pdf([None]), "application/pdf")})
    ).json()
    forgotten: list[tuple[str, str]] = []

    async def shelf(db_, stored):
        return "shelf-key"

    async def forget(*, collection, doc_id):
        forgotten.append((collection, doc_id))
        return True

    monkeypatch.setattr(ws, "shelf_of", shelf)
    monkeypatch.setattr(index_client, "forget_document", forget)
    assert (await client.delete(f"/files/{made['id']}")).status_code == 204
    assert forgotten == [("shelf-key", made["id"])]
