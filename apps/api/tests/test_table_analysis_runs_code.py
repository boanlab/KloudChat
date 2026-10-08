"""A figure asked of an attached table is computed by run_code, not read off the rows."""

import pytest
from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession
from app.models.user import User
from app.models.workspace import StoredFile
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import calculation_policy, settings_store
from app.services.tools.registry import build_tools
from app.services.tools.sandbox_tools import RUN_CODE
from app.services.workspace_context import ContextBlock, ContextFile, WorkspaceContext

_CSV = "월,방문자수,대출\n2025-03,65000,17000\n2025-07,27000,6000\n".encode()
_ASK = "월별 이용 통계야. 학기 중이랑 방학의 평균을 비교하고 차트 하나 만들어 줘."


async def _turn(monkeypatch, *, question=_ASK, files=("library.csv",), run_code=True):
    user = User(email="table@example.test", name="Learner", password_hash="hash")
    model = {
        **_external_model("strict-local/synthetic"),
        "supportsTools": True,
        "contextWindow": 64_000,
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "privacyOnly": True,
    }
    session = ChatSession(user_id=user.id, model=model["id"])
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    rows = [
        StoredFile(user_id=user.id, name=name, size=len(_CSV), storage_key=f"k/{name}")
        for name in files
    ]
    workspace = WorkspaceContext(
        blocks=(ContextBlock("attachment", "# 이번 요청에 첨부된 파일", False),) if rows else (),
        applied_skills=(),
        attachments=tuple(ContextFile(row.name, "included", 80, 80) for row in rows),
    )
    captured: dict = {}

    async def assemble(*_args, **_kwargs):
        return workspace

    async def owned(*_args, **_kwargs):
        return rows, [{"id": row.id, "name": row.name} for row in rows] or None

    async def tools(*args, **kwargs):
        kwargs["include_connectors"] = False
        made = await build_tools(*args, **kwargs)
        return [*made, RUN_CODE] if run_code else made

    async def none(*_args, **_kwargs):
        return [], ""

    async def backends():
        return settings_store.ToolBackends()

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
    monkeypatch.setattr(settings_store, "tools_config", backends)
    monkeypatch.setattr(sessions, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions.file_service, "read_blob", lambda _key: _CSV)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions.litellm_service, "user_key", lambda _user: "synthetic-unused-key")
    monkeypatch.setattr(sessions, "_run_turn", stream)
    payload = SendMessage(
        content=question, web_search=False, attachments=[row.id for row in rows] or None
    )
    response = await sessions.send_message(session.id, payload, _request(), user, Db())
    _ = [event async for event in response.body_iterator]
    return captured


def _system(captured) -> str:
    return "\n".join(str(m.get("content")) for m in captured["messages"])


async def test_an_analysis_of_an_attached_csv_starts_with_run_code(monkeypatch):
    captured = await _turn(monkeypatch)

    assert captured["force_tool"] == "run_code"
    # The sandbox computes; the calculator gate and the prose plan stay out of the way.
    assert captured["calculation_required"] is False
    assert captured["plan_messages"] is None
    sent = _system(captured)
    assert "library.csv" in sent
    assert "방문자수" in sent, "열 이름이 전달되지 않았습니다"


async def test_a_spreadsheet_counts_as_a_table(monkeypatch):
    monkeypatch.setattr(sessions.file_schema, "describe", lambda name, _data: f"파일 {name}")
    captured = await _turn(monkeypatch, files=("예산.xlsx",), question="부서별 합계 내 줘")
    assert captured["force_tool"] == "run_code"


async def test_no_attachment_is_not_forced(monkeypatch):
    captured = await _turn(monkeypatch, files=())
    assert captured["force_tool"] is None


async def test_no_run_code_is_not_forced(monkeypatch):
    captured = await _turn(monkeypatch, run_code=False)
    assert captured["force_tool"] is None


async def test_a_document_attachment_is_not_forced(monkeypatch):
    captured = await _turn(monkeypatch, files=("회의록.docx",))
    assert captured["force_tool"] is None


@pytest.mark.parametrize(
    "question",
    [
        "이 파일 설명만 해 줘",
        "계산은 하지 말고 어떤 열이 있는지 알려 줘",
        "이 자료를 한 문단으로 소개해 줘",
    ],
)
async def test_describing_the_file_is_not_forced(monkeypatch, question):
    assert not calculation_policy.asks_table_analysis(question)
    captured = await _turn(monkeypatch, question=question)
    assert captured["force_tool"] is None


@pytest.mark.parametrize(
    "question", ["월별 추이를 정리해 줘", "3월 방문자는 몇 명이었어?", "Plot the trend by month"]
)
def test_figures_out_of_the_table_are_an_analysis(question):
    assert calculation_policy.asks_table_analysis(question)
