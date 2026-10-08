"""고객 제안서, 비교·분석 보고서, 경력기술서: what is detected, which 서식 fixes it, and the
genre rules each brings."""

from __future__ import annotations

import pytest

from app.services import design_templates, doc_formats, report


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("사업 제안서 써 줘", "client_proposal"),
        ("고객 제안서 초안 만들어 줘", "client_proposal"),
        ("고객 맞춤 제안서를 써 줘", "client_proposal"),
        ("첨부한 RFP에 맞춰 제안서 작성해 줘", "client_proposal"),
        ("○○시청 제안요청서에 맞춘 제안서", "client_proposal"),
        ("제안서를 써 줘. 고객사는 한빛물류야", "client_proposal"),
        ("협업 툴 비교 분석 보고서 써 줘", "comparison"),
        ("세 업체 비교·검토 보고서 만들어 줘", "comparison"),
        ("백업 솔루션 도입 비교 보고서", "comparison"),
        ("대안 비교해서 하나 골라 줘", "comparison"),
        ("경력기술서 만들어 줘", "career_history"),
        ("경력 기술서 써 줘", "career_history"),
        # The capstone proposal stays the student form.
        ("캡스톤 사업 제안서 써 줘", "proposal"),
        ("프로젝트 제안서 초안", "proposal"),
        ("제안서 써 줘", "proposal"),
        ("기획서 써 줘", "proposal"),
        # Neighbours keep their own format.
        ("연구 제안서 써 줘", "plan"),
        ("졸업 작품 계획서 써 줘", "plan"),
        ("아래 내용으로 회로실험 결과 보고서를 써 줘.", "lab"),
    ],
)
def test_the_request_names_its_document(request_text: str, expected: str):
    assert doc_formats.detect(request_text).id == expected


def test_a_cover_letter_and_a_comparison_question_are_not_documents():
    assert doc_formats.detect("자기소개서 1번 문항 700자로 써 줘") is None
    assert doc_formats.detect("두 요금제 차이가 뭐야?") is None


@pytest.mark.parametrize(
    ("template_id", "format_id"),
    [
        ("doc-client-proposal", "client_proposal"),
        ("doc-comparison", "comparison"),
        ("doc-career-history", "career_history"),
    ],
)
def test_each_template_fixes_its_format(template_id: str, format_id: str):
    template = design_templates.get(template_id)
    assert template is not None and template.surface.value == "report"
    assert template.form_file and template.docx_template
    # The 서식 wins over the words and over an agent's bound format.
    assert doc_formats.detect("회로실험 결과 보고서", template_id).id == format_id
    assert doc_formats.detect("회로실험 결과 보고서", template_id, "minutes").id == format_id


def test_an_agents_format_wins_over_the_words_when_the_template_fixes_none():
    assert doc_formats.detect("회의록 정리해 줘", None, "comparison").id == "comparison"
    assert doc_formats.detect("회의록 정리해 줘", "doc-report", "comparison").id == "comparison"
    assert doc_formats.detect("회의록 정리해 줘", None, "not-a-format").id == "minutes"


def test_the_heads_carry_the_business_fields_not_the_student_ones():
    proposal = doc_formats.BY_ID["client_proposal"]
    assert proposal.head == "cover"
    assert {"고객사", "제안사", "제안일", "담당자", "연락처", "유효기간"} <= set(proposal.fields)
    assert not {"팀명", "지도교수"} & set(proposal.fields)
    comparison = doc_formats.BY_ID["comparison"]
    assert {"작성일", "작성자", "검토 대상"} <= set(comparison.fields)
    career = doc_formats.BY_ID["career_history"]
    assert career.numbering == "none"
    assert {"이름", "지원 회사", "지원 직무"} <= set(career.fields)
    block = doc_formats.title_block(proposal, {"fields": {"고객사": "지어낸 회사"}}, "제안서 써 줘")
    # A value the request never gave is a blank, not an invention.
    assert dict(block["fields"])["고객사"] == ""


@pytest.mark.parametrize(
    ("format_id", "musts"),
    [
        (
            "client_proposal",
            ("RFP", "요구 ID", "공급가액·부가세·합계", "never invent them", "「협의」"),
        ),
        ("comparison", ("weights", "scoring formula", "출처별 차이", "조건 미확인", "자료 없음")),
        (
            "career_history",
            ("YYYY.MM–YYYY.MM", "never inflated", "블라인드", "table inside a table"),
        ),
    ],
)
def test_each_format_brings_its_genre_rules(format_id: str, musts: tuple[str, ...]):
    rule = report._genre_rule("아무 문서", doc_formats.BY_ID[format_id])
    for must in musts:
        assert must in rule, (format_id, must)
    # The words reach the same rule without a 서식.
    request = {
        "client_proposal": "사업 제안서 써 줘",
        "comparison": "협업 툴 비교 분석 보고서 써 줘",
        "career_history": "경력기술서 만들어 줘",
    }[format_id]
    assert report._genre_rule(request) == rule


def test_the_capstone_genre_no_longer_claims_a_business_proposal():
    assert report._genre_rule("사업 제안서 써 줘").startswith("Genre: 고객 제안서")
    assert report._genre_rule("캡스톤 프로젝트 제안서 써 줘").startswith("Genre: 프로젝트 제안서")


def test_every_mapped_template_exists():
    for template_id, format_id in doc_formats.TEMPLATE_FORMATS.items():
        assert design_templates.get(template_id) is not None, template_id
        assert format_id in doc_formats.BY_ID
