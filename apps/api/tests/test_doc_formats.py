"""Documents by purpose: the head fields and numbering each format carries."""

from app.services import doc_formats


def test_each_persona_request_finds_its_format():
    cases = {
        "아래 개요로 한국근현대사 세미나 기말 리포트를 써 줘.": "term",
        "아래 내용으로 사회조사방법론 조사 계획서를 써 줘.": "plan",
        "아래 내용으로 회로실험 결과 보고서를 써 줘.": "lab",
        "아래 내용으로 캡스톤디자인 프로젝트 제안서를 써 줘.": "proposal",
        "보안 워크숍 투고용 논문 초안을 써 줘.": "paper",
        "아래 논문 초안을 프로그램 위원 입장에서 피어리뷰해 줘.": "review",
        "오늘 팀 회의록 정리해 줘": "minutes",
        "신제품 출시 보도자료 써 줘": "press",
        "사내 문서검색 현황 보고 써 줘": "status",
    }
    for request, expected in cases.items():
        assert doc_formats.detect(request).id == expected, request
    assert doc_formats.detect("파이썬 가상환경 정리 문서") is None


def test_head_values_come_only_from_the_request():
    fmt = doc_formats.BY_ID["lab"]
    request = "회로실험 결과 보고서를 써 줘. 실험일 2026-10-02, 3조, 공동실험자 김민수."
    answer = {"fields": {"실험일": "2026-10-02", "조": "3조", "학번·이름": "20231234 홍길동",
                         "공동실험자": "김민수", "과목·분반": "회로실험"}}
    block = doc_formats.title_block(fmt, answer, request)
    fields = dict(block["fields"])
    assert fields["실험일"] == "2026-10-02" and fields["조"] == "3조"
    assert fields["공동실험자"] == "김민수" and fields["과목·분반"] == "회로실험"
    # A student number and name the request never gave are blanks, not inventions.
    assert fields["학번·이름"] == "" and fields["제출일"] == ""
    assert block["head"] == "cover" and block["numbering"] == "decimal"


def test_numbering_styles():
    n = doc_formats.number_heading
    assert n("decimal", 1, [2]) == "2." and n("decimal", 2, [2, 3]) == "2.3"
    assert n("roman", 1, [3]) == "III." and n("roman", 2, [3, 2]) == "B."
    assert n("official", 2, [1, 2]) == "나." and n("official", 3, [1, 1, 4]) == "4)"
    assert n("none", 1, [1]) == ""


def test_a_pasted_outline_is_followed_and_named_sections_are_kept():
    from app.services.report import given_outline, with_listed_sections

    request = (
        "아래 개요로 한국근현대사 세미나 기말 리포트를 써 줘. 끝에 참고문헌.\n\n---\n"
        "[3]\n**논지(한 문장)**\n협력과 저항의 회색지대.\n\n**서론**\n문제 제기.\n\n"
        "**본론 1절: \'저항\'의 증거 — 검열·정간**\n...\n\n**본론 2절: \'협력\'의 증거**\n...\n\n"
        "**본론 3절: 회색지대로 읽기**\n...\n\n**결론**\n...\n"
    )
    outline = given_outline(request)
    assert outline[0] == "서론" and outline[-1] == "결론" and len(outline) == 5
    assert "논지(한 문장)" not in outline
    assert with_listed_sections(outline, request)[-1] == "참고문헌"
    # Without 「개요로」 the pasted lines are material, not the plan.
    assert given_outline(request.replace("아래 개요로", "아래 내용으로")) == []
    plan = (
        "아래 내용으로 조사 계획서를 써 줘. 구성은 연구 배경, 선행 연구, 연구 방법, 참고문헌."
        "\n---\nx"
    )
    assert with_listed_sections(["연구 배경", "연구 방법"], plan) == [
        "연구 배경", "선행 연구", "연구 방법", "참고문헌"
    ]


def test_a_copied_aside_leaves_the_heading_and_a_graph_slide_is_a_chart():
    from app.services.deck import _grounded_layouts
    from app.services.report import without_instruction_aside

    request = "결과(측정 표와 계산한 이득·이론값·오차), 고찰, 결론으로 써 줘."
    assert without_instruction_aside("결과(측정 표와 계산한 이득·이론값·오차)", request) == "결과"
    assert without_instruction_aside("서론(문제 제기)", request) == "서론(문제 제기)"
    plan = [
        {"title": "측정 결과 그래프", "layout": "bullets"}, {"title": "고찰", "layout": "bullets"}
    ]
    out = _grounded_layouts(plan, "100 Hz 1.98 V, 500 Hz 1.91 V", [])
    assert out[0]["layout"] == "chart" and out[1]["layout"] == "bullets"
    assert _grounded_layouts(plan, "그래프로 보여 줘", [])[0]["layout"] == "bullets"


