"""The web-search toggle's 「자동」 setting: the web tools are offered, and a first
search is forced only when the words ask for research or for something that
changes with time. Weather questions go to the weather tool."""

from __future__ import annotations

import json

import pytest

from app.models.chat import SessionKind
from app.services import context
from app.services.tools import builtin
from app.services.tools.base import ToolResult

# ── the plan ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("toggle", "words", "expected"),
    [
        # auto: offered, forced only by a cue
        ("auto", "파이썬 리스트 컴프리헨션 설명해 줘", (True, None)),
        ("auto", "안녕, 오늘 기분이 어때?", (True, None)),  # 「오늘」, but small talk
        ("auto", "전이학습이 왜 되는지 알려줘", (True, None)),
        ("auto", "앤트로픽 최신 모델이 뭐야", (True, "web_search")),
        ("auto", "2026년 근로기준법 연차 규정", (True, "web_search")),
        ("auto", "이 주장을 검증해 줘", (True, "web_search")),
        ("auto", "분당 날씨 알려줘", (True, "weather")),
        ("auto", "내일 우산 챙겨야 해?", (True, "weather")),
        # A named release, a bibliography, a change: stale in memory, so searched.
        ("auto", "Python 3.14에서 바뀐 주요 기능이 뭐야?", (True, "web_search")),
        ("auto", "React 19에서 forwardRef 없어졌어?", (True, "web_search")),
        ("auto", "Mamba 논문 arXiv 번호랑 저자 알려줘", (True, "web_search")),
        ("auto", "리스트 3개를 합치는 법", (True, None)),
        ("auto", "일본 갈 때 비짓재팬 등록 아직 필요해?", (True, "web_search")),
        # Fees, deadlines, places and sign-ups change; small talk never searches.
        ("auto", "인천공항 제2터미널 주차 요금 알려줘", (True, "web_search")),
        ("auto", "2027학년도 수능 원서 접수 기간 알려줘", (True, "web_search")),
        ("auto", "분당구 대형 폐기물 스티커 어디서 사?", (True, "web_search")),
        ("auto", "오늘 기분이 별로야. 위로해줘", (True, None)),
        ("auto", "오늘 뉴스 알려줘", (True, "web_search")),
        # A count set by law — how many renewals — is looked up, not recalled.
        ("auto", "임대차 계약 갱신요구권 몇 번까지 쓸 수 있어?", (True, "web_search")),
        # on: forced every turn, weather still to the weather tool
        (True, "파이썬 리스트 컴프리헨션 설명해 줘", (True, "web_search")),
        (True, "서울 기온 몇 도야", (True, "weather")),
        # off: nothing, unless the words ask for research
        (False, "앤트로픽 최신 모델이 뭐야", (False, None)),
        (False, "출처를 찾아 확인해 줘", (True, "web_search")),
    ],
)
def test_the_plan_follows_the_toggle_and_the_words(toggle, words, expected) -> None:
    assert context.search_plan(toggle, words) == expected


# ── the prompt ────────────────────────────────────────────────────────


def test_auto_gets_the_lighter_rule() -> None:
    prompt = context.system_prompt(
        SessionKind.chat, with_tools=True, web_search=True, web_search_auto=True
    )
    assert "필요할 때만 쓰세요" in prompt
    assert "검색 없이 답할 것" in prompt
    assert "weather 도구" in prompt
    # Not the every-turn nudge.
    assert "사용자가 웹 검색을 켰습니다" not in prompt
    assert "축마다" not in prompt


def test_a_forced_turn_keeps_the_full_nudge() -> None:
    prompt = context.system_prompt(SessionKind.chat, with_tools=True, web_search=True)
    assert "축마다" in prompt
    assert "필요할 때만 쓰세요" not in prompt


def test_build_messages_passes_the_auto_flag_through() -> None:
    messages = context.build_messages(
        SessionKind.chat,
        [{"role": "user", "content": "q"}],
        with_tools=True,
        web_search=True,
        web_search_auto=True,
    )
    assert "필요할 때만 쓰세요" in messages[0]["content"]


# ── the weather tool ──────────────────────────────────────────────────

_GEO = [
    {
        "lat": "37.3777369",
        "lon": "127.1238612",
        "display_name": "분당, 분당구, 성남시, 경기도, 대한민국",
    }
]
_FORECAST = {
    "latitude": 37.4,
    "longitude": 127.125,
    "timezone": "Asia/Seoul",
    "current": {
        "time": "2026-09-07T22:45",
        "temperature_2m": 18.3,
        "apparent_temperature": 19.3,
        "relative_humidity_2m": 71,
        "precipitation": 0.0,
        "weather_code": 3,
        "wind_speed_10m": 6.1,
    },
    "daily": {
        "time": ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"],
        "weather_code": [3, 61, 0, 2],
        "temperature_2m_max": [25.1, 23.0, 27.4, 26.0],
        "temperature_2m_min": [17.0, 16.2, 15.8, 16.5],
        "precipitation_probability_max": [20, 80, 5, 10],
    },
}


