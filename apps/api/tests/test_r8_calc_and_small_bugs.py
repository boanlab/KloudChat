"""Round 8: the calculation pass's time limit and four small bugs, with the round's own strings.

- calc (all groups): at 120 s the pass timed out on every setup's market-size task and the
  writer's own arithmetic went 10~100x wrong (A10-T2: TAM < SAM); now 240 s, one finished
  program's lines are used unchecked, and figures the writer works out itself are marked
  「(추정)」.
- verify (all groups; seen on the light 27B): a repair reply item `{'sentence': …}` went into
  the text as a dict (L27L A07-T1).
- code-values (all groups): a correct 4.17 bound to the row's sample size became 「24점」
  (LFLF A06-T1); clashes are marked, never rewritten.
- citations (all groups): 네이버웍스 prices linked to msn articles on streaming subscriptions
  (L27F A11-T2); a number whose page shares no word with its sentence is taken out.
- deck tables (all groups): a six-column comparison lost the priority algorithm to the
  five-column cap (LFLF A09-T2); a wide, short table is turned instead.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.pipeline import report_stages
from app.pipeline.stage import Context
from app.services import agent, calc, calc_check, deck, model_tier, verify

L27, STRONG = "local/qwen3.8-27b", "deepseek/deepseek-v4-pro-0813"
GROUPS = pytest.mark.parametrize("writer", [L27, STRONG])

#: r8 tasks/R3-A10-T2 (the same task as r7)
A10_T2 = "\n".join([
    "국내 초·중·고 대상 「AI 학습 튜터(에듀테크)」 시장 규모를 추정해 줘. 신사업 TF 1차 검토용이야.",  # noqa: E501
    "- TAM·SAM·SOM 구분해서, 하향식과 상향식 둘 다로",
    "- 학생 수 등 기초 통계는 공개 출처(교육부·통계청 등)를 찾아서 쓰고 출처 달기",
    "- 우리 가정(한빛소프트 사내 가정, 가상): 월 구독료 9,900원, 초기 3년 목표 점유율은 유료 이용 학생 기준 2%",  # noqa: E501
    "- 고객군(학부모 직접 구매, 학교·교육청 구매, 학원)별로 나눠서 보여 주고, 각 숫자가 어떤 가정에서 나왔는지 계산식으로",  # noqa: E501
])


# ── calc ────────────────────────────────────────────────────────────────


def test_the_deadline_leaves_room_for_two_programs():
    assert calc.DEADLINE >= 240


async def test_one_finished_program_is_used_when_the_check_run_is_late(monkeypatch):
    class Backends:
        exec = "sandbox"

    async def tools_config():
        return Backends()

    calls = {"n": 0}

    async def one_run(prompt, material, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return ["학생 수 = 5,130,000", "TAM(연) = 5,130,000 × 118,800 = 609,444,000,000",
                    "SOM = 609,444,000,000 × 0.02 = 12,188,880,000"]
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(calc.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(calc, "_one_run", one_run)
    block, _, status = await calc.computed_values(
        A10_T2, ["학생 수 5,130,000명, 9,900원, 2%, 12개월"], complete=None, model="m",
        api_key="k", deadline=0.2)
    assert status == "partial"
    assert "검산 전" in block and "SOM = 609,444,000,000 × 0.02 = 12,188,880,000" in block
    # what the writer computes beyond these lines it marks itself
    assert "「(추정)」" in block


@GROUPS
async def test_a_partial_run_reaches_the_writer_as_code_values(monkeypatch, writer):
    async def partial(*_a, **_k):
        return ("# 코드로 계산한 값(검산 전)\nTAM(연) = 609,444,000,000",
                {"inputTokens": 0, "outputTokens": 0}, "partial")

    monkeypatch.setattr(report_stages.calc, "computed_values", partial)
    bundle = report_stages.bundle_for(request=A10_T2, model=writer, api_key="k")
    events = [e async for e in report_stages.Calc().run(bundle, Context())]
    assert events[-1]["status"] == "done" and "한 번 실행한 값" in events[-1]["label"]
    assert "TAM(연) = 609,444,000,000" in bundle.computed.values
    assert report_stages.CALC_MISSING_NOTE not in [p.text for p in bundle.material.trusted]


@GROUPS
async def test_a_timed_out_pass_has_the_writer_mark_its_own_figures(monkeypatch, writer):
    async def timed_out(*_a, **_k):
        return "", {"inputTokens": 0, "outputTokens": 0}, "timeout"

    monkeypatch.setattr(report_stages.calc, "computed_values", timed_out)
    bundle = report_stages.bundle_for(request=A10_T2, model=writer, api_key="k")
    events = [e async for e in report_stages.Calc().run(bundle, Context())]
    assert "추정" in events[-1]["label"]
    assert "「(추정)」" in report_stages.CALC_MISSING_NOTE
    assert report_stages.CALC_MISSING_NOTE in [p.text for p in bundle.material.trusted]


# ── verify: a repair reply's shape ──────────────────────────────────────

#: r8 L27L A07-T1, the sentence that came out as 「{'sentence': '이에 대한 첫 단계로, …'}」
A07_OLD = ("이에 대한 첫 단계로, 교육부의 ‘학교에서의 안전한 AI 도입·활용 가이드라인’(2026년 3월 "
           "발표)[11]을 바탕으로 교내 시범 교과목 2~30개를 선정하여 소규모 파일럿을 가동합니다.")
A07_NEW = ("이에 대한 첫 단계로, 교육부의 ‘학교에서의 안전한 AI 도입·활용 가이드라인’(2026년 3월 "
           "예정)[11] 발표 전까지는 교내 시범 교과목 2~30개를 선정하여 소규모 파일럿을 가동합니다.")


@pytest.mark.parametrize("reply, kept", [
    ([{"sentence": A07_NEW}], True),
    ([A07_NEW], True),
    ([{"problem": "시점"}], False),
    ([7], False),
])
def test_a_repair_reply_item_is_a_sentence_or_nothing(reply, kept):
    doc = verify.Doc(sections=[{"heading": "추진 방안", "content": f"{A07_OLD} 다음 문장."}])
    finding = verify.Finding(section=0, sentence=A07_OLD, code="date", why="시점")

    async def complete(*_a, **_k):
        return json.dumps(reply, ensure_ascii=False), {}

    sections, left, _ = asyncio.run(verify.repair(
        doc, [finding], complete=complete, model="m", api_key="k"))
    content = sections[0]["content"]
    assert "{'sentence'" not in content and "{\"sentence\"" not in content
    assert (A07_NEW in content) is kept
    assert (finding in left) is not kept


# ── code-values: marked, never rewritten ───────────────────────────────

#: r8 LFLF A06-T1: the code's per-college lines (표본, 전반만족)
A06_PRINTED = ["\n".join([
    "사회과학대학: 표본 = 24, 전반만족 = 4.17",
    "인문대학: 표본 = 25, 전반만족 = 3.92",
    "예술대학: 표본 = 18, 전반만족 = 3.39",
])]
A06_ANSWER = ("단과대학별 전반만족은 사회과학대학 4.17점이 가장 높고 예술대학 3.39점이 가장 "
              "낮습니다.")


def _stated():
    return calc_check.printed_values(A06_PRINTED)


def test_the_printed_lines_read_by_row():
    rows = {(s.row, s.label): s.value for s in _stated()}
    assert rows.get(("사회과학대학", "전반만족")) == 4.17
    assert rows.get(("사회과학대학", "표본")) == 24


def _marks(writer: str) -> bool:
    """What `agent.run_turn` passes: the self-hosted writers' clashes are marked."""
    return model_tier.group(writer) == model_tier.SELF_HOSTED


