"""검색 결과는 질문의 낱말을 더 많이 담은 것이 앞에 온다."""

from app.services.tools.builtin import _rank, _terms


def _row(title: str, url: str, snippet: str = "") -> dict[str, str]:
    return {"title": title, "url": url, "snippet": snippet}


def test_a_statistics_site_that_matches_one_word_goes_below_the_paper() -> None:
    rows = [
        _row("Social Blade - YouTube, Instagram Statistics", "https://socialblade.com/"),
        _row(
            "Social Media and Depression Symptoms: a Meta-Analysis",
            "https://link.springer.com/article/10.1007/s10802-020-00715-7",
            "adolescents social media use depressive symptoms",
        ),
        _row("Rockstar Games Social Club", "https://socialclub.rockstargames.com/"),
    ]
    ranked = _rank(rows, "social media depression meta-analysis")
    assert ranked[0]["url"].startswith("https://link.springer.com")
    # 나머지는 엔진의 순서 그대로.
    assert [r["title"][:6] for r in ranked[1:]] == ["Social", "Rockst"]


def test_a_korean_particle_does_not_hide_a_match() -> None:
    rows = [
        _row("청소년 - 나무위키", "https://namu.wiki/w/청소년"),
        _row("청소년의 SNS 과의존이 우울에 미치는 영향", "https://www.dbpia.co.kr/x", "우울 SNS"),
    ]
    ranked = _rank(rows, "청소년의 SNS 사용과 우울의 관계")
    assert ranked[0]["url"].startswith("https://www.dbpia")


def test_a_one_word_query_keeps_the_engine_order() -> None:
    rows = [_row("b", "https://b"), _row("a", "https://a")]
    assert _rank(rows, "청소년") == rows
    assert _terms("2024 \"social media\"") == ["social", "media"]


def test_results_that_share_no_word_with_the_query_are_off_topic() -> None:
    from app.services.tools.builtin import _off_topic

    junk = [
        _row("Tracking | UPS - United States", "https://www.ups.com/track"),
        _row("Package Tracking Service", "https://parcelszen.com/"),
    ]
    assert _off_topic(junk, "전고체 배터리 양산 일정 전망 2025")
    assert not _off_topic(junk + [_row("전고체 배터리 양산", "https://x")], "전고체 배터리 양산 일정")
    # 한 낱말짜리 질문은 판단하지 않는다.
    assert not _off_topic(junk, "전고체")