def test_a_box_drawn_with_line_characters_is_not_a_stuck_decoder():
    from app.services.agent import _runaway

    box = "## 1. 시스템 아키텍처\n\n```\n┌" + "─" * 60
    assert _runaway([box]) is None
    assert _runaway(["═" * 80]) is None
    assert _runaway(["결과 " + "0" * 60]) is not None


def test_pasted_material_is_carried_whole_and_the_prompt_quotes_the_instruction():
    from app.services.context import pasted_material, prompt_request

    proposal = "## 요구사항\n" + "FR-01 분실물 등록. " * 300 + "\n## 위험 요소\n이미지 매칭 정확도"
    request = "아래 제안서로 캡스톤 중간 발표 자료를 만들어 줘. 10~12장.\n\n---\n" + proposal
    block = pasted_material(request)
    assert block.startswith("# 요청에 붙여 넣은 자료") and block.endswith("이미지 매칭 정확도")
    quoted = prompt_request(request, 2000)
    assert quoted.startswith("아래 제안서로") and "FR-01" not in quoted
    assert "참고 자료에 실었다" in quoted
    # A short request is quoted as it is.
    assert prompt_request("팀 소개 발표 5장", 2000) == "팀 소개 발표 5장"
    assert pasted_material("팀 소개 발표 5장") == ""


def test_an_outline_is_taken_from_the_answer_that_has_it_not_every_pasted_heading():
    from app.services import report

    material = (
        "[1]\n## 1. 기획된 산물론\n본문\n## 2. 대항적 공론장론\n본문\n## 3. 절충론\n본문\n\n"
        "[2]\n## 1. 신문 지면\n## 2. 검열 기록\n## 3. 경영 사료\n\n"
        "[3]\n## 서론\n## 본론 1절 — 검열의 경계\n## 본론 2절 — 지면의 낙차\n"
        "## 본론 3절 — 바깥에서 본 신문\n## 결론\n"
    )
    request = "아래 개요로 서론·본론 3절·결론의 기말 리포트를 써 줘.\n\n---\n" + material
    assert report.given_outline(request) == [
        "서론", "본론 1절 — 검열의 경계", "본론 2절 — 지면의 낙차",
        "본론 3절 — 바깥에서 본 신문", "결론",
    ]
    # Without parts named, the latest run is the outline.
    plain = "아래 개요로 써 줘.\n\n---\n" + material.replace("서론", "도입").replace("결론", "정리")
    assert report.given_outline(plain)[0] == "본론 1절 — 검열의 경계"


def test_the_reference_section_keeps_entries_and_drops_citing_advice():
    from app.services import report

    content = (
        "**영문 연구·편저**\n\n"
        "Robinson, Michael. *Cultural Nationalism in Colonial Korea*. University of "
        "Washington Press, 1988. Schmid, Andre. *Korea Between Empires*. Columbia "
        "University Press, 2002. 영문 저서 세 종은 자료가 출판사와 연도를 명시한 항목입니다. "
        "인용 전에 판본을 확인해 부제까지 대조하시기 바랍니다.\n\n"
        "박찬승 (1992). 『한국근대정치사상사연구』. 역사비평사.\n\n"
        "**인용할 때의 유의점**\n\n"
        "위 목록 가운데 출판 연도가 비어 있는 항목은 자료에 그 값이 없어 비워 둔 것입니다."
    )
    out = report.reference_list_only([{"heading": "참고문헌", "content": content}])[0]["content"]
    assert "바랍니다" not in out and "비워 둔" not in out and "세 종은" not in out
    assert "Press, 1988.\n\nSchmid, Andre." in out
    assert "박찬승 (1992). 『한국근대정치사상사연구』. 역사비평사." in out
    body = [{"heading": "결론", "content": "확인하시기 바랍니다."}]
    assert report.reference_list_only(body) == body


