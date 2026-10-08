"""Before the first draft, the facts the format needs and the material lacks are asked, once.

The minutes memo of minutes-3 gives no meeting day; a proposal memo with prices but no VAT
basis cannot be quoted on the budget's basis. The card asks those and only those; 「있는 자료로
진행」 (or the evaluation's typed 「더 있는 정보는 없어. 자료대로 진행해 줘.」) goes on with each
missing fact as its bracket blank; a complete material puts no card up.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import check_skills, report_checks
from app.services.workspace_context import WorkspaceContext

_MINUTES = """축제 준비 회의 때 휴대폰에 막 적은 거야. 회의록으로 만들어 줘.

축제 부스 회의 (참석: 회장, 지민, 도윤, 수아)
- 부스 위치 A동 앞으로 (회장)
- 포스터 → 지민, 다음 주 금요일까지
- 재료 업체 견적 → 도윤, 모레까지"""

_PROPOSAL = """그린텍스타일 인사팀 미팅 메모로 교육 제안서 써 줘.

[미팅 메모 — 그린텍스타일(주) 인사팀, 2026-10-05(월)]
- 대상: 사무직 120명, 30명씩 4개 반, 반당 2일(하루 8시간)
- 우리 단가: 주강사 시간당 200,000원, 교재 1인 20,000원"""

_FIXTURES = Path(__file__).parent / "fixtures" / "grounding"


class _Reached(Exception):
    """The turn got past the questions to the writer's route."""


def _card(request: str, *ids: str) -> dict:
    found = check_skills.gaps(("gap-check",), facts="minutes", request=request, material="")
    found = [f for f in found if f["fact"] in ids]
    return {
        "stage": "clarify", "request": request, "attachments": [],
        "questions": [q.wire() for q in check_skills.questions(found)], "answers": {},
    }


async def _send(monkeypatch, *, content, doc_format="minutes", template="doc-minutes", pending=None,
                answers=None, history=()):
    user = User(email="gap@example.test", name="Learner", password_hash="hash")
    model = {**_external_model("synthetic/writer"), "kinds": ["chat", "report"]}
    session = ChatSession(user_id=user.id, kind=SessionKind.report, model=model["id"],
                          pending=pending, render_template_id=template)
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    workspace = WorkspaceContext(blocks=(), applied_skills=(), doc_format=doc_format)
    seen: dict = {}

    async def assemble(*_args, **_kwargs):
        return workspace

    async def owned(*_args, **_kwargs):
        return [], None

    async def stored_history(*_args, **_kwargs):
        return list(history)

    async def route(**kwargs):
        seen["request"] = kwargs["request"]
        raise _Reached

    class Db(_NoWriteDb):
        async def get(self, *_args):
            return None

    monkeypatch.setattr(sessions, "assemble", assemble)
    monkeypatch.setattr(sessions, "_owned_attachments", owned)
    monkeypatch.setattr(sessions, "_history", stored_history)
    monkeypatch.setattr(sessions, "_document_route", route)
    db = Db()
    try:
        response = await sessions.send_message(
            session.id, SendMessage(content=content, answers=answers), _request(), user, db)
    except _Reached:
        return None, seen, session, db
    return response, seen, session, db


async def test_minutes_without_a_meeting_day_ask_for_it(monkeypatch):
    response, _seen, session, db = await _send(monkeypatch, content=_MINUTES)

    assert response is not None and session.pending["stage"] == "clarify"
    asked = {q["id"]: q for q in session.pending["questions"]}
    assert list(asked) == ["gap:meeting_date"], "참석자·기한은 자료에 있어 묻지 않습니다"
    assert "회의 날짜" in asked["gap:meeting_date"]["detail"]
    said = [m for m in db.added if isinstance(m, Message) and m.role is Role.assistant]
    assert said[0].routing["askedCard"]["ids"] == ["gap:meeting_date"]


