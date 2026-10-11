"""What kind of document is asked for is read from the ask, not from the memo under it.

Quality lane Q6/Q9: 「아래 자료로 행사 결과보고서 써 줘」 over a memo line 「- 문제: 2일차 새벽 …
와이파이 장애」 came out as an 장애 보고서, and a 공모사업 신청용 사업계획서 took the start-up
business-plan genre (핵심 가정, monthly profit and loss).
"""

from __future__ import annotations

from pathlib import Path

from app.pipeline import report_stages
from app.services import doc_formats, report
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


def test_a_work_document_with_its_material_is_not_searched():
    # Quality lane Q7: a press release and a grant application written from the person's
    # notes cited unrelated web pages (a 한국코드페어 notice, a 경북 수산물 기획전) as grounds.
    memo = "\n".join(f"- 항목 {n}: 자료 내용 {n}번째 줄, 참가 {n * 10}명" for n in range(1, 15))
    press = f"아래 내용으로 보도자료 써 줘.\n\n{memo}"
    assert report.material_complete(press, "press")
    grant = f"아래 메모로 사업계획서 써 줘. 한빛시 공모사업 신청용이야.\n\n{memo}"
    assert report.material_complete(grant, "plan")
    # Asked for the web, a short request with no material, or a research plan: searched.
    searched = press.replace("써 줘.", "관련 사례도 검색해서 써 줘.")
    assert not report.material_complete(searched, "press")
    assert not report.material_complete("보도자료 써 줘.", "press")
    assert not report.material_complete(f"아래 메모로 연구계획서 써 줘.\n\n{memo}", "plan")
    assert report.material_complete("보도자료 써 줘.", "press", has_files=True)


def test_plain_work_documents_draw_only_when_asked_and_keep_no_tiles():
    # Quality lane Q13: a 공문 with two generated pictures, a 보도자료 with KPI tiles and a
    # callout, a 회의록 with KPI 「42명·50만원·9편」.
    from app.services import process_settings

    for fmt in ("official", "press", "minutes", "result_report", "internal_draft"):
        assert process_settings.resolve(template_id=None, format_id=fmt).draws == "when-asked"
    assert process_settings.resolve(template_id=None, format_id="paper").draws == "auto"
    # The agent's own setting still wins.
    own = process_settings.resolve(template_id=None, format_id="press", own={"draws": "auto"})
    assert own.draws == "auto"
    text = ("행사를 마쳤다.\n```kpi\n172명 | 참가자\n4.5점 | 만족도\n```\n"
            "```callout\n잔액 처리\n285만원 반납 여부 결정 필요\n```\n끝.")
    assert report.plain_blocks(text) == (
        "행사를 마쳤다.\n- 참가자: 172명\n- 만족도: 4.5점\n"
        "※ 잔액 처리: 285만원 반납 여부 결정 필요\n끝.")
    bundle = report_stages.bundle_for(request=_S09, model="m", api_key="k")
    assert bundle.spec.settings.draws == "when-asked"


def test_a_single_body_section_prints_without_a_chapter_heading():
    # Quality lane Q11: a 공문 template's body was always written under chapter titles.
    from app.services import report_export

    body = [{"id": "s0", "heading": "본문",
             "content": "1. 협조를 요청합니다.\n  가. 기한: 2026. 11. 6.(금)"}]
    assert report.untitled_body(body, ("본문",))[0]["heading"] == ""
    assert report.untitled_body(body, ("요청 사항",)) == body
    assert report.untitled_body(body, ()) == body
    sections = report.untitled_body(body, ("본문",))
    pdf = report_export.to_pdf("협조 요청", sections)
    assert pdf.startswith(b"%PDF")
    docx = report_export.to_docx("협조 요청", sections)
    assert docx[:2] == b"PK"


def test_a_comparison_whose_options_come_priced_is_not_searched():
    # S10 (verify1 Q7): the default comparison report searched and renamed the source's
    # ISMS to ISMS-P.
    request = open_scenario_s10()
    # 「비교·검토하는 보고서」 names the comparison format.
    assert doc_formats.detect(lead_instruction(request)).id == "comparison"
    assert report.material_complete(request, "comparison")
    assert not report.material_complete(request.replace("결론적으로", "관련 사례도 검색해서 "
                                                        "결론적으로"), "comparison")
    assert not report.material_complete("슬랙과 팀즈를 비교하는 보고서 써 줘.", "comparison")
    assert report.material_complete("두 안 비교 보고서 써 줘.", "comparison", has_files=True)


def open_scenario_s10() -> str:
    return "\n".join([
        "우리 팀(교직원 40명) 업무 협업 도구 두 가지 안을 비교·검토하는 보고서 써 줘. "
        "결론적으로 어느 안을 추천하는지까지.",
        "",
        "A안: 지금 쓰는 사내 메신저(무료) 유지 + 화상회의 라이선스만 추가 구매",
        "- 화상회의 라이선스 1인당 월 8,000원 × 40명",
        "- 문서 공동편집 불가(지금처럼 파일 메일 첨부), 일정·할 일 관리 기능 없음",
        "- 교육 필요 없음, 전환 작업 없음",
        "",
        "B안: 통합 협업툴(메신저·화상회의·공동편집·일정·할 일) 전환",
        "- 1인당 월 14,000원 × 40명, 연 단위 계약 시 15% 할인",
        "- 초기 전환 컨설팅·데이터 이전 1회 300만원",
        "- 전 직원 교육 2시간 1회 필요",
        "- 외부 클라우드 저장(국내 리전), 정보보호 인증(ISMS) 보유",
        "- 지난 분기 설문에서 '파일 버전 혼선' 불만이 응답자 35명 중 21명",
        "",
        "판단 기준: 연간 비용(첫해·이듬해), 업무 효율, 보안, 도입 부담.",
    ])