def test_numbered_entries_broken_across_lines_are_kept_and_a_trailing_note_goes():
    from app.services import report

    numbered = (
        "[10] Chen, S. StruQ. USENIX Security 2025. [11]\n"
        "SecAlign: Defending Against Prompt Injection. arXiv:2410.05451, 2024. [12] NAVIGATE. "
        "[13] Adversarial preference learning. ACL Findings 2025."
    )
    out = report.reference_list_only([{"heading": "참고문헌", "content": numbered}])[0]["content"]
    lines = [line for line in out.split("\n") if line.strip()]
    assert [line[:4] for line in lines] == ["[10]", "[11]", "[12]", "[13]"]
    assert "SecAlign" in lines[1]
    noted = (
        "김지영. (2016). 『Representations of Colonial Collaboration』. University of Chicago. "
        "(저자 확인 필요). (연도 확인 필요). 『A HISTORY OF KOREA』. Penn Press. "
        "본문에 인용된 사료와 학술 문헌은 위 목록에 정리되어 있습니다."
    )
    out = report.reference_list_only([{"heading": "참고문헌", "content": noted}])[0]["content"]
    assert out.split("\n\n")[1].startswith("(저자 확인 필요)") and "정리되어" not in out


def test_subheadings_inside_a_section_sit_below_it():
    from app.services import report

    body = (
        "목표는 셋이다.\n\n## 분류 기준 확립\n내용\n\n# 원소 기호\n"
        "```\n## 코드 주석\n```\n### 그대로"
    )
    out = report.demote_subheadings(body).split("\n")
    assert out[2] == "### 분류 기준 확립" and out[5] == "### 원소 기호"
    assert "## 코드 주석" in out and "### 그대로" in out


def test_a_long_prose_paragraph_is_cut_at_sentence_ends():
    from app.services import report

    sentence = "보안 모델은 내부망에 설치하고 외부 통신을 막습니다. "
    body = (sentence * 40).strip() + "\n| 유형 | 특징 |\n|---|---|\n| 온프레미스 | 격리 |"
    out = report.split_long_paragraphs(body).split("\n\n")
    prose = [p for p in out if not p.startswith("|")]
    assert len(prose) >= 4 and all(len(p) <= 600 for p in prose)
    assert all(p.endswith("막습니다.") for p in prose)
    assert out[-1].startswith("| 유형 | 특징 |")
    short = "짧은 문단입니다.\n\n- 목록\n- 항목"
    assert report.split_long_paragraphs(short) == short


def test_an_approval_inherits_the_planning_turns_web_search():
    from app.models.chat import Message, Role
    from app.routers import sessions

    asked = Message(session_id="s", role=Role.user, content="보고서",
                    routing={"turnOptions": {"webSearch": "auto"}})
    plan = Message(session_id="s", role=Role.assistant, content="이렇게 구성하려고 합니다.")
    assert sessions._planned_web_search([asked, plan]) == "auto"
    assert sessions._planned_web_search([plan]) is False


def test_figures_are_captioned_below_and_tables_above_numbered_through_the_document():
    from app.services import report

    sections = [
        {"heading": "시스템 구조", "content": (
            "구성은 다음과 같습니다.\n\n| 구성 요소 | 주요 역할 | 보안 위치 |\n|---|---|---|\n"
            "| 게이트웨이 | 필터링 | 외곽 |\n\n![네트워크 구조](data:image/png;base64,AAAA)\n\n"
            "*그림: 네트워크 구조*\n\n설명 문단입니다.")},
        {"heading": "데이터 흐름", "content": (
            "표: 단계별 처리\n\n| 단계 | 처리 |\n|---|---|\n| 1 | 마스킹 |\n\n"
            "```mermaid\nflowchart LR\n a-->b\n```\n\n*그림: 데이터 흐름*")},
        {"heading": "참고문헌", "content": "김상현 (1990). 『동아일보의 역사』."},
    ]
    out = report.caption_figures_and_tables(sections)
    first, second = out[0]["content"], out[1]["content"]
    assert "**표 1. 구성 요소·주요 역할·보안 위치**\n\n| 구성 요소" in first
    assert "![그림 1. 네트워크 구조](data:image/png;base64,AAAA)" in first
    assert "*그림: 네트워크 구조*" not in first and first.endswith("설명 문단입니다.")
    assert second.startswith("**표 2. 단계별 처리**\n\n| 단계 | 처리 |")
    assert second.endswith("```\n\n*그림 2. 데이터 흐름*")
    assert out[2] == sections[2]


