"""A minutes agent's 「필요할 때」 minutes skill reaches its report, and the minutes keep no
open flag, advice, consequence, next-meeting agenda (in a sentence or a table) or 오전 the memo
never gives. Shapes from the round 15 re-verify (tc, tc2, fpm)."""

from __future__ import annotations

import time

import pytest
from test_agent_skills_reach_every_surface import _member, _shipped, db  # noqa: F401
from test_minutes_say_only_what_the_memo_says import SALES, TUTOR

from app.models.chat import ChatSession, SessionKind
from app.models.workspace import Agent, Skill
from app.services import report_checks, skill_turn, stated_meeting, workspace_context

TUTOR_TABLE = ("**표 1. 2학기 튜터링 모집**\n\n| 항목 | 내용 |\n| --- | --- |\n"
               "| 모집 기간 | 11/3~11/14 |\n| 담당자 | 이수연 |")
FLAGGED = ("모집 홍보에 사용할 포스터 시안은 이수연이 준비하기로 했습니다. 포스터 최종본은 모집 "
           "시작 전 배포될 예정이며, 구체적인 배포 채널과 방법은 확인 필요합니다(표 1).\n\n"
           f"{TUTOR_TABLE}\n\n확인 필요: 포스터 배포 방법(그림 2).")
TUTOR_NEXT = ("다음 주간회의는 10월 21일(화) 오전 10시에 3층 회의실에서 진행됩니다. "
              "주요 안건은 이전 회의에서 미결로 남긴 설문 도구 최종 선정입니다. "
              "추가로 논의할 안건이 있으면 10월 20일(화)까지 김지훈 팀장에게 알려야 합니다.")
ADVICE = ("수집된 비교 자료는 두 도구의 주요 기능, 사용 편의성 등을 항목별로 대조하여 "
          "작성하는 것이 좋습니다.")
ASK_LATER = ("이 부분에 대한 확인이 필요하다면 다음 회의에서 관련 담당자에게 직접 문의하시는 것이 "
             "적절합니다.")
IF_NOT = ("미제출 시 팀장 승인 절차가 지연되어 다음 정산 주기에 반영되므로, 10월 28일(수) 회의 "
          "전에는 원본 서류를 영업1팀장실에 제출합니다.")
LATER_AGENDA = ("리스트가 완성되면 팀장 오세훈에게 공유 후 다음 회의인 10월 28일(수)에 우선 협상 "
                "대상을 확정합니다.")
CAPTION = "**표 2. 다음 회의 의제별 검토 사항**"
HEADER = "| 안건 | 회의 시 검토 사항 | 기준 |"
SALES_AGENDA = (f"다음 회의 의제는 다음과 같습니다(표 2).\n\n{CAPTION}\n\n"
                f"{HEADER}\n| --- | --- | --- |\n"
                "| 4분기 신규 거래처 목표 | 후보 리스트 10곳 우선 협상 대상 확정 "
                "| 리스트 공유 여부 |\n"
                "| 견적 양식 개편 | 새 양식 초안 피드백 및 승인 결정 | 11월 1주차 적용 여부 |")


@pytest.mark.asyncio
async def test_a_report_only_skill_kept_for_when_needed_runs_on_the_report(db):  # noqa: F811
    user = await _member(db)
    minutes, dates = await _shipped(db, user, "minutes-writing", "date-calc")
    loose_chat = Skill(owner_id=user.id, name="챗 겸용", body="x", kinds=["chat", "report"])
    db.add(loose_chat)
    # 「직접 고르기」 + 「설치하고 추가」: the skill sits in the list, not kept on
    agent = Agent(owner_id=user.id, name="회의록 U", kinds=["chat", "report"], surface="report",
                  skill_ids=[minutes.id, loose_chat.id, dates.id], active_skill_ids=[])
    session = ChatSession(user_id=user.id, kind=SessionKind.report, agent_id=agent.id)
    db.add_all([agent, session])
    await db.commit()

    rows = await workspace_context.agent_skill_rows(db, user.id, agent, "report")
    # a document writer has no tool to pick a skill: one made for reports alone applies
    assert [r.id for r in rows] == [minutes.id]
    assert await workspace_context.agent_skill_rows(db, user.id, agent, "chat") == []
    found = await skill_turn.brought(db, user, session, agent, [])
    assert found.template == "doc-minutes"


