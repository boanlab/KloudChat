"""이미지 고치기: a picture changed as told by a model that edits. The source goes to the model as
an image content part, the result is a new version linked to it (the source kept as it was),
billed like a picture; models that only draw are refused, and strict-local or internal material
keeps the picture inside, refused here where no strict-local model edits (with one:
test_a_strict_local_picture_is_edited_inside)."""

from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.chat import ChatSession, Message, SessionKind
from app.models.governance import Governance
from app.models.user import CreditLedger, User
from app.models.workspace import Artifact, ArtifactKind, StoredFile
from app.routers import sessions
from app.schemas.chat import ImageRequest
from app.services import files as file_service
from app.services import imagegen, models

EDITOR = {
    "id": "google/gemini-3-pro-image", "kinds": ["image"], "modality": "image",
    "dataBoundary": "external", "creditCost": 240, "inputCreditCost": 4, "editsImages": True,
}
DRAWER = {
    "id": "bytedance-seed/seedream-5-5", "kinds": ["image"], "modality": "image",
    "dataBoundary": "external", "creditCost": 17, "editsImages": False,
}
STRICT_DRAWER = {
    "id": "strict-local/z-image-turbo", "kinds": ["image"], "modality": "image",
    "dataBoundary": "self_hosted", "strictLocal": True, "creditCost": 0, "editsImages": False,
}
STRICT_CHAT = {
    "id": "strict-local/qwen", "kinds": ["chat"], "modality": "chat",
    "dataBoundary": "self_hosted", "strictLocal": True, "creditCost": 0,
}
CATALOGUE = {
    "models": [EDITOR, DRAWER, STRICT_DRAWER, STRICT_CHAT],
    "defaultModelByKind": {"image": EDITOR["id"]},
}


def _png(colour: tuple[int, int, int], size: int = 64) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(out, "PNG")
    return out.getvalue()


# ── capability ─────────────────────────────────────────────────────────────


def test_only_models_checked_to_edit_say_they_edit():
    for model_id in imagegen.EDITING_MODELS:
        assert imagegen.edits_images(model_id, {})
    # not checked, so off until the gateway declares it; the self-hosted drawers draw from text
    assert not imagegen.edits_images("google/gemini-nano-banana-2.1", {})
    assert not imagegen.edits_images("bytedance-seed/seedream-5-5", {})
    assert not imagegen.edits_images("strict-local/z-image-turbo", {})
    assert not imagegen.edits_images("strict-local/qwen-image-2512", {})
    # the gateway's declaration wins either way
    assert imagegen.edits_images("strict-local/qwen-image-edit", {"kchat_edits_images": True})
    assert not imagegen.edits_images(EDITOR["id"], {"kchat_edits_images": False})


def test_the_catalogue_carries_the_flag():
    row = models._shape({
        "model_name": EDITOR["id"],
        "litellm_params": {"model": f"openrouter/{EDITOR['id']}"},
        "model_info": {"mode": "image_generation", "output_cost_per_token": 0.00012,
                       "kchat_data_boundary": "external"},
    })
    assert row is not None and row["editsImages"] is True
    drawer = models._shape({
        "model_name": DRAWER["id"],
        "litellm_params": {"model": f"openrouter/{DRAWER['id']}"},
        "model_info": {"mode": "image_generation", "output_cost_per_token": 0.0000083832},
    })
    assert drawer is not None and drawer["editsImages"] is False
    chat = models._shape({
        "model_name": "vendor/chat",
        "model_info": {"mode": "chat", "output_cost_per_token": 0.00001},
    })
    assert chat is not None and chat["editsImages"] is False