@GROUPS
def test_a_correct_value_is_never_rewritten_to_another_label_s(writer):
    text, found = agent.code_checked(A06_ANSWER, A06_PRINTED, mark=_marks(writer))
    assert text == A06_ANSWER and found == []
    # the round's slip: a sentence read as naming the row's sample size. Without the row
    # guard (the textbook schedule's check) it became 「24」; the code-values check leaves the
    # row's own printed 4.17 as written, in either group
    bound = "사회과학대학 표본은 4.17점으로 가장 높습니다."
    old, fixes = calc_check.check_markdown(bound, _stated(), A06_PRINTED)
    assert "24" in old and fixes
    text, found = agent.code_checked(bound, A06_PRINTED, mark=_marks(writer))
    assert text == bound and found == []


@GROUPS
def test_a_value_the_code_never_printed_for_the_row_is_marked_or_set_by_group(writer):
    wrong = A06_ANSWER.replace("4.17점", "4.71점")
    table = "| 단과대학 | 표본 | 전반만족 |\n|---|---|---|\n| 사회과학대학 | 24 | 4.71 |"
    text, found = agent.code_checked(wrong, A06_PRINTED, mark=_marks(writer))
    cell, _ = agent.code_checked(table, A06_PRINTED, mark=_marks(writer))
    assert len(found) == 1
    if _marks(writer):
        # self-hosted: marked where it stands, the writer's number kept
        assert "4.71점(확인 필요)이 가장 높고" in text and "| 4.71(확인 필요) |" in cell
    else:
        # strong external: set to the code's value, as before
        assert "4.17점이 가장 높고" in text and "| 4.17 |" in cell


# ── citations: a number on a page that does not say it ────────────────

