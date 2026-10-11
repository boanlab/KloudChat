"""What kind of document is asked for is read from the ask, not from the memo under it.

Quality lane Q6/Q9: 「아래 자료로 행사 결과보고서 써 줘」 over a memo line 「- 문제: 2일차 새벽 …
와이파이 장애」 came out as an 장애 보고서, and a 공모사업 신청용 사업계획서 took the start-up
business-plan genre (핵심 가정, monthly profit and loss).
"""

from __future__ import annotations

from pathlib import Path

from app.pipeline import report_stages
from app.services import report
from app.services.context import instruction_part, lead_instruction

_S09 = (
    "아래 자료로 행사 결과보고서 써 줘. 총장님까지 올라가는 내부 보고야.\n\n"
    "- 행사명: 2026 한빛 AI 해커톤 / 주관: SW중심대학사업단\n"
    "- 참가: 신청 61개 팀 → 48개 팀 172명 선발\n"
    "- 예산: 편성 4,500만원 / 집행 4,215만원\n"
    "- 문제: 2일차 새벽 2~4시 미래관 2층 와이파이 장애(AP 2대 과부하), 식음료 야식 부족\n"
)


def test_the_ask_is_the_first_paragraph_over_a_memo():
    ask = "아래 자료로 행사 결과보고서 써 줘. 총장님까지 올라가는 내부 보고야."
    assert lead_instruction(_S09) == ask
    # Conditions in prose after a blank line are still the instruction.
    asked = "보고서 써 줘.\n\n3쪽 이내로 쓰고 표는 두 개만 넣어 줘."
    assert lead_instruction(asked) == instruction_part(asked)
    assert lead_instruction("결과보고서 써 줘") == "결과보고서 써 줘"


def test_a_result_report_over_an_outage_memo_stays_a_result_report():
    bundle = report_stages.bundle_for(request=_S09, model="m", api_key="k")
    assert bundle.spec.format_id == "result_report"
    assert not bundle.spec.genre.startswith("Genre: 장애")
    # An 장애 report asked for in the ask itself still is one.
    outage = report_stages.bundle_for(
        request="어제 와이파이 장애 보고서 써 줘.\n\n- 02:00 AP 과부하", model="m", api_key="k")
    assert outage.spec.format_id == "incident"


def test_a_grant_application_is_not_a_start_up_business_plan():
    grant = ("아래 메모로 「2027 지역 상생 디지털 배움터」 사업계획서 써 줘. "
             "한빛시 공모사업 신청용이고, 심사위원이 읽는 문서야.\n\n"
             "- 신청 기관: 한빛대학교 평생교육원\n- 예산: 1억 2,000만원")
    rule = report._genre_rule(grant)
    assert rule.startswith("Genre: 공모·지원사업 계획서") and "profit-and-loss" in rule
    business = "Genre: 사업 기획안"
    assert report._genre_rule("신사업 기획안 써 줘. 구독 박스 매출 목표 포함").startswith(business)
    assert report._genre_rule("사업계획서 써 줘. 창업 아이템은 밀키트 구독.").startswith(business)
    genres = Path(report.__file__).resolve().parent.parent / "genres.toml"
    assert "grant-plan" in genres.read_text(encoding="utf-8")
