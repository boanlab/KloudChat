"""논문에 넣을 도식은 이름표가 있다.

The image path draws shapes and cannot spell; the diagram path writes the
figure as mermaid, which is nothing but names and arrows. What is asserted
here is the part that does not need a model: the answer is read correctly,
colour the model was told not to write is stripped, and a starting point
says what it needs before anybody commits.
"""

from __future__ import annotations

import pytest

from app.services import diagram, prompt_templates


def test_the_source_and_the_caption_come_apart() -> None:
    text = (
        "```mermaid\n"
        "flowchart LR\n"
        '  subgraph enc["인코더"]\n'
        "    x[입력] --> h(어텐션)\n"
        "  end\n"
        "  h --> out[결과]:::hot\n"
        "  out -.->|손실| h\n"
        "```\n"
        "그림: 입력이 인코더를 거쳐 결과가 되는 흐름.\n"
    )
    source, caption = diagram._parse(text)
    assert source.startswith("flowchart LR")
    assert ":::hot" in source
    assert caption == "입력이 인코더를 거쳐 결과가 되는 흐름."


def test_colour_the_model_was_told_not_to_write_is_stripped() -> None:
    text = (
        "```mermaid\nflowchart LR\n  a --> b\n"
        "  style a fill:#f00\n  classDef hot fill:#0f0\n  linkStyle 0 stroke:red\n```\n"
    )
    source, _ = diagram._parse(text)
    assert "style" not in source
    assert "classDef" not in source
    assert "linkStyle" not in source
    assert "a --> b" in source


def test_every_built_in_says_how_to_fill_each_blank() -> None:
    # 「기간·언어」 alone is a noun. The example beside it is the instruction.
    for row in prompt_templates.all_templates():
        assert len(row.examples) == len(row.fills), row.id


def test_a_survey_needs_the_web_and_a_reading_needs_the_file() -> None:
    by_id = {row.id: row for row in prompt_templates.all_templates()}
    assert "web" in by_id["t_report_literature"].needs
    assert "file" in by_id["t_paper_read"].needs
    assert "citation" in by_id["t_report_literature"].skills


def test_an_edit_recomputes_the_findings() -> None:
    """검사 결과는 지금 있는 절만 가리킨다.

    Findings were computed once, at generation, and a report edited by hand
    kept naming sections it no longer had — so 모두 고치기 found nothing to
    rewrite and reported success. Recomputed from the text on every write.
    """
    from app.models.workspace import ArtifactKind
    from app.routers.workspace import _relint

    stale = {
        "sections": [
            {"id": "s1", "heading": "배경", "content": "여기에 내용을 입력하세요."},
        ],
        "lint": [{"severity": "P0", "code": "cjk", "message": "옛 지적", "where": "추진 계획"}],
    }
    fresh = _relint(ArtifactKind.report, stale)
    wheres = {row["where"] for row in fresh["lint"]}
    assert "추진 계획" not in wheres
    assert wheres <= {"배경", ""}


def test_a_source_without_a_publisher_does_not_fail_the_rewrite() -> None:
    """A hand-typed source has a title and a url. One missing `publisher`
    used to be a KeyError that failed every rewrite of the document."""
    from app.services.report import _refs_block

    block = _refs_block([{"title": "출처 하나", "url": "https://example.org"}])
    assert "출처 하나" in block


def test_a_draft_is_cut_along_the_headings_it_was_asked_to_write() -> None:
    """한 번에 쓴 초안은 목차의 제목 줄에서 잘린다 — 번호·콜론·띄어쓰기가 달라도."""
    from app.services.report import _split_draft

    draft = (
        "## 1. 요약\n권고는 교체입니다.\n\n"
        "## 세 대안의 비교:\n| 기준 | A |\n| --- | --- |\n\n"
        "### 결론\n이건 소제목.\n"
        "## 권고안과 다음 단계\n교체를 권고합니다.\n"
    )
    parts = _split_draft(draft, ["요약", "세 대안의 비교", "권고안과 다음 단계"])
    assert parts["요약"] == "권고는 교체입니다."
    assert parts["세 대안의 비교"].startswith("| 기준 | A |")
    # 목차에 없는 소제목은 절 안에 굵은 글씨로 남는다.
    assert "**결론**" in parts["세 대안의 비교"]
    assert parts["권고안과 다음 단계"] == "교체를 권고합니다."


