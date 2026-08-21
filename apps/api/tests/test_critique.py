"""One reading of a finished document by somebody who did not write it.

The score is an opinion and nothing is blocked by it, so what has to hold is
the part that is rendered beside the linter's own findings: a made-up row there
would make the whole list read as decoration. Everything the model sends is
therefore normalised — a score off the scale, a severity that is not one of the
two, a finding with no sentence in it — before it can be stored.
"""

from __future__ import annotations

import pytest

from app.services import critique
from app.services import design_templates as dt


class _Response:
    def __init__(self, text: str):
        self.status_code = 200
        self._text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": self._text}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 150},
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


_BODY = (
    "## 배경\n현재 서버는 보증이 끝났고 장애가 세 번 있었다.\n"
    "## 대안\n유지와 교체를 견준다.\n"
    "## 다음 행동\n견적을 모으고 복구를 시험한다."
)


async def _review(monkeypatch, reply: str, *, body: str = _BODY, rubric: str = ""):
    posts: list[dict] = []

    async def litellm_config():
        return "http://mock-litellm", "unused"

    monkeypatch.setattr(critique.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(
        critique.httpx, "AsyncClient", lambda **kw: _Client(reply, posts, **kw)
    )
    result = await critique.review(
        title="서버 교체 검토", body=body, rubric=rubric, model="m", api_key="k"
    )
    return result, posts


_GOOD = (
    '{"score": 6.5, "findings": ['
    '{"severity": "P0", "where": "대안", "message": "같은 기준으로 견주지 않았다."},'
    '{"severity": "P1", "where": "", "message": "결론에 담당과 기한이 없다."}]}'
)


@pytest.mark.asyncio
async def test_a_review_comes_back_as_a_score_and_things_to_fix(monkeypatch):
    (result, usage), _ = await _review(monkeypatch, _GOOD)

    assert result["score"] == 6.5
    assert [f["severity"] for f in result["findings"]] == ["P0", "P1"]
    assert result["findings"][0]["where"] == "대안"
    # The linter's shape, so the panel renders one list rather than two.
    assert result["findings"][0]["rule"] == "critique"
    assert usage == {"inputTokens": 900, "outputTokens": 150}


@pytest.mark.asyncio
async def test_a_score_off_the_scale_is_pulled_back_onto_it(monkeypatch):
    (result, _), _ = await _review(monkeypatch, '{"score": 42, "findings": []}')
    assert result["score"] == 10.0
    (result, _), _ = await _review(monkeypatch, '{"score": -3, "findings": []}')
    assert result["score"] == 0.0


@pytest.mark.asyncio
async def test_a_severity_that_is_not_one_of_the_two_becomes_the_quieter_one(monkeypatch):
    reply = '{"score": 5, "findings": [{"severity": "치명적", "message": "고쳐야 한다."}]}'
    (result, _), _ = await _review(monkeypatch, reply)
    assert result["findings"][0]["severity"] == "P1"


@pytest.mark.asyncio
async def test_a_finding_with_no_sentence_in_it_is_dropped(monkeypatch):
    reply = (
        '{"score": 5, "findings": ['
        '{"severity": "P0", "message": "  "},'
        '{"severity": "P1", "message": "쓸 말이 있다."}]}'
    )
    (result, _), _ = await _review(monkeypatch, reply)
    assert [f["message"] for f in result["findings"]] == ["쓸 말이 있다."]


@pytest.mark.asyncio
async def test_more_findings_than_a_review_holds_are_cut(monkeypatch):
    """Beyond a handful this is a rewrite, not a review."""
    many = ",".join(
        f'{{"severity": "P1", "message": "지적 {n} 입니다."}}' for n in range(20)
    )
    (result, _), _ = await _review(monkeypatch, '{"score": 3, "findings": [%s]}' % many)  # noqa: UP031
    assert len(result["findings"]) == critique.MAX_FINDINGS


@pytest.mark.asyncio
async def test_an_answer_that_is_not_a_review_is_refused(monkeypatch):
    with pytest.raises(critique.CritiqueError):
        await _review(monkeypatch, "이 문서는 훌륭합니다.")


@pytest.mark.asyncio
async def test_a_reply_with_no_score_is_refused(monkeypatch):
    """A review with no number is not the thing that was asked for."""
    with pytest.raises(critique.CritiqueError):
        await _review(monkeypatch, '{"findings": [{"severity": "P0", "message": "가."}]}')


# ── a reply that stops early ───────────────────────────────────────────
#
# Small models end their JSON a bracket short often enough to matter: the
# reader pressed 검토 받기, the call was made and charged, and every word of
# the answer is there except the last `}`. What the model finished saying is
# kept; what it stopped in the middle of is dropped.


@pytest.mark.asyncio
async def test_a_review_missing_its_last_bracket_is_still_read(monkeypatch):
    (result, _), _ = await _review(monkeypatch, _GOOD.rstrip("}"))
    assert result["score"] == 6.5
    assert [f["where"] for f in result["findings"]] == ["대안", ""]


@pytest.mark.asyncio
async def test_a_finding_cut_off_mid_sentence_is_dropped_whole(monkeypatch):
    """Half a sentence read as a finding is worse than no finding at all."""
    reply = (
        '{"score": 4, "findings": ['
        '{"severity": "P0", "where": "대안", "message": "같은 기준으로 견주지 않았다."},'
        '{"severity": "P1", "where": "", "message": "결론에 담당과'
    )
    (result, _), _ = await _review(monkeypatch, reply)
    assert result["score"] == 4.0
    assert [f["message"] for f in result["findings"]] == ["같은 기준으로 견주지 않았다."]


@pytest.mark.asyncio
async def test_a_score_survives_a_reply_that_stops_in_the_first_finding(monkeypatch):
    """The number is the part the panel shows; it arrives before the findings do."""
    (result, _), _ = await _review(
        monkeypatch, '{"score": 7, "findings": [{"severity": "P1", "message": "결론에'
    )
    assert result["score"] == 7.0
    assert result["findings"] == []


@pytest.mark.asyncio
async def test_a_brace_with_no_review_after_it_is_still_refused(monkeypatch):
    """Closing what is open must not turn prose into a score of zero."""
    with pytest.raises(critique.CritiqueError):
        await _review(monkeypatch, "{ 이 문서는 훌륭합니다.")


@pytest.mark.asyncio
async def test_nothing_is_spent_reviewing_an_empty_document(monkeypatch):
    posts: list[dict] = []

    async def litellm_config():
        return "http://mock-litellm", "unused"

    monkeypatch.setattr(critique.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(
        critique.httpx, "AsyncClient", lambda **kw: _Client(_GOOD, posts, **kw)
    )
    with pytest.raises(critique.CritiqueError):
        await critique.review(title="t", body="  짧음  ", rubric="", model="m", api_key="k")
    assert posts == []


# ── what it is read against ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_template_is_read_against_its_own_checklist(monkeypatch):
    checklist = dt.get("doc-brief").checklist
    _, posts = await _review(monkeypatch, _GOOD, rubric=checklist)
    prompt = posts[0]["messages"][0]["content"]

    assert "무엇을 결정해야 하는지 첫 줄에서 알 수 있는가" in prompt
    # And the document itself, not a summary of it.
    assert "보증이 끝났고" in prompt


@pytest.mark.asyncio
async def test_without_a_template_the_default_rubric_stands_in(monkeypatch):
    _, posts = await _review(monkeypatch, _GOOD)
    assert "마지막에 읽는 사람이 할 일이 남는가" in posts[0]["messages"][0]["content"]


def test_a_document_reaches_the_reviewer_as_headings_and_words():
    body = critique.document(
        [
            {"heading": "현황", "text": "<ul><li>보유 42대</li></ul>"},
            {"heading": "", "text": "제목 없는 부분"},
        ]
    )
    assert body == "## 현황\n보유 42대\n제목 없는 부분"
    # Markup would be read as words the review then comments on.
    assert "<" not in body


def test_every_writing_template_carries_a_rubric():
    """A shape with no checklist would be reviewed against the generic one."""
    for template in dt.all_templates():
        if template.kind in dt.HTML_KINDS:
            assert template.checklist.strip(), template.id
        else:
            assert not template.checklist, template.id
