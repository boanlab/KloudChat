"""Pattern slides are written in their own shape, or fall back to bullets."""

import json

import pytest

from app.services import deck, slide_patterns


def _example(pattern):
    return json.loads(pattern.example.replace("{{", "{").replace("}}", "}"))


@pytest.mark.parametrize("pattern", slide_patterns.PATTERNS, ids=lambda p: p.name)
def test_every_pattern_fills_from_its_own_example(pattern):
    slide = {"id": "s", "title": "제목", "layout": pattern.name}
    deck._fill_pattern(slide, _example(pattern))
    assert slide["layout"] == pattern.name
    assert deck.has_content(slide)
    field = slide_patterns.field_of(pattern.name)
    assert slide.get(field)
    if pattern.shape == "chart":
        assert slide["chart"]["kind"] == pattern.params["kind"]
    # Every pattern has a writer prompt and a height model that fits after auto-fit.
    assert pattern.name in deck._PROMPTS
    assert deck._need(deck.auto_fit(dict(slide)), 1.0) > 0 or pattern.render == "text"


def test_a_pattern_answer_in_the_wrong_shape_becomes_bullets():
    slide = {"id": "s", "title": "SWOT", "layout": "swot"}
    deck._fill_pattern(slide, {"bullets": ["강점: 데이터", "약점: 인력"]})
    assert slide["layout"] == "bullets" and slide["bullets"]


def test_numeric_patterns_are_bullets_without_numbers():
    plan = [{"title": "지표", "layout": "kpi-grid"}, {"title": "분석", "layout": "swot"}]
    out = deck._grounded_layouts(plan, "팀 소개 발표", [])
    assert out[0]["layout"] == "bullets" and out[1]["layout"] == "swot"
    # Patterns are never offered to vary a deck.
    assert not set(deck._offered_layouts("작년 32% 줄었다", [])) & set(deck.slide_patterns.NAMES)


def test_the_outline_lists_every_pattern():
    for name in slide_patterns.NAMES:
        assert f"- {name}:" in deck._OUTLINE_PROMPT


def test_a_title_that_names_a_patterns_use_takes_the_pattern():
    plan = [
        {"title": "캡스톤 중간 발표", "layout": "title"},
        {"title": "기술 스택 장단점", "layout": "bullets"},
        {"title": "SWOT 분석", "layout": "two-column"},
        {"title": "예상 질문", "layout": "bullets"},
        {"title": "역할 분담", "layout": "cards"},
        {"title": "15주 개발 일정", "layout": "bullets"},
        {"title": "참고문헌", "layout": "bullets"},
        {"title": "측정 결과", "layout": "table"},
        {"title": "또 다른 장단점", "layout": "bullets"},
    ]
    layouts = [s["layout"] for s in deck.suggest_patterns(plan, "캡스톤 발표 10장", [])]
    assert layouts == [
        "title", "pros-cons", "swot", "faq", "team", "roadmap", "references", "table", "bullets"
    ]
    # A layout the person named is theirs; a numeric pattern needs figures.
    asked = deck.suggest_patterns(plan, "역할 분담 장은 표로 바꿔 줘", [])
    assert [s["layout"] for s in asked] == [s["layout"] for s in plan]
    kpi = [{"title": "성과 지표", "layout": "bullets"}]
    assert deck.suggest_patterns(kpi, "발표", [])[0]["layout"] == "objectives"


def test_a_pasted_reports_figures_are_not_restated_and_decimals_stay_whole():
    report = (
        "| 5000 | 0.310 | -10.182 | 0.3162 | -10.000 | -0.182 |\n\n"
        "차단 주파수 부근인 1590 Hz에서 측정 이득은 -3.098 dB, 이론 이득은 -3.015 dB였다.\n"
        "3000 Hz에서 -0.257 dB, 5000 Hz에서 -0.182 dB였다."
    )
    request = "아래 실험 보고서로 결과 발표 자료를 만들어 줘. 6~8장.\n\n---\n" + report
    slides = [
        {"layout": "title", "title": "RC 필터"},
        {"layout": "bullets", "title": "결론", "bullets": ["저역통과 특성 확인"]},
    ]
    assert deck.restate_missing_facts(slides, request) == []
    assert slides[1]["bullets"] == ["저역통과 특성 확인"]
    # A decimal stays inside its clause.
    text = "측정 이득은 -3.098 dB, 이론 이득은 -3.015 dB"
    at = text.index("-3.098")
    assert deck._clause_around(text, at, at + 6) == "측정 이득은 -3.098 dB"


