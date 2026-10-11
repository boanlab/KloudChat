"""A research plan, a paper and a term report are written in 「~다」, the rest in 「~합니다」."""

from app.services import report

_PLAN = "한국연구재단 개인기초연구 연구계획서 초안을 써 줘."


def _section_prompt(genre: str) -> str:
    return report._shaped(report._SECTION_PROMPT.format(
        heading="연구 목표", outline="1. 연구 목표", written="(nothing yet)", refs="",
        request=_PLAN, role="", blocks="", others="", facts="", genre=genre), genre)


def test_a_research_plan_is_written_in_the_plain_register():
    # A 연구계획서 is written in 「~다」, not 「~합니다」.
    genre = report._genre_rule(_PLAN)
    assert "연구·조사 계획서" in genre
    for prompt in (_section_prompt(genre), report._shaped(report._DRAFT_PROMPT.format(
            outline="## 연구 목표", refs="", facts="", genre=genre, request=_PLAN), genre)):
        assert "「~다」 (「제안한다」" in prompt
        assert "Every sentence ends in the 「~합니다」" not in prompt
        assert "the first section uses 「~합니다」" not in prompt
        assert prompt.count("One register for the whole document") == 1


def test_a_paper_and_a_term_report_too_but_not_a_business_report():
    assert report._PLAIN_REGISTER in report._genre_rule("학회 논문 초안 써 줘")
    assert report._PLAIN_REGISTER in report._genre_rule("기말 리포트 써 줘")
    genre = report._genre_rule("주간 업무 보고 써 줘")
    assert report._PLAIN_REGISTER not in genre
    assert "Every sentence ends in the 「~합니다」" in _section_prompt(genre)
