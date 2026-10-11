"""A document that shows a chart the code step drew carries the picture itself.

The data lane's analysis document said the scatter plot was inserted, but the page held
`<img src="scatter.png">`: the file lived in the chat's files, not beside the document, so the
panel and the download showed a broken picture.
"""

from __future__ import annotations

import base64

import pytest

from app.services import sandbox
from app.services.tools import builtin as builtin_tools
from app.services.tools.base import ToolContext

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
BODY = "<h1>분석</h1>" + "<p>" + "집단별 평균과 회귀 결과를 정리했습니다. " * 40 + "</p>"


@pytest.fixture
def chat_files(monkeypatch):
    files = [sandbox.InputFile("f1", "scatter.png", len(PNG), "k1")]

    async def session_files(user_id, session_id):
        return files

    monkeypatch.setattr(sandbox, "session_files", session_files)
    monkeypatch.setattr(sandbox, "read_input", lambda file: PNG)
    return files


def ctx() -> ToolContext:
    return ToolContext(user_id="u", session_id="s", request="분석해서 문서로 만들어 줘")


@pytest.mark.asyncio
async def test_a_chart_named_by_file_is_embedded(chat_files):
    context = ctx()
    content = BODY + '<img src="outputs/scatter.png" alt="산점도">'
    result = await builtin_tools.create_artifact(
        {"kind": "html", "title": "분석", "content": content, "userRequested": True}, context
    )
    assert not result.failed
    stored = context.pending_artifacts[0]["data"]["content"]
    uri = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert f'<img src="{uri}" alt="산점도">' in stored
    assert "outputs/scatter.png" not in stored


@pytest.mark.asyncio
async def test_markdown_pictures_are_embedded_too(chat_files):
    context = ctx()
    content = "# 분석\n\n" + "본문 " * 300 + "\n\n![산점도](scatter.png)\n"
    await builtin_tools.create_artifact(
        {"kind": "code", "language": "markdown", "title": "분석", "content": content,
         "userRequested": True},
        context,
    )
    assert "![산점도](data:image/png;base64," in context.pending_artifacts[0]["data"]["content"]


@pytest.mark.asyncio
async def test_a_picture_the_chat_does_not_have_is_sent_back(chat_files):
    context = ctx()
    content = BODY + '<img src="regression.png">'
    result = await builtin_tools.create_artifact(
        {"kind": "html", "title": "분석", "content": content, "userRequested": True}, context
    )
    assert context.pending_artifacts == []
    # Not an error step: the model fixes the name and calls again.
    assert result.failed is False
    assert "「regression.png」" in result.content and "「scatter.png」" in result.content


@pytest.mark.asyncio
async def test_web_pictures_and_data_uris_are_left_alone(monkeypatch):
    async def no_files(user_id, session_id):  # pragma: no cover — must not be asked
        raise AssertionError("no lookup for remote pictures")

    monkeypatch.setattr(sandbox, "session_files", no_files)
    context = ctx()
    content = BODY + '<img src="https://example.org/a.png"><img src="data:image/png;base64,AA==">'
    await builtin_tools.create_artifact(
        {"kind": "html", "title": "분석", "content": content, "userRequested": True}, context
    )
    assert context.pending_artifacts[0]["data"]["content"] == content
