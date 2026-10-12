"""An ordinary chat turn on the person's material traces its answer to it, agent or not."""

from __future__ import annotations

import pytest

from app.pipeline import chat_stages
from app.pipeline.check_stages import AnswerGrounding
from app.pipeline.stage import Context
from app.routers import sessions
from app.services.workspace_context import ContextBlock, WorkspaceContext

MATERIAL = (
    "신입생 오리엔테이션 계획\n- 일시: 2026. 3. 2.(월) 10:00\n- 장소: 대강당\n"
    "- 대상: 신입생 1,200명\n- 준비물: 학생증, 필기구\n- 예산: 1,850만 원"
)


def _bundle(answer: str, *, traced: bool, material: str = MATERIAL, check_keys=()):
    return chat_stages.bundle_for(
        answer=answer, request="신입생에게 보낼 안내 문자 3줄로", model={"id": "m"},
        actual_model=None, api_key="k", session_id="s", today=None, first_message="",
        messages=[], material="신입생에게 보낼 안내 문자 3줄로", check_keys=check_keys,
        check_material=material, tool_outputs=[], judge="", kept_steps=[], usage={},
        traced=traced,
    )


async def _run(bundle) -> list[dict]:
    stage = AnswerGrounding()
    if stage.skip(bundle):
        return []
    return [event async for event in stage.run(bundle, Context())]


async def test_an_item_the_material_never_asks_for_is_dropped(monkeypatch):
    from app.services import report

    async def no_model(*_a, **_k):
        raise AssertionError("the trace is code only")

    monkeypatch.setattr(report, "_complete", no_model)
    bundle = _bundle("3월 2일 10시 대강당에서 OT가 열립니다. 신분증 지참 후 참석해 주세요.\n"
                     "학생증과 필기구를 챙겨 주세요.", traced=True)
    events = await _run(bundle)
    assert "신분증" not in bundle.document.answer
    assert "학생증과 필기구를 챙겨 주세요." in bundle.document.answer
    assert "대강당" in bundle.document.answer
    assert events and events[-1]["id"] == "grounding"


async def test_numbers_and_dates_trace_to_the_material_and_its_arithmetic():
    bundle = _bundle("신입생 1,200명, 예산 1,850만 원(1인당 약 15,417원)입니다. "
                     "참가비 3만 원을 3월 1일까지 내 주세요.", traced=True)
    await _run(bundle)
    answer = bundle.document.answer
    assert "1,200명" in answer and "1,850만 원" in answer and "15,417원" in answer
    # a sentence carried only by invented values goes whole
    assert "3만 원" not in answer and "3월 1일" not in answer


async def test_a_turn_without_material_is_left_alone():
    bundle = _bundle("신분증 지참 후 참석해 주세요. 참가비는 3만 원입니다.", traced=False,
                     material="")
    assert AnswerGrounding().skip(bundle)
    assert await _run(bundle) == []


async def test_an_agent_s_own_checks_still_run_with_the_trace():
    bundle = _bundle("신분증 지참 후 참석해 주세요.", traced=True,
                     check_keys=("grounding-check",))
    from app.pipeline.check_stages import answer_checks

    assert answer_checks(bundle.request) == ("grounding-check",)
    await _run(bundle)
    assert "신분증" not in bundle.document.answer


def _workspace(*blocks: tuple[str, str, bool]) -> WorkspaceContext:
    return WorkspaceContext(blocks=tuple(ContextBlock(*b) for b in blocks), applied_skills=())


def test_files_and_project_knowledge_are_material_but_memories_are_not():
    assert sessions._carries_material(_workspace(("attachment", "# 첨부\n내용", False)))
    assert sessions._carries_material(_workspace(("attachment.earlier", "앞서 올린 파일", False)))
    assert sessions._carries_material(_workspace(("project.knowledge", "프로젝트 파일", False)))
    assert not sessions._carries_material(_workspace(("memory", "선호: 존댓말", False),
                                                     ("agent.instructions", "규칙", True)))


def test_pasted_material_counts_and_a_plain_question_does_not():
    pasted = "아래 내용으로 안내문 써 줘.\n\n" + MATERIAL * 3
    assert sessions._pasted_in([{"role": "user", "content": pasted},
                                {"role": "assistant", "content": "네"},
                                {"role": "user", "content": "3줄로 줄여 줘"}])
    assert not sessions._pasted_in([{"role": "user", "content": "OT 안내 문자 예시 하나 써 줘"}])
    assert not sessions._pasted_in([{"role": "assistant", "content": pasted}])