def test_cleanup_keeps_paragraphs_fences_and_tables_on_their_own_lines():
    from app.services import report

    body = ("첫 문장입니다 [4]. 둘째 문장입니다.\n\n셋째 문단입니다. 넷째입니다.\n\n"
            "```kpi\n99.9% | 탐지율 [4]\n```")
    assert report.fix_point_units(body) == body
    assert report.fix_point_units("0.853에서 0.912로 0.059%p 증가했다.\n\n다음.") == (
        "0.853에서 0.912로 0.059 증가했다.\n\n다음.")
    glued = ("발전합니다 [4]. ```kpi\n99.9% | 탐지율\n```\n\n가능합니다 [5]. | 기준 | A |\n"
             "|---|---|\n| x | y |")
    out = report.unglue_blocks(glued)
    assert "발전합니다 [4].\n\n```kpi" in out and "가능합니다 [5].\n\n| 기준 | A |" in out


def test_a_table_caption_written_onto_the_paragraph_is_lifted_above_the_table():
    from app.services import report

    sec = [{"heading": "배포", "content": (
        "새로운 접근이 필요합니다 [5]. 표: 배포 모델과 아키텍처 유형 비교\n\n"
        "| 기준 | 클라우드 |\n|---|---|\n| 비용 | 낮음 |")}]
    out = report.caption_figures_and_tables(sec)[0]["content"]
    # Nobody mentions the table, so the paragraph before it points at it.
    assert out.startswith(
        "새로운 접근이 필요합니다(표 1) [5].\n\n**표 1. 배포 모델과 아키텍처 유형 비교**\n\n| 기준"
    )


def test_a_run_of_sentences_on_one_source_cites_it_once():
    from app.services import report

    body = (
        "게이트웨이 방어는 세 가지 검증으로 구성됩니다 [2]. 첫 번째는 입력 검증입니다 [2]. "
        "두 번째는 출력 검증입니다 [2]. 공격 벡터가 확장됩니다 [4]. "
        "이 공격은 외부 콘텐츠로 발생합니다 [4].\n\n"
        "| 가 | 나 |\n|---|---|\n| 1 [2] | 2 [2] |\n\n"
        "탐지율은 99.9%입니다 [3]. 이는 제품 발표 수치입니다 [3]."
    )
    out = report.collapse_repeated_citations(body)
    first = out.split("\n\n")[0]
    assert first == (
        "게이트웨이 방어는 세 가지 검증으로 구성됩니다. 첫 번째는 입력 검증입니다. "
        "두 번째는 출력 검증입니다 [2]. 공격 벡터가 확장됩니다. "
        "이 공격은 외부 콘텐츠로 발생합니다 [4]."
    )
    assert "| 1 [2] | 2 [2] |" in out
    # A sentence with a number keeps its own citation.
    assert out.endswith("탐지율은 99.9%입니다 [3]. 이는 제품 발표 수치입니다 [3].")


def test_a_caption_written_under_the_table_moves_above_it():
    from app.services import report

    sec = [{"heading": "대응", "content": (
        "설명입니다.\n\n| 통제 영역 | 방법 |\n|---|---|\n| 인젝션 | 가드레일 |\n\n"
        "표: 게이트웨이 레이어 보안 통제 매트릭스\n\n다음 문단입니다.")}]
    out = report.caption_figures_and_tables(sec)[0]["content"]
    assert out.count("게이트웨이 레이어 보안 통제 매트릭스") == 1
    assert "**표 1. 게이트웨이 레이어 보안 통제 매트릭스**\n\n| 통제 영역" in out
    assert out.endswith("| 인젝션 | 가드레일 |\n\n다음 문단입니다.")


def test_placed_figures_are_parsed_shown_and_numbered_where_the_text_points():
    from app.services import report

    body = (
        "요청은 게이트웨이와 마스킹을 거칩니다. 〔그림〕과 같이 응답은 로그에 남습니다.\n\n"
        "[[그림: flow | 요청 처리 흐름 | 사용자 요청 → 게이트웨이 → 마스킹 → LLM 서버 → 로그]]\n\n"
        "비교는 〔표〕에 정리했습니다.\n\n| 항목 | 값 |\n|---|---|\n| 가 | 나 |"
    )
    marks = report.figure_marks(body)
    assert marks[0]["kind"] == "flow" and marks[0]["caption"] == "요청 처리 흐름"
    assert "〔그림 준비 중: 요청 처리 흐름〕" in report.shown_while_writing(body)
    drawn = body.replace(marks[0]["mark"], "![요청 처리 흐름](data:image/png;base64,AAAA)")
    out = report.caption_figures_and_tables([{"heading": "구조", "content": drawn}])[0]["content"]
    assert "그림 1과 같이 응답은" in out and "〔그림〕" not in out
    assert "비교는 표 1에 정리했습니다." in out and "**표 1. 항목·값**" in out