#: r8 L27F A11-T2: the first search returned two msn articles, the second the pricing page
A11_SEARCH = "\n".join([
    "'네이버웍스 요금' 검색 결과:", "",
    "[1] \"유튜브, 넷플릭스, 챗GPT\"…하나둘 구독했더니 '텅장'",
    "https://www.msn.com/ko-kr/news/other/ar-AA2dENeP",
    "구독 서비스 지출이 늘어 통장이 비는 직장인이 많다.", "",
    "[2] \"SKT 자급제 전용 요금제 '에어', 월 1만원대로 쓰세요\"",
    "https://www.msn.com/ko-kr/news/techandscience/ar-AA2diAeN",
    "SK텔레콤이 자급제 전용 요금제를 내놨다.", "",
    "[3] 네이버웍스 요금 안내",
    "https://naver.worksmobile.com/pricing/",
    "네이버웍스 Lite 월 4,000원(연간 3,000원), Standard 월 8,500원, 화상회의 최대 200명.",
])
A11_ANSWER = "\n".join([
    "| 요금제 | 월간 | 연간 | 비고 |",
    "|---|---|---|---|",
    "| 네이버웍스 Lite | 4,000원 | 3,000원 | 메신저 중심, 화상 200명 [1] |",
    "| 네이버웍스 Standard | 8,500원 | 7,000원 | 메일·드라이브 [3] |",
    "",
    "네이버웍스는 클라우드 올인원(화상 200명)이 강하고 [1][2], 요금은 공식 안내를 따릅니다 [3].",
])


def _numbered() -> tuple[list[str], dict[str, str]]:
    sources: list[str] = []
    seen: dict[str, str] = {}
    agent._number_sources(A11_SEARCH, sources, {"name": "web_search", "arguments": "{}"}, {},
                          seen)
    return sources, seen


def test_the_writer_s_reading_is_kept_by_url():
    sources, seen = _numbered()
    assert sources[2] == "https://naver.worksmobile.com/pricing/"
    assert "Lite 월 4,000원" in seen[sources[2]]
    assert "텅장" in seen[sources[0]] and "Lite" not in seen[sources[0]]


def test_a_citation_on_an_unrelated_page_is_taken_out():
    sources, seen = _numbered()
    linked, cited = agent._link_citations(A11_ANSWER, sources, seen)
    assert "msn.com" not in linked
    assert cited == [3]
    assert linked.count("[[3]](https://naver.worksmobile.com/pricing/)") == 2
    assert "강하고, 요금은" in linked
    # without what was read, numbers are linked as before
    plain, plain_cited = agent._link_citations(A11_ANSWER, sources)
    assert "msn.com" in plain and plain_cited == [1, 2, 3]


def test_a_citation_its_page_bears_out_is_linked():
    sources, seen = _numbered()
    linked, cited = agent._link_citations("SK텔레콤이 자급제 전용 요금제를 냈습니다 [2].",
                                          sources, seen)
    assert cited == [2] and "[[2]](https://www.msn.com/ko-kr/news/techandscience/" in linked


def test_a_page_in_another_language_or_too_short_is_not_judged():
    sources = ["https://www.reuters.com/markets/fed", "https://a.example/short"]
    seen = {sources[0]: "[1] Fed holds rates steady as inflation cools\nThe Federal Reserve "
                        "left its benchmark rate unchanged on Wednesday, citing slower price "
                        "growth and a softer labour market.",
            sources[1]: "[2] 공고\n요약"}
    linked, cited = agent._link_citations(
        "연준은 기준금리를 동결했습니다 [1]. 공채는 다음 달 열립니다 [2].", sources, seen)
    assert cited == [1, 2]


# ── deck tables: turned, not cut ────────────────────────────────────────

#: r8 LFLF A09-T2: the comparison table the writer gave, six columns
A09_ROWS = [
    ["구분", "FCFS", "SJF", "SRTF", "RR(q=2)", "우선순위"],
    ["평균 대기시간", "4.75", "4.0", "3.0", "5.0", "5.5"],
    ["선점", "아니오", "아니오", "예", "예", "아니오"],
]


def test_a_wide_short_table_keeps_every_column_turned():
    rows = deck._clean_rows(A09_ROWS)
    assert rows[0] == ["구분", "평균 대기시간", "선점"]
    assert ["우선순위", "5.5", "아니오"] in rows
    assert len(rows) == 6 and all(len(r) <= 5 for r in rows)


def test_a_table_too_long_to_turn_keeps_the_cap():
    rows = [["항목", *(f"열{i}" for i in range(1, 7))]] + [
        [f"행{k}", *(str(k * i) for i in range(1, 7))] for k in range(1, 7)]
    out = deck._clean_rows(rows)
    assert all(len(r) == 5 for r in out) and out[0][0] == "항목"


@GROUPS
def test_the_same_for_every_group(writer):
    # none of these depend on the writer's group: code decides each
    assert model_tier.group(writer) in model_tier.GROUPS
    assert deck._clean_rows(A09_ROWS)[0][0] == "구분"
