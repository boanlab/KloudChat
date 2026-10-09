"""Tool selection for plain chat, not a claim about any model's response formatting."""

import pytest
from fastapi import HTTPException
from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession, Message
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import calculation_policy, settings_store
from app.services.tools.base import openai_snapshot
from app.services.tools.registry import build_tools
from app.services.workspace_context import AppliedSkill, ContextBlock, ContextFile, WorkspaceContext

MISSING_COUNTS = (
    "A반 평균은 60점이고 B반 평균은 80점이야. 두 반 인원은 알려주지 않았어. "
    "전체 평균을 확정할 수 있어? 짧게 답해줘. 파일은 만들지 마."
)


async def _routed_turn(
    monkeypatch,
    *,
    strict,
    question=MISSING_COUNTS,
    blocks=(),
    skill=None,
    agent=False,
    allowed=None,
    history=(),
    extra_tool=None,
    project=False,
    file_context=None,
    history_attachment=False,
    expect_unavailable=False,
):
    user = User(email="selection@example.test", name="Learner", password_hash="hash")
    model = {
        **_external_model("strict-local/synthetic" if strict else "synthetic/external"),
        "supportsTools": True,
        "contextWindow": 64_000,
        "dataBoundary": "self_hosted" if strict else "external",
        "strictLocal": strict,
        "privacyOnly": strict,
    }
    session = ChatSession(
        user_id=user.id,
        model=model["id"],
        agent_id="agent" if agent else None,
        project_id="project" if project else None,
    )
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    workspace = WorkspaceContext(
        blocks=tuple(blocks),
        applied_skills=(AppliedSkill("selected", "Selected skill", skill, 20),) if skill else (),
        **(
            {file_context: (ContextFile("question.txt", "included", 20, 20),)}
            if file_context else {}
        ),
    )
    captured = {"available": [], "privacy_sources": [], "keys": []}

    async def assemble(*_args, **kwargs):
        captured["available"].append(kwargs["available_tool_names"])
        return workspace

    async def agent_settings(*_args):
        return None, allowed, None

    async def shelf(*_args):
        return [], ""

    async def backends():
        return settings_store.ToolBackends()

    async def tools(*args, **kwargs):
        kwargs["include_connectors"] = False
        result = await build_tools(*args, **kwargs)
        if extra_tool:
            result.append(extra_tool)
        return result

    async def stored_history(*_args):
        return [
            Message(
                session_id=session.id,
                role=role,
                content=text,
                attachments=(
                    [{"id": "file", "name": "question.txt"}] if history_attachment else None
                ),
            )
            for role, text in history
        ]

    resolve = sessions._resolve_privacy

    async def privacy(**kwargs):
        captured["privacy_sources"].append(kwargs["sources"])
        return await resolve(**kwargs)

    async def unused_key(*_args, **_kwargs):
        captured["keys"].append(True)
        return "synthetic-unused-key"

    async def credentials(*_args, **_kwargs):
        return "http://unused.test", "synthetic-unused-key"

    async def stream(**kwargs):
        captured.update(kwargs)
        yield sessions.chat_service.sse({"type": "done"})

    class Db(_NoWriteDb):
        async def get(self, *_args):
            return None

        def is_modified(self, _row):
            return False

    monkeypatch.setattr(sessions, "assemble", assemble)
    monkeypatch.setattr(sessions, "agent_settings", agent_settings)
    monkeypatch.setattr(sessions, "_knowledge_shelf", shelf)
    monkeypatch.setattr(sessions, "build_tools", tools)
    monkeypatch.setattr(sessions, "_history", stored_history)
    monkeypatch.setattr(sessions, "_resolve_privacy", privacy)
    monkeypatch.setattr(settings_store, "tools_config", backends)
    monkeypatch.setattr(sessions, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", unused_key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions.litellm_service, "user_key", lambda _user: "synthetic-unused-key")
    monkeypatch.setattr(sessions, "_run_turn", stream)
    db = Db()
    payload = SendMessage(
        content=question,
        web_search=False,
        activated_skill_ids=["selected"] if skill else [],
    )
    if expect_unavailable:
        with pytest.raises(HTTPException) as caught:
            await sessions.send_message(session.id, payload, _request(), user, db)
        assert caught.value.status_code == 409
        assert caught.value.detail == "calculation_tool_unavailable"
        assert db.added == [] and db.commits == 0 and captured["keys"] == []
        assert "tools" not in captured
        return captured
    response = await sessions.send_message(session.id, payload, _request(), user, db)
    _ = [event async for event in response.body_iterator]
    assert captured["tool_definitions"] == openai_snapshot(captured["tools"])
    captured["names"] = [row["function"]["name"] for row in captured["tool_definitions"]]
    return captured


@pytest.mark.parametrize("strict", [False, True])
async def test_plain_missing_counts_needs_no_calculation_gate(monkeypatch, strict):
    captured = await _routed_turn(monkeypatch, strict=strict)
    assert "calculate" in captured["names"]
    assert "calculate" in captured["privacy_sources"][0]["tool_definitions"]
    assert captured["calculation_required"] is False
    assert captured["preflight_tool"] is None
    assert not calculation_policy.requires_calculation(MISSING_COUNTS)


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("context", ["agent", "unrelated-skill", "user", "project"])
async def test_explicit_contexts_keep_the_calculator(monkeypatch, strict, context):
    if context == "agent":
        options = {"agent": True, "allowed": ["calculate"]}
    elif context in {"user", "project"}:
        options = {"blocks": [ContextBlock(f"{context}.instructions", "Follow my workflow.", True)]}
    else:
        options = {"skill": context}
    captured = await _routed_turn(monkeypatch, strict=strict, **options)
    assert "calculate" in captured["names"]
    assert captured["preflight_tool"] is None


@pytest.mark.parametrize("context", ["attachments", "carried", "knowledge"])
async def test_file_contexts_keep_the_calculator(monkeypatch, context):
    captured = await _routed_turn(monkeypatch, strict=True, file_context=context)
    assert "calculate" in captured["names"]


@pytest.mark.parametrize("strict", [False, True])
async def test_calculation_uses_the_calculator(monkeypatch, strict):
    captured = await _routed_turn(monkeypatch, strict=strict, question="17 * 23은 얼마야?")
    assert captured["preflight_tool"] == "calculate"
    assert "check_ncs_answer" not in captured["messages"][0]["content"]


@pytest.mark.parametrize("strict", [False, True])
async def test_an_allowlist_without_the_calculator_refuses_a_calculation(monkeypatch, strict):
    await _routed_turn(
        monkeypatch,
        strict=strict,
        question="17 * 23은 얼마야?",
        agent=True,
        allowed=["create_chart"],
        expect_unavailable=True,
    )
