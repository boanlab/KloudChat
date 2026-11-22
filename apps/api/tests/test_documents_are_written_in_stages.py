"""Staged model routing of reports and decks: the `document` row writes ordinary parts; plan,
hard sections and repairs go to the upper-model rows; the strict column stays inside."""

from __future__ import annotations

import httpx
import pytest
from test_complex_documents_think_harder_on_flash import (
    CATALOGUE as BASE_CATALOGUE,
)
from test_complex_documents_think_harder_on_flash import (
    LOCAL_27B,
    STRICT_27B,
    _route,
)
from test_super_agent_turns import FLASH, STRICT_FLASH, _model

from app.models.chat import RoutingMode, SessionKind
from app.models.governance import Governance
from app.services import deck, doc_stages, model_health, thinking
from app.services import super_agent as sa

GLM = _model("z-ai/glm-5.3", boundary="external", cost=2)
GLM_FLASH = {**_model("z-ai/glm-5.3-flash", boundary="external", cost=1), "reasoning": {
    "efforts": ["low", "high", "max"], "default": "max", "mandatory": True}}
KIMI = _model("moonshotai/kimi-k3.1-pro", boundary="external", cost=8)
CATALOGUE = [*BASE_CATALOGUE, GLM, GLM_FLASH, KIMI]
RULES = sa.normalise_rules(None)


@pytest.fixture(autouse=True)
def _clean():
    model_health.reset()
    doc_stages.CURRENT.set(None)
    yield
    model_health.reset()
    doc_stages.CURRENT.set(None)


def _pick(stage, *, strict=False, allowed=frozenset(), writer=LOCAL_27B, rules=RULES,
          catalogue=CATALOGUE, skip=frozenset()):
    return sa.stage_pick(rules, stage, catalogue, kind="report", strict=strict,
                         allowed=set(allowed), writer=writer, skip=skip)


# -- the table ---------------------------------------------------------------------------


def test_the_table_has_three_stage_rows_with_the_owners_defaults() -> None:
    assert sa.STAGE_ROWS == {"plan": "document_plan", "hard": "document_hard",
                             "repair": "document_repair"}
    for row in sa.STAGE_ROWS.values():
        assert row in sa.CLASSES and row in sa.OVERRIDE_ROWS
        assert sa.DEFAULT_RULES[row]["model"] == (
            "z-ai/glm-5.3-flash" if row == "document_plan" else "z-ai/glm-5.3")
        assert sa.DEFAULT_RULES[row]["strictModel"] == "strict-local/qwen3.8-flash"
        assert sa.DEFAULT_RULES[row]["strictEffort"] == "low"
    assert sa.DEFAULT_RULES["document_hard"]["effort"] == "default"
    assert sa.DEFAULT_RULES["document"]["model"] == "local/qwen3.8-flash"
    assert sa.ROUTER_VERSION.startswith("super-agent-2026-11-")


def test_an_admin_changes_one_row_without_touching_the_others() -> None:
    rules = sa.normalise_rules({"document_hard": {"model": "moonshotai/kimi-k3.1-pro"},
                                "document": {"model": "local/qwen3.8-flash"}})
    assert rules["document_hard"]["model"] == KIMI["id"]
    assert rules["document_plan"]["model"] == GLM_FLASH["id"]
    assert rules["document"]["model"] == "local/qwen3.8-flash"
    assert sa.stored_rules({"document_hard": {"model": KIMI["id"]}}) == {
        "document_hard": {"model": KIMI["id"]}}
    # an empty column switches the stage off (kept stored); an unknown row is dropped
    assert sa.normalise_rules({"document_repair": {"model": ""}})["document_repair"][
        "model"] == ""
    assert "document_x" not in sa.normalise_rules({"document_x": {"model": "a"}})