# ── the call ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_source_goes_beside_the_instruction(monkeypatch):
    sent: list[dict] = []
    reply = "data:image/png;base64," + base64.b64encode(_png((0, 0, 255))).decode()

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"images": [{"image_url": {"url": reply}}]}}],
            "usage": {"prompt_tokens": 580, "completion_tokens": 1266, "cost": 0.137},
        })

    real = httpx.AsyncClient
    monkeypatch.setattr(imagegen.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    source = imagegen.source_image(_png((220, 30, 30)))
    made = await imagegen.generate(
        base_url="http://llm", api_key="k", model=EDITOR["id"],
        prompt=imagegen.edit_prompt("배경만 파랗게"), aspect="16:9", source=source,
    )
    assert made.width == 64 and made.cost_usd == 0.137
    content = sent[0]["messages"][0]["content"]
    assert content[0]["type"] == "text" and "배경만 파랗게" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == source.data_url
    assert base64.b64decode(source.data_url.split(",", 1)[1]) == _png((220, 30, 30))
    # an edit keeps the source's shape: no ratio asked (Recraft refuses it on edits)
    assert "image_config" not in sent[0]


def test_a_large_source_is_scaled_down_before_it_leaves():
    big = io.BytesIO()
    Image.new("RGB", (4096, 1024), (1, 2, 3)).save(big, "BMP")
    source = imagegen.source_image(big.getvalue())
    assert source.mime == "image/png" and (source.width, source.height) == (2048, 512)
    with pytest.raises(imagegen.ImageError):
        imagegen.source_image(b"not a picture")


# ── the route ──────────────────────────────────────────────────────────────


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[
            metadata.tables[m.__tablename__]
            for m in (User, ChatSession, Message, Artifact, StoredFile, CreditLedger)])

    policy = {"policy": Governance(internal_data_strict_local=False)}
    calls: list[dict] = []
    audits: list[str] = []

    async def current():
        return policy["policy"]

    async def no_history(db, session_id):
        return []

    async def catalogue(force: bool = False):
        return CATALOGUE

    async def nothing(*args, **kwargs):
        return None

    async def credentials(user):
        return "http://llm", "key"

    async def generate(**kwargs):
        calls.append(kwargs)
        data = _png((0, 0, 255))
        return imagegen.GeneratedImage(data=data, mime="image/png", input_tokens=580,
                                       output_tokens=1266, width=64, height=64, cost_usd=0.137)

    async def audit(user, request, action, detail, *, metadata=None):
        audits.append(action)

    monkeypatch.setattr(sessions, "_require_egress_policy", current)
    monkeypatch.setattr(sessions, "_history", no_history)
    monkeypatch.setattr(sessions.model_service, "list_models", catalogue)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", nothing)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions.imagegen, "generate", generate)
    monkeypatch.setattr(sessions, "_audit_policy", audit)

    me = User(id="me", email="me@example.test", name="나", password_hash="x",
              monthly_credits=100_000)
    original = _png((220, 30, 30))
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add(me)
        db.add(ChatSession(id="s1", user_id="me", kind=SessionKind.image, model=EDITOR["id"]))
        db.add(ChatSession(id="s-strict", user_id="me", kind=SessionKind.image,
                           model=STRICT_CHAT["id"]))
        for file_id, origin in (("f-made", "generated"), ("f-upload", "upload")):
            key = file_service.write_blob("me", file_id, "image.png", original)
            db.add(StoredFile(id=file_id, user_id="me", origin=origin, session_id="s1",
                              name=f"{file_id}.png", mime="image/png", size=len(original),
                              storage_key=key))
        db.add(Artifact(id="a-made", user_id="me", session_id="s1", kind=ArtifactKind.image,
                        title="빨간 정사각형", data={
                            "kind": "image", "prompt": "빨간 정사각형", "aspect": "1:1",
                            "model": DRAWER["id"],
                            "src": f"{settings.api_prefix}/files/f-made/content"}))
        db.add(Artifact(id="a-inside", user_id="me", session_id="s1", kind=ArtifactKind.image,
                        title="내부에서 그림", data={
                            "kind": "image", "prompt": "내부에서 그림", "aspect": "1:1",
                            "model": "strict-local/z-image-turbo",
                            "src": f"{settings.api_prefix}/files/f-made/content"}))
        await db.commit()

    class Stack:
        pass

    out = Stack()
    out.engine, out.policy, out.calls, out.audits, out.original = (
        engine, policy, calls, audits, original)
    out.db = lambda: AsyncSession(engine, expire_on_commit=False)
    out.me = me
    return out


@pytest.mark.asyncio
async def test_an_edit_is_a_new_version_linked_to_its_source(stack):
    async with stack.db() as db:
        me = await db.get(User, "me")
        rows = await sessions.generate_images(
            "s1", ImageRequest(prompt="배경만 파랗게", model=EDITOR["id"],
                               source_artifact_id="a-made"), me, db, None)
    assert len(rows) == 1
    made = rows[0].data
    assert made["editOf"]["artifactId"] == "a-made" and made["editOf"]["fileId"] == "f-made"
    assert made["lineage"] == "a-made" and made["editVersion"] == 2
    assert made["prompt"] == "배경만 파랗게" and made["model"] == EDITOR["id"]
    # the stored source went to the model, unchanged
    assert stack.calls[0]["source"].data == stack.original
    assert "배경만 파랗게" in stack.calls[0]["prompt"]

    async with stack.db() as db:
        source = await db.get(Artifact, "a-made")
        assert source.data["src"].endswith("/files/f-made/content")
        assert file_service.read_blob((await db.get(StoredFile, "f-made")).storage_key) == (
            stack.original)
        ledger = (await db.exec(select(CreditLedger))).all()
        assert [row.reason for row in ledger] == ["image.edit"]
        # charged like a picture: the reported upstream cost
        assert ledger[0].delta == -sessions.charge_for_image(EDITOR, 580, 1266, 0.137)
        turns = (await db.exec(select(Message).where(Message.session_id == "s1"))).all()
        assert [m.content for m in turns if m.role.value == "user"] == ["배경만 파랗게"]

        # the edited picture edited again: the same line, the next number
        me = await db.get(User, "me")
        again = await sessions.generate_images(
            "s1", ImageRequest(prompt="원을 노랗게", model=EDITOR["id"],
                               source_artifact_id=rows[0].id), me, db, None)
    assert again[0].data["lineage"] == "a-made" and again[0].data["editVersion"] == 3
    assert again[0].data["editOf"]["artifactId"] == rows[0].id