class _Response:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _Client:
    """Answers the geocoder and the forecast from canned payloads; records the calls."""

    calls: list[tuple[str, dict]] = []

    def __init__(self, *_args, **_kwargs) -> None:
        return None

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def get(self, url: str, *, params: dict, headers: dict | None = None) -> _Response:
        _Client.calls.append((url, params))
        if "nominatim" in url:
            assert headers and headers["User-Agent"].startswith("KloudChat")
            return _Response(_GEO if params["q"] != "없는곳" else [])
        return _Response(_FORECAST)


@pytest.mark.asyncio
async def test_the_weather_tool_reads_a_korean_place_name(monkeypatch) -> None:
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls.clear()

    result: ToolResult = await builtin.weather({"location": "분당"})

    assert not result.failed
    assert result.detail == "분당"
    lines = result.content.splitlines()
    assert lines[0] == "[1] Open-Meteo 날씨 예보 · 분당, 분당구, 성남시, 경기도, 대한민국"
    assert lines[1].startswith("https://open-meteo.com/en/docs#latitude=37.4")
    assert "현재: 18.3°C(체감 19.3°C), 흐림, 습도 71%" in result.content
    assert "내일(2026-09-08): 약한 비, 최고 23.0°C / 최저 16.2°C, 강수 확률 80%" in result.content
    # The forecast was asked for the geocoded point, in local time.
    _, params = _Client.calls[1]
    assert params["latitude"] == pytest.approx(37.3777369)
    assert params["timezone"] == "auto"


@pytest.mark.asyncio
async def test_an_unknown_place_fails_plainly(monkeypatch) -> None:
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    result = await builtin.weather({"location": "없는곳"})
    assert result.failed
    assert "위치를 찾지 못했습니다" in result.content


@pytest.mark.asyncio
async def test_the_weather_tool_follows_the_web_toggle(monkeypatch) -> None:
    class _Backends:
        fetch = "http://fetch"
        search = "http://search"
        exec = ""

    async def tools_config():
        return _Backends()

    monkeypatch.setattr(builtin.settings_store, "tools_config", tools_config)
    offered = [t.name for t in await builtin.available_builtins(True)]
    withheld = [t.name for t in await builtin.available_builtins(False)]
    assert "weather" in offered
    assert "weather" not in withheld


def test_the_payload_accepts_auto() -> None:
    from app.schemas.chat import SendMessage

    assert SendMessage.model_validate({"content": "q", "webSearch": "auto"}).web_search == "auto"
    assert SendMessage.model_validate({"content": "q", "webSearch": True}).web_search is True
    assert json.loads(SendMessage(content="q").model_dump_json())["content"] == "q"


# ── the server's own first call ───────────────────────────────────────


@pytest.mark.parametrize(
    ("words", "query"),
    [
        ("앤트로픽 최신 모델 이름이 뭐야", "앤트로픽 최신 모델 이름"),
        ("삼성전자 주가 얼마야?", "삼성전자 주가"),
        ("2026년 근로기준법 연차 규정 좀 알려줘", "2026년 근로기준법 연차 규정"),
        ("이 주장을 검증해 줘", "이 주장"),
        ("latest anthropic model", "latest anthropic model"),
        ("프리랜서 종합소득세 신고 기간이 언제까지야?", "프리랜서 종합소득세 신고 기간"),
        ("요즘 배추 도매 가격 얼마 정도야?", "요즘 배추 도매 가격"),
        ("국민취업지원제도 구직촉진수당 월 얼마야?", "국민취업지원제도 구직촉진수당"),
        ("임대차 계약 갱신요구권 몇 번까지 쓸 수 있어?", "임대차 계약 갱신요구권 몇 번까지"),
        ("2026년 부모급여 월 얼마 받아?", "2026년 부모급여"),
    ],
)
def test_the_query_is_the_sentence_without_the_asking(words, query) -> None:
    assert context.search_query(words) == query


@pytest.mark.parametrize(
    ("words", "place"),
    [
        ("분당 날씨 알려줘", "분당"),
        ("오늘 서울 날씨 어때", "서울"),
        ("내일 대전 유성구 기온 몇 도야", "대전 유성구"),
        ("서울의 날씨는?", "서울"),
        ("내일 우산 챙겨야 해?", None),
        ("날씨 어때", None),
    ],
)
def test_the_place_is_read_off_a_weather_question(words, place) -> None:
    assert context.weather_location(words) == place