def test_the_outline_runs_on_glm_flash_with_explicit_low_effort_never_enabled_false() -> None:
    row = sa.DEFAULT_RULES["document_plan"]
    assert row["model"] == "z-ai/glm-5.3-flash" and row["effort"] == "low"
    assert (row["strictModel"], row["strictEffort"]) == ("strict-local/qwen3.8-flash", "low")
    picked = _pick("plan")
    level = sa.stage_effort(RULES, "plan", sa.AUTO)
    assert level == "low"
    # sent from the first call, as a reasoning level: the model cannot switch reasoning off
    assert sa.reasoning_fields(picked["id"], level, picked) == {"reasoning": {"effort": "low"}}
    # the hard and repair rows keep GLM-5.3 and its own default effort
    for stage in ("hard", "repair"):
        assert sa.DEFAULT_RULES[sa.STAGE_ROWS[stage]]["model"] == "z-ai/glm-5.3"
        assert sa.DEFAULT_RULES[sa.STAGE_ROWS[stage]]["effort"] == "default"
    # the fast chip would ask for none: a model that cannot turn it off gets its lowest level
    fast = sa.stage_effort(RULES, "plan", sa.FAST)
    assert sa.reasoning_fields(picked["id"], fast, picked) == {"reasoning": {"effort": "low"}}


def test_glm_flash_is_a_small_external_model_for_the_mechanisms() -> None:
    from app.services import model_tier

    assert model_tier.group("z-ai/glm-5.3-flash") == model_tier.SMALL_EXTERNAL
    assert model_tier.group("z-ai/glm-5.3") == model_tier.STRONG_EXTERNAL


# -- which model ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["plan", "hard", "repair"])
def test_super_goes_up_and_strict_stays_inside(stage) -> None:
    assert _pick(stage)["id"] == (GLM_FLASH if stage == "plan" else GLM)["id"]
    strict = _pick(stage, strict=True, writer=STRICT_27B)
    assert strict["id"] == STRICT_FLASH["id"] and sa.strict_model(strict)


@pytest.mark.parametrize("stage", ["plan", "hard", "repair"])
def test_strict_stages_run_on_the_strict_writers_own_flash_because_they_think(stage) -> None:
    """The strict writer is flash and so are the strict stage rows: a stage on the writer's own
    model is a stage only when it asks it to think, which the built-in rows do (low)."""
    picked = _pick(stage, strict=True, writer=STRICT_FLASH)
    assert picked is not None and picked["id"] == STRICT_FLASH["id"]
    level = sa.stage_effort(RULES, stage, sa.AUTO, strict=True)
    assert level == "low"
    assert sa.reasoning_fields(picked["id"], level, picked) == {
        "chat_template_kwargs": {"enable_thinking": True}, "thinking_token_budget": 500}
    # without the stage's thinking it would be the writer's own call: no stage
    plain = sa.normalise_rules({sa.STAGE_ROWS[stage]: {"strictEffort": "none"}})
    assert _pick(stage, strict=True, writer=STRICT_FLASH, rules=plain) is None




def test_a_non_strict_model_in_the_strict_column_is_never_used() -> None:
    rules = sa.normalise_rules({"document_hard": {"strictModel": GLM["id"]}})
    assert _pick("hard", strict=True, rules=rules, writer=STRICT_27B) is None


def test_a_missing_upper_model_leaves_the_stage_with_the_writer() -> None:
    no_glm = [m for m in CATALOGUE if m["id"] != GLM["id"]]
    assert _pick("hard", catalogue=no_glm) is None            # not served
    assert _pick("hard", allowed={LOCAL_27B["id"]}) is None   # not allowed
    assert _pick("hard", skip=frozenset({GLM["id"]})) is None  # failed lately
    assert _pick("hard", writer=GLM) is None                   # the writer itself
    off = sa.normalise_rules({"document_hard": {"model": ""}})
    assert _pick("hard", rules=off) is None


