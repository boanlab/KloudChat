"""Broken chat answers: list loops, loop-only answers, dropped streams, repeated questions."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services import agent
from app.services.chat import ChatStreamError
from app.services.tools.base import ToolContext

#: A shopping list up to where it starts looping.
_LIST_HEAD = (
    "## 장보기 목록 (총 80,000원)\n\n"
    "**채소·과일 코너 (25,000원)**\n"
    "- 양상추 2통: 4,000원\n- 시금치 2단: 3,000원\n- 당근 3개: 2,000원\n\n"
    "**단백질 코너 (25,000원)**\n"
    "- 닭가슴살 1kg: 10,000원\n- 달걀 10개: 5,000원\n- 두부 3모: 1,500원\n"
)
_LOOP_LINE = "- 두부 2모: 1,000원\n"
#: The loop as streamed: 37 copies, cut mid-line when the detector fired.
_LOOPED = _LIST_HEAD + _LOOP_LINE * 37 + "- 두부 2"
_GOOD = (_LIST_HEAD + "- 현미 2kg: 8,000원\n\n**밀프렙 요령**\n"
         "- 일요일에 현미밥을 한 번에 짓습니다.\n")
_NOTE = "같은 내용이 되풀이되어 여기서 멈췄습니다"


def _shown(events: list[dict]) -> str:
    """The answer as the session stores it: deltas, retracts removing their first match."""
    text = ""
    for event in events:
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            assert event["text"] in text
            text = text.replace(event["text"], "", 1)
    return text


def _scripted(hops: list, seen: list[dict]):
    """`_stream_once` stand-in: each hop is (text, looped) or an exception raised mid-stream."""

    async def stream(_model, _messages, _tools, *_args, **kwargs):
        seen.append(kwargs)
        hop = hops[len(seen) - 1]
        acc = agent._Accumulator()
        text, failure = (hop, None) if not isinstance(hop, tuple) else hop
        for line in text.splitlines(keepends=True):
            acc.content.append(line)
            yield "delta", line
        if isinstance(failure, Exception):
            raise failure
        acc.looped = failure == "looped"
        acc.finish_reason = None if acc.looped else "stop"
        yield "done", acc

    return stream


async def _turn(monkeypatch, hops: list, seen: list[dict],
                content: str = "1주일 식단과 장보기 목록"):
    monkeypatch.setattr(agent, "_stream_once", _scripted(hops, seen))
    monkeypatch.setattr(agent, "_DROP_RETRY_AFTER", 0)
    return [
        event
        async for event in agent.run_turn(
            "local/qwen3.8-27b",
            [{"role": "user", "content": content}],
            [],
            ToolContext(user_id="u", session_id="s"),
            temperature=0.4,
        )
    ]


def test_a_list_loop_is_seen_after_a_few_lines_not_a_screenful() -> None:
    assert agent._line_loop([_LIST_HEAD + _LOOP_LINE * 8])
    assert not agent._line_loop([_LIST_HEAD + _LOOP_LINE * 7])
    # two-line cycle 「3모 / 2모」, as a retry at the same point wrote it
    assert agent._line_loop([_LIST_HEAD + ("- 두부 3모: 1,500원\n" + _LOOP_LINE) * 6])
    # empty form rows and table borders are not a loop
    assert not agent._line_loop(["| 이름 | 서명 |\n|---|---|\n" + "|  |  |\n" * 20])
    assert not agent._line_loop(["---\n" * 20])


def test_a_loop_is_cut_to_one_copy_of_its_cycle() -> None:
    assert agent._trim_loop(_LOOPED) == _LIST_HEAD + _LOOP_LINE
    alternating = _LIST_HEAD + ("- 두부 3모: 1,500원\n" + _LOOP_LINE) * 10 + "- 두부 3"
    assert agent._trim_loop(alternating) == _LIST_HEAD + "- 두부 3모: 1,500원\n" + _LOOP_LINE
    assert agent._trim_loop(_GOOD) == _GOOD


@pytest.mark.asyncio
async def test_a_looping_answer_is_taken_back_and_asked_again_with_a_penalty(monkeypatch) -> None:
    seen: list[dict] = []
    events = await _turn(monkeypatch, [(_LOOPED, "looped"), _GOOD], seen)
    assert _shown(events) == _GOOD
    assert _NOTE not in _shown(events)
    assert "presence_penalty" not in seen[0]
    assert seen[1]["presence_penalty"] == agent._LOOP_RETRY_PRESENCE


@pytest.mark.asyncio
async def test_a_second_loop_keeps_one_copy_and_says_it_stopped(monkeypatch) -> None:
    events = await _turn(monkeypatch, [(_LOOPED, "looped"), (_LOOPED, "looped")], [])
    shown = _shown(events)
    assert shown.startswith(_LIST_HEAD + _LOOP_LINE)
    assert shown.count("두부 2모") == 1
    assert _NOTE in shown


@pytest.mark.asyncio
async def test_a_loop_with_nothing_before_it_is_no_answer_not_a_note(monkeypatch) -> None:
    # A loop-only answer: an empty turn reporting no_answer, not a note.
    loop_only = "인천 → 리스본 최저가 경로 후보\n" * 12
    events = await _turn(monkeypatch, [(loop_only, "looped"), (loop_only, "looped")], [])
    assert _shown(events).strip() == ""


@pytest.mark.asyncio
async def test_a_stream_dropped_mid_answer_is_asked_again(monkeypatch) -> None:
    # A stream closed by the proxy 2,400 characters in, mid item 3
    half = "### 3. 승차권 vs 레일패스 비교\n\n**레일패스 (Interrail Global Pass)**:\n- 1인당 3일권"
    whole = half + " 또는 4일권 기준 약 200~300유로입니다.\n\n### 4. 일정\n- 1일차: 암스테르담\n"
    dropped = ChatStreamError("upstream_unreachable: ReadError")
    seen: list[dict] = []
    events = await _turn(monkeypatch, [(half, dropped), whole], seen)
    assert _shown(events) == whole
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_a_second_drop_is_still_an_error(monkeypatch) -> None:
    dropped = ChatStreamError("upstream_unreachable: ReadError")
    with pytest.raises(ChatStreamError):
        await _turn(monkeypatch, [("앞부분\n", dropped), ("앞부분\n", dropped)], [])


@pytest.mark.asyncio
async def test_a_refusal_is_not_retried(monkeypatch) -> None:
    seen: list[dict] = []
    with pytest.raises(ChatStreamError):
        await _turn(monkeypatch, [("", ChatStreamError("upstream_400: bad request")), "답"], seen)
    assert len(seen) == 1


def test_the_same_question_asked_twice_is_an_anomaly() -> None:
    # A second turn giving the occasion instead of the tax basis
    messages = [
        {"role": "user",
         "content": "내 개인 쇼퍼가 되어 줘. 첫 요청: 예산 100달러로 새 원피스를 찾고 있어."},
        {"role": "assistant",
         "content": "예산 100달러가 부가세와 배송비 포함 총액인지 상품가만인지 먼저 알려 주세요."},
        {"role": "user", "content": "여름 결혼식 하객룩으로 입을 거고, 너무 화려한 건 싫어."},
    ]
    again = "100달러가 상품가인지 부가세와 배송비 포함 총액인지 알려 주세요."
    assert agent._answer_anomaly(again, messages)[0] == "reask"
    other = "결혼식이 실내인지 야외인지 알려 주세요."
    assert agent._answer_anomaly(other, messages) is None
    answer = "- 네이비 랩 미디 원피스 (약 80달러)\n- 세이지 플리츠 원피스 (약 95달러)"
    assert agent._answer_anomaly(answer, messages) is None


@pytest.mark.asyncio
async def test_a_repeated_question_is_answered_on_the_retry(monkeypatch) -> None:
    messages = [
        {"role": "user", "content": "예산 100달러로 새 원피스를 찾고 있어. 추천 물건만 답해."},
        {"role": "assistant",
         "content": "예산 100달러가 부가세와 배송비 포함 총액인지 상품가만인지 먼저 알려 주세요."},
        {"role": "user", "content": "여름 결혼식 하객룩으로 입을 거고, 너무 화려한 건 싫어."},
    ]
    hops = ["100달러가 상품가인지 부가세와 배송비 포함 총액인지 알려 주세요.",
            "- 네이비 랩 미디 원피스 (약 80달러)\n- 세이지 플리츠 원피스 (약 95달러)\n"]
    seen: list[dict] = []
    monkeypatch.setattr(agent, "_stream_once", _scripted(hops, seen))
    events = [
        event
        async for event in agent.run_turn(
            "local/qwen3.8-27b", messages, [], ToolContext(user_id="u", session_id="s"))
    ]
    assert _shown(events) == hops[1]


class _Response:
    status_code = 200

    def __init__(self, lines: list[str], fail: Exception | None = None) -> None:
        self._lines, self._fail = lines, fail

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        if self._fail:
            raise self._fail


def _upstream(monkeypatch, lines: list[str], fail: Exception | None = None) -> list[dict]:
    seen: list[dict] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, _path, *, json):
            seen.append(json)
            return _Response(lines, fail)

    async def client(*_args, **_kwargs):
        return Client()

    monkeypatch.setattr(agent, "_client", client)
    return seen


def _chunk(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)


async def _once(model: str, **kwargs) -> tuple[list[str], agent._Accumulator]:
    deltas, acc = [], None
    async for kind, value in agent._stream_once(
        model, [{"role": "user", "content": "식단"}], [], "u", "k", **kwargs
    ):
        if kind == "delta":
            deltas.append(value)
        else:
            acc = value
    return deltas, acc


@pytest.mark.asyncio
async def test_27b_is_sent_a_presence_penalty_and_flash_is_not(monkeypatch) -> None:
    seen = _upstream(monkeypatch, [_chunk("답"), "data: [DONE]"])
    await _once("local/qwen3.8-27b", temperature=0.4)
    await _once("strict-local/qwen3.8-27b")
    await _once("local/qwen3.8-flash")
    await _once("local/qwen3.8-27b", presence_penalty=1.5)
    assert seen[0]["presence_penalty"] == 1.0 and seen[0]["temperature"] == 0.4
    assert seen[1]["presence_penalty"] == 1.0
    assert "presence_penalty" not in seen[2]
    assert seen[3]["presence_penalty"] == 1.5
    monkeypatch.setattr(agent.settings, "self_hosted_presence_penalty_models", "")
    await _once("local/qwen3.8-27b")
    assert "presence_penalty" not in seen[4]


@pytest.mark.asyncio
async def test_the_stream_stops_at_the_eighth_copy_of_a_list_line(monkeypatch) -> None:
    lines = [_chunk(line) for line in _LIST_HEAD.splitlines(keepends=True)]
    lines += [_chunk(_LOOP_LINE)] * 40 + ["data: [DONE]"]
    _upstream(monkeypatch, lines)
    deltas, acc = await _once("local/qwen3.8-27b")
    assert acc.looped
    assert "".join(deltas).count(_LOOP_LINE) <= 9


@pytest.mark.asyncio
async def test_a_dropped_stream_names_its_cause(monkeypatch) -> None:
    _upstream(monkeypatch, [_chunk("앞부분")], fail=httpx.ReadError(""))
    with pytest.raises(ChatStreamError) as caught:
        await _once("local/qwen3.8-27b")
    assert str(caught.value) == "upstream_unreachable: ReadError"


def test_a_spreadsheet_formula_request_is_not_held_for_the_calculator() -> None:
    # A formula request: no calculator gate, so no 「검산을 완료하지 못해…」
    from app.services import calculation_policy

    p46 = (
        "엑셀 수식 생성기가 되어 줘. 지역별·월별 매출 합계를 계산하는 수식이 필요해.\n\n"
        "데이터: '판매' 시트에 A열 날짜(2026-01-03 같은 날짜 값), B열 지역(서울/부산/대구/광주/"
        "대전), C열 담당자, D열 금액이 2행부터 약 5,000행까지 있어. '요약' 시트에 지역은 A2:A6, "
        "월은 B1:M1(1~12월 숫자)로 놓을 거야.\n\n"
        "조건: 반품은 금액이 음수로 들어오는데 이건 빼고 합계를 내고 싶어. 연도는 2026년만."
    )
    assert not calculation_policy.requires_calculation(p46)
    assert not calculation_policy.requires_calculation(
        "Give me an Excel formula that sums column D when B is 서울 and D > 0, rows 2 to 5000.")
    # a number asked of the given numbers still goes to the calculator
    assert calculation_policy.requires_calculation("1,250,000원의 15%와 3%를 더하면 얼마야?")


#: A stored first turn: the model's reasoning in brackets above the answer.
_P59_THINKING = (
    "[Thinking: The user wants me to role-play as an admissions interviewer for the School of "
    "Informatics at the University of Edinburgh, and to conduct a mock interview that lasts "
    "about 30 minutes. I should start the interview now, ask the first question, and maintain "
    "the persona.\n\nI should not dump all the questions at once. Start like a real interview: "
    "welcome, brief framing, then the first question (self-introduction / motivation). Keep it "
    "professional and encouraging, in Korean.\n\nLength: moderate — a short scene-setting "
    "paragraph plus the opening question. Don't overload.]\n\n"
)
_P59_ANSWER = (
    "좋습니다. 지금부터 저는 에든버러 대학교 정보학과(School of Informatics) 입학 면접관입니다. "
    "편하게 임해 주세요.\n\n첫 질문입니다. 먼저 2분 내외로 자기소개를 부탁드립니다."
)


def test_reasoning_written_into_the_answer_is_taken_out() -> None:
    from app.services import internal_words

    assert internal_words.strip_reasoning(_P59_THINKING + _P59_ANSWER) == _P59_ANSWER
    tagged = "<think>사용자는 면접을 원한다. 첫 질문부터.</think>\n\n" + _P59_ANSWER
    assert internal_words.strip_reasoning(tagged) == _P59_ANSWER
    opened_by_template = (
        "The user wants an interview. Start with a greeting.\n</think>\n\n" + _P59_ANSWER)
    assert internal_words.strip_reasoning(opened_by_template) == _P59_ANSWER
    # a short bracket is content, and a reasoning-only reply is left for the turn to judge
    short = "(생각: 30초 안에 답하기)\n" + _P59_ANSWER
    assert internal_words.strip_reasoning(short) == short
    assert internal_words.strip_reasoning(_P59_THINKING.strip()) == _P59_THINKING.strip()


def test_a_check_mark_never_splits_a_word() -> None:
    # 「0→1, 조직·사람 관리」 read as 「1, 조」 (one trillion) and marked inside 「조직」
    from app.services.value_guard import Evidence, settle

    before = (
        "| 항목 | A | B |\n|---|---|---|\n"
        "| 성장(경험) | 제조업 마케팅, 깊이 | 0→1, 조직·사람 관리, P&L 감각 |\n"
    )
    after = before.replace("0→1, 조직", "0→[수치], 조직")
    out = settle(before, after, Evidence.of(["회사 세 곳의 제안 비교"], answer=before)).text
    assert "조(확인 필요)직" not in out
    assert "조직·사람 관리" in out