async def test_going_on_leaves_the_meeting_day_a_blank(monkeypatch):
    response, seen, _session, _db = await _send(
        monkeypatch, content="있는 자료로 진행해 주세요",
        pending=_card(_MINUTES, "meeting_date"), answers={})

    assert response is None
    assert "회의 일시 → [회의 일자]" in seen["request"]


async def test_the_typed_go_on_reply_proceeds_without_answering_the_fact(monkeypatch):
    response, seen, _session, _db = await _send(
        monkeypatch, content="더 있는 정보는 없어. 자료대로 진행해 줘.",
        pending=_card(_MINUTES, "meeting_date"))

    assert response is None, "같은 질문을 다시 물었습니다"
    request = seen["request"]
    assert request.count("자료대로 진행해 줘") == 1
    assert "회의 일시: 더 있는" not in request
    assert "[회의 일자]" in request


async def test_an_answer_becomes_the_meeting_day(monkeypatch):
    response, seen, _session, _db = await _send(
        monkeypatch, content="10월 7일 오후 2시", pending=_card(_MINUTES, "meeting_date"),
        answers={"gap:meeting_date": "10월 7일 오후 2시"})

    assert response is None
    request = seen["request"]
    assert "회의 일시: 10월 7일 오후 2시" in request
    assert "[회의 일자]" not in request
    day, _ = report_checks.meeting_base(request, "", dt.date(2026, 10, 8))
    assert day == dt.date(2026, 10, 7)


async def test_the_card_is_not_put_up_twice_in_a_row(monkeypatch):
    asked = Message(session_id="s", role=Role.assistant,
                    content="시작하기 전에 확인할 것이 있습니다.",
                    routing={"askedCard": {"ids": ["gap:meeting_date"], "attachments": []}})
    response, seen, _session, _db = await _send(monkeypatch, content=_MINUTES, history=[asked])

    assert response is None
    assert "[회의 일자]" in seen["request"]


async def test_a_proposal_memo_without_a_vat_basis_asks_for_it(monkeypatch):
    response, _seen, session, _db = await _send(
        monkeypatch, content=_PROPOSAL, doc_format="client_proposal",
        template="doc-client-proposal")

    assert response is not None
    ids = [q["id"] for q in session.pending["questions"]]
    assert "gap:vat" in ids
    assert "gap:client" not in ids and "gap:staffing" not in ids


async def test_a_complete_material_puts_no_card_up(monkeypatch):
    run = json.loads((_FIXTURES / "client-proposal-1__r1.json").read_text(encoding="utf-8"))
    response, seen, _session, _db = await _send(
        monkeypatch, content=run["request"], doc_format="client_proposal",
        template="doc-client-proposal")

    assert response is None
    assert "[부가세 기준]" not in seen["request"]


def test_the_chat_agents_ask_their_own_facts():
    letter = check_skills.gaps(("gap-check",), facts="career-docs", request="자소서 좀 써 줘.",
                               material="[메모] 축제 자원봉사단 조장(12명), 민원 23건 → 9건")
    assert [f["fact"] for f in letter] == ["question", "length_rule"]
    analysis = check_skills.gaps(("gap-check",), facts="data-analysis",
                                 request="월별 이용 통계야. 학기 중과 방학을 비교해 줘.",
                                 material="월,방문자수\n2025-03,61200")
    assert [f["fact"] for f in analysis] == ["period_split"]
    told = check_skills.gaps(("gap-check",), facts="data-analysis",
                             request="학기 중(3~6월, 9~12월)과 방학을 비교해 줘.", material="")
    assert told == []


def test_the_shipped_agents_and_forms_attach_the_checks():
    assert check_skills.attached(agent_key="minutes") == ("gap-check", "grounding-check")
    assert check_skills.attached(agent_key="data-analysis") == ("gap-check", "grounding-check")
    assert "grounding-check" in check_skills.attached(template_checks=("grounding-check",))
    # A key that is not a check skill is not run as one.
    assert check_skills.attached(template_checks=("date-calc",)) == ()
