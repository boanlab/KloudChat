"""Document edits (fact-check, critique, rewrites, design extraction) follow the chat
turn's egress rules: internal material, a strict-local conversation or personal data
stay on strict-local models, refused when there is none, and the allowlist holds."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models.chat import ChatSession, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind
from app.routers import sessions, workspace

EXTERNAL = {"id": "vendor/big", "kinds": ["chat", "report"], "dataBoundary": "external",
            "creditCost": 5}
CHEAP = {"id": "vendor/cheap", "kinds": ["chat", "report"], "dataBoundary": "external",
         "creditCost": 1}
LOCAL = {"id": "local/qwen", "kinds": ["chat", "report"], "dataBoundary": "hybrid",
         "creditCost": 0}
STRICT = {"id": "strict-local/qwen", "kinds": ["chat", "report"],
          "dataBoundary": "self_hosted", "strictLocal": True, "creditCost": 0}
OTHER_STRICT = {"id": "strict-local/aaa", "kinds": ["chat", "report"],
                "dataBoundary": "self_hosted", "strictLocal": True, "creditCost": 0}
EVERYTHING = [EXTERNAL, CHEAP, LOCAL, STRICT, OTHER_STRICT]
OUTSIDE_ONLY = [EXTERNAL, CHEAP, LOCAL]


class _Db:
    def __init__(self, session: ChatSession | None):
        self.session = session

    async def get(self, _model, _key):
        return self.session


def _user(allowed: list[str] | None = None) -> User:
    return User(id="u-edit", email="person@example.test", password_hash="hash",
                name="Person", allowed_models=allowed or [])


def _artifact() -> Artifact:
    return Artifact(id="a-edit", user_id="u-edit", session_id="s-edit",
                    kind=ArtifactKind.report, title="보고서", data={})


def _session(model: str = EXTERNAL["id"], project: str | None = None) -> ChatSession:
    return ChatSession(id="s-edit", user_id="u-edit", kind=SessionKind.report, model=model,
                       project_id=project)


@pytest.fixture
def boundary(monkeypatch):
    holder = {"policy": Governance(internal_data_strict_local=True, external_data_guard=True)}
    audits: list[str] = []

    async def policy():
        return holder["policy"]

    async def no_history(db, session_id):
        return []

    async def audit(user, request, action, detail, *, metadata=None):
        audits.append(action)

    monkeypatch.setattr(sessions, "_require_egress_policy", policy)
    monkeypatch.setattr(sessions, "_history", no_history)
    monkeypatch.setattr(sessions, "_audit_policy", audit)
    holder["audits"] = audits
    return holder


async def _pick(db, catalogue, *, user=None, text="본문", **kwargs):
    return await workspace._edit_model(
        db, user or _user(), None, _artifact(), catalogue, kind="report", text=text, **kwargs
    )


@pytest.mark.asyncio
async def test_internal_material_is_edited_on_strict_local_only(boundary) -> None:
    db = _Db(_session(LOCAL["id"], project="p1"))
    # the conversation's local/ model: its own strict-local entry
    assert await _pick(db, EVERYTHING) is STRICT
    assert boundary["audits"][-1] == "privacy.internal_strict_local"
    with pytest.raises(HTTPException) as refused:
        await _pick(db, OUTSIDE_ONLY)
    assert refused.value.status_code == 409
    assert refused.value.detail == "internal_data_requires_strict_local"
    assert boundary["audits"][-1] == "privacy.internal_data_requires_strict_local"
    # a web fact-check would send the claims out: refused even with strict-local models
    with pytest.raises(HTTPException) as searched:
        await _pick(db, EVERYTHING, follow_session=False, searches=True)
    assert searched.value.status_code == 409 and searched.value.detail == "factcheck_needs_web"


@pytest.mark.asyncio
async def test_a_strict_local_conversation_keeps_its_edits_inside(boundary) -> None:
    db = _Db(_session(STRICT["id"]))
    assert await _pick(db, EVERYTHING) is STRICT
    with pytest.raises(HTTPException):
        await _pick(db, EVERYTHING, follow_session=False, searches=True)


@pytest.mark.asyncio
async def test_personal_data_goes_to_strict_local_not_masked(boundary) -> None:
    db = _Db(_session())
    said = "담당 홍길동 010-1234-5678"
    picked = await _pick(db, EVERYTHING, text=said)
    assert picked.get("strictLocal") is True
    assert boundary["audits"][-1] == "privacy.strict_local"
    with pytest.raises(HTTPException) as refused:
        await _pick(db, OUTSIDE_ONLY, text=said)
    assert refused.value.detail == "internal_data_requires_strict_local"
    # protection off: the conversation's model as before
    boundary["policy"] = Governance(internal_data_strict_local=True,
                                    external_data_guard=False, pii_masking=False)
    assert await _pick(db, EVERYTHING, text=said) is EXTERNAL


@pytest.mark.asyncio
async def test_a_plain_edit_keeps_the_conversations_model_within_the_allowlist(
    boundary,
) -> None:
    db = _Db(_session())
    assert await _pick(db, EVERYTHING) is EXTERNAL
    # a fact-check takes the cheapest, not the conversation's
    assert await _pick(db, EVERYTHING, follow_session=False, searches=True) in (LOCAL, CHEAP)
    # the conversation's model no longer allowed: the cheapest allowed one
    narrow = _user([CHEAP["id"]])
    assert await _pick(db, EVERYTHING, user=narrow) is CHEAP
    assert await _pick(_Db(None), EVERYTHING, user=narrow) is CHEAP


@pytest.mark.asyncio
async def test_a_design_read_from_an_own_file_is_internal_but_a_public_page_is_not(
    boundary,
) -> None:
    db = _Db(None)
    own = await workspace._edit_model(db, _user(), None, None, EVERYTHING, kind="chat",
                                      text="사내 브랜드 지침", follow_session=False,
                                      own_file=True)
    assert own.get("strictLocal") is True
    page = await workspace._edit_model(db, _user(), None, None, EVERYTHING, kind="chat",
                                       text="", follow_session=False)
    assert page.get("strictLocal") is not True
