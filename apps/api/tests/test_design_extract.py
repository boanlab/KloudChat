"""`design_extract`: a design-system draft read from a document is normalised before storing."""

from __future__ import annotations

import pytest

from app.services import design, design_extract

_DOC = (
    "○○대학교 정보시스템팀 공문\n\n"
    "제목: 연구실 장비 관리 지침 개정 알림\n\n"
    "1. 관련: 학사운영규정 제12조\n"
    "2. 위 호와 관련하여 장비 관리 지침을 붙임과 같이 개정하였음을 알려드립니다.\n"
    "3. 각 연구실은 2026년 3월 31일까지 점검 결과를 회신하여 주시기 바랍니다.\n\n"
    "붙임  1. 개정 지침 1부.  끝."
)


class _Response:
    def __init__(self, text: str):
        self.status_code = 200
        self._text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": self._text}}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 120},
        }


class _Client:
    def __init__(self, reply: str, posts: list[dict], **_kwargs):
        self.reply = reply
        self.posts = posts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _path: str, *, json: dict):
        self.posts.append(json)
        return _Response(self.reply)


async def _extract(monkeypatch, reply: str, source: str = _DOC):
    posts: list[dict] = []

    async def litellm_config():
        return "http://mock-litellm", "unused"

    monkeypatch.setattr(design_extract.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(
        design_extract.httpx, "AsyncClient", lambda **kw: _Client(reply, posts, **kw)
    )
    return await design_extract.extract(source=source, model="m", api_key="k"), posts


_GOOD = (
    '{"name": "학과 공문", "description": "대내외 공문에 쓰는 서식",'
    ' "tokens": {"accent": "#1E3A8A", "ink": "#111827", "muted": "#6B7280", "font": "serif"},'
    ' "body": "제목 다음에 근거를 밝히고, 한 문장에 한 사실만 담는다.",'
    ' "image_style": "muted documentary photography",'
    ' "craft": ["restraint", "typography"]}'
)


@pytest.mark.asyncio
async def test_a_document_becomes_a_draft(monkeypatch):
    (draft, usage), posts = await _extract(monkeypatch, _GOOD)

    assert draft["name"] == "학과 공문"
    assert draft["tokens"] == {
        "accent": "#1e3a8a",
        "ink": "#111827",
        "muted": "#6b7280",
        "font": "serif",
        # visualStyle, footer and logo are never extracted; they keep their defaults.
        "visualStyle": "editorial",
        "footer": "",
        "logo": "",
    }
    assert draft["craft"] == ["restraint", "typography"]
    assert usage == {"inputTokens": 800, "outputTokens": 120}
    assert "연구실 장비 관리 지침" in posts[0]["messages"][0]["content"]
    assert posts[0]["reasoning"] == design_extract.thinking.NO_REASONING


@pytest.mark.asyncio
async def test_a_fenced_answer_is_still_read(monkeypatch):
    (draft, _), _ = await _extract(monkeypatch, f"```json\n{_GOOD}\n```")
    assert draft["name"] == "학과 공문"


@pytest.mark.asyncio
async def test_an_invented_colour_becomes_a_default_rather_than_a_value(monkeypatch):
    """A malformed token value becomes the default; well-formed neighbours survive."""
    reply = (
        '{"name": "무언가", "tokens": {"accent": "짙은 남색", "ink": "#111827",'
        ' "font": "바탕"}, "craft": ["restraint", "홍보용"]}'
    )
    (draft, _), _ = await _extract(monkeypatch, reply)

    assert draft["tokens"]["accent"] == design.DEFAULT_TOKENS["accent"]
    assert draft["tokens"]["font"] == design.DEFAULT_TOKENS["font"]
    assert draft["tokens"]["ink"] == "#111827"
    assert draft["craft"] == ["restraint"]


@pytest.mark.asyncio
async def test_prose_that_runs_long_is_cut_to_what_the_column_holds(monkeypatch):
    reply = '{"name": "긴 것", "body": "%s"}' % ("가" * 900)
    (draft, _), _ = await _extract(monkeypatch, reply)
    assert len(draft["body"]) == design.MAX_BODY


@pytest.mark.asyncio
async def test_an_answer_that_is_not_a_draft_is_refused(monkeypatch):
    with pytest.raises(design_extract.ExtractError):
        await _extract(monkeypatch, "이 문서에서는 디자인 시스템을 찾을 수 없습니다.")


@pytest.mark.asyncio
async def test_an_answer_with_no_name_is_refused(monkeypatch):
    """A draft without a name is refused."""
    with pytest.raises(design_extract.ExtractError):
        await _extract(monkeypatch, '{"tokens": {"accent": "#1e3a8a"}}')


@pytest.mark.asyncio
async def test_nothing_is_spent_on_a_document_with_nothing_in_it(monkeypatch):
    posts: list[dict] = []

    async def litellm_config():
        return "http://mock-litellm", "unused"

    monkeypatch.setattr(design_extract.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(
        design_extract.httpx, "AsyncClient", lambda **kw: _Client(_GOOD, posts, **kw)
    )
    with pytest.raises(design_extract.ExtractError):
        await design_extract.extract(source="   짧음   ", model="m", api_key="k")
    assert posts == []