def test_the_chip_steers_the_stage_effort() -> None:
    assert sa.stage_effort(RULES, "plan", sa.AUTO) == "low"
    assert sa.stage_effort(RULES, "hard", sa.AUTO) == "default"
    assert sa.stage_effort(RULES, "hard", sa.FAST) == "none"
    assert sa.stage_effort(RULES, "repair", sa.DEEP) == sa.HIGHEST


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [SessionKind.report, SessionKind.slides])
async def test_the_document_route_names_the_stages_and_keeps_the_writer(kind) -> None:
    model, strict, route = await _route(None, kind=kind, catalogue=CATALOGUE)
    assert model["id"] == FLASH["id"] and not strict
    assert route["stages"] == {s: (GLM_FLASH if s == "plan" else GLM)["id"]
                              for s in doc_stages.STAGES}


@pytest.mark.asyncio
async def test_the_strict_entry_names_only_strict_local_stages() -> None:
    model, strict, route = await _route(None, mode=RoutingMode.super_strict,
                                        catalogue=CATALOGUE)
    # the strict writer is flash, and its stages run on flash too (they ask it to think)
    assert model["id"] == STRICT_FLASH["id"] and strict
    assert route["stages"] == {s: STRICT_FLASH["id"] for s in doc_stages.STAGES}


@pytest.mark.asyncio
async def test_personal_data_moves_the_stages_to_the_strict_column() -> None:
    policy = Governance(external_data_guard=True, internal_data_strict_local=False,
                        pii_masking=True)
    model, strict, route = await _route(
        None, policy=policy, catalogue=CATALOGUE,
        request="김민수(주민번호 900101-1234567)의 이직 보고서를 써 줘")
    assert strict and sa.strict_model(model)
    assert set(route["stages"].values()) == {STRICT_FLASH["id"]}


@pytest.mark.asyncio
async def test_no_usable_upper_model_means_no_stages_and_a_written_document() -> None:
    only_house = [m for m in BASE_CATALOGUE]
    model, _, route = await _route(None, catalogue=only_house)
    assert model["id"] == FLASH["id"] and "stages" not in route


@pytest.mark.asyncio
async def test_a_model_picked_by_hand_is_not_staged() -> None:
    model, _, route = await _route(None, mode=RoutingMode.manual, catalogue=CATALOGUE)
    assert "stages" not in route and model["id"] == LOCAL_27B["id"]


def test_stage_models_drop_non_strict_rows_in_a_strict_turn() -> None:
    ids = {"plan": GLM["id"], "hard": STRICT_FLASH["id"]}
    stages = doc_stages.from_ids(ids, CATALOGUE, strict=True)
    assert stages.ids == {"hard": STRICT_FLASH["id"]}
    assert doc_stages.from_ids(ids, CATALOGUE).ids == ids


# -- what is hard --------------------------------------------------------------------------

TABLE = "분기,매출,영업이익\n1분기,1820,412\n2분기,1950,470\n3분기,2100,530\n4분기,2300,600"


def test_report_sections_are_hard_by_role_and_by_figures_in_the_source() -> None:
    heads = ["요약", "배경", "현황 분석", "도입 방안", "실적 결과", "결론"]
    hard = doc_stages.hard_report_sections(heads, TABLE)
    assert hard == {0: "summary", 2: "analysis", 5: "conclusion"}  # capped at half
    # no cap beyond the share: the strongest reasons stay
    assert len(hard) <= len(heads) * doc_stages.HARD_MAX_SHARE


def test_figures_only_make_a_section_hard_when_the_source_has_figures() -> None:
    heads = ["배경", "실적 결과", "일정", "다음 단계"]
    assert doc_stages.hard_report_sections(heads, "숫자 없는 자료") == {}
    assert doc_stages.hard_report_sections(heads, TABLE) == {1: "figures"}


def test_a_short_document_keeps_something_for_the_writer() -> None:
    assert doc_stages.hard_report_sections(["요약"], TABLE) == {}
    assert len(doc_stages.hard_report_sections(["요약", "결론"], TABLE)) == 1