def test_an_open_flag_the_memo_never_raises_goes_and_a_deadline_flag_stays():
    sections = [{"heading": "2학기 튜터링 모집", "content": FLAGGED}]
    found = [p for _, p, _ in stated_meeting.findings(sections, TUTOR)]
    assert found == ["포스터 최종본은 모집 시작 전 배포될 예정이며, 구체적인 배포 채널과 방법은 "
                     "확인 필요합니다(표 1).", "확인 필요: 포스터 배포 방법(그림 2)."]
    weak = report_checks.settle_unsourced(sections, TUTOR, strong=False)
    assert "배포" not in weak[0]["content"]
    # the dropped sentence's table pointer stays on the sentence before
    assert "이수연이 준비하기로 했습니다(표 1)." in weak[0]["content"]
    assert "| 모집 기간 |" in weak[0]["content"]
    # the skill's 「※」 line on a vague deadline is no invented question
    note = "※ 3번 과제의 기한은 회의에서 「이번 주 안」으로만 언급되어 확인이 필요합니다."
    assert not stated_meeting.untraced(note, SALES, rows=[])


def test_the_memos_bare_hour_loses_the_morning_the_writer_put_before_it():
    sections = [{"heading": "다음 회의 안내", "content": TUTOR_NEXT}]
    assert stated_meeting.untraced(TUTOR_NEXT.split(". ")[0] + ".", TUTOR, next_part=True) == [
        "오전 10시"]
    for strong in (True, False):
        out = report_checks.settle_unsourced(sections, TUTOR, strong=strong)[0]["content"]
        assert "10월 21일(화) 10시에 3층 회의실" in out and "오전" not in out
        assert "설문 도구 최종 선정입니다." in out
    # a notice the memo never asks for goes from a self-hosted writer's part, its names elsewhere
    sections = [{"heading": "회의 개요", "content": "김지훈 팀장이 주재했습니다."}, *sections]
    weak = report_checks.settle_unsourced(sections, TUTOR, strong=False)[1]["content"]
    assert "김지훈" not in weak
    # the memo's own 오후 2시 stands
    assert not stated_meeting.untraced("다음 회의는 10월 28일(수) 오후 2시에 열립니다.", SALES,
                                       next_part=True)


def test_advice_and_what_follows_an_undone_task_are_the_writers_own():
    for advice in (ADVICE, ASK_LATER):
        assert stated_meeting.advised(advice, TUTOR)
    assert stated_meeting.if_not(IF_NOT, SALES)
    for plain in ("서은우가 영수증 3건을 이번 주 안에 제출합니다.",
                  "초안은 다음 미팅 시 공유합니다."):
        assert not stated_meeting.if_not(plain, SALES)
    sections = [{"heading": "다음 회의", "content": f"최다은이 비교 자료를 가져옵니다. {ADVICE}"},
                {"heading": "출장비 정산",
                 "content": f"서은우가 영수증 3건을 제출합니다. {IF_NOT}"},
                {"heading": "다음 회의", "content": "다음 회의는 10월 28일(수)입니다."}]
    weak = report_checks.settle_unsourced(sections, TUTOR + " " + SALES, strong=False)
    assert weak[0]["content"] == "최다은이 비교 자료를 가져옵니다."
    assert weak[1]["content"] == "서은우가 영수증 3건을 제출합니다."


def test_a_next_meeting_agenda_the_memo_never_sets_is_found_in_a_table_and_anywhere_named():
    assert stated_meeting.untraced(LATER_AGENDA, SALES)
    # a deferral naming nothing sets no agenda
    assert not stated_meeting.untraced("기한은 미정이며, 다음 회의에서 재논의가 진행됩니다.", SALES)
    sections = [{"heading": "다음 회의 일정", "content": SALES_AGENDA}]
    found = [p for _, p, _ in stated_meeting.findings(sections, SALES)]
    # the rows, not the header, the caption or the pointer sentence
    assert found == [ln for ln in SALES_AGENDA.split("\n") if ln.startswith("| 4분기")
                     or ln.startswith("| 견적")]
    out = report_checks.settle_unsourced(sections, SALES, strong=False)[0]["content"]
    assert "| 4분기 신규 거래처 목표 | 후보 리스트 10곳 우선 협상 대상 확정 | 리스트 공유 여부 " \
           "(확인 필요) |" in out
    assert CAPTION in out and HEADER in out
    # an agenda the memo gives stands
    assert not stated_meeting.untraced("예정 안건은 최다은의 비교표 검토입니다.", TUTOR,
                                       next_part=True)


def test_the_new_minutes_checks_finish_on_a_long_run_of_spaces():
    spaces = " " * 50_000
    started = time.perf_counter()
    for text in (f"오전{spaces}10시", f"것이{spaces}좋습니다", f"미제출{spaces}시",
                 f"확인{spaces}필요", f"필요가{spaces}있", f"지{spaces}않으면", f"다음과{spaces}같",
                 f"**표{spaces}2."):
        stated_meeting.untraced(text, SALES, next_part=True, rows=[])
        stated_meeting.settle(text, text, [text], strong=False, material=SALES)
        stated_meeting.findings([{"heading": "다음 회의", "content": text}], SALES)
    assert time.perf_counter() - started < 0.5