@pytest.mark.asyncio
async def test_a_file_box_picture_finds_the_picture_it_shows(stack):
    async with stack.db() as db:
        me = await db.get(User, "me")
        rows = await sessions.generate_images(
            "s1", ImageRequest(prompt="흑백으로", model=EDITOR["id"],
                               source_file_id="f-made"), me, db, None)
    assert rows[0].data["editOf"]["artifactId"] == "a-made"
    assert rows[0].data["lineage"] == "a-made"


@pytest.mark.asyncio
async def test_a_model_that_only_draws_is_refused(stack):
    async with stack.db() as db:
        me = await db.get(User, "me")
        with pytest.raises(HTTPException) as refused:
            await sessions.generate_images(
                "s1", ImageRequest(prompt="배경만 파랗게", model=DRAWER["id"],
                                   source_artifact_id="a-made"), me, db, None)
    assert refused.value.status_code == 422
    assert refused.value.detail == "image_model_cannot_edit"
    assert stack.calls == []


@pytest.mark.asyncio
async def test_strict_local_keeps_the_picture_inside_and_refuses(stack):
    async with stack.db() as db:
        me = await db.get(User, "me")
        with pytest.raises(HTTPException) as refused:
            await sessions.generate_images(
                "s-strict", ImageRequest(prompt="배경만 파랗게", model=EDITOR["id"],
                                         source_artifact_id="a-made"), me, db, None)
    assert refused.value.status_code == 409
    assert refused.value.detail == "image_edit_strict_local"
    assert stack.calls == []


@pytest.mark.asyncio
async def test_an_uploaded_picture_is_internal_material(stack):
    stack.policy["policy"] = Governance(internal_data_strict_local=True)
    async with stack.db() as db:
        me = await db.get(User, "me")
        with pytest.raises(HTTPException) as refused:
            await sessions.generate_images(
                "s1", ImageRequest(prompt="배경만 파랗게", model=EDITOR["id"],
                                   source_file_id="f-upload"), me, db, None)
    assert refused.value.detail == "image_edit_strict_local"
    assert stack.audits == ["privacy.internal_data_requires_strict_local"]
    assert stack.calls == []

    # the organisation leaving internal material free: it goes, like an attachment
    stack.policy["policy"] = Governance(internal_data_strict_local=False)
    async with stack.db() as db:
        me = await db.get(User, "me")
        rows = await sessions.generate_images(
            "s1", ImageRequest(prompt="배경만 파랗게", model=EDITOR["id"],
                               source_file_id="f-upload"), me, db, None)
    made = rows[0].data
    assert made["lineage"] == "file:f-upload" and made["internalSource"] is True
    assert made["editOf"]["artifactId"] is None


@pytest.mark.asyncio
async def test_another_accounts_picture_is_not_found(stack):
    async with stack.db() as db:
        db.add(User(id="other", email="o@example.test", name="남", password_hash="x"))
        db.add(Artifact(id="a-theirs", user_id="other", kind=ArtifactKind.image, title="x",
                        data={"src": f"{settings.api_prefix}/files/f-made/content"}))
        await db.commit()
        me = await db.get(User, "me")
        with pytest.raises(HTTPException) as refused:
            await sessions.generate_images(
                "s1", ImageRequest(prompt="x", model=EDITOR["id"],
                                   source_artifact_id="a-theirs"), me, db, None)
    assert refused.value.status_code == 404


@pytest.mark.asyncio
async def test_a_picture_a_strict_local_model_drew_stays_inside(stack):
    stack.policy["policy"] = Governance(internal_data_strict_local=False)
    async with stack.db() as db:
        me = await db.get(User, "me")
        with pytest.raises(HTTPException) as refused:
            await sessions.generate_images(
                "s1", ImageRequest(prompt="배경만 파랗게", model=EDITOR["id"],
                                   source_artifact_id="a-inside"), me, db, None)
    assert refused.value.status_code == 409
    assert refused.value.detail == "image_edit_strict_local"
    assert stack.calls == []