@pytest.mark.asyncio
async def test_a_preset_call_runs_before_the_model_is_asked(monkeypatch) -> None:
    from app.services import agent
    from app.services.tools.base import Tool, ToolContext

    seen: list[dict] = []
    calls: list[dict] = []

    class _Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        async def aiter_lines(self):
            for line in ['data: {"choices":[{"delta":{"content":"답"}}]}', "data: [DONE]"]:
                yield line

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        def stream(self, _m, _p, *, json):
            seen.append(json)
            return _Response()

    async def client(*_a, **_k):
        return _Client()

    monkeypatch.setattr(agent, "_client", client)

    async def run(args):
        calls.append(args)
        return ToolResult(content="[1] 결과\nhttps://example.com/a\n요약")

    tool = Tool(
        name="web_search",
        description="d",
        parameters={"type": "object"},
        run=run,
        label="웹 검색 중",
    )
    events = [
        e
        async for e in agent.run_turn(
            "m",
            [{"role": "user", "content": "앤트로픽 최신 모델 이름이 뭐야"}],
            [tool],
            ToolContext(user_id="u", session_id="s", api_key="k"),
            preset_call=("web_search", {"query": "앤트로픽 최신 모델 이름"}),
        )
    ]

    # The tool ran with the server's query, before any model request.
    assert calls == [{"query": "앤트로픽 최신 모델 이름"}]
    assert len(seen) == 1
    roles = [m["role"] for m in seen[0]["messages"]]
    assert roles[-2:] == ["assistant", "tool"]
    assert seen[0]["messages"][-1]["content"].startswith("[1] 결과")
    # And it was shown as a step, then the answer came.
    steps = [e for e in events if e["type"] == "step"]
    assert steps[0]["label"] == "웹 검색 중" and steps[-1]["status"] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "delta").startswith("답")


@pytest.mark.asyncio
async def test_page_reading_stops_after_the_cap(monkeypatch) -> None:
    """A model that keeps reading pages is told to answer after `MAX_FETCHES`."""
    from app.services import agent
    from app.services.tools.base import Tool, ToolContext

    seen: list[dict] = []
    fetch_call = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c0",'
        '"function":{"name":"fetch_url","arguments":"{\\"url\\":\\"https://x.test/p\\"}"}}]}}]}',
        "data: [DONE]",
    ]
    script = [fetch_call] * 10 + [
        ['data: {"choices":[{"delta":{"content":"답"}}]}', "data: [DONE]"]
    ]

    class _Response:
        status_code = 200

        def __init__(self, lines):
            self._lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        def stream(self, _m, _p, *, json):
            seen.append(json)
            return _Response(
                script.pop(0)
                if script
                else ['data: {"choices":[{"delta":{"content":"답"}}]}', "data: [DONE]"]
            )

    async def client(*_a, **_k):
        return _Client()

    monkeypatch.setattr(agent, "_client", client)

    async def run(_args):
        return ToolResult(content="페이지")

    tool = Tool(
        name="fetch_url",
        description="d",
        parameters={"type": "object"},
        run=run,
        label="문서 읽는 중",
    )
    _ = [
        e
        async for e in agent.run_turn(
            "m",
            [{"role": "user", "content": "버스 노선 알려줘"}],
            [tool],
            ToolContext(user_id="u", session_id="s", api_key="k"),
        )
    ]
    # Six reads, then one tool-free closing request.
    assert len(seen) == agent.MAX_FETCHES + 1
    assert "tools" not in seen[-1]
    assert "문서는 충분히 읽었습니다" in seen[-1]["messages"][-1]["content"]


@pytest.mark.parametrize(
    ("words", "hints", "query"),
    [
        (
            "2026년 NeurIPS 마감일 공식 사이트 기준으로 알려줘",
            {"official": True},
            "2026년 NeurIPS 마감일",
        ),
        (
            "삼성전자 관련 이번 주 뉴스로 정리해줘",
            {"time_range": "week", "kind": "news"},
            "삼성전자 관련 이번 주 뉴스로",
        ),
        ("Ubuntu 24.04 EOL 영어 자료로 찾아줘", {"language": "en"}, "Ubuntu 24.04 EOL"),
        (
            "국가장학금 2차 신청 기간 site:kosaf.go.kr",
            {"site": "kosaf.go.kr"},
            "국가장학금 2차 신청 기간",
        ),
        ("오늘 휘발유 평균 가격 얼마야?", {}, "오늘 휘발유 평균 가격"),
    ],
)
def test_hints_in_the_question_are_read_and_left_out_of_the_query(words, hints, query) -> None:
    assert context.search_hints(words) == hints
    assert context.search_query(words) == query
