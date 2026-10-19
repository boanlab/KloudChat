"""What an agent's 지시 states is known before writing: not asked again, in the head, and the
section order it gives is the outline."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.pipeline import report_stages
from app.pipeline.stage import Context, Stop
from app.services import check_skills, doc_formats, gap_questions, report, report_checks
from app.services.workspace_context import ContextBlock, WorkspaceContext, standing_of

WEEKLY = "시설과장님께 올리는 주간보고. 금주 실적, 이슈, 차주 계획 순서. 1쪽."
MEMO = ("이번 주 메모: 3층 화장실 배관 교체 완료(10/14). 승강기 점검 업체 견적 2건 받음. "
        "다음 주 소방 점검 대응 예정.")


def _workspace(prompt: str) -> WorkspaceContext:
    return WorkspaceContext(
        blocks=(ContextBlock("agent.instructions", f"# 역할\n{prompt}", True),
                ContextBlock("attachment", MEMO, False)),
        applied_skills=())


def _addressee_asked(material: str) -> bool:
    questions = gap_questions.resolve(own=None, template_id="doc-gaejo-report")
    found = check_skills.gaps(("gap-check",), questions=questions,
                              request="아래 메모로 이번 주 주간보고를 써 줘.", material=material,
                              today=dt.date(2026, 10, 19))
    return any(gap.get("fact") == "addressee" for gap in found)


def test_what_the_agents_instructions_state_is_not_asked_again():
    workspace = _workspace(WEEKLY)
    assert workspace.standing == [f"# 역할\n{WEEKLY}"]
    # The memo alone does not say whom it reports to; the 지시 does.
    assert _addressee_asked("\n".join(workspace.untrusted))
    assert not _addressee_asked("\n".join([*workspace.untrusted, *workspace.standing]))


def test_the_writer_finds_the_agents_and_projects_instructions_among_its_texts():
    trusted = ["# 서식: 개조식", f"# 역할\n{WEEKLY}", "# 프로젝트 지침 — 시설팀\n결재선 과장"]
    assert standing_of(trusted) == trusted[1:]


@pytest.mark.parametrize(("said", "sections"), [
    (WEEKLY, ["금주 실적", "이슈", "차주 계획"]),
    ("목차는 개요 → 결과 → 다음 단계 순으로 쓴다.", ["개요", "결과", "다음 단계"]),
    ("현황·문제·대책 순서로 정리한다.", ["현황", "문제", "대책"]),
    # one item ordered, not a list of sections
    ("실행 항목은 담당자 순서로 정렬한다.", []),
    ("교육혁신팀 회의록을 쓴다.", []),
])
def test_a_section_order_in_the_instructions_is_read(said, sections):
    assert report.instructed_sections(said) == sections


async def _outline(monkeypatch, *, template_id: str, trusted: list[str], request: str,
                   planned: list[str]) -> list[str]:
    async def complete(_model, _messages, _key, _max):
        return json.dumps({"title": "보고", "style": "편집형", "subject": "주간",
                           "sections": planned, "alternatives": []}, ensure_ascii=False), {
            "inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(report, "_complete", complete)
    bundle = report_stages.bundle_for(request=request, model="m", api_key="k",
                                      trusted_context=trusted, untrusted_context=[MEMO],
                                      template_id=template_id, web_search=False)
    bundle.material.web_search = False
    with pytest.raises(Stop):
        async for _event in report_stages.Outline().run(bundle, Context(options={})):
            pass
    return bundle.plan.headings


async def test_the_order_an_agents_instructions_give_is_the_outline(monkeypatch):
    headings = await _outline(
        monkeypatch, template_id="doc-gaejo-report", trusted=[f"# 역할\n{WEEKLY}"],
        request=f"아래 메모로 이번 주 주간보고를 써 줘.\n\n{MEMO}", planned=["현황", "향후 조치"])
    assert headings == ["금주 실적", "이슈", "차주 계획"]
    # Without that 지시 the template's fixed shape stays.
    headings = await _outline(
        monkeypatch, template_id="doc-gaejo-report", trusted=[],
        request=f"아래 메모로 이번 주 주간보고를 써 줘.\n\n{MEMO}", planned=["현황", "향후 조치"])
    assert headings[0] == "현황" and "금주 실적" not in headings


def test_the_head_takes_the_writer_the_instructions_name():
    minutes = doc_formats.BY_ID["minutes"]
    said = "# 역할\n교육혁신팀 회의록을 쓴다. 작성자는 「교육혁신팀 김에이전트」로 적는다."
    read = doc_formats.head_values(minutes, f"10/14 교육혁신팀 주간회의.\n{said}")
    assert read["작성자"] == "교육혁신팀 김에이전트"
    block = doc_formats.title_block(minutes, {}, f"10/14 교육혁신팀 주간회의.\n{said}", read)
    assert dict(map(tuple, block["fields"]))["작성자"] == "교육혁신팀 김에이전트"


def test_a_year_answer_dates_the_memos_day_and_is_never_the_date_itself():
    request = ("10/14 교육혁신팀 주간회의. 참석 김지훈(팀장), 이수연. 이수연이 포스터 시안을 "
               "다음 주 금요일까지 준비하기로 함.\n\n덧붙인 조건:\n- 회의 일시: 2026")
    day, source = report_checks.meeting_base(request, "", dt.date(2026, 10, 19))
    assert (day, source) == (dt.date(2026, 10, 14), "text")
    # 「2026」 alone is no 일시: the head takes the meeting day instead.
    assert not doc_formats.field_value("일시", "2026")
    assert not doc_formats.field_value("일시", "2026년")
    assert doc_formats.field_value("일시", "2026-10-14(수)")
    read = doc_formats.head_values(doc_formats.BY_ID["minutes"], request)
    assert "일시" not in read
