"""Each post-writing mechanism decides by the writer's model group (docs/architecture.md,
"Mechanisms by model group"): self-hosted (27B, flash), small external, strong external.

Round 8 ran 48 tasks on seven setups; the cases here use its own strings. Self-hosted writers
are `local/qwen3.8-27b` and `local/qwen3.8-flash`, a strong external one DeepSeek V4 Pro, a
small external one GLM-5.3 Flash."""

from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest

from app.pipeline import chat_stages, check_stages
from app.pipeline.bundle import Bundle, Request
from app.pipeline.check_stages import AnswerGrounding
from app.pipeline.stage import Context
from app.services import check_skills, model_tier, post_checks, value_guard

L27, LFL = "local/qwen3.8-27b", "local/qwen3.8-flash"
SMALL_EXT, STRONG_EXT = "z-ai/glm-5.3-flash", "deepseek/deepseek-v4-pro-0813"
#: a self-hosted writer and a strong external one: the two groups every change is tested on
GROUPS = pytest.mark.parametrize("writer", [L27, STRONG_EXT])


def test_the_groups(monkeypatch):
    monkeypatch.delenv("KCHAT_LIGHT_WRITERS", raising=False)
    assert model_tier.group(L27) == model_tier.group(LFL) == model_tier.SELF_HOSTED
    assert model_tier.group("strict-local/qwen3.8-27b") == model_tier.SELF_HOSTED
    assert model_tier.group("") == model_tier.SELF_HOSTED
    assert model_tier.group(SMALL_EXT) == model_tier.SMALL_EXTERNAL
    assert model_tier.group("deepseek/deepseek-v4.1-flash") == model_tier.SMALL_EXTERNAL
    assert model_tier.group(STRONG_EXT) == model_tier.STRONG_EXTERNAL
    assert model_tier.group("anthropic/claude-x") == model_tier.STRONG_EXTERNAL
    # the light-setup switch changes the prompt, not the group
    monkeypatch.setenv("KCHAT_LIGHT_WRITERS", L27)
    assert model_tier.strong(L27) and model_tier.group(L27) == model_tier.SELF_HOSTED
    # the model that actually wrote decides (a privacy route's local twin is self-hosted)
    assert Request(text="", model=STRONG_EXT, api_key="", actual_model=L27).group \
        == model_tier.SELF_HOSTED


# ── marking apart from deleting: post checks (matrix rows 7–9) ─────────────────────