def test_the_numbers_a_report_may_use_are_read_off_the_request() -> None:
    from app.services.report import _facts_line

    line = _facts_line("유지비(연 380만 원), 교체 견적(2,400만 원), 최근 6개월 4회", [])
    assert "380만원" in line and "2,400만원" in line and "6개월" in line and "4회" in line


def test_sections_named_after_alternatives_fold_into_one_comparison() -> None:
    from app.services.report import _fold_alternatives

    folded = _fold_alternatives(
        [
            "요약",
            "장애 현황과 결정할 사안",
            "기존 서버 1년 연장 사용",
            "전체 서버 교체",
            "클라우드 마이그레이션",
            "위험과 남은 문제",
            "권고안과 다음 단계",
        ],
        ["교체", "1년 연장", "클라우드"],
    )
    assert folded == [
        "요약",
        "장애 현황과 결정할 사안",
        "대안 비교",
        "위험과 남은 문제",
        "권고안과 다음 단계",
    ]
    # 비교 절이 이미 있으면 그대로 두고, 대안 이름이 없는 문서는 손대지 않는다.
    assert _fold_alternatives(["요약", "대안 비교", "권고"], ["교체"]) == [
        "요약",
        "대안 비교",
        "권고",
    ]
    assert _fold_alternatives(["요약", "배경", "결론"], []) == ["요약", "배경", "결론"]


def test_bullets_that_arrive_as_objects_become_lines() -> None:
    """모델이 불릿을 `{"left","right"}` 객체로 내면 그 값을 한 줄로 잇는다 —
    버리면 그 장이 「쓰지 못했습니다」가 된다."""
    from app.services.deck import _clean_bullets

    out = _clean_bullets(
        [{"left": "python -m venv .venv", "right": "순수 파이썬"}, ["a", "b"], "- 셋째"]
    )
    assert out == ["python -m venv .venv – 순수 파이썬", "a – b", "셋째"]


def test_a_subject_the_request_never_named_becomes_a_question() -> None:
    from app.services.report import _subject_missing

    ask = "처장님 결재용 한 장 보고를 써 주세요. 결정할 것, 대안 두 개, 권고안을 담아 주세요."
    assert _subject_missing(
        '{"title": "전산망 교체", "subject": "전산망 교체", "sections": []}', ask
    )
    assert _subject_missing('{"title": "보고", "subject": "", "sections": []}', ask)
    topical = "학과 서버 교체 여부를 정하는 보고서를 써 줘"
    assert not _subject_missing('{"subject": "학과 서버 교체", "sections": []}', topical)
    # 계획이 subject 를 말하지 않았으면 판단하지 않는다.
    assert not _subject_missing('{"title": "x", "sections": ["a"]}', ask)