def test_deck_hard_slides_are_data_slides_and_the_summary() -> None:
    slides = [{"layout": "title", "title": "분기 실적"}, {"layout": "agenda", "title": "목차"},
              {"layout": "bullets", "title": "배경"}, {"layout": "chart", "title": "분기별 매출"},
              {"layout": "table", "title": "제품별 비교"}, {"layout": "bullets", "title": "과제"},
              {"layout": "bullets", "title": "요약과 결정 사항"},
              {"layout": "closing", "title": "감사합니다"}]
    hard = doc_stages.hard_slides(slides, TABLE)
    assert hard == {6: "summary", 3: "data", 4: "data"} or set(hard) <= {3, 4, 6}
    assert all(slides[i]["layout"] not in ("title", "agenda", "closing") for i in hard)
    # no figures in the source: the data layouts are ordinary
    assert set(doc_stages.hard_slides(slides, "자료 없음")) <= {6}


# -- the call ------------------------------------------------------------------------------


def _stages(**ids) -> doc_stages.StageModels:
    stages = doc_stages.from_ids(ids, CATALOGUE)
    doc_stages.CURRENT.set(stages)
    return stages


@pytest.mark.asyncio
async def test_a_stage_call_goes_up_and_its_tokens_are_kept_apart() -> None:
    _stages(hard=GLM["id"])
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        asked.append(model)
        return "답", {"inputTokens": 100, "outputTokens": 40}

    usage: dict = {}
    text, spent = await doc_stages.staged(complete, "hard", LOCAL_27B["id"],
                                          lambda m: [{"m": m}], "k", 100, usage)
    assert text == "답" and asked == [GLM["id"]] and spent == {"inputTokens": 0,
                                                              "outputTokens": 0}
    assert usage["stageUsage"]["hard"] == {"model": GLM["id"], "inputTokens": 100,
                                           "outputTokens": 40, "calls": 1, "fallbacks": 0}
    # a stage with no upper model, or no stage at all, is the writer's
    for stage in ("repair", None):
        text, spent = await doc_stages.staged(complete, stage, LOCAL_27B["id"],
                                              lambda m: [], "k", 100, usage)
        assert asked[-1] == LOCAL_27B["id"] and spent["inputTokens"] == 100


def test_the_writers_own_model_is_a_stage_only_when_the_stage_asks_it_to_think() -> None:
    rules = sa.normalise_rules({"document_hard": {"model": GLM["id"], "effort": "default"},
                                "document_plan": {"model": GLM["id"], "effort": "low"}})
    assert _pick("hard", writer=GLM, rules=rules) is None
    assert _pick("plan", writer=GLM, rules=rules) == GLM


@pytest.mark.asyncio
async def test_a_stage_on_the_writers_model_with_thinking_is_called_with_its_own_fields() -> None:
    stages = _stages(plan=GLM["id"], hard=GLM["id"])
    stages.fields["plan"] = {"reasoning": {"effort": "low"}}
    stages.fields["hard"] = {}
    seen: list[dict | None] = []

    async def complete(model, messages, api_key, max_tokens):
        seen.append(thinking.turn_fields(model))
        return "답", {"inputTokens": 1, "outputTokens": 1}

    usage: dict = {}
    await doc_stages.staged(complete, "plan", GLM["id"], lambda m: [], "k", 100, usage)
    await doc_stages.staged(complete, "hard", GLM["id"], lambda m: [], "k", 100, usage)
    assert seen == [{"reasoning": {"effort": "low"}}, None]
    assert "plan" in usage["stageUsage"] and "hard" not in usage["stageUsage"]


