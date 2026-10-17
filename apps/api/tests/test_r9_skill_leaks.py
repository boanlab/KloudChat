"""Skill examples kept out of answers, and weekdays kept right after every rewrite."""

from __future__ import annotations

import datetime as dt

from app.pipeline import chat_stages
from app.pipeline import registry as pipeline_registry
from app.pipeline.stage import Context
from app.services import guide_echo, internal_words, skill_specs

TODAY = dt.date(2026, 10, 14)


def _bundle(answer: str, request: str, **kw):
    return chat_stages.bundle_for(
        answer=answer, request=request, model={"id": "m"}, actual_model=None, api_key="k",
        session_id="s", today=TODAY, first_message=request,
        messages=[{"role": "user", "content": request}], material="",
        check_keys=(), check_material="", tool_outputs=[], judge="", kept_steps=[],
        usage={}, **kw)


async def _run(name: str, bundle) -> None:
    stage = pipeline_registry.stage(name)
    if not stage.skip(bundle):
        _ = [event async for event in stage.run(bundle, Context())]


# ── weekdays after every rewrite ─────────────────────────────

A13_REQUEST = (
    "11월 한 달 회사 SNS(인스타그램, 링크드인) 게시물 계획 짜 줘. 주 2회씩.\n"
    "- 11월 주요 일정: 11/1 무료 체험 45일 캠페인 시작, 11/12~13 「공공 디지털 혁신 박람회」 부스 "
    "참가(코엑스, B-21), 11/20 신기능 「AI 회의록 요약」 베타 출시, 11/27 고객 웨비나 "
    "「망분리 환경에서 협업툴 쓰기」 14시")
#: rows of the final answer, as the copy check's rewrite left them
A13_ROWS = (
    "| 날짜 | 채널 | 형식 | 주제 |\n|---|---|---|---|\n"
    "| 11/2(월) | 인스타 | 피드 이미지 | 무료 체험 45일 시작 |\n"
    "| 11/6(목) | 인스타 | 릴스(짧은 영상) | 기능 사용 팁: 회의 일정 관리 |\n"
    "| 11/19(화) | 링크드인 | 텍스트 글 | 웨비나 신청 안내 | 11월 27일(목) 14시 고객 웨비나 "
    "「망분리 환경에서 협업툴 쓰기」를 개최합니다. |\n"
    "| 11/20(수) | 인스타 | 피드 이미지 | AI 회의록 요약 베타 출시 |\n"
    "| 11/28(목) | 인스타 | 릴스(짧은 영상) | 웨비나 주요 하이라이트 |")


def test_the_weekday_pass_runs_after_every_stage_that_may_rewrite():
    finish = list(pipeline_registry.for_chat("").flow("finish"))
    at = finish.index("answer-weekdays")
    for earlier in ("answer-normalize", "answer-han", "check:answer-grounding", "check:copy",
                    "check:skills", "text-fit", "copy-marks", "answer-echo"):
        assert finish.index(earlier) < at
    assert finish[-2:] == ["answer-urgent", "answer-replace"]


async def test_weekdays_a_rewrite_brought_back_are_set_to_the_calendar():
    bundle = _bundle(A13_ROWS, A13_REQUEST)
    await _run("answer-weekdays", bundle)
    out = bundle.document.answer
    for right in ("11/2(월)", "11/6(금)", "11/19(목)", "11월 27일(금)", "11/20(금)", "11/28(토)"):
        assert right in out
    assert "(목) 14시" not in out


async def test_a_year_the_task_names_wins_over_the_current_year():
    bundle = _bundle("웨비나는 11월 27일(금) 14시입니다.", "2027년 11월 웨비나 안내문 써 줘.")
    await _run("answer-weekdays", bundle)
    assert "11월 27일(토)" in bundle.document.answer


async def test_an_answer_with_no_weekday_is_left_alone():
    bundle = _bundle("11월 27일 14시 웨비나", A13_REQUEST)
    assert pipeline_registry.stage("answer-weekdays").skip(bundle) == "no weekday"


# ── example names ────────────────────────────────────────────

#: a sales-email output format with an example signature
OLD_FORMAT = (
    "## Output format\n```\n### 제목 (택1)\n1. …\n### 본문\n…\n김현수 드림\n"
    "한빛소프트 영업팀 | [전화번호] | [이메일]\n```\n")
A15_REQUEST = (
    "한솔제지 정보시스템팀 박지훈 차장에게 보낼 후속 메일 써 줘. 9월 28일 온라인 미팅, "
    "10월 2일 데모 영상·일정 연동 답변 보냄, 이후 답 없음. 우리는 한빛소프트.")
A15_ANSWER = (
    "### 본문\n차장님, 안녕하세요. 한빛소프트 김현수입니다.\n\n"
    "지난 9월 28일 온라인 미팅에서 말씀 나눈 현장 직원용 모바일 기능 관련해 연락드립니다.\n\n"
    "김현수 드림  \n한빛소프트 영업팀 | [전화번호] | [이메일]")