def test_a_results_report_with_nothing_to_report_is_asked_for_its_data() -> None:
    from app.services.report import _results_without_data

    ask = "「신규 소재 적용 타당성 검토」 보고서를 써 주세요. 시험 방법, 결과, 위험, 권고 순서로."
    assert _results_without_data(ask, [])
    # 수치가 요청에 있거나 파일이 붙었으면 묻지 않는다.
    assert not _results_without_data(ask + " 인장 강도 420 MPa, 피로 수명 12% 향상.", [])
    assert not _results_without_data(ask, ["# 시험 성적서\n인장 강도 420"])
    # 결과를 말하지 않는 문서는 자료가 없어도 쓴다.
    assert not _results_without_data("신입생 오리엔테이션 안내문을 써 주세요.", [])
    # 자료가 있다고 말하고 붙이지 않았으면 묻는다.
    assert _results_without_data("학과 세미나 녹취를 회의록으로 바꿔 주세요.", [])
    assert not _results_without_data("학과 세미나 녹취를 회의록으로 바꿔 주세요.", ["녹취: …"])
    # 동향·문헌처럼 검색으로 쓰는 문서는 자료를 묻지 않는다.
    assert not _results_without_data("PEFT 최근 1년 동향 분석 보고서를 써 주세요.", [])
    # 「초안을 써 주세요」는 자료가 있다는 말이 아니다. 학위논문 장은 그 사람의 연구가 있어야 쓴다.
    assert not _results_without_data("행사 안내문 초안을 써 주세요.", [])
    assert _results_without_data("학위논문 3장 「제안 방법」 초안을 써 주세요.", [])


def test_the_cost_table_advice_is_kept_for_decisions() -> None:
    from app.services.report import _facts_line

    assert "비교표의 행" in _facts_line("서버 교체와 클라우드 이전 대안을 비교해 주세요.", [])
    assert "비교표의 행" not in _facts_line("학과 세미나 회의록을 써 주세요.", [])
    assert "쓰지 마라" in _facts_line("학과 세미나 회의록을 써 주세요.", [])


def test_material_pasted_into_the_request_is_not_asked_for() -> None:
    from app.services.report import _results_without_data

    memo = (
        "아래 랩 미팅 메모를 회의록으로 바꿔 주세요.\n"
        + "- 교수: 논문 4장 그림 3 축 라벨 빠짐.\n" * 12
    )
    assert not _results_without_data(memo, [])
    assert not _results_without_data(
        "표를 붙입니다.\n| 학과 | 2024 |\n|---|---|\n| 컴공 | 312 |", []
    )


def test_a_slide_that_retells_earlier_figures_is_dropped_and_the_table_wins() -> None:
    from app.services.deck import _retold

    slides = [
        {"title": "개편안", "layout": "title"},
        {"title": "변경 사항", "layout": "two-column"},
        {"title": "증액 수치", "layout": "metrics"},
        {"title": "비교 요약", "layout": "table"},
        {"title": "유지 원칙", "layout": "quote"},
    ]
    drafted = {
        1: {
            "layout": "two-column",
            "bullets": [
                "학자금 연 500만 원→700만 원",
                "재택 주 1일→2일",
                "경조사비 100만 원→150만 원",
            ],
        },
        2: {
            "layout": "metrics",
            "metrics": [["700만 원", "학자금"], ["150만 원", "경조사비"], ["2일", "재택"]],
        },
        3: {
            "layout": "table",
            "rows": [
                ["구분", "기존", "변경"],
                ["학자금", "500만 원", "700만 원"],
                ["재택", "주 1일", "주 2일"],
                ["경조사비", "100만 원", "150만 원"],
            ],
        },
        4: {"layout": "quote", "body": "연차 15일, 식대 월 20만 원, 상여 각 50만 원은 그대로"},
    }
    assert _retold(slides, drafted) == {1, 2}


def test_a_timeline_padded_with_invented_steps_becomes_bullets() -> None:
    from app.services.deck import _split_deck_draft

    request = "복지제도 개편안. 바뀌는 점(2027년 1월 1일부터): 재택 주 2일."
    slides = [{"title": "적용 시점", "layout": "timeline"}]
    draft = (
        '{"slides":[{"title":"적용 시점","layout":"timeline","timeline":['
        '["2027년 1월 1일","규정 발효"],["매주 월요일","일정 제출"],["분기별","점검"]]}]}'
    )
    out = _split_deck_draft(draft, slides, set(), request)
    assert out[0]["layout"] == "bullets" and "timeline" not in out[0]
    assert out[0]["bullets"][0].startswith("2027년 1월 1일")
    # 요청에 시점이 둘 이상 있으면 그것만 남긴다.
    request2 = request + " 9월 15일 초안 리뷰, 9월 20일 마감."
    draft2 = (
        '{"slides":[{"title":"적용 시점","layout":"timeline","timeline":['
        '["2027년 1월 1일","발효"],["9월 15일","초안 리뷰"],["매주 월요일","제출"]]}]}'
    )
    out2 = _split_deck_draft(
        draft2, [{"title": "적용 시점", "layout": "timeline"}], set(), request2
    )
    assert [t[0] for t in out2[0]["timeline"]] == ["2027년 1월 1일", "9월 15일"]