def test_pasted_material_with_added_conditions_starts_after_the_separator():
    from app.services.context import pasted_material

    body = "보고서 본문 " * 60
    request = f"아래로 발표 만들어 줘\n\n---\n{body}\n\n덧붙인 조건: 장수는 8장"
    block = pasted_material(request)
    assert block.startswith("# 요청에 붙여 넣은 자료\n보고서 본문")
    assert "덧붙인 조건" not in block and "발표 만들어" not in block


def test_a_copied_table_row_is_not_a_bullet_or_item():
    rows = ["층당 30명 이상 | | 설문지 | 소득", "300명 표본 | 층화 추출", "설문 기간은 4주"]
    assert deck._clean_bullets(rows) == ["300명 표본 | 층화 추출", "설문 기간은 4주"]
    pairs = [["표본 | 크기", "300"], ["추출", "| 10 | 12 |"], ["기간", "4주"], ["도구", "설문지"]]
    kept = slide_patterns.clean_pairs("feature-grid", pairs)
    assert kept == [["기간", "4주"], ["도구", "설문지"]]


def test_head_and_line_bullets_become_cards_or_a_grid():
    slides = [
        {"layout": "title", "title": "캡스톤"},
        {"layout": "bullets", "title": "핵심 기능", "bullets": [
            "사진 등록: 분실물 사진과 위치를 올린다", "자동 매칭: 이미지 유사도로 후보를 찾는다",
            "알림: 후보가 생기면 푸시로 알린다", "보관함 연동: 학생회 보관 목록과 맞춘다",
        ]},
        {"layout": "bullets", "title": "세 원칙", "bullets": [
            "정확성 – 매칭 결과는 사람이 확인", "개인정보 – 얼굴은 흐리게", "속도 – 등록 3초 안",
        ]},
        {"layout": "bullets", "title": "결과",
         "bullets": ["차단 주파수 1.59 kHz", "오차 0.26 dB 이내"]},
    ]
    assert deck.shape_patterns(slides, "발표 10장") == [1, 2]
    assert slides[1]["layout"] == "feature-grid" and slides[1]["items"][0][0] == "사진 등록"
    assert slides[2]["layout"] == "cards-3" and slides[2]["items"][2] == ["속도", "등록 3초 안"]
    assert slides[3]["layout"] == "bullets"
    # A layout the person named is kept.
    lines = ["가나: 다라마바", "사아: 자차카타", "파하: 가나다라"]
    again = [{"layout": "bullets", "title": "x", "bullets": lines}]
    assert deck.shape_patterns(again, "모두 글머리표로 만들어 줘") == []


def test_a_chart_slide_without_a_drafted_chart_is_drawn_from_the_material_table():
    report = (
        "## 측정 결과\n\n"
        "| 주파수(Hz) | 출력(V) | 측정 이득(dB) | 이론 이득(dB) | 오차(dB) |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| 100 | 0.99 | -0.09 | -0.00 | -0.09 |\n"
        "| 1590 | 0.70 | -3.09 | -3.01 | -0.08 |\n"
        "| 10000 | 0.16 | -15.92 | -14.08 | -1.84 |\n\n"
        "| 구분 | 담당 |\n| --- | --- |\n| 측정 | 김 |\n| 정리 | 이 |\n"
    )
    chart = deck.chart_from_material("측정 이득과 이론 이득 그래프", ["발표 만들어 줘", report])
    assert chart["kind"] == "line" and chart["categories"] == ["100", "1590", "10000"]
    assert [s["name"] for s in chart["series"]] == ["측정 이득", "이론 이득"]
    assert chart["series"][0]["values"] == [-0.09, -3.09, -15.92] and chart["unit"] == "dB"
    assert deck.chart_from_material("결과 그래프", ["숫자 표가 없는 자료"]) is None


