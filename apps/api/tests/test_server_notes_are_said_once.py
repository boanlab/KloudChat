"""Server notes under an answer, said once and left out of history."""

from __future__ import annotations

from test_an_answer_cites_sources_by_number import _calls, _collect, _content, _final_text

from app.pipeline import check_stages
from app.services import agent, server_notes
from app.services.tools.base import Tool, ToolResult

#: a first turn as stored: the answer, the search note, the advice line.
_P56_TURN1 = (
    "연 매출 4억 원이면 월 매출은 약 3,333만 원입니다. 정책자금은 소상공인시장진흥공단에 "
    "직접 문의하세요."
    + server_notes.WEB_NONE_KO
    + "\n\n확인 필요: 「30만 원」, 「360만 원」, 「1,000만 원」 — 검색 결과나 주신 내용으로 "
    "확인하지 못했으니 공식 기관이나 전문가에게 확인하세요."
)


def test_a_stored_answer_goes_back_without_the_server_notes():
    assert server_notes.strip(_P56_TURN1) == (
        "연 매출 4억 원이면 월 매출은 약 3,333만 원입니다. 정책자금은 소상공인시장진흥공단에 "
        "직접 문의하세요.")
    for note in server_notes.WEB_NOTES:
        assert server_notes.strip("본문입니다." + note) == "본문입니다."
    # an answer without notes is left as it is, its own 「확인 필요」 words too
    plain = "확인 필요: 담당자 연락처는 자료에 없습니다.\n\n끝."
    assert server_notes.strip(plain) == plain


def test_the_advice_line_the_check_writes_is_the_one_taken_off():
    text = "월 약 1,167만 원입니다(확인 필요)."
    noted = check_stages._advice_note(text, "", [{"text": "1,167만 원", "kind": "consequence"}])
    assert noted != text and server_notes.strip(noted) == "월 약 1,167만 원입니다."


def test_answers_that_say_they_could_not_confirm():
    assert server_notes.says_unconfirmed(
        "이번 검색에서 특정 은행의 당일 최고 금리 수치는 확인하지 못했으니 파인에서 확인하세요.")
    assert server_notes.says_unconfirmed(
        "What follows is established archival knowledge, not something I verified in this "
        "session.")
    assert not server_notes.says_unconfirmed("예금자보호 한도는 1인당 5천만 원입니다.")


async def test_an_empty_search_says_to_answer_from_what_is_known(monkeypatch):
    # four empty searches and a Fermi estimate declined
    from app.services.tools import builtin

    async def tools_config():
        class B:
            search = "http://search"
            fetch = ""
        return B()

    async def nothing(*_args, **_kwargs):
        return []

    monkeypatch.setattr(builtin.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(builtin, "_searxng", nothing)
    result = await builtin.web_search({"query": "서울 피아노 조율사 수"})
    assert result.empty and "아는 것과 밝힌 가정으로 답하되" in result.content


def _empty_search_tools() -> list[Tool]:
    async def run(_args):
        return ToolResult(content="'금리' 검색 결과가 없습니다.", empty=True)

    return [Tool(name="web_search", description="웹 검색", parameters={"type": "object"},
                 run=run, label="웹 검색")]


async def test_no_search_note_when_the_answer_already_says_so(monkeypatch):
    answer = [_content("이번 검색에서 당일 최고 금리는 확인하지 못했으니 파인에서 확인하세요."),
              "data: [DONE]"]
    _, events = await _collect(monkeypatch, [_calls("web_search", {"query": "금리"}), answer],
                               _empty_search_tools())
    assert "_웹 검색이" not in _final_text(events)


async def test_the_search_note_still_follows_an_answer_that_does_not(monkeypatch):
    answer = [_content("예금자보호 한도는 1인당 5천만 원입니다."), "data: [DONE]"]
    _, events = await _collect(monkeypatch, [_calls("web_search", {"query": "금리"}), answer],
                               _empty_search_tools())
    assert _final_text(events).endswith(server_notes.WEB_NONE_KO)
    assert agent.server_notes is server_notes
