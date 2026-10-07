"""Report and deck turns: internal material stays strict-local, and an Auto session's lane
chooses the writer the way it does for a chat turn."""

import pytest

from app.models.chat import ChatSession, RoutingMode, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.routers import sessions
from app.services.workspace_context import ContextBlock


def _model(model_id: str, **extra) -> dict:
    return {"id": model_id, "label": model_id, "kinds": ["chat", "report", "slides"],
            "dataBoundary": "hybrid", "strictLocal": False, **extra}


HYBRID = _model("local/qwen")
STRICT = _model("strict-local/qwen", dataBoundary="self_hosted", strictLocal=True)
EXTERNAL = _model("vendor/big", dataBoundary="external")
USER = User(id="u-doc", email="person@example.test", password_hash="hash", name="Person")


def _session(kind=SessionKind.report, mode=RoutingMode.manual) -> ChatSession:
    return ChatSession(
        id="s-doc", user_id=USER.id, kind=kind, model=HYBRID["id"], routing_mode=mode
    )


@pytest.mark.asyncio
async def test_a_document_with_an_attachment_is_written_strict_local():
    blocks = [ContextBlock(source="attachment", text="사내 매출표", trusted=False)]
    for kind in (SessionKind.report, SessionKind.slides):
        model, strict, note = await sessions._document_route(
            db=None, user=USER, session=_session(kind),
            policy=Governance(internal_data_strict_local=True),
            catalogue=[HYBRID, STRICT, EXTERNAL], model=HYBRID, blocks=blocks,
            attachments=["f1"], history=[], request="첨부 자료로 보고서를 써 줘",
        )
        assert model["id"] == "strict-local/qwen" and strict
        assert note["action"] == "internal_strict_local"


@pytest.mark.asyncio
async def test_internal_material_with_no_strict_model_is_refused():
    out = await sessions._document_route(
        db=None, user=USER, session=_session(), policy=Governance(internal_data_strict_local=True),
        catalogue=[EXTERNAL], model=EXTERNAL, blocks=[], attachments=["f1"], history=[],
        request="첨부로 써 줘",
    )
    assert out.status_code == 409


@pytest.mark.asyncio
async def test_an_auto_document_without_internal_material_goes_through_the_lane(monkeypatch):
    calls = {}

    async def lane(**kwargs):
        calls.update(kwargs)
        return EXTERNAL, {"mode": "auto_quality", "decision": "routed"}

    monkeypatch.setattr(sessions, "_resolve_cost_routing", lane)
    model, strict, note = await sessions._document_route(
        db=None, user=USER, session=_session(SessionKind.slides, RoutingMode.auto_quality),
        policy=Governance(internal_data_strict_local=True), catalogue=[HYBRID, STRICT, EXTERNAL],
        model=HYBRID, blocks=[], attachments=None, history=[], request="양자 컴퓨팅 동향 발표",
    )
    assert model["id"] == "vendor/big" and not strict
    assert note == {"costRouting": {"mode": "auto_quality", "decision": "routed"}}
    assert calls["quality_model"]["id"] == "local/qwen"
    # A manual session is left on the model it chose.
    model, _, note = await sessions._document_route(
        db=None, user=USER, session=_session(), policy=Governance(internal_data_strict_local=True),
        catalogue=[HYBRID, STRICT, EXTERNAL], model=HYBRID, blocks=[], attachments=None,
        history=[], request="보고서",
    )
    assert model["id"] == "local/qwen" and note is None


def test_auto_is_accepted_for_reports_and_decks():
    assert {SessionKind.report, SessionKind.slides} <= sessions._AUTO_KINDS