@pytest.mark.asyncio
async def test_a_failed_upper_model_is_replaced_by_the_writer_for_the_rest_of_the_turn() -> None:
    stages = _stages(hard=GLM["id"], repair=GLM["id"])
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        asked.append(model)
        if model == GLM["id"]:
            raise httpx.ConnectError("down")
        return "써 둔 글", {"inputTokens": 5, "outputTokens": 7}

    usage: dict = {}
    text, spent = await doc_stages.staged(complete, "hard", LOCAL_27B["id"], lambda m: [],
                                          "k", 100, usage)
    assert text == "써 둔 글" and spent["outputTokens"] == 7
    assert asked == [GLM["id"], LOCAL_27B["id"]] and GLM["id"] in stages.down
    await doc_stages.staged(complete, "repair", LOCAL_27B["id"], lambda m: [], "k", 100, usage)
    assert asked[2:] == [LOCAL_27B["id"]]
    assert usage["stageUsage"]["hard"]["fallbacks"] == 1


@pytest.mark.asyncio
async def test_an_empty_upper_answer_is_billed_and_written_again_by_the_writer() -> None:
    _stages(plan=GLM["id"])
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        asked.append(model)
        return ("" if model == GLM["id"] else "개요"), {"inputTokens": 3, "outputTokens": 2}

    usage: dict = {}
    text, _ = await doc_stages.staged(complete, "plan", LOCAL_27B["id"], lambda m: [], "k",
                                      50, usage)
    assert text == "개요" and asked == [GLM["id"], LOCAL_27B["id"]]
    assert usage["stageUsage"]["plan"]["inputTokens"] == 3
    assert usage["stageUsage"]["plan"]["fallbacks"] == 1


@pytest.mark.asyncio
async def test_each_stage_is_sent_its_own_reasoning_and_it_is_put_back() -> None:
    stages = _stages(plan=KIMI["id"], hard=KIMI["id"])
    stages.fields = {"plan": {"reasoning": {"effort": "low"}}, "hard": {}}
    seen: list[dict | None] = []

    async def complete(model, messages, api_key, max_tokens):
        seen.append(thinking.turn_fields(model))
        return "x", {"inputTokens": 1, "outputTokens": 1}

    token = thinking.TURN_REASONING.set({"other": {"a": 1}})
    try:
        for stage in ("plan", "hard"):
            await doc_stages.staged(complete, stage, LOCAL_27B["id"], lambda m: [], "k", 9, {})
        assert seen == [{"reasoning": {"effort": "low"}}, None]
        assert thinking.TURN_REASONING.get() == {"other": {"a": 1}}
    finally:
        thinking.TURN_REASONING.reset(token)


def test_stage_tokens_are_billed_at_their_own_price_and_shown_as_a_share() -> None:
    usage = {"inputTokens": 6000, "outputTokens": 3000, "outlineInputTokens": 0,
             "outlineOutputTokens": 0,
             "stageUsage": {"hard": {"model": GLM["id"], "inputTokens": 1000,
                                     "outputTokens": 500, "calls": 2, "fallbacks": 0}}}
    rows = {GLM["id"]: GLM}
    assert doc_stages.credits(usage, rows, lambda row, i, o: (i + o) * row["creditCost"]) == 3000
    info = doc_stages.run_info(usage, LOCAL_27B["id"], "", rows)
    assert info["selfHostedShare"] == pytest.approx(9000 / 10500, abs=0.001)
    assert info["selfHostedOutputShare"] == pytest.approx(3000 / 3500, abs=0.001)
    assert info["stages"][0]["model"] == GLM["id"]
    assert doc_stages.run_info({"inputTokens": 5}, LOCAL_27B["id"], "") is None


# -- the deck's storyline ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_decks_outline_is_asked_of_the_plan_model(monkeypatch) -> None:
    _stages(plan=GLM["id"])
    asked: list[str] = []

    async def fake(model, messages, api_key, max_tokens):
        asked.append(model)
        return "", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(deck, "_complete", fake)
    events = [e async for e in deck.write(
        request="신입 온보딩 슬라이드 6장", model=LOCAL_27B["id"], api_key="k",
        web_search=False)]
    # the plan stage fell back to the writer once the upper model answered nothing
    assert asked[0] == GLM["id"] and LOCAL_27B["id"] in asked
    assert events[-1]["type"] == "usage"
    assert events[-1]["stageUsage"]["plan"]["fallbacks"] >= 1