def test_a_pasted_reports_tables_do_not_count_as_a_named_layout():
    request = (
        "아래 제안서로 발표 자료를 만들어 줘. 문제, 목표, 일정 순서로.\n\n---\n"
        "| 주차 | 할 일 |\n| --- | --- |\n| 1 | 요구사항 |\n| 2 | 설계 |"
    )
    plan = [{"title": "역할 분담", "layout": "bullets"}]
    assert deck.suggest_patterns(plan, request, [])[0]["layout"] == "team"


def test_a_pattern_answered_in_bullets_keeps_the_pattern():
    slide = {"layout": "objectives", "title": "목적"}
    lines = ["차단 주파수 확인", "이론과 측정 비교", "오차 원인 찾기"]
    deck._fill_pattern(slide, {"bullets": lines})
    assert slide["layout"] == "objectives"
    assert slide["items"] == [[line, ""] for line in lines]
    grid = {"layout": "feature-grid", "title": "기능"}
    deck._fill_pattern(grid, {"bullets": ["등록: 사진을 올린다", "매칭: 후보를 찾는다",
                                          "알림: 푸시로 알린다", "보관: 목록과 맞춘다"]})
    assert grid["layout"] == "feature-grid" and grid["items"][1] == ["매칭", "후보를 찾는다"]
    plain = {"layout": "feature-grid", "title": "기능"}
    deck._fill_pattern(plain, {"bullets": ["사진을 올린다", "후보를 찾는다"]})
    assert plain["layout"] == "bullets"


def test_a_chart_slide_whose_draft_lost_its_chart_is_drawn_from_the_table():
    material = (
        "발표 만들어 줘\n| 주파수(Hz) | 측정 이득(dB) |\n| --- | --- |\n"
        "| 100 | -0.09 |\n| 1590 | -3.09 |\n| 10000 | -15.92 |"
    )
    slides = [{"title": "측정 결과 그래프", "layout": "chart"}]
    draft = '{"slides": [{"title": "측정 결과 그래프", "layout": "bullets", ' \
        '"bullets": ["이득이 줄어든다", "차단 주파수 부근"], "notes": "그래프를 봅니다."}]}'
    rows = deck._split_deck_draft(draft, slides, deck._facts_set(material), material)
    assert rows[0]["layout"] == "chart"
    assert rows[0]["chart"]["series"][0]["values"] == [-0.09, -3.09, -15.92]


def test_a_stated_range_of_slides_is_read_from_the_instruction():
    ask = "아래 리포트로 세미나 10분 발표 자료를 만들어 줘. 8~10장, 발표자 노트 포함."
    assert deck.slide_range(ask + "\n\n---\n표 1장, 그림 2~3장") == (8, 10)
    assert deck.slide_range("발표 7-9 슬라이드로") == (7, 9)
    assert deck.slide_range("발표 6장으로") is None
    assert deck.slide_range("본문 3~4장 포함 발표") is None
    # Material under the instruction does not set the range.
    assert deck.slide_range("발표 만들어 줘\n\n---\n부록은 2~3장") is None


def test_a_deck_one_or_two_short_of_its_floor_gets_a_summary_and_questions():
    plan = [{"title": "표지", "layout": "title"}, {"title": "배경", "layout": "bullets"},
            {"title": "방법", "layout": "bullets"}, {"title": "감사합니다", "layout": "closing"}]
    padded = deck.pad_to_floor(plan, 6)
    assert [p["title"] for p in padded] == [
        "표지", "배경", "방법", "핵심 정리", "감사합니다", "질의응답"
    ]
    assert deck.pad_to_floor(plan, 5)[3]["title"] == "핵심 정리"
    assert deck.pad_to_floor(plan, 8) == plan  # short by more: left alone
    assert deck.pad_to_floor(plan, 4) == plan