def test_a_figure_that_could_not_be_drawn_takes_its_reference_with_it():
    from app.services import report

    body = ("〔그림〕과 같이 요청은 세 단계를 거칩니다.\n\n"
            "[[그림: flow | 흐름 | 가 → 나 → 다]]\n\n다음 문단입니다.")
    out = report.drop_figure(body, report.figure_marks(body)[0]["mark"])
    assert out == "요청은 세 단계를 거칩니다.\n\n다음 문단입니다."


def test_a_figure_the_text_never_mentions_is_pointed_at_and_particles_agree():
    from app.services import report

    sec = [{"heading": "구조", "content": (
        "요청은 세 단계를 거쳐 처리됩니다 [2].\n\n![흐름](data:image/png;base64,AAAA)\n\n"
        "〔표〕는 두 방식을 비교합니다. 〔표〕과 함께 봅니다.\n\n"
        "| 가 | 나 |\n|---|---|\n| 1 | 2 |")}]
    out = report.caption_figures_and_tables(sec)[0]["content"]
    assert out.startswith("요청은 세 단계를 거쳐 처리됩니다(그림 1) [2].")
    fixed = report.fix_number_particles("표 1는 그림 2과 표 3로 그림 10가")
    assert fixed == "표 1은 그림 2와 표 3으로 그림 10이"


def test_a_word_with_stray_cyrillic_is_mended_from_the_document():
    from app.services import hangul

    text = "프로мп트 인젝션 공격 원리. 프롬프트 인젝션은 지시를 바꿉니다. 각도 α는 그대로."
    mended, slips = hangul.repair_mixed_script(text)
    assert mended.startswith("프롬프트 인젝션 공격 원리.") and "α" in mended
    assert slips == ["프로мп트→프롬프트"]


def test_figures_and_tables_nobody_mentions_are_pointed_at_from_the_prose_before():
    from app.services import report

    sec = [{"heading": "기능", "content": (
        "기능들은 통합되어 작동합니다.\n\n| 구분 | 기술 |\n|---|---|\n| 탐지 | 행위 |\n\n"
        "![연동 구조](data:image/png;base64,AAAA)")}]
    out = report.caption_figures_and_tables(sec)[0]["content"]
    assert out.startswith("기능들은 통합되어 작동합니다(표 1, 그림 1).")


def test_a_table_written_without_pipes_or_rule_is_repaired_and_captioned():
    from app.services import report

    body = ("비교하면 〔표〕와 같은 구분이 가능합니다.\n\n표: 벤더별 특성 비교\n"
            "기준 | 플랫폼 A | 벤더 B\n포지션 | 상단 계층 | 분산 배치\n의존도 | 높음 | 낮음\n\n"
            "```kpi\n99.9% | 탐지율\n2025 | 출시 연도\n```")
    fixed = report.repair_pipe_tables(body)
    head = "| 기준 | 플랫폼 A | 벤더 B |\n|---|---|---|\n"
    assert head + "| 포지션 | 상단 계층 | 분산 배치 |" in fixed
    assert "```kpi\n99.9% | 탐지율\n2025 | 출시 연도\n```" in fixed
    out = report.caption_figures_and_tables([{"heading": "벤더", "content": fixed}])[0]["content"]
    assert out.startswith("비교하면 표 1과 같은 구분이 가능합니다.")
    assert "**표 1. 벤더별 특성 비교**" in out and "\n표:" not in out
    lone = [{"heading": "x", "content": "비교하면 〔표〕와 같은 구분입니다."}]
    gone = report.caption_figures_and_tables(lone)
    assert gone[0]["content"] == "비교하면 같은 구분입니다."


def test_glued_lesson_tables_split_and_take_numbered_captions_and_a_stray_fence_goes():
    from app.services import report

    head = "| 단계 | 교수 활동 | 시간 |\n|---|---|---|\n"
    body = ("```\n본 단원은 3차시로 구성됩니다.\n\n표: 1차시 교수·학습 과정\n\n" + head
            + "| 도입 | 질문 | 5 |\n" + head + "| 도입 | 실험 | 5 |\n\n"
            + "![흐름](data:image/png;base64,AAAA)")
    fixed = report.split_glued_tables(report.balance_fences(body))
    assert not fixed.startswith("```")
    out = report.caption_figures_and_tables([{"heading": "차시", "content": fixed}])[0]["content"]
    assert "**표 1. 1차시 교수·학습 과정**" in out and "**표 2. 2차시 교수·학습 과정**" in out
    assert "![그림 1. 흐름](data:image/png;base64,AAAA)" in out
