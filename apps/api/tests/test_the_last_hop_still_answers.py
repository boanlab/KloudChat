"""After the tool limit, a final answer is still produced."""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services import agent
from app.services.tools.base import Tool, ToolContext, ToolResult


class _Response:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _Client:
    """Calls a tool on every hop until it is given none, then answers."""

    def __init__(self, seen: list[dict]) -> None:
        self._seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        if json.get("tools"):
            return _Response(
                [
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                    '"function":{"name":"lookup","arguments":"{}"}}]}}]}',
                    "data: [DONE]",
                ]
            )
        return _Response(
            [
                'data: {"choices":[{"delta":{"content":"모은 자료로 쓴 답"}}]}',
                "data: [DONE]",
            ]
        )


@pytest.mark.asyncio
async def test_the_cap_ends_in_an_answer_written_without_tools(monkeypatch) -> None:
    seen: list[dict] = []

    async def client(*_args, **_kwargs):
        return _Client(seen)

    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr(settings, "max_tool_hops", 2)

    async def lookup(_args):
        return ToolResult(content="찾은 것")

    tool = Tool(
        name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup, label="찾기"
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "확인해 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]

    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "모은 자료로 쓴 답" in text
    assert "중단했습니다" not in text
    # The closing call carried no tools and the instruction to answer.
    assert "tools" not in seen[-1]
    assert any("도구는 더 쓸 수 없습니다" in (m.get("content") or "") for m in seen[-1]["messages"])


@pytest.mark.asyncio
async def test_a_link_no_tool_returned_is_named_as_unverified(monkeypatch) -> None:
    """A link not in the search results is flagged at the end of the answer."""
    seen: list[dict] = []

    class _Once(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                        '"function":{"name":"lookup","arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            return _Response(
                [
                    'data: {"choices":[{"delta":{"content":"근거: '
                    "https://arxiv.org/abs/2106.09685 와 "
                    'https://arxiv.org/abs/2208.04691 참고."}}]}',
                    "data: [DONE]",
                ]
            )

    async def client(*_args, **_kwargs):
        return _Once(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def lookup(_args):
        return ToolResult(content="LoRA 논문: https://arxiv.org/abs/2106.09685")

    tool = Tool(
        name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup, label="찾기"
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "선행연구 정리"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "검색·열람한 결과에 없던" in text
    assert "2208.04691" in text.split("없던")[1]
    assert "2106.09685" not in text.split("없던")[1]


@pytest.mark.asyncio
async def test_uncited_search_links_are_not_presented_as_verified_sources(monkeypatch) -> None:
    """A searched answer keeps the real tool URLs even when the model leaves them out."""
    seen: list[dict] = []

    class _SearchOnce(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                        '"function":{"name":"web_search","arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            return _Response(
                [
                    'data: {"choices":[{"delta":{"content":'
                    '"검색 결과를 비교한 답입니다."}}]}',
                    "data: [DONE]",
                ]
            )

    async def client(*_args, **_kwargs):
        return _SearchOnce(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def search(_args):
        return ToolResult(
            content=(
                "정부 자료: https://example.go.kr/policy/2026\n"
                "연구 자료: https://journal.example.org/article/42"
            )
        )

    tool = Tool(
        name="web_search",
        description="검색한다",
        parameters={"type": "object"},
        run=search,
        label="웹 검색",
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "최신 자료를 검색해 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "### 확인한 출처" not in text
    assert "https://example.go.kr/policy/2026" not in text
    assert "https://journal.example.org/article/42" not in text


@pytest.mark.asyncio
async def test_exact_long_paragraphs_are_kept_once(monkeypatch) -> None:
    """A model's copied closing paragraph is removed from screen and storage."""
    paragraph = "역발행의 초안 작성자와 법적 발급자를 구분해 설명합니다. " * 6
    answer_line = (
        'data: {"choices":[{"delta":{"content":'
        + json.dumps(f"{paragraph}\n\n{paragraph}", ensure_ascii=False)
        + "}}]}"
    )

    class _Repeated(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            return _Response([answer_line, "data: [DONE]"])

    async def client(*_args, **_kwargs):
        return _Repeated([])

    monkeypatch.setattr(agent, "_client", client)
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "역발행을 설명해 줘"}],
            [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    visible = ""
    for event in events:
        if event["type"] == "delta":
            visible += event["text"]
        elif event["type"] == "retract":
            visible = visible.replace(event["text"], "", 1)
    assert visible.count(paragraph) == 1




@pytest.mark.asyncio
async def test_an_institution_homepage_is_not_presented_as_direct_evidence(monkeypatch) -> None:
    seen: list[dict] = []

    class _HomepageCitation(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                        '"id":"c1","function":{"name":"web_search",'
                        '"arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            return _Response(
                [
                    'data: {"choices":[{"delta":{"content":'
                    '"기관 자료입니다: https://www.nia.or.kr"}}]}',
                    "data: [DONE]",
                ]
            )

    async def client(*_args, **_kwargs):
        return _HomepageCitation(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def search(_args):
        return ToolResult(content="기관 홈페이지: https://www.nia.or.kr")

    tool = Tool(
        name="web_search",
        description="검색한다",
        parameters={"type": "object"},
        run=search,
        label="웹 검색",
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "통계를 검증해 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert "직접 출처가 아닙니다" in text


@pytest.mark.asyncio
async def test_an_empty_completion_after_tools_is_asked_again(monkeypatch) -> None:
    """An empty answer after tool use is asked for once more."""
    seen: list[dict] = []

    class _Blank(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                        '"function":{"name":"lookup","arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            if len(self._seen) == 2:
                return _Response(["data: [DONE]"])
            answer = 'data: {"choices":[{"delta":{"content":"찾은 것으로 답합니다."}}]}'
            return _Response([answer, "data: [DONE]"])

    async def client(*_args, **_kwargs):
        return _Blank(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def lookup(_args):
        return ToolResult(content="찾은 것")

    tool = Tool(
        name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup, label="찾기"
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "찾아 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text == "찾은 것으로 답합니다."
    assert len(seen) == 3
    assert seen[2].get("tools") in (None, [])
    assert "답이 비어 있습니다" in seen[2]["messages"][-1]["content"]


def test_a_repeating_stream_is_recognised() -> None:
    """The same paragraph four times is a loop; a short answer or different sentences are not."""
    paragraph = "역발행은 공급받는 자가 세금계산서를 발행하는 방식이며 절차가 다릅니다. " * 3
    assert agent._is_looping([paragraph] * 8)
    assert not agent._is_looping([paragraph])
    different = [f"{i}번째 문장은 서로 다르며 되풀이가 아닙니다. " for i in range(60)]
    assert not agent._is_looping(different)


@pytest.mark.asyncio
@pytest.mark.parametrize("lead", ["", "역발행은 세금계산서 발행 방식의 하나입니다.\n\n"])
async def test_a_looping_answer_is_cut_and_told(monkeypatch, lead) -> None:
    """A looping stream is cut off and asked once more; looping again, one copy stays with a
    note, and a loop that is the whole answer leaves no answer instead of a note."""
    seen: list[dict] = []
    paragraph = "역발행은 공급받는 자가 세금계산서를 발행하는 방식이며 절차가 다릅니다. " * 3
    chunk = json.dumps({"choices": [{"delta": {"content": paragraph}}]}, ensure_ascii=False)
    first = json.dumps({"choices": [{"delta": {"content": lead}}]}, ensure_ascii=False)
    lines = [f"data: {first}"] + [f"data: {chunk}"] * 40 + ["data: [DONE]"]

    class _Loop(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            return _Response(lines)

    async def client(*_args, **_kwargs):
        return _Loop(seen)

    monkeypatch.setattr(agent, "_client", client)
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "역발행이 뭔가요"}],
            [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    text = ""
    for event in events:
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            text = text.replace(event["text"], "", 1)
    assert len(seen) == 2
    assert seen[1]["presence_penalty"] == agent._LOOP_RETRY_PRESENCE
    if lead:
        assert text.startswith(lead)
        assert "되풀이되어 여기서 멈췄습니다" in text
        assert text.count("역발행은 공급받는 자가") == 1
    else:
        assert text == ""


@pytest.mark.asyncio
async def test_a_rate_limit_is_retried_before_it_is_reported(monkeypatch) -> None:
    """A 429 is retried after a short wait, and the second answer is used."""
    seen: list[dict] = []
    waited: list[float] = []

    class _Limited(_Response):
        status_code = 429

        async def aread(self) -> bytes:
            return b"rate limited"

    class _Client429(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Limited([])
            return _Response(['data: {"choices":[{"delta":{"content":"답"}}]}', "data: [DONE]"])

    async def client(*_args, **_kwargs):
        return _Client429(seen)

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr(agent.asyncio, "sleep", sleep)
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "안녕"}],
            [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "답"
    assert len(seen) == 2 and waited == [agent._RETRY_AFTER[0]]


#: r3 A02 T2 flash: pasted press release, an empty search, an answer made of the release
_A02_T2 = ("아래 발표문을 부서장 보고용으로 정리해 줘. 핵심은 5줄 이내로 요약하고, 우리 대학에 "
           "미치는 영향 3가지와 우리가 지금 준비할 일을 정리해 줘.\n\n---\n"
           "[교육부 보도 참고자료(가상)] 2027년 대학 재정지원사업 개편 방향\n\n" + "교육부는 " * 80)


@pytest.mark.asyncio
@pytest.mark.parametrize(("request_text", "material", "footer"), [
    ("선행연구 정리", False, True),
    (_A02_T2, True, False),
    # the person's material and a lookup asked for: the empty search still matters
    ("아래 회사 소개를 보고 최신 기사도 검색해서 정리해 줘\n\n---\n" + "소개 " * 200, True, True),
])
async def test_an_answer_whose_searches_all_came_back_empty_says_so(
        monkeypatch, request_text: str, material: bool, footer: bool) -> None:
    """When every search comes back empty, the answer notes that nothing was verified,
    unless it is made of the person's own material and no lookup was asked for."""
    seen: list[dict] = []

    class _Searching(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                        '"function":{"name":"web_search","arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            answer = 'data: {"choices":[{"delta":{"content":"Twenge (2018) 은 ..."}}]}'
            return _Response([answer, "data: [DONE]"])

    async def client(*_args, **_kwargs):
        return _Searching(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def search(_args):
        return ToolResult(content="검색 결과가 질문과 무관한 것뿐입니다", empty=True)

    tool = Tool(
        name="web_search",
        description="검색",
        parameters={"type": "object"},
        run=search,
        label="검색",
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": request_text}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key",
                        request=request_text),
            material_given=material,
        )
    ]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text.startswith("Twenge (2018)")
    assert ("검색으로 확인하지 못했습니다" in text) is footer


@pytest.mark.asyncio
async def test_narration_before_a_tool_is_taken_back(monkeypatch) -> None:
    """Text spoken while calling a tool leaves the answer; the screen gets `retract`."""
    seen: list[dict] = []

    class _Narrating(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response(
                    [
                        'data: {"choices":[{"delta":{"content":"검색해 보겠습니다."}}]}',
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                        '"function":{"name":"lookup","arguments":"{}"}}]}}]}',
                        "data: [DONE]",
                    ]
                )
            return _Response(
                ['data: {"choices":[{"delta":{"content":"답입니다."}}]}', "data: [DONE]"]
            )

    async def client(*_args, **_kwargs):
        return _Narrating(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def lookup(_args):
        return ToolResult(content="찾은 것")

    tool = Tool(
        name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup, label="찾기"
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "찾아 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    retracted = [e["text"] for e in events if e["type"] == "retract"]
    assert retracted == ["검색해 보겠습니다."]
    # The model gets its own words back unchanged, so the conversation context is intact.
    assert seen[1]["messages"][-1]["content"] == "찾은 것"
    assert "검색해 보겠습니다." in (seen[1]["messages"][-2].get("content") or "")


def test_an_answer_written_twice_around_a_tool_is_recognised() -> None:
    first = (
        "SELECT c.course_id, COUNT(*)\nFROM courses c JOIN enrollments e ON …\n"
        "GROUP BY c.course_id\nHAVING COUNT(*) >= 20\nORDER BY avg DESC;"
    )
    assert agent._repeats(first, "검증했습니다.\n\n" + first + "\n\n결과가 맞습니다.")
    assert not agent._repeats(first, "시뮬레이션 결과 두 과목이 남았습니다.")


_DRAFT = (
    "**결론부터**\n- 이번 학기 평균 평점: 참여 3.43, 미참여 3.44 — 차이가 0.01로 사실상 같음.\n"
    "- 학년별로는 1학년만 참여(3.30)가 미참여(2.96)보다 높음.\n\n**데이터 정리**\n"
    "- 원본 198행 → 완전 동일 중복 3건 제거 → 195행.\n"
    "- 공과대학 55행은 100점 환산으로 들어와 있어 4.5 만점으로 환산해 통합.\n"
    + "".join(f"| {g}학년 | {10 + g} | 3.{20 + g} | {40 + g} | 2.{90 + g} | +0.{30 + g} |\n"
              for g in range(1, 5))
    + "참여·미참여는 무작위 배분이 아니라 학생 스스로 선택한 것이므로 단정할 수 없습니다.\n"
)
_FINAL = (
    "**결론**\n- 이번 학기 평균 평점: 참여 3.469, 미참여 3.303 — 참여가 0.166 높음.\n"
    "- 원본 253행 → 중복 3건 제거 → 250행, 휴학 4행 제외 → 분석 246행.\n"
    "- 직전 학기 평점도 참여 3.469, 미참여 3.247로 참여자가 원래 성적이 높았습니다.\n"
    + "".join(f"| {g}학년 | {g * 6} | 3.{500 + g} | {70 - g * 9} | 3.{100 + g * 7} |\n"
              for g in range(1, 5))
    + "자발적 참여라 인과는 알 수 없고, 기선 격차가 대부분을 설명합니다.\n"
)


def _say(text: str) -> str:
    return 'data: {"choices":[{"delta":{"content":' + json.dumps(text, ensure_ascii=False) + "}}]}"


def _two_hop(first: str, final: str):
    first_line, final_line = _say(first), _say(final)

    class _Drafting(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            if len(self._seen) == 1:
                return _Response([
                    first_line,
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
                    '"function":{"name":"lookup","arguments":"{}"}}]}}]}',
                    "data: [DONE]",
                ])
            return _Response([final_line, "data: [DONE]"])

    return _Drafting


async def _run_two_hop(monkeypatch, first: str, final: str):
    seen: list[dict] = []
    cls = _two_hop(first, final)

    async def client(*_args, **_kwargs):
        return cls(seen)

    monkeypatch.setattr(agent, "_client", client)

    async def lookup(_args):
        return ToolResult(content="253행, 참여 3.469, 미참여 3.303")

    tool = Tool(
        name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup, label="찾기"
    )
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "튜터링 참여 학생 평점 비교해 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    visible = ""
    for event in events:
        if event["type"] == "delta":
            visible += event["text"]
        elif event["type"] == "retract":
            visible = visible.replace(event["text"], "", 1)
    return events, visible, seen


@pytest.mark.asyncio
async def test_figures_written_before_the_tool_answered_are_withdrawn(monkeypatch) -> None:
    """A draft 「198행 … 3.43 vs 3.44」 before run_code is not the answer."""
    events, visible, seen = await _run_two_hop(monkeypatch, _DRAFT, _FINAL)
    assert visible == _FINAL
    assert any(e["type"] == "retract" and e["text"] == _DRAFT for e in events)
    # The guessed figures do not anchor the hop that reads the tool result either.
    assert "198행" not in json.dumps(seen[1]["messages"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_a_draft_stays_when_the_last_hop_says_next_to_nothing(monkeypatch) -> None:
    answer = _FINAL + "\n**한계**\n" + "".join(
        f"- 확인할 것 {n}: 직전 학기 평점·학년·단과대학으로 맞춘 비교가 필요합니다.\n"
        for n in range(1, 4)
    )
    assert len(answer) >= agent._NARRATION_CHARS
    _events, visible, _seen = await _run_two_hop(monkeypatch, answer, "차트를 붙였습니다.")
    assert visible.startswith(answer.rstrip())
    assert visible.endswith("차트를 붙였습니다.")


@pytest.mark.asyncio
async def test_a_turn_without_tools_is_unchanged(monkeypatch) -> None:
    line = _say(_FINAL)

    class _Plain(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            return _Response([line, "data: [DONE]"])

    async def client(*_args, **_kwargs):
        return _Plain([])

    monkeypatch.setattr(agent, "_client", client)
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model",
            [{"role": "user", "content": "평점 비교"}],
            [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    assert not any(e["type"] == "retract" for e in events)
    assert "".join(e["text"] for e in events if e["type"] == "delta") == _FINAL


@pytest.mark.asyncio
async def test_the_stored_answer_is_the_post_tool_answer_only(monkeypatch) -> None:
    """Of two 「결론」 blocks, only the one after run_code is stored."""
    from test_privacy import _external_model
    from test_tool_result_answer import _database

    from app.models.chat import Message
    from app.routers import sessions as sessions_router

    user, session, _, _, added = _database(monkeypatch)
    hops = []

    async def stream(*_args, **_kwargs):
        acc = agent._Accumulator()
        if not hops:
            acc.content = [_DRAFT]
            acc.calls[0] = {"id": "c1", "name": "lookup", "arguments": "{}"}
            hops.append(1)
            yield "delta", _DRAFT
        else:
            acc.content = [_FINAL]
            yield "delta", _FINAL
        yield "done", acc

    async def lookup(_args):
        return ToolResult(content="253행, 참여 3.469, 미참여 3.303")

    async def nothing(**_kwargs):
        return None

    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(sessions_router, "_store_artifacts", nothing)
    monkeypatch.setattr(sessions_router, "_enrich_memory", nothing)
    monkeypatch.setattr(sessions_router, "record_searches", lambda *_a, **_k: None)
    monkeypatch.setattr(sessions_router, "settle", lambda *_a, **_k: None)
    tool = Tool(name="lookup", description="찾는다", parameters={"type": "object"},
                run=lookup, label="찾기", source="builtin", read_only=True)
    chunks = [chunk async for chunk in sessions_router._run_turn(
        user_id=user.id, api_key="k", auto_memory=False, session_id=session.id,
        model={**_external_model("synthetic/paid"), "inputCreditCost": 1, "creditCost": 1},
        messages=[{"role": "user", "content": "튜터링 참여 학생 평점 비교해 줘"}],
        tools=[tool], first_user_message="튜터링 참여 학생 평점 비교해 줘", is_first_turn=False,
    )]
    stored = next(row for row in added if isinstance(row, Message))
    assert stored.content == _FINAL
    assert "198행" not in stored.content
    assert any('"retract"' in chunk for chunk in chunks)