def test_years_in_a_table_header_do_not_make_it_a_new_slide() -> None:
    from app.services.deck import _claims

    assert _claims(
        {"rows": [["혜택", "2026년", "2027년"], ["학자금", "500만 원", "700만 원"]]}
    ) == {
        "500만원",
        "700만원",
    }


def test_the_requests_data_table_is_carried_into_the_results() -> None:
    from app.services.report import _carry_table

    request = (
        "실험 보고서.\n| f (Hz) | Vout |\n|---|---|\n| 100 | 1.99 |\n| 1000 | 1.71 |\n이론 fc."
    )
    headings = ["목적", "결과", "오차 분석"]
    drafted = {"목적": "목적입니다.", "결과": "100 Hz에서 1.99…", "오차 분석": "오차."}
    out = _carry_table(request, headings, dict(drafted))
    assert out["결과"].startswith("측정 데이터는 다음과 같습니다.\n\n| f (Hz) | Vout |")
    assert out["결과"].endswith("100 Hz에서 1.99…")
    # 초안에 이미 표가 있으면 그대로.
    with_table = {**drafted, "결과": "| f | g |\n|---|---|\n| 100 | 1 |"}
    assert _carry_table(request, headings, dict(with_table)) == with_table
    # 요청에 표가 없으면 그대로.
    assert _carry_table("표 없는 요청", headings, dict(drafted)) == drafted


@pytest.mark.asyncio
async def test_a_rewrite_may_use_the_numbers_the_document_already_has(monkeypatch) -> None:
    from app.services import report

    seen: list[str] = []

    async def complete(_model, messages, _key, _max):
        seen.append(messages[-1]["content"])
        return "다시 쓴 절", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    sections = [
        {"id": "a", "heading": "비용", "content": "시험 기간 운영은 연 1,200만 원이다."},
        {"id": "b", "heading": "권고안", "content": "대안 1을 권고한다. 연간 120만 원 절감."},
    ]
    await report.rewrite_section(
        request="열람실 야간 운영",
        heading="권고안",
        sections=sections,
        target_id="b",
        model="m",
        api_key="k",
        note="근거를 숫자로 앞에.",
    )
    assert "1,200만원" in seen[0] and "120만원" in seen[0]
    assert "수치가 하나도 없다" not in seen[0]


def test_a_subject_read_off_the_attachment_is_not_invented() -> None:
    from app.services.grounding import subject_missing

    ask = "첨부한 녹취를 회의록으로 바꿔 주세요."
    plan = '{"title": "교육과정위원회 회의록", "subject": "교육과정위원회", "sections": []}'
    assert subject_missing(plan, ask)
    assert not subject_missing(plan, ask, "2026-08-28 학과 교육과정위원회 녹취 …")


def test_a_kpi_block_of_placeholders_is_dropped_and_a_real_one_kept() -> None:
    from app.services.report import _grounded_figures

    empty = "본문.\n\n```kpi\n(미정) | 산학 프로젝트 확보 건수\n(미정) | 겸임 교원\n```\n\n끝."
    assert _grounded_figures(empty, True) == "본문.\n\n끝."
    real = "본문.\n\n```kpi\n32% | 오탐 감소\n1.4초 | 평균 응답 시간\n```\n\n끝."
    assert _grounded_figures(real, True) == real