class _Judge:
    """Judge, confirmer and writer by prompt; the writer hands back `reply`."""

    def __init__(self, judged: list[dict], reply: str) -> None:
        self.judged, self.reply, self.prompts = judged, reply, []

    async def __call__(self, model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        self.prompts.append((model, prompt))
        if prompt.startswith("You check an answer"):
            return json.dumps({"unsupported": self.judged}, ensure_ascii=False), {}
        if prompt.startswith("Below are the material an answer was written from and statements"):
            return json.dumps({"verdicts": []}), {}
        return self.reply, {}


#: r8 A02-T2: the person's own context in the request
A02_T2 = (
    "아래 발표문을 부서장 보고용으로 정리해 줘. 핵심은 5줄 이내로 요약하고, 우리 대학(비수도권 "
    "사립대, 재학생 약 8,000명, 올해 RISE 사업 참여 중)에 미치는 영향 3가지와 우리가 지금 준비할 "
    "일을 정리해 줘.\n\n---\n[교육부 보도 참고자료(가상)] 2027년 대학 재정지원사업 개편 방향\n\n"
    "교육부는 2027년부터 대학 재정지원사업을 '성과 중심·지역 연계형'으로 개편한다고 밝혔다.")
#: LFLF's sentence the flash judge called unsupported and the rewrite took out (its opening
#: as the step showed it)
A02_SENTENCE = "우리 대학은 올해 RISE 사업 참여 중으로 성과계획을 지역 연계형으로 다시 짜야 합니다."
#: r8 A11-T2 LFLF: the Free plan the search result [14] gives
A11_SEARCH = ("[14] 네이버웍스 코어 이용요금 - 네이버웍스 · naver.worksmobile.com/pricing\n"
              "Free: 무료, 바로 가입해 쓰는 요금제(화상회의 최대 4명·60분). Lite: 월 3,000원")
A11_SENTENCE = ("한빛워크는 클라우드형도 있습니다. 다만 네이버웍스처럼 Free 플랜으로 바로 가입해 "
                "쓰는 구조는 아닙니다.")


def _post(answer, judged, *, writer, request, computed=()):
    model = _Judge(judged, answer)
    out = asyncio.run(post_checks.run(
        answer, keys=["internal-comms"], request=request, material=request, attached="",
        computed=list(computed), today="2026-10-13", complete=model, model=writer,
        judge="flash", api_key="k", strong=model_tier.strong(writer)))
    return out, model


@GROUPS
def test_a_judged_sentence_on_the_person_s_own_facts_is_never_cut(writer):
    answer = f"## 영향\n- {A02_SENTENCE}\n- 재정 진단 지표 공개에 대비합니다."
    out, model = _post(answer, [{"quote": A02_SENTENCE.rstrip("."),
                                 "why": "재료는 대학의 주체 포함 여부를 명시하지 않았음"}],
                       writer=writer, request=A02_T2)
    assert A02_SENTENCE in out.text and not out.dropped and not out.sent
    assert any("근거에 있는 값이라 남김" in f["why"] for f in out.flags)
    # nothing went to a rewrite
    assert not any(p.startswith("Below are the material and") for _, p in model.prompts)


@GROUPS
def test_a_judged_sentence_a_search_page_bears_out_stays(writer):
    out, _ = _post(A11_SENTENCE, [{"quote": A11_SENTENCE, "why": "Free 플랜 언급 없음"}],
                   writer=writer, request="경쟁 비교 카드 만들어 줘", computed=[A11_SEARCH])
    assert out.text == A11_SENTENCE and not out.sent


def test_code_reads_figures_names_and_quotes():
    assert post_checks.key_tokens("재학생 약 8,000명, RISE 참여") == ["8000", "rise"]
    assert post_checks.code_reading("재학생 약 8,000명", A02_T2) == "present"
    assert post_checks.code_reading("재학생 약 9,000명", A02_T2) == "absent"
    assert post_checks.code_reading("사업은 일정대로 정상 추진 중입니다", A02_T2) == "unchecked"
    # 「80」 is not read inside 「8,000」
    assert post_checks.code_reading("80명", A02_T2) == "absent"


def test_a_counted_figure_is_looked_up_with_its_counter():
    # r10 LFLF SK-company-2: a long search page holds a bare 「306」 and 「9」 by chance
    page = "회사 소개 306 페이지 9 2026 리벨리온 투자 39 기업"
    assert post_checks.code_reading("임직원 수 306명", page) == "absent"
    assert post_checks.code_reading("총 9개 투자 라운드, 투자자 39개사", page) == "absent"
    assert post_checks.code_reading("임직원 수 306명", page + " 임직원 306 명") == "present"
    assert post_checks.code_reading("투자자 39개사", page + " 투자자 39개 회사") == "present"
    # dates and 만·억 stay bare: the page may write them another way
    assert post_checks.code_reading("2026년 9월 투자", page) == "present"


@GROUPS
def test_a_judged_sentence_code_cannot_check_is_marked_not_cut(writer):
    # r7 helps kept: 「정상 추진 중」 is still caught, now marked where it stands for a
    # self-hosted writer; a strong writer gets it as a note and its revision stands
    answer = "## 현황\n- 1차 시안을 받았습니다.\n- 사업은 일정대로 정상 추진 중입니다."
    out, model = _post(answer, [{"quote": "사업은 일정대로 정상 추진 중입니다",
                                 "why": "자료에 없음"}],
                       writer=writer, request="팀장 보고 써 줘\n1차 시안 수령")
    assert not out.dropped
    if model_tier.strong(writer):
        assert out.sent and "it may be wrong" in model.prompts[-1][1]
    else:
        assert "사업은 일정대로 정상 추진 중입니다(확인 필요)." in out.text and not out.sent


# ── marking: what is never marked (matrix row 7) ───────────────────────────────

TRACE = check_skills.entry("grounding-check")

#: r8 P02: the person's trip, and GLP's assumption block on it
P02 = ("느린 기차 여행 플래너가 되어 줘.\n\n내 여행:\n- 출발: 암스테르담 / 도착: 로마\n"
       "- 일정: 9월 하순 10일, 앞뒤로 이틀 조정 가능\n"
       "- 인원: 성인 2명, 한 명은 버스를 타면 멀미함\n"
       "- 교통비 예산: 두 사람 합쳐 약 600유로")
P02_ANSWER = ("# 암스테르담 → 로마, 느린 기차 10일\n\n"
              "**먼저 합리적으로 가정한 것** (틀리면 알려 주세요):\n"
              "- 9월 하순 = 9월 21일경 출발, 10일 = 9박 10일로 계산\n"
              "- 숙박비는 예산에 포함 안 함(교통비 600유로만 계산)\n")


def _ground(answer: str, request: str, *, writer: str, advice: str = "") -> str:
    bundle = chat_stages.bundle_for(
        answer=answer, request=request, model={"id": writer}, actual_model=None, api_key="k",
        session_id="s", today=dt.date(2026, 10, 14), first_message=request,
        messages=[{"role": "user", "content": request}], material=request, check_keys=(),
        check_material="", tool_outputs=[], judge="", kept_steps=[], usage={}, traced=True,
        advice=advice)

    async def go():
        return [e async for e in AnswerGrounding().run(bundle, Context())]

    asyncio.run(go())
    return bundle.document.answer


@GROUPS
def test_a_day_the_writer_assumed_inside_the_person_s_month_part_is_not_marked(writer):
    out = _ground(P02_ANSWER, P02, writer=writer)
    assert "9월 21일경 출발" in out and "9월 21일(확인 필요)" not in out


def test_the_assumption_label_covers_its_list_only():
    lines = P02_ANSWER.split("\n") + ["", "**솔직한 결론 하나**: 1인당 약 370유로"]
    under = TRACE.assumed_lines(lines)
    assert 3 in under and len(lines) - 1 not in under
    # a day in the person's 「9월 하순」, not in 「9월 초」
    assert TRACE._in_person_part(P02, 9, 21) and not TRACE._in_person_part(P02, 9, 5)


def test_the_system_date_a_note_dates_itself_with_is_not_marked():
    # r8 A15-T2 DSP: the research note's 「확인일」
    line = ("- 한솔제지 공식 사이트에 대전·장항 외에 천안·신탄진 공장이 확인됨(확인일 2026-10-14, "
            "hansol.com).")
    assert not [f for f in TRACE.check(line, "한솔제지 콜드메일", [], {"today": "2026-10-14"})
                if f["kind"] == "date"]
    # a date that is not today is still traced
    other = line.replace("2026-10-14", "2026-10-02")
    assert [f for f in TRACE.check(other, "한솔제지 콜드메일", [], {"today": "2026-10-14"})
            if f["kind"] == "date"]


def test_a_slot_s_own_length_and_a_fraction_are_not_values_to_source():
    # r8 A01-T2 (DSP·GLP·LFLF·QMX): 「14:55~15:00(5분(확인 필요), 확인 필요)」
    row = "| 4 | 워크숍(10/22) 준비 점검 | 미정 | 14:55~15:00(5분) | 논의 |"
    ev = value_guard.Evidence.of(["안건 4개, 오후 2시~3시 회의"])
    at = row.index("5분")
    assert value_guard.supported("5분", row, at, ev, 0)
    assert not value_guard.supported("7분", row.replace("5분", "7분"), at, ev, 0)
    # r8 A09-T1 QMX: 「10분의 1」 read as ten minutes
    line = "타임 퀀텀을 10분의 1로 줄이면 문맥 교환이 늘어납니다."
    assert value_guard.supported("10분", line, line.index("10분"), ev, 0)


def test_a_value_marked_twice_over_two_passes_reads_once():
    assert value_guard.tidy_marks("| 14:55~15:00(5분(확인 필요), 확인 필요) |") \
        == "| 14:55~15:00(5분, 확인 필요) |"
    assert value_guard.tidy_marks("점검 차단 사항 0건(확인 필요)(확인 필요)입니다.") \
        == "점검 차단 사항 0건(확인 필요)입니다."


def test_self_punishment_is_no_legal_consequence():
    # r8 P16 LFLF: 「자기 처벌」 marked as a legal consequence
    line = "실수한 자신을 자기 처벌처럼 몰아붙이지 않는 것이 먼저입니다."
    assert not TRACE.check(line, "요즘 너무 힘들어", [], {"advice": True})
    assert TRACE.check("음주운전은 형사처벌 대상입니다.", "x", [], {"advice": True})


@GROUPS
def test_an_advice_answer_with_marks_of_its_own_still_gets_one_box(writer):
    # r8 P19 L27F: the draft had its own 「(확인 필요)」, so the 3-mark cleanup was skipped
    # and 18 marks stayed in the body
    answer = ("예금 이자는 세전 연 2,000만원(확인 필요) 초과분부터 15.4% 원천징수됩니다. "
              "파킹통장 금리는 연 2.5%, CMA는 연 2.8%, 정기예금은 연 3.1%, 발행어음은 연 3.4% "
              "수준입니다.")
    out = check_stages._advice_note(
        answer.replace("2.5%", "2.5%(확인 필요)").replace("2.8%", "2.8%(확인 필요)")
        .replace("3.1%", "3.1%(확인 필요)"), answer,
        [{"kind": "number", "text": "2.5%"}, {"kind": "number", "text": "2.8%"},
         {"kind": "number", "text": "3.1%"}])
    body, note = out.rsplit("\n\n", 1)
    assert value_guard.MARK not in body
    assert note.startswith("확인 필요:") and "2,000만원" in note and "2.5%" in note


# ── where marks stand: inline, or the end box (matrix row 7) ─────────────────────

def _boxed(answer: str, writer: str) -> str:
    bundle = chat_stages.bundle_for(
        answer=answer, request="문의", model={"id": writer}, actual_model=None, api_key="k",
        session_id="s", today=None, first_message="문의", messages=[], material="",
        check_keys=(), check_material="", tool_outputs=[], judge="", kept_steps=[],
        usage={})
    stage = chat_stages.CopyMarks()
    if stage.skip(bundle):
        return bundle.document.answer
    asyncio.run(stage.apply(bundle, Context()))
    return bundle.document.answer


@pytest.mark.parametrize("writer", [L27, LFL, SMALL_EXT, STRONG_EXT])
def test_a_small_external_writer_s_marks_go_to_one_box_at_the_end(writer):
    answer = "방학 월평균 26,300명, 학기 월평균 99,999명(확인 필요)입니다."
    out = _boxed(answer, writer)
    if model_tier.group(writer) == model_tier.SMALL_EXTERNAL:
        assert out.startswith("방학 월평균 26,300명, 학기 월평균 99,999명입니다.")
        # the thousands comma is no clause break: the whole value is named
        assert out.endswith("확인 필요: 「학기 월평균 99,999명」 — 주신 자료나 검색 결과로 "
                            "확인하지 못한 내용이니 쓰기 전에 확인하세요.")
    else:
        assert out == answer


# ── check:quality follow-through (matrix row 10) ──────────────────────────────────

#: r8 A10-T2 LFLF: a figure the check found wrong, shipped unmarked
A10_SENTENCE = ("전체 초·중·고 학생 500만 명 중 유료 이용 학생의 비중은 확인되지 않았으므로, 가정: "
                "유료 이용 학생 비율 50%를 설정했습니다 [7].")


@GROUPS
def test_a_figure_code_still_finds_wrong_is_fixed_once_then_marked(writer, monkeypatch):
    from app.pipeline import report_stages
    from app.services import verify

    finding = verify.Finding(0, A10_SENTENCE, "judged_figure", "학생 수는 503만 5천 명",
                             rank=1, auto=False, still=lambda s: "500만" in s)

    async def found(doc, checks, **kwargs):
        return doc.sections, [finding], {}

    async def repair(doc, findings, **kwargs):
        repair.model = kwargs["model"]
        assert all(f.auto for f in findings)
        return doc.sections, findings, {}  # the repair could not fix it

    monkeypatch.setattr(report_stages.verify, "verify_and_repair", found)
    monkeypatch.setattr(report_stages.verify, "repair", repair)
    bundle = Bundle(request=Request(text="에듀테크 시장 규모 보고서", model=writer, api_key="k",
                                    outline_model="flash"))
    bundle.document.sections = [{"heading": "상향식", "content": f"{A10_SENTENCE} 다음 문장."}]
    asyncio.run(report_stages.Verify().apply(bundle, Context()))
    # fixed once by the writer when strong, by the planner for a self-hosted writer
    assert repair.model == (STRONG_EXT if model_tier.strong(writer) else "flash")
    assert "설정했습니다 [7] (확인 필요)." in bundle.document.sections[0]["content"]


def test_a_judge_s_reasoning_never_reaches_the_step():
    from app.services import report

    why = ("자료의 최종 담당은 「미정」일 수 있다(판정: 각자 본인에게 해당하므로 담당자가 "
           "특정되지 않음). 확인 필요.")
    assert report.reader_note("owner_change", why) \
        == "자료의 최종 담당은 「미정」일 수 있다. 확인 필요."
    # r8 L27L A04-T2: 「표 3」 read as a value, the judge's reasoning shown
    bound = ("「60」은 자료에서 「화상회의 동시 참여 인원 (조건)」의 값일 수 있다(문맥상 "
             "'최저 연비 비용'을 선택하는 기준을 설명하는 문장 안에 포함되어 있어). 확인 필요.")
    assert report.reader_note("number_binding", bound) \
        == "「60」은 자료에서 「화상회의 동시 참여 인원 (조건)」의 값일 수 있다. 확인 필요."
    # a web dispute keeps what the page says
    assert report.reader_note("fact", "출처는 2027년 7월 시행") == "출처는 2027년 7월 시행"
