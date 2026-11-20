"""Code and artifacts on self-hosted flash, except those that draw data (2026-11-20): over 46
artifact prompts flash matched or beat DeepSeek V4.1 Flash high on calculators, shop pages,
games, Python and forms, but clearly lost on dashboards (6.81 vs 7.96). A request to build
something (`code`) that draws data goes to the `visual` row; a question about a chart that asks
for nothing to be built stays where it was."""

from __future__ import annotations

import pytest
from test_super_agent_turns import DEEPSEEK, FLASH, STRICT_FLASH, _turn

from app.schemas.admin import GovernanceIn
from app.services import super_agent as sa


def _class(text: str, **kwargs) -> sa.Decision:
    return sa.classify(sa.Turn(text=text, **kwargs))


# ── the table ────────────────────────────────────────────────────────────────


def test_the_visual_row_sits_beside_code_in_every_list() -> None:
    assert sa.CLASSES.index("visual") == sa.CLASSES.index("code") + 1
    assert sa.DEFAULT_RULES["visual"] == {
        "model": "deepseek/deepseek-v4.1-flash", "strictModel": "strict-local/qwen3.8-flash",
        "effort": "high", "strictEffort": "none"}
    assert sa.DEFAULT_RULES["code"] == {
        "model": "local/qwen3.8-flash", "strictModel": "strict-local/qwen3.8-flash",
        "effort": "none"}
    # an ordinary class row: not an override row, and an admin may change it
    assert "visual" not in sa.OVERRIDE_ROWS
    rules = sa.normalise_rules({"visual": {"model": "z-ai/glm-5.3", "effort": "low"}})
    assert rules["visual"]["model"] == "z-ai/glm-5.3" and rules["visual"]["effort"] == "low"
    assert sa.stored_rules({"visual": {"model": "z-ai/glm-5.3"}}) == {
        "visual": {"model": "z-ai/glm-5.3"}}
    # an empty cell is not stored over a class row
    assert sa.normalise_rules({"visual": {"model": ""}})["visual"]["model"] == (
        sa.DEFAULT_RULES["visual"]["model"])
    # the admin schema holds every row
    assert GovernanceIn.model_fields["super_agent_rules"].metadata[0].max_length >= len(
        sa.CLASSES)


# ── the classifier signal ────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "월별 매출 표로 대시보드 만들어줘",
    "지역별 판매량 대시보드 만들어줘. 지역 선택 필터를 바꾸면 차트랑 카드가 같이 바뀌게 해 줘.",
    "웹사이트 방문자 동향 대시보드 만들어줘",
    "월별 매출 추이를 차트로 그려줘",
    "React로 차트 컴포넌트 만들어 줘",
    "matplotlib으로 그래프 그리는 코드 짜줘",
    "이 코드에서 차트가 안 그려지는 버그 고쳐 줘",
    "분기별 실적을 시각화하는 HTML 페이지 만들어 줘",
    "Build a dashboard web page for this sales data",
    "Plot the sine function in python",
    "Draw me a bar chart of these numbers",
])
def test_a_request_to_build_something_that_draws_data_is_visual(text) -> None:
    decision = _class(text)
    assert decision.task_class == "visual", decision
    assert decision.source == "rule" and decision.reason_code == "visual_request"
    assert {"code", "visual"} <= set(decision.signals)


def test_pasted_data_under_the_ask_still_makes_it_visual() -> None:
    rows = "\n".join(f"{m}월 | {100 + m * 7}" for m in range(1, 13))
    ask = ("아래 월별 매출 표로 대시보드 웹페이지 만들어줘. "
           "차트랑 요약 카드(합계, 평균, 최고 월) 보여 줘. HTML 하나로.")
    text = f"{ask}\n\n월 | 매출(백만원)\n{rows}\n\n" + "비고: 단위는 백만원\n" * 30
    assert _class(text).task_class == "visual"


@pytest.mark.parametrize("text", [
    "계산기 웹앱 만들어줘",
    "계산기 웹앱 만들어줘. 숫자 버튼이랑 사칙연산, 지우기(C) 있으면 돼. HTML 파일 하나로.",
    "쇼핑몰 홈페이지 목업 만들어줘. 상품 카드 그리드랑 장바구니 담기 되는 걸로.",
    "커피 원두 온라인 스토어 랜딩 페이지 만들어줘",
    "테트리스 게임 만들어줘",
    "회원가입 폼 HTML로 만들어줘",
    "파이썬 정렬 알고리즘 짜줘",
    "파이썬으로 CSV 열 평균 내는 함수를 짜 줘",
    "Make a todo app in React",
    "Write a Python script that renames files",
    # a list or a pick the app is about, not a plan of it
    "할 일 목록 앱 만들어줘",
    "게임 추천 앱 만들어줘",
    # a graph of nodes and edges is not a picture of data
    "파이썬으로 그래프 BFS 탐색 코드 짜줘",
    "Implement Dijkstra's shortest path on a weighted graph in Python",
    "LangGraph로 에이전트 그래프 만드는 코드 짜줘",
])
def test_other_code_and_artifacts_stay_code(text) -> None:
    assert _class(text).task_class == "code", text


