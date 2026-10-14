"""Round 8: a skill's frame gives way to the form the request names, a turn on the person's
own material searches nothing on 「auto」, and the answer plan keeps to the person's facts.

The same for every model group (docs/architecture.md, "Mechanisms by model group"): each test
runs for a self-hosted writer and a strong external one, with the round's own requests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.models.chat import SessionKind
from app.routers import sessions
from app.services import answer_plan, asked_form, context, report, skill_packages
from app.services.workspace_context import WorkspaceContext, _skill_blocks

L27, STRONG_EXT = "local/qwen3.8-27b", "deepseek/deepseek-v4-pro-0813"
GROUPS = pytest.mark.parametrize("writer", [L27, STRONG_EXT])

# ── item 3: skill frames yield to the request's own form ─────────────────────

A08_T2 = ("이 영상 보고 금리 개념 학습 노트 정리해 줘. 신입 직원 경제 기초 스터디에서 같이 볼 "
          "거야.\n"
          "https://www.youtube.com/watch?v=EAs8qDciTak\n"
          "3줄 요약, 영상에서 설명한 핵심 개념, 스터디 토론 질문 2개로.")
A02_T2 = ("아래 발표문을 부서장 보고용으로 정리해 줘. 핵심은 5줄 이내로 요약하고, 우리 "
          "대학(비수도권 "
          "사립대, 재학생 약 8,000명, 올해 RISE 사업 참여 중)에 미치는 영향 3가지와 우리가 지금 "
          "준비할 "
          "일을 정리해 줘.\n\n---\n[교육부 보도 참고자료(가상)] 2027년 대학 재정지원사업 개편 "
          "방향\n\n"
          "교육부는 2027년부터 대학 재정지원사업을 '성과 중심·자율 혁신'으로 개편한다. 재정 "
          "건전성의 4개 지표를 새로 두고, 성과 지표는 3개로 줄인다.")
A13_T2 = ("11월 한 달 회사 SNS(인스타그램, 링크드인) 게시물 계획 짜 줘. 주 2회씩.\n"
          "자료(한빛소프트, 가상 — 아래가 전부임):\n"
          "- 11월 주요 일정: 11/1 무료 체험 45일 캠페인 시작, 11/12~13 「공공 디지털 혁신 박람회」 "
          "부스 참가(코엑스, B-21), 11/20 신기능 「AI 회의록 요약」 베타 출시\n"
          "- 회사 SNS 팔로워: 인스타그램 1,800명, 링크드인 4,200명\n"
          "날짜·채널·형식·주제·본문 초안·해시태그를 표로, 콘텐츠 전략 요점도 같이.")
A02_T1 = ("아래 메모로 이번 주 주간 업무 보고서 써 줘. 팀장님께 올릴 거야.\n\n"
          "- 기간: 10/5~10/9\n- LMS 개편: 업체 1차 시안 일정 확인(10/15 도착 예정)\n"
          "- 이슈: 디자인 업체 견적 A사 380만원 / B사 420만원(납기 1주 빠름) — 결정 필요")


def test_the_form_a_request_names():
    assert asked_form.asked(A08_T2) == ["3줄 요약", "질문 2개"]
    assert asked_form.asked(A13_T2) == ["날짜·채널·형식·주제·본문 초안·해시태그를 표로"]
    # the press release's own counts are material, not the ask's form
    assert asked_form.asked(A02_T2) == ["핵심은 5줄", "영향 3가지"]
    # a weekly report from a memo leaves the form to the skill
    assert asked_form.asked(A02_T1) == [] and asked_form.note(A02_T1) == ""
    assert asked_form.asked("대웅제약 총무·IT 담당자에게 보낼 첫 영업 메일 써 줘. 제목 3안이랑 "
                            "본문 1안.") == ["제목 3안", "본문 1안"]


def _gaejo_block(request: str) -> str:
    package = skill_packages.package_for("gaejo-briefing")
    skill = SimpleNamespace(id="s1", name=package.name, when_to_use=package.when_to_use,
                            body=package.body, description=package.description)
    return _skill_blocks([(skill, {})], SessionKind.chat, form=asked_form.note(request))[0].text


@GROUPS
def test_an_always_on_skill_yields_to_the_named_form(writer):
    from app.services import model_tier

    block = _gaejo_block(A08_T2)
    assert "The request names its own form (「3줄 요약」 · 「질문 2개」)" in block
    assert "a study note reads as a note, not a report" in block
    # every writer gets it, the light setup too
    prompt = context.system_prompt(SessionKind.chat, extra=[block],
                                   light=model_tier.strong(writer))
    assert "This skill's frame gives way to it" in prompt
    # no named form: the skill's frame as it was
    assert "names its own form" not in _gaejo_block(A02_T1)


def test_the_skill_texts_name_where_their_frame_stops():
    gaejo = skill_packages.package_for("gaejo-briefing").body
    assert "A study or lecture note, a video summary" in gaejo
    assert "never compose one" in gaejo and "「정상 추진 중」" in gaejo
    headlines = skill_packages.package_for("headline-matrix").body
    assert "default 25" not in headlines and "3–5 per channel" in headlines
    copy = skill_packages.package_for("copywriting").body
    assert "write exactly those parts, in its order and counts" in copy
    strategy = skill_packages.package_for("content-strategy").body
    assert "no 우선순위 table, no scores" in strategy


def test_the_headline_grid_defaults_to_a_short_list():
    from app.services import skill_scripts

    grid = skill_scripts.module("headline-matrix", "matrix").scaffold({})
    assert grid["count"] == 12


def test_a_genre_rule_yields_to_the_named_form():
    asked = ("공지문 써 줘. 3줄 요약, 바뀌는 것, 문의처 순서로.\n- 대상: 전 직원\n- 시행: 11/1")
    rule = report._genre_rule(asked)
    assert rule.startswith("Genre: 안내·정책 문서") and rule.endswith(report.GENRE_YIELD)
    assert not report._genre_rule("공지문 써 줘.\n- 대상: 전 직원").endswith(report.GENRE_YIELD)


# ── item 6: no automatic search on the person's own material ─────────────────

A09_T1 = ("다음 주 학회(한국정보과학회 추계) 구두 발표용 슬라이드 만들어 줘. 발표 15분, 10장 안팎. "
          "아래 초록과 실험 결과로.\n\n제목: 소형 언어모델을 위한 검색 증강 생성에서의 문서 "
          "재순위화 "
          "경량화\n저자: 이서준, 김나래, 최영훈(한빛대학교 컴퓨터공학과)\n\n초록: 검색 증강 "
          "생성(RAG)은 "
          "소형 언어모델(sLM)의 사실성을 높이지만, 교차 인코더 기반 재순위화가 전체 지연 시간의 "
          "상당 "
          "부분을 차지한다. 본 연구는 교차 인코더를 지식 증류한 경량 재순위화기 LiteRerank(22M "
          "파라미터)를 제안한다.\n\n| 재순위화 방식 | KorQuAD-RAG EM | 사내 QA 정확도 |\n"
          "| 없음(BM25만) | 61.2 | 58.4 |\n| LiteRerank(제안) | 71.9 | 68.3 |")
A12_T2 = ("새 기능 「AI 회의록 요약」 출시 공지용 헤드라인 여러 안 뽑아 줘. 메일 "
          "제목·배너·SNS용으로 "
          "쓸 거야.\n기능 정보(한빛소프트 「한빛워크」, 가상):\n- 화상회의가 끝나면 5분 안에 "
          "요약·결정 "
          "사항·할 일 목록을 자동으로 만들어 회의 참석자에게 보냄\n- 베타 기간(11월 한 달) 기존 "
          "고객 "
          "무료\n각도(혜택·문제·호기심 등)를 달리해서 여러 개 만들고, 어디에 쓰면 좋을지도 붙여 "
          "줘.")
A15_T1 = ("대웅제약 총무·IT 담당자에게 보낼 첫 영업 메일 써 줘. 제목 3안이랑 본문 1안.\n"
          "우리: 한빛소프트(가상 회사) 영업팀 김현수 매니저.\n"
          "- 우리 사례(가상): 제조·제약 업종 고객 없음. 공공기관 한빛시청 1,200명 사용 중\n"
          "대웅제약에 대해선 공개 자료로 조사해서, 그 회사에 맞는 이유 하나를 잡아 줘.")
P02 = ("느린 기차 여행 플래너가 되어 줘.\n\n내 여행:\n- 출발: 암스테르담 / 도착: 로마\n"
       "- 일정: 9월 하순 10일\n\n해 줄 것:\n1. 경로 두 가지\n"
       "2. 구간마다 대략 소요 시간, 좌석 예약이 필수인지, 현실적인 가격대.")


def test_material_the_person_gives_needs_no_search():
    assert report.given_material_suffices(A09_T1)
    assert report.given_material_suffices(A12_T2)
    assert report.given_material_suffices(A13_T2)
    assert report.given_material_suffices(A02_T2)
    # an explicit 「조사해서」 still searches, made-up company or not
    assert not report.given_material_suffices(A15_T1)
    # a plan of the outside world (prices, seats) is not the person's material
    assert not report.given_material_suffices(P02)
    assert not report.given_material_suffices("최신 아이폰 가격 알려 줘")


def _turn(content: str, web_search: str = "auto", forced: str | None = None) -> bool:
    session = SimpleNamespace(kind=SessionKind.chat)
    payload = SimpleNamespace(web_search=web_search)
    workspace = WorkspaceContext(blocks=(), applied_skills=())
    return sessions._material_only_turn(session, payload, forced, content, workspace,
                                        published=False)


@GROUPS
def test_a_chat_turn_on_given_material_offers_no_search(writer):
    # the guard reads the ask and the material, never the writer: every group alike
    assert _turn(A12_T2) and _turn(A02_T2)
    # the toggle turned on, or a search the turn must make first, stays
    assert not _turn(A12_T2, web_search=True)
    assert not _turn(A12_T2, forced="web_search")
    assert not _turn(A15_T1)


def test_a_search_asks_the_question_not_the_role_play():
    p14 = ("가상 의사가 되어 줘. 내가 증상을 말하면 진단과 치료 계획을 알려 줘. 진단과 치료 계획만 "
           "답하고 다른 건 쓰지 마. 설명도 쓰지 마. 첫 요청: 며칠째 두통과 어지럼증이 있어.")
    assert context.search_subject(p14) == "며칠째 두통과 어지럼증이 있어."
    assert context.search_query(context.search_subject(p14)).startswith("며칠째 두통과 어지럼증")
    plain = "2026년 최저임금 얼마야?"
    assert context.search_subject(plain) == plain


def test_a_pasted_press_release_forces_no_current_fact_search():
    # the ask alone decides: 「2027년부터 … 개편한다」 in the pasted body is no current fact
    assert sessions._ask_of(A02_T2).startswith("아래 발표문을 부서장 보고용으로")
    assert "개편" not in sessions._ask_of(A02_T2)


# ── R4-P38: the answer plan keeps to the person's facts ──────────────────────

P38 = ("전문 이메일 작가가 되어 줘. 상황과 목적에 맞게 이메일을 쓰고, 알맞은 인사와 맺음말을 "
       "넣어 줘.\n"
       "- 어조: 격식 있게\n- 언어: 영어\n- 길이: 짧게\n\n상황: 싱가포르 거래처 담당자에게 다음 주 "
       "화상회의를 잡고 싶어. 3분기 납품 일정이 2주 늦어진 걸 사과하고 새 일정을 협의하려는 거야.")


@GROUPS
def test_a_short_mail_gets_no_plan_and_a_plan_invents_nothing(writer):
    # 「길이: 짧게」 fixes the length: no expert skeleton to grow the mail
    assert answer_plan.restricted(P38) and not answer_plan.wanted(P38)
    sent: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        sent.append(messages[-1]["content"])
        return "- 사과\n- 새 일정 협의\n- 화상회의 제안\n- 정확히: 날짜", {}

    unbounded = P38.replace("- 길이: 짧게\n", "")
    assert answer_plan.wanted(unbounded)
    out = asyncio.run(answer_plan.plan([{"role": "user", "content": unbounded}],
                                       complete=complete, model=writer, api_key="k"))
    assert out.startswith("- 사과")
    assert "never a cause, a remedy, a date, a time, a name or a promise" in sent[0]
    assert "지연 사유·대체 공급·우선 처리를 지어내지 않는다" in sent[0]