def test_a_figured_slide_keeps_words_and_the_agenda_drops_written_numbers():
    from app.services import deck

    steps = [{"title": "프롬프트 주입", "body": "지시를 바꾼다"},
             {"title": "데이터 유출", "body": "기밀을 내보낸다"}]
    slide = {"layout": "steps", "title": "주요 위협", "steps": steps,
             "diagram": {"description": "AI 에이전트 위협은 네 갈래로 나뉜다."}}
    deck._words_under_figure(slide)
    assert slide["bullets"][:2] == ["프롬프트 주입: 지시를 바꾼다", "데이터 유출: 기밀을 내보낸다"]
    told = "요청은 게이트웨이를 거친다. 응답은 로그에 남는다."
    bare = {"layout": "diagram", "title": "구조", "diagram": {"description": told}}
    deck._words_under_figure(bare)
    assert bare["bullets"] == ["요청은 게이트웨이를 거친다.", "응답은 로그에 남는다."]
    slides = [{"layout": "section", "title": "01. 시장 구조"},
              {"layout": "section", "title": "02) 핵심 취약점"}]
    assert deck._agenda_lines(slides) == ["시장 구조", "핵심 취약점"]


def test_a_slide_amount_a_power_of_ten_off_the_report_is_put_back():
    from app.services import deck, quality_gate

    material = "구독 의향 10%인 12만 가구가 월 2회, 회당 3만 원을 쓰면 연 864억 원입니다."
    slides = [
        {"id": "a", "title": "시장", "bullets": ["구독 의향 1.2만 가구", "연 864억 원"],
         "notes": "12만 가구 기준입니다."},
        {"id": "b", "title": "손익", "bullets": ["손익분기 2,500박스", "구독 의향 12만 가구"]},
    ]
    assert quality_gate.scaled_numbers(slides, material) == [(0, "1.2만 가구", "12만 가구")]
    assert deck.mend_scaled_numbers(slides, material) == [0]
    assert slides[0]["bullets"][0] == "구독 의향 12만 가구"
    # An amount the material does not have at any power of ten (2,500박스) is the slide's own.
    assert slides[1]["bullets"] == ["손익분기 2,500박스", "구독 의향 12만 가구"]


def test_a_part_asked_for_but_only_named_on_its_slide_is_sent_back():
    from app.services import deck

    lesson = ("1차시 수업용 슬라이드(45분)를 만들어 줘. "
              "도입 질문, 개념 설명, 활동, 정리 퀴즈 순서로.")
    slides = [
        {"title": "원소와 화합물"},
        {"title": "발표 순서", "bullets": ["도입 질문", "개념 설명", "활동", "정리 퀴즈"]},
        {"title": "정리 퀴즈", "bullets": ["원소는 한 종류의 원자로 이루어진 물질"],
         "notes": "문항 1: 산소는 원소입니까? 정답은 원소."},
    ]
    needs = deck.requested_contents(slides, lesson)
    assert set(needs) == {1, 2} and "퀴즈" in needs[2] and "45분" in needs[1]
    # Questions on screen (a Korean question ending, no 「?」) and a time plan: nothing to do.
    slides[2]["bullets"] = ["O는 원소입니까, 화합물입니까", "CO2의 2가 뜻하는 것은 무엇입니까"]
    slides[1]["bullets"] = ["도입 5분", "개념 15분", "활동 15분", "정리 10분"]
    assert deck.requested_contents(slides, lesson) == {}

    board = "경영진 보고 자료를 만들어 줘. 결론 먼저, 시장, 수익성, 요청 사항 순서로."
    slides = [
        {"title": "수익성", "bullets": ["월 판매량이 늘며 5월에 흑자"]},
        {"title": "요청 사항", "bullets": ["초기 자금 확보"], "notes": "7,000만 원 승인을 요청"},
    ]
    assert set(deck.requested_contents(slides, board)) == {0, 1}
    slides[0]["bullets"].append("손익분기 2,500박스, 월 영업이익 1,800만 원")
    slides[1]["bullets"] = ["초기 자금 7,000만 원 승인 요청 (6월 30일까지)"]
    assert deck.requested_contents(slides, board) == {}


def test_a_slide_the_writer_gave_up_on_is_reported_not_dropped_quietly():
    """A slide left as 「이 장을 쓰지 못했습니다」 is reported, not silently dropped."""
    from app.services import deck, quality_gate

    slides = [{"title": "주요 플랫폼 비교", "body": deck.UNWRITTEN, "layout": "bullets"}]
    assert any(f["code"] == "unwritten_slide" for f in quality_gate.deck_findings(slides))
