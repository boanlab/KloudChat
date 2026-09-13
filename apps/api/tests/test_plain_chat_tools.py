"""Domain tool selection, not a claim about any model's response formatting."""

from dataclasses import replace

import pytest
from fastapi import HTTPException
from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession, Message, Role
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import calculation_policy, settings_store
from app.services.tools.base import openai_snapshot
from app.services.tools.ncs_check import CHECK_NCS_ANSWER
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
    starting_template=False,
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
        agent_id="ncs-agent" if agent else None,
        project_id="project" if project else None,
    )
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    workspace = WorkspaceContext(
        blocks=tuple(blocks),
        applied_skills=(AppliedSkill("selected", "Selected skill", skill, 20),) if skill else (),
        started_from={"templateId": "selected", "title": "Selected"} if starting_template else None,
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
        starting_template_id="selected" if starting_template else None,
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
async def test_plain_missing_counts_omits_ncs_schema_before_privacy_and_outbound(
    monkeypatch, strict,
):
    captured = await _routed_turn(monkeypatch, strict=strict)
    assert "calculate" in captured["names"]
    assert "check_ncs_answer" not in captured["names"]
    assert "check_ncs_answer" not in captured["privacy_sources"][0]["tool_definitions"]
    assert "check_ncs_answer" in captured["available"][0]
    assert "check_ncs_answer" not in captured["available"][-1]
    assert captured["calculation_required"] is False
    assert captured["preflight_tool"] is None
    assert not calculation_policy.requires_calculation(MISSING_COUNTS)


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "question",
    [
        "NCS 수리 문항을 설명해줘.",
        "ncs를 연습하고 싶어.",
        "check_ncs_answer로 확인해줘.",
        "선지를 함께 점검해줘.",
        "내가 제출한 답을 채점해줘.",
        "객관식 퀴즈를 내줘.",
        "Please grade my answer.",
        "Review this multiple-choice quiz.",
    ],
)
async def test_explicit_ncs_and_grading_requests_keep_checker(monkeypatch, strict, question):
    captured = await _routed_turn(monkeypatch, strict=strict, question=question)
    assert "check_ncs_answer" in captured["names"]


@pytest.mark.parametrize("role,keep", [(Role.user, True), (Role.assistant, False)])
async def test_only_retained_user_requests_establish_ncs_followup(monkeypatch, role, keep):
    captured = await _routed_turn(
        monkeypatch, strict=True, question="다음 것도 해줘.", history=[(role, "NCS 연습 문제")]
    )
    assert ("check_ncs_answer" in captured["names"]) is keep


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "context",
    ["agent", "ncs-reasoning", "ncs-arithmetic", "unrelated-skill", "template", "user", "project"],
)
async def test_explicit_contexts_preserve_existing_tool_selection(monkeypatch, strict, context):
    options = {}
    if context == "agent":
        options = {"agent": True, "allowed": ["check_ncs_answer"]}
    elif context == "template":
        options = {"starting_template": True}
    elif context in {"user", "project"}:
        options = {"blocks": [ContextBlock(f"{context}.instructions", "Follow my workflow.", True)]}
    else:
        options = {"skill": context}
    captured = await _routed_turn(monkeypatch, strict=strict, **options)
    assert "check_ncs_answer" in captured["names"]
    assert captured["preflight_tool"] == (
        "check_ncs_answer" if context == "ncs-arithmetic" else None
    )


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("allowed", [[], ["calculate"]])
async def test_explicit_ncs_never_adds_an_allowlist_excluded_checker(monkeypatch, strict, allowed):
    captured = await _routed_turn(
        monkeypatch, strict=strict, agent=True, allowed=allowed, question="NCS 개념을 설명해줘."
    )
    assert captured["names"] == allowed


async def test_untrusted_ncs_source_is_not_a_workflow_selection(monkeypatch):
    captured = await _routed_turn(
        monkeypatch,
        strict=True,
        blocks=[ContextBlock("memory", "NCS quiz grading", False)],
    )
    assert "check_ncs_answer" not in captured["names"]


@pytest.mark.parametrize("context", ["project", "attachments", "carried", "knowledge", "history"])
async def test_file_and_project_contexts_preserve_existing_tools(monkeypatch, context):
    options = {}
    if context == "project":
        options = {"project": True}
    elif context == "history":
        options = {"history": [(Role.user, "이 자료를 참고해줘.")], "history_attachment": True}
    else:
        options = {"file_context": context}
    captured = await _routed_turn(monkeypatch, strict=True, **options)
    assert "check_ncs_answer" in captured["names"]


async def test_only_builtin_checker_is_filtered(monkeypatch):
    other = replace(CHECK_NCS_ANSWER, source="synthetic_connector")
    captured = await _routed_turn(monkeypatch, strict=False, extra_tool=other)
    assert [tool.source for tool in captured["tools"] if tool.name == other.name] == [other.source]


@pytest.mark.parametrize("checker", [False, True])
async def test_calculation_instruction_mentions_only_selected_checker(monkeypatch, checker):
    captured = await _routed_turn(
        monkeypatch,
        strict=True,
        question="17 * 23은 얼마야?",
        agent=checker,
        allowed=["check_ncs_answer"] if checker else None,
    )
    assert captured["preflight_tool"] == ("check_ncs_answer" if checker else "calculate")
    assert ("decision=calculate" in captured["messages"][0]["content"]) is checker


@pytest.mark.parametrize("strict", [False, True])
async def test_plain_chat_does_not_fall_back_to_unselected_checker(monkeypatch, strict):
    await _routed_turn(
        monkeypatch,
        strict=strict,
        question="17 * 23은 얼마야?",
        allowed=["check_ncs_answer"],
        expect_unavailable=True,
    )