def test_the_example_signature_name_becomes_a_blank():
    echoes = guide_echo.scrub(A15_ANSWER, OLD_FORMAT, [A15_REQUEST])
    assert "김현수" not in echoes.text
    assert "한빛소프트 [이름]입니다." in echoes.text and "[이름] 드림" in echoes.text
    assert echoes.names == ["김현수"]
    # the recipient the person named stays
    assert "차장님" in echoes.text


def test_a_name_the_person_gives_stays():
    request = A15_REQUEST + " 보내는 사람은 영업팀 김현수."
    assert guide_echo.scrub(A15_ANSWER, OLD_FORMAT, [request]).text == A15_ANSWER


def test_a_persons_own_skill_is_read_the_same_way():
    # a skill a person wrote (turn key `skill:<id>`): its body is the guidance, no catalogue key
    spec = skill_specs.Spec(key="skill:abc", name="내 후속 메일", body=OLD_FORMAT)
    guide = guide_echo.guidance(["skill:abc"], {"skill:abc": spec})
    assert "김현수" in guide
    assert "김현수" not in guide_echo.scrub(A15_ANSWER, guide, [A15_REQUEST]).text


async def test_the_chat_stage_blanks_the_name_and_says_so():
    spec = skill_specs.Spec(key="skill:abc", name="내 후속 메일", body=OLD_FORMAT)
    bundle = _bundle(A15_ANSWER, A15_REQUEST, skill_keys=("skill:abc",),
                     skill_specs={"skill:abc": spec})
    await _run("answer-echo", bundle)
    assert "김현수" not in bundle.document.answer
    step = next(row for row in bundle.document.kept_steps if row["id"] == "guide_echo")
    assert "김현수" in step["detail"]


def test_shipped_skills_carry_no_example_names():
    from app.services import design_templates, skill_packages

    for key in skill_packages.packages():
        assert not guide_echo._names(guide_echo.guidance([key])), key
    for template in design_templates.all_templates():
        assert not guide_echo._names(guide_echo.guidance([], None, template)), template.id


def test_an_example_date_the_material_does_not_give_becomes_a_blank():
    guide = "Dates 「2026. 10. 20.(화)」, times 「14:00」."
    out = guide_echo.scrub("회의: 2026. 10. 20.(화) 14:00", guide, ["회의 안건 써 줘"])
    assert out.text == "회의: [날짜] 14:00" and out.dates == ["2026. 10. 20.(화)"]
    kept = guide_echo.scrub("회의: 2026. 10. 20.(화) 14:00", guide, ["회의는 10월 20일"])
    assert kept.text == "회의: 2026. 10. 20.(화) 14:00"


# ── rule lines ───────────────────────────────────────────────────────────

#: a person's own skill, its rules in Korean
RULES = ("## 쓰는 법\n- 자료에 없는 수치나 사례는 지어내지 않고 빈칸으로 둡니다.\n"
         "- 「확인 필요」 줄은 끝에 한 번만 씁니다.\n")


def test_a_rule_line_copied_from_the_guidance_goes():
    answer = ("한빛메모는 2018년 출시된 메모 앱입니다. 자료에 없는 수치나 사례는 지어내지 않고 "
              "빈칸으로 둡니다.\n\n문의: support@hanbitsoft.example")
    out = guide_echo.scrub(answer, RULES, ["한빛메모 소개 써 줘. 2018년 출시."])
    assert "지어내지" not in out.text
    assert out.text.startswith("한빛메모는 2018년 출시된 메모 앱입니다.")
    assert "support@hanbitsoft.example" in out.text


def test_a_quoted_example_meant_to_be_copied_stays():
    guide = "- 담당이 없으면 「※ [안건 번호]번 안건 담당은 자료에 없어 확인이 필요합니다.」 한 줄."
    answer = "※ 2번 안건 담당은 자료에 없어 확인이 필요합니다."
    assert guide_echo.scrub(answer, guide, ["안건 써 줘"]).text == answer


def test_a_sentence_the_person_wrote_stays():
    answer = "자료에 없는 수치나 사례는 지어내지 않고 빈칸으로 둡니다."
    assert guide_echo.scrub(answer, RULES, [answer]).text == answer


def test_the_agenda_rule_for_an_empty_section_is_not_told_to_the_reader():
    # a 사전 자료 section that tells the rule to the reader
    body = ("자료에 사전 배포 목록이 없어 별도의 표를 만들지 않습니다.\n\n"
            "사전 자료 없음. 단, 안건 1(LMS 1차 시안)과 안건 2(견적 비교)는 회의 전 관련 자료가 "
            "참석자에게 공유돼야 합니다.")
    out = internal_words.strip_work_notes(body, request="회의 안건 써 줘", document=True)
    assert "만들지 않습니다" not in out and out.startswith("사전 자료 없음.")