def test_a_slide_that_says_the_same_thing_in_other_words_is_dropped() -> None:
    from app.services.deck import _retold

    slides = [
        {"title": "표지", "layout": "title"},
        {"title": "캡스톤 학점 증설 논의", "layout": "bullets"},
        {"title": "캡스톤 분할안과 조건", "layout": "bands"},
        {"title": "다음 회의 일정", "layout": "bullets"},
    ]
    drafted = {
        1: {
            "layout": "bullets",
            "bullets": [
                "캡스톤디자인 3학점을 6학점으로 증설하는 안 논의",
                "4학년 1학기 전공 선택 과목 감소 우려 제기",
                "산학 프로젝트 감소로 무임승차 우려",
                "절충안으로 캡스톤 1·2 분할 제안, 산학 프로젝트 확보 조건",
            ],
        },
        2: {
            "layout": "bands",
            "bands": [
                ["분할안 채택", "캡스톤디자인을 3-2와 4-1학기로 나누어 3+3학점으로 잠정 채택"],
                ["확보 조건", "산학 프로젝트 확보 여부를 확인한 뒤 10월 회의에서 확정"],
                ["문제 완화", "4학년 1학기 전공 선택 과목 감소와 무임승차 우려 완화"],
            ],
        },
        3: {
            "layout": "bullets",
            "bullets": [
                "다음 회의 10월 16일 오후 2시",
                "박 교수 10월 9일까지 세부 초안",
                "교학팀에 겸임 교원 요청",
            ],
        },
    }
    # 같은 말을 bands 로 다시 한 장이 뒤에 오면 bullets 쪽이 빠진다. 일정 장은 남는다.
    assert _retold(slides, drafted) == {1}


def test_an_english_request_gets_an_english_rule_and_a_korean_one_none() -> None:
    from app.models.chat import SessionKind
    from app.services.context import build_document_messages, language_rule

    english = (
        "Write a one-page decision memo for the department head on whether to renew "
        "our annual JMP licence (30 seats, $4,800/yr) or switch to R."
    )
    assert "entire output in English" in language_rule(english)
    assert language_rule("학과 서버 교체 여부를 정하는 보고서를 써 주세요.") == ""
    # 한국어 요청에 영어 낱말이 섞여도 한국어다.
    assert language_rule("PEFT 기법 LoRA, Adapter, Prefix Tuning 동향을 정리해 주세요.") == ""
    messages = build_document_messages(SessionKind.report, "prompt", request=english)
    assert "entire output in English" in messages[0]["content"]


def test_an_english_question_puts_the_english_rule_in_the_chat_system_turn() -> None:
    from app.models.chat import SessionKind
    from app.services.context import build_messages

    history = [
        {"role": "user", "content": "학습률 웜업이 왜 필요한가요?"},
        {"role": "assistant", "content": "초기 불안정을 줄입니다."},
        {
            "role": "user",
            "content": "We are a small biology lab. Explain what a false discovery rate is "
            "and when we should use it instead of Bonferroni correction.",
        },
    ]
    messages = build_messages(SessionKind.chat, history)
    assert "entire answer in English" in messages[0]["content"]
    korean = build_messages(SessionKind.chat, history[:1])
    assert "in English" not in korean[0]["content"].split("글 쓰는 법")[0]


def test_a_bullet_with_a_quantity_the_request_never_gave_is_dropped() -> None:
    from app.services.deck import _split_deck_draft, _unrequested_quantity

    request = "캡스톤 중간발표 10분 슬라이드. 센서 3종 연동 완료, 대시보드 70%, 11월 말 시연."
    assert _unrequested_quantity("교내 강의실 11개소를 선정하여 설치합니다", request)
    assert _unrequested_quantity("10분 간격으로 데이터를 수집합니다", request) is False
    assert not _unrequested_quantity("센서 3종 연동을 완료했습니다", request)
    assert not _unrequested_quantity("2026년 계획", request)
    slides = [{"title": "다음 단계", "layout": "bullets"}]
    draft = (
        '{"slides":[{"title":"다음 단계","layout":"bullets","bullets":['
        '"11월 시연 준비","교내 강의실 11개소 선정","대시보드 70% → 완성"]}]}'
    )
    out = _split_deck_draft(draft, slides, set(), request)
    assert out[0]["bullets"] == ["11월 시연 준비", "대시보드 70% → 완성"]