@pytest.mark.parametrize("answer", [
    "학생증을 꼭 지참해 주세요.", "서류를 제출해 주세요.", "10월 5일까지 제출해 주세요.",
    "신청한 뒤 제출해 주세요.",
])
def test_requirements_the_material_or_any_notice_names_stay(answer: str):
    from app.services import skill_scripts

    trace = skill_scripts.module("grounding-check", "trace")
    found = trace.check(answer, MATERIAL + "\n마감 10월 5일", [], {"requirements": True})
    assert not [f for f in found if f["kind"] == "requirement"]


# S03 (quality scenario 03_official_letter): the default 공문 run, no agent and no 서식.
S03 = """각 학과에 보낼 협조 요청 공문을 써 줘.

- 제목 취지: 2027학년도 실험실습 기자재 수요조사 협조 요청
- 발신: 한빛대학교 기획처장 / 수신: 각 학과(부)장
- 대상: 1건당 300만원 이상 실험실습 기자재 (소모품·소프트웨어 라이선스 제외)
- 제출 기한: 2026. 11. 6.(금) 18:00까지
- 제출 방법: 붙임 서식 작성 후 기획예산팀 메일(budget@hanbit.ac.kr)로 제출, 학과장 확인 필수
- 우선순위(1~3순위)와 활용 계획(수혜 교과목, 연간 활용 학생 수) 반드시 기재
- 미제출 학과는 2027학년도 기자재 예산 배정에서 제외될 수 있음
- 담당: 기획예산팀 윤서영 (내선 2214)
- 붙임: 실험실습 기자재 수요조사서 1부"""


async def test_a_default_official_letter_gets_its_deadline_back():
    from app.pipeline import report_stages
    from app.pipeline.check_stages import GroundCheck

    keys = sessions._traced((), True)
    assert keys == ("grounding-check",) and sessions._traced((), False) == ()
    bundle = report_stages.bundle_for(request=S03, model="m", api_key="k", check_keys=keys)
    assert bundle.spec.format_id == "official"
    bundle.material.given = report_stages._given(bundle)
    bundle.findings.check_keys = keys
    bundle.document.sections = [{"id": "s1", "heading": "본문", "level": 1, "content": (
        "1. 관련: 기획처-1234\n"
        "2. 2027학년도 실험실습 기자재 수요조사를 실시하오니 협조하여 주시기 바랍니다.\n"
        "  가. 대상: 1건당 300만원 이상 실험실습 기자재\n"
        "  나. 제출 기한: 2026. 11. 18. 00(:00)까지\n"
        "  다. 제출처: 기획예산팀(budget@hanbit.ac.kr)")}]
    stage = GroundCheck("check:grounding", again=False)
    assert not stage.skip(bundle)
    await stage.apply(bundle, Context())
    content = bundle.document.sections[0]["content"]
    assert "제출 기한: 2026. 11. 6.(금) 18:00까지" in content
    assert "11. 18" not in content and "300만원" in content


def test_a_report_without_format_is_traced_once_the_material_is_the_persons():
    from app.pipeline import report_stages
    from app.pipeline.check_stages import GroundCheck

    ask = "아래 내용을 문서로 정리해 줘.\n\n" + MATERIAL.replace("계획", "안내") * 2
    bundle = report_stages.bundle_for(request=ask, model="m", api_key="k")
    assert bundle.spec.format_id == ""
    bundle.material.given = report_stages._given(bundle)
    stage = GroundCheck("check:grounding", again=False)
    assert stage.skip(bundle) == "no document format"
    bundle.findings.check_keys = ("grounding-check",)
    assert not stage.skip(bundle)


async def test_slides_on_the_persons_material_are_traced():
    from app.pipeline.bundle import Bundle, Document, Request
    from app.pipeline.check_stages import DeckGrounding

    slides = [
        {"layout": "title", "title": "신입생 오리엔테이션", "subtitle": "2026. 3. 2.(월) 대강당"},
        {"layout": "bullets", "title": "준비물",
         "bullets": ["학생증과 필기구", "신분증을 지참하세요.", "참가비 3만 원"]},
        {"layout": "table", "title": "예산", "rows": [["항목", "금액"], ["전체", "1,850만 원"],
                                                    ["간식", "420만 원"]]},
    ]
    request = Request(text="아래 계획으로 OT 안내 슬라이드 만들어 줘", model="m", api_key="k",
                      turn={"traced": True, "writer": {"untrusted_context": [MATERIAL]}})
    bundle = Bundle(request=request, document=Document(sections=slides))
    stage = DeckGrounding()
    assert not stage.skip(bundle)
    events = [event async for event in stage.run(bundle, Context())]
    out = bundle.document.sections
    assert out[0] == slides[0]
    assert out[1]["bullets"] == ["학생증과 필기구", "참가비 [수치]"]
    assert out[2]["rows"][1] == ["전체", "1,850만 원"] and out[2]["rows"][2][1] == "[수치]"
    assert any(e["type"] == "step" for e in events)
    request.turn["traced"] = False
    assert DeckGrounding().skip(bundle)
