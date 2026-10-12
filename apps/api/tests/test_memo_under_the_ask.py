"""What kind of document is asked for is read from the ask, not from the memo under it.

「아래 자료로 행사 결과보고서 써 줘」 over a memo line 「- 문제: 2일차 새벽 … 와이파이 장애」 is a
result report, not an 장애 보고서; a 공모사업 신청용 사업계획서 is not the start-up business-plan
genre (핵심 가정, monthly profit and loss).
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
    # A press release or grant application written from the person's notes is not searched,
    # so no unrelated web page is cited as grounds.
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
    # No generated pictures in a 공문, no KPI tiles or callouts in a 보도자료 or 회의록.
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
    # A 공문's single 「본문」 prints without a chapter title.
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
    # The default comparison report does not search, so the source's ISMS stays ISMS.
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


def test_any_work_document_on_attached_or_given_facts_is_not_searched():
    # 공고문·FAQ on an attached 규정, a 학사일정 memo, an IRB form, a numbered procedure.
    notice = ("첨부한 장학금 지급 규정을 근거로 「장학금 신청 안내」 공고문을 "
              "학생 대상으로 써 줘. 문의: 학생지원팀 041-550-1150.")
    assert report.material_complete(notice, None, has_files=True)
    assert report.material_complete("첨부 규정 보고 학생들이 자주 묻는 질문 FAQ 8개 만들어 줘.",
                                    None, has_files=True)
    haksa = ("학생들한테 보낼 2027학년도 1학기 학사일정 안내문 써 줘. 요일도 붙여서.\n"
             "- 수강신청: 2027. 2. 15. ~ 2. 18.\n- 개강: 2027. 3. 2.\n"
             "- 수강신청 정정: 개강일부터 5일간\n- 중간고사: 2027. 4. 20. ~ 4. 26.")
    assert report.material_complete(haksa, "official")
    assert report.material_complete(haksa, None)
    irb = ("IRB 심의 신청서(연구계획 요약) 초안 써 줘.\n- 연구제목: 대학생의 생성형 AI 학습 활용\n"
           "- 대상: 본교 학부생 240명, 온라인 설문(약 15분)\n- 위험: 최소 위험. 중도 철회 가능\n"
           "- 자료 보관: 연구 종료 후 3년")
    assert report.material_complete(irb, "plan")
    flow = ("우리 학과 「학부연구생 신청 절차」 안내 보고서 써 줘. 절차 흐름도도 넣어서.\n"
            "절차: ① 지원 메일 → ② 교수 면담 → ③ 서류 제출 → ④ 서류 확인\n"
            "신청 기간: 매 학기 개강 후 2주 이내 / 문의: 학과 사무실 내선 2301")
    assert report.material_complete(flow, None)
    # A memo with a lookup ask, a short bare ask, or a research plan: searched.
    assert not report.material_complete(haksa.replace("써 줘.", "다른 대학 사례도 찾아서 써 줘."),
                                        None)
    assert not report.material_complete("학사일정 안내문 써 줘.", None)
    memo = "\n".join(f"- 항목 {n}: 자료 내용 {n}번째 줄" for n in range(1, 5))
    assert not report.material_complete(f"아래 메모로 연구계획서 써 줘.\n{memo}", "plan")


def test_kpi_figures_the_minutes_already_state_are_not_dumped_as_a_list():
    body = ("| 항목 | 내용 |\n|---|---|\n| 일시 | 2026-10-15(목) 14:00 |\n\n"
            "워크숍 신청자는 42명이다.\n\n```kpi\n2026-10-15(목) | 다음 회의일\n"
            "42명 | 워크숍 신청자\n420만원 | 사례집 디자인 견적 B사\n```\n")
    out = report.plain_blocks(body, body)
    assert "- 다음 회의일" not in out and "- 워크숍 신청자" not in out
    assert "- 사례집 디자인 견적 B사: 420만원" in out
    every = body.replace("420만원 | 사례집 디자인 견적 B사\n", "")
    assert "```" not in report.plain_blocks(every, every) and "- " not in report.plain_blocks(
        every, every)


def test_forms_and_notices_are_text_documents():
    for ask in ("IRB 심의 신청서(연구계획 요약) 초안 써 줘.\n- 대상: 학부생 240명",
                "첨부 규정 보고 학생들이 자주 묻는 질문 FAQ 8개 만들어 줘.",
                "장학금 신청 안내 공고문을 학생 대상으로 써 줘."):
        assert report.form_document(ask)
    assert not report.form_document("생성형 AI 교육 활용 동향 보고서 써 줘.")
    assert not report.form_document("아래 메모로 사업계획서 써 줘. 한빛시 공모사업 신청용이야.")


def test_an_untitled_body_exports_without_an_empty_heading():
    out = report.to_markdown("안내", [{"heading": "", "content": "1. 개강: 2027. 3. 2.(화)"}])
    assert out == "# 안내\n\n1. 개강: 2027. 3. 2.(화)\n"


def test_a_short_guide_with_figures_gets_no_word_table_of_contents():
    from app.services import report_export

    picture = "![흐름](data:image/png;base64," + "A" * 20000 + ")"
    sections = [{"heading": f"절 {n}", "content": f"짧은 안내 {n}.\n\n{picture}"} for n in range(6)]
    assert not report_export._wants_toc(sections)
    assert report_export._wants_toc([{"heading": f"절 {n}", "content": "가" * 1200}
                                     for n in range(6)])


def test_listed_pipe_rows_become_a_table_without_list_markers():
    body = "- 비목 | 협약 예산(원) | 집행액(원)\n- 인건비 | 1,000 | 900\n- 여비 | 500 | 450"
    out = report.repair_pipe_tables(body)
    assert out.splitlines()[0] == "| 비목 | 협약 예산(원) | 집행액(원) |"
    assert "| 인건비 | 1,000 | 900 |" in out and "- " not in out


def test_a_dropped_figure_takes_its_reference_particle_with_it():
    mark = "[[그림: flow | 비용 산식 | 단가 → 인원 → 개월]]"
    body = f"비용은 〔그림〕에서 제시한 산식대로 계산한다.\n\n{mark}\n\n끝."
    out = report.drop_figure(body, mark)
    assert "〔그림〕" not in out and " 에서" not in out
    assert out.startswith("비용은 제시한 산식대로")
    assert report.drop_figure(f"위 〔그림〕과 같이 나뉜다.\n{mark}", mark).startswith("나뉜다.")


def test_official_items_print_as_levels_not_a_list_beside_flush_lines():
    from app.services import report_export

    body = ("1. 협조를 요청합니다.\n\n가. 조사 대상: 기자재\n\n※ 소모품은 제외\n\n"
            "1) 세부 항목\n\n가) 더 세부\n\n2. 문의: 기획예산팀")
    lines = report_export._section_lines({"content": body}, {"numbering": "official"}, False, "  ")
    shown = [(kind, marker, depth) for kind, _text, marker, depth in lines]
    assert shown == [("gaejo", "1.", 0), ("gaejo", "가.", 1), ("remark", "※", 2),
                     ("gaejo", "1)", 2), ("gaejo", "가)", 3), ("gaejo", "2.", 0)]
