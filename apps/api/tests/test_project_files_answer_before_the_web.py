"""A question the project's files answer reads them; a year in it does not force a search."""

import pytest
from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services.tools.builtin import CALCULATE, FETCH_URL, WEB_SEARCH
from app.services.workspace_context import ContextBlock, ContextFile, WorkspaceContext

#: Answered from grant.xlsx in the project, with no web search first.
_ASK = ("프로젝트에 올린 연구비 집행 자료로 2024년과 2025년 연도별 예산 대비 집행률(%)을 "
        "항목별로 계산하고, 집행률이 90% 미만인 항목을 표시해 줘.")


async def _turn(monkeypatch, *, question=_ASK, knowledge=True):
    user = User(email="project@example.test", name="Researcher", password_hash="hash")
    model = {**_external_model("local/synthetic"), "supportsTools": True,
             "contextWindow": 64_000, "dataBoundary": "self_hosted"}
    session = ChatSession(user_id=user.id, model=model["id"])
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    files = (ContextFile("grant.xlsx", "included", 80, 80),) if knowledge else ()
    workspace = WorkspaceContext(
        blocks=(ContextBlock("knowledge", "# 프로젝트 지식", False),) if knowledge else (),
        applied_skills=(),
        knowledge=files,
    )
    captured: dict = {}

    async def assemble(*_args, **_kwargs):
        return workspace

    async def owned(*_args, **_kwargs):
        return [], None

    async def tools(*_args, web_search=False, **_kwargs):
        return [*([WEB_SEARCH, FETCH_URL] if web_search else []), CALCULATE]

    async def none(*_args, **_kwargs):
        return [], ""

    async def key(*_args, **_kwargs):
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
    monkeypatch.setattr(sessions, "_owned_attachments", owned)
    monkeypatch.setattr(sessions, "_knowledge_shelf", none)
    monkeypatch.setattr(sessions, "build_tools", tools)
    monkeypatch.setattr(sessions, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions.litellm_service, "user_key", lambda _user: "synthetic-unused-key")
    monkeypatch.setattr(sessions, "_run_turn", stream)
    payload = SendMessage(content=question, web_search="auto")
    response = await sessions.send_message(session.id, payload, _request(), user, Db())
    _ = [event async for event in response.body_iterator]
    return captured


def _searches_first(captured: dict) -> bool:
    """Whether the turn's first hop is a web search, forced or preset."""
    preset = captured.get("preset_call") or ("",)
    return captured["force_tool"] == "web_search" or preset[0] == "web_search"


async def test_a_question_about_project_files_is_not_forced_to_search(monkeypatch):
    captured = await _turn(monkeypatch)
    assert not _searches_first(captured)
    # The tool stays offered for the model to call if the files fall short.
    assert "web_search" in {t.name for t in captured["tools"]}


async def test_without_the_files_the_same_words_still_search(monkeypatch):
    captured = await _turn(monkeypatch, knowledge=False)
    assert _searches_first(captured)


@pytest.mark.parametrize("question", [
    "프로젝트 자료 말고 웹에서 2025년 연구비 집행 기준을 찾아 줘.",
    "요즘 LLM 요약 평가 연구 동향 알려 줘.",
])
async def test_a_question_that_asks_for_the_web_still_searches(monkeypatch, question):
    captured = await _turn(monkeypatch, question=question)
    assert _searches_first(captured)