@pytest.mark.parametrize("text", [
    "이 차트가 뭘 뜻하는지 설명해줘",
    "대시보드가 뭐야?",
    "차트와 그래프의 차이가 뭐야?",
    "엑셀 차트 만드는 방법 알려줘",
    "주식 차트 보는 법 알려줘",
    "Chart.js랑 ECharts 중 뭐가 나아?",
    "Explain what this chart means",
    "플로우차트 그리는 법 알려줘",
    "이 그래프에서 3분기 매출이 왜 떨어졌는지 분석해 줘",
])
def test_a_question_about_a_chart_that_builds_nothing_does_not_move(text) -> None:
    decision = _class(text)
    assert decision.task_class not in ("visual", "code"), (text, decision)


@pytest.mark.parametrize("text", ["앱 기획서 작성해 줘", "모바일 앱 아이디어 추천해 줘",
                                  "팀 회식 계획 짜줘", "파이썬 공부 계획 짜줘", "보고서 써 줘",
                                  "Write a plan for our new app"])
def test_a_plan_or_list_about_a_thing_is_not_building_it(text) -> None:
    assert "code" not in _class(text).signals


def test_the_classifiers_code_for_a_request_that_draws_data_becomes_visual() -> None:
    # no build verb, so the rules leave it to the classifier, which knows `code` only
    decision = _class("매출 대시보드 하나 부탁해")
    assert decision.ambiguous and "visual" in decision.signals
    said = sa.Decision("code", "classifier", "classified", confidence=0.9,
                       signals=decision.signals)
    moved = sa.visual_class(said)
    assert (moved.task_class, moved.source, moved.confidence) == ("visual", "classifier", 0.9)
    # another class, or code without the signal, stays
    chat = sa.Decision("chat", "classifier", "classified", signals=decision.signals)
    assert sa.visual_class(chat) is chat
    plain = sa.Decision("code", "classifier", "classified", signals=[])
    assert sa.visual_class(plain) is plain
    assert "code" in sa._CLASSIFIER_PROMPT and "dashboard" in sa._CLASSIFIER_PROMPT


# ── the picks ────────────────────────────────────────────────────────────────


def test_the_visual_row_picks_deepseek_outside_and_flash_without_thinking_inside() -> None:
    rules = sa.normalise_rules({})
    catalogue = [FLASH, STRICT_FLASH, DEEPSEEK]
    model, how = sa.pick(rules, "visual", catalogue, kind="chat", strict=False, allowed=set())
    assert (model["id"], how) == (DEEPSEEK["id"], "rule")
    decision = sa.Decision("visual", "rule", "visual_request")
    assert sa.steer(rules, decision, sa.AUTO) == ("visual", "high")
    model, how = sa.pick(rules, "visual", catalogue, kind="chat", strict=True, allowed=set())
    assert (model["id"], how) == (STRICT_FLASH["id"], "rule")
    assert sa.steer(rules, decision, sa.AUTO, strict=True) == ("visual", "none")
    # code runs on flash without thinking in both columns
    code = sa.Decision("code", "rule", "code_markers")
    model, _ = sa.pick(rules, "code", catalogue, kind="chat", strict=False, allowed=set())
    assert model["id"] == FLASH["id"] and sa.steer(rules, code, sa.AUTO) == ("code", "none")
    # 빠르게 moves the reasoning visual row to the everyday row
    assert sa.steer(rules, decision, sa.FAST) == ("chat", "none")


@pytest.mark.asyncio
async def test_a_dashboard_turn_is_written_on_the_visual_row(monkeypatch) -> None:
    out = await _turn(monkeypatch, "월별 매출 표로 대시보드 만들어줘")
    call = out["calls"][0]
    assert call["model"] == DEEPSEEK["id"]
    assert out["route"]["taskClass"] == "visual" and out["route"]["reasonCode"] == "visual_request"
    assert out["route"]["effort"] == "high" and out["route"]["tools"] == "full"
    assert out["classifier"] == []  # the rules decided


@pytest.mark.asyncio
async def test_a_calculator_app_turn_is_written_on_flash(monkeypatch) -> None:
    out = await _turn(monkeypatch, "계산기 웹앱 만들어줘")
    assert out["calls"][0]["model"] == FLASH["id"]
    assert out["route"]["taskClass"] == "code" and out["route"]["effort"] == "none"
    # tools stay on: the artifact is made with them
    assert out["route"]["tools"] == "full" and out["calls"][0]["tools"]


@pytest.mark.asyncio
async def test_the_classifiers_code_moves_a_dashboard_ask_to_the_visual_row(monkeypatch) -> None:
    out = await _turn(monkeypatch, "매출 대시보드 하나 부탁해", classify="code")
    assert out["classifier"]  # ambiguous to the rules: the classifier was asked
    assert out["route"]["taskClass"] == "visual" and out["route"]["source"] == "classifier"
    assert out["calls"][0]["model"] == DEEPSEEK["id"]


@pytest.mark.asyncio
async def test_the_strict_entry_writes_a_dashboard_inside_without_thinking(monkeypatch) -> None:
    out = await _turn(monkeypatch, "월별 매출 표로 대시보드 만들어줘", mode="super_strict")
    assert out["calls"][0]["model"] == STRICT_FLASH["id"]
    assert out["route"]["taskClass"] == "visual" and out["route"]["effort"] == "none"
