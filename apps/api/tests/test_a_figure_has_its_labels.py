"""논문에 넣을 도식은 이름표가 있다.

The image path draws shapes and cannot spell; the diagram path writes the
figure as mermaid, which is nothing but names and arrows. What is asserted
here is the part that does not need a model: the answer is read correctly,
colour the model was told not to write is stripped, and a starting point
says what it needs before anybody commits.
"""

from __future__ import annotations

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
    assert "file" in by_id["t_translate"].needs
    assert "인용 형식 맞추기" in by_id["t_report_literature"].skills


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