def test_a_rewrite_does_not_borrow_another_sections_table() -> None:
    from app.services.report import _without_borrowed_tables

    table = "| 기준 | 교체 | 클라우드 |\n|---|---|---|\n| 첫해 | 2,780만 원 | 744만 원 |"
    body = f"현황입니다.\n\n{table}\n\n그래서 셋을 견줍니다."
    # 다른 절에 같은 표가 있으면 뺀다.
    assert _without_borrowed_tables(body, "", [f"비교.\n\n{table}"], "근거를 보강") == (
        "현황입니다.\n\n그래서 셋을 견줍니다."
    )
    # 원래 표가 없었고 표를 달라고도 안 했으면 뺀다.
    assert "|" not in _without_borrowed_tables(body, "현황 줄글", [], "근거를 보강")
    # 표를 달라고 했으면 남긴다.
    assert table in _without_borrowed_tables(body, "현황 줄글", [], "표로 정리해 줘")


def test_a_request_for_many_pages_is_long_form() -> None:
    from app.services.report import _long_form

    assert _long_form("「교육과정 개편 백서」 초안을 15장 이상 분량으로 써 주세요.")
    assert _long_form("20쪽 내외 보고서")
    assert not _long_form("한 장짜리 결재 보고")
    assert not _long_form("슬라이드 5장 분량")


def test_a_section_that_repeats_its_own_heading_loses_the_repeat() -> None:
    from app.services.report import _without_own_heading

    assert _without_own_heading("## 현행 진단\n\n본문.", "현행 진단") == "본문."
    assert _without_own_heading("**현행 진단**\n본문.", "현행 진단") == "본문."
    assert _without_own_heading("본문부터.", "현행 진단") == "본문부터."
    assert _without_own_heading("## 다른 제목\n본문.", "현행 진단") == "## 다른 제목\n본문."


def test_a_slide_left_with_nothing_after_its_chart_is_dropped_is_written_again() -> None:
    from app.services.deck import _split_deck_draft

    slides = [{"title": "상태 전이", "layout": "chart"}, {"title": "비용", "layout": "metrics"}]
    draft = (
        '{"slides":[{"title":"상태 전이","layout":"chart","chart":{"series":[{"values":[3,5]}]},'
        '"notes":"노트"},{"title":"비용","layout":"metrics","metrics":[["75","총 소요 시간"]]}]}'
    )
    out = _split_deck_draft(draft, slides, {"75"}, "75분 강의")
    # 지어낸 차트가 빠지고 남은 것이 없으면 초안에서 빠져 따로 쓴다.
    assert 0 not in out
    # 지표 하나짜리 metrics 는 metrics 가 아니다.
    assert 1 not in out or out[1].get("layout") != "metrics"


def test_money_in_a_document_with_no_figures_becomes_undetermined() -> None:
    from app.services.report import _without_invented_money

    text = (
        "| 저랭크 어댑터 | 380만 원 | 380만 원 × 3 = 1,140만 원 |\n"
        "연 2,400 만 원이 든다. 2026년 계획."
    )
    out = _without_invented_money(text)
    assert "380만 원" not in out and "1,140만 원" not in out and "2,400 만 원" not in out
    assert out.count("(미정)") == 4 and "2026년" in out


def test_a_proposal_with_nothing_behind_it_is_asked_about() -> None:
    from app.services.report import _results_without_data

    assert _results_without_data("캡스톤 팀에 낼 한 장짜리 설계 변경 제안서를 써 주세요.", [])
    assert not _results_without_data(
        "설계 변경 제안서: 모터를 24V에서 48V로 바꿔 배선 손실 30% 절감, 부품비 12만 원 증가.", []
    )
