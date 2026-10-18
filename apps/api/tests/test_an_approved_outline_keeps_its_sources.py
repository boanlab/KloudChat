"""An approved outline's planning pages kept as the writing sources."""

from __future__ import annotations

import pytest
from test_documents_are_researched import _Client, _user_blocks

from app.pipeline import report_stages
from app.services import report, research


def _page(n: int, host: str, body: str) -> tuple[dict, str]:
    url = f"https://{host}/p{n}"
    source = {"id": f"src{n}", "ordinal": n, "title": f"{host} 자료", "publisher": host,
              "url": url, "origin": "web", "originLabel": "웹 검색", "quote": body[:40]}
    return source, f"[{n}] {host} 자료\n출처: {url}\n본문 발췌:\n{body}"


_OUTLINE_PAGES = [
    _page(1, "digitalmarket.kr", "디지털서비스 이용지원시스템(디지털서비스몰) 등록 절차: "
          "서비스 등록 신청 → 심사 → 카탈로그 게시, 공공기관 수의계약 조달."),
    _page(2, "mois.go.kr", "행정안전부 공공부문 클라우드 전환 정책, 민간 클라우드 우선 이용."),
    _page(3, "isms-p.or.kr",
          "클라우드 보안인증(CSAP) 상·중·하 등급 체계 개편, 하 등급 논리적 망분리."),
]

#: An approved outline.
_HEADINGS = [
    "조사 배경과 범위",
    "공공부문 클라우드 전환 정책 흐름 (행정안전부·과기정통부)",
    "클라우드 보안인증(CSAP) 제도 및 등급 체계 개편",
    "디지털서비스 이용지원 및 조달 경로",
    "출처 및 확인 필요 사항",
]


def _outline_findings() -> research.Findings:
    return research.Findings(
        sources=[s for s, _ in _OUTLINE_PAGES],
        context="\n\n".join(block for _, block in _OUTLINE_PAGES),
        queries=["과학기술정보통신부 디지털서비스몰 등록 기준 심사 절차 2025"],
        searched=True,
    )


def test_the_proposal_carries_the_pages_and_the_browser_does_not_see_them():
    findings = _outline_findings()
    findings.sources.append({"id": "p", "ordinal": 4, "title": "사내 파일", "url": "",
                             "origin": "file", "originLabel": "프로젝트 파일"})
    stored = research.carried(findings, web_count=3)
    assert [s["publisher"] for s in stored["sources"]] == [
        "digitalmarket.kr", "mois.go.kr", "isms-p.or.kr"]
    back = research.from_carried(stored)
    assert back is not None and back.searched and "카탈로그 게시" in back.context
    plan = {"title": "t", "sections": _HEADINGS, report_stages.PLAN_RESEARCH: stored}
    assert report_stages.PLAN_RESEARCH not in report_stages.public_plan(plan)
    assert research.from_carried(None) is None
    assert research.from_carried({"sources": []}) is None


def test_headings_the_pages_cover_are_not_searched_again():
    assert research.uncovered(_HEADINGS, _outline_findings().context) == []
    # spacing differs on the page: still covered
    assert research.uncovered(["디지털서비스몰 등재 요건"], "디지털 서비스몰 등재 요건은") == []
    assert research.uncovered(["학령인구 학생 수 추이"], _outline_findings().context) == [
        "학령인구 학생 수 추이"]


def test_merge_keeps_the_first_shelf_and_numbers_new_pages_after_it():
    more = research.Findings(
        sources=[{**_OUTLINE_PAGES[0][0]},  # same URL: counted once
                 {"id": "n", "ordinal": 2, "title": "새 자료", "publisher": "kosis.kr",
                  "url": "https://kosis.kr/x", "origin": "web", "originLabel": "웹 검색"}],
        context=_OUTLINE_PAGES[0][1] + "\n\n[2] 새 자료\n출처: https://kosis.kr/x\n학생 수 52만 명",
        queries=["학생 수"],
        searched=True,
    )
    merged = research.merge(_outline_findings(), more)
    assert [s["url"] for s in merged.sources[:3]] == [s["url"] for s, _ in _OUTLINE_PAGES]
    assert merged.sources[3]["ordinal"] == 4 and merged.sources[3]["publisher"] == "kosis.kr"
    assert "[4] 새 자료" in merged.context and merged.context.count("digitalmarket.kr/p1") == 1


@pytest.fixture
def gateway(monkeypatch):
    async def litellm_config():
        return "http://mock-litellm", "unused"

    for module in (report, research):
        monkeypatch.setattr(module.settings_store, "litellm_config", litellm_config)
    calls: list[str] = []

    async def available():
        return True

    async def run(request, **_kwargs):
        calls.append(request)
        return research.Findings(
            sources=[{"id": "x", "ordinal": 1, "title": "재검색", "publisher": "comworld.co.kr",
                      "url": "https://comworld.co.kr/a", "origin": "web",
                      "originLabel": "웹 검색"}],
            context="[1] 재검색\n출처: https://comworld.co.kr/a\n학령인구 학생 수 추이 자료",
            queries=["학생 수"], searched=True)

    monkeypatch.setattr(report.research, "available", available)
    monkeypatch.setattr(report.research, "run", run)
    return monkeypatch, calls


async def test_writing_the_approved_outline_uses_the_outline_pages(gateway):
    patch, calls = gateway
    posts: list[dict] = []
    patch.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(["본문"] * 8, posts, **kw))
    approved = {"title": "공공 SaaS 정책 동향", "sections": _HEADINGS,
                report_stages.PLAN_RESEARCH: research.carried(_outline_findings(), 3)}
    events = [e async for e in report.write(request="공공기관 SaaS 정책 동향 보고서",
                                            model="m", api_key="k", approved_plan=approved)]
    assert calls == [], "every heading was covered: no second search"
    shelf = next(e["sources"] for e in events if e["type"] == "sources")
    assert [s["publisher"] for s in shelf] == ["digitalmarket.kr", "mois.go.kr", "isms-p.or.kr"]
    assert any("카탈로그 게시" in _user_blocks(p) for p in posts)


async def test_a_heading_the_pages_miss_is_searched_and_merged(gateway):
    patch, calls = gateway
    posts: list[dict] = []
    patch.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(["본문"] * 8, posts, **kw))
    approved = {"title": "t", "sections": [*_HEADINGS, "학령인구 학생 수 추이"],
                report_stages.PLAN_RESEARCH: research.carried(_outline_findings(), 3)}
    events = [e async for e in report.write(request="에듀테크 시장 보고서", model="m",
                                            api_key="k", approved_plan=approved)]
    assert len(calls) == 1 and calls[0].startswith("자료를 더 찾을 부분: 학령인구 학생 수 추이")
    shelf = next(e["sources"] for e in events if e["type"] == "sources")
    assert [s["publisher"] for s in shelf] == [
        "digitalmarket.kr", "mois.go.kr", "isms-p.or.kr", "comworld.co.kr"]


async def test_the_outline_pass_stores_its_pages_on_the_proposal(gateway):
    patch, _calls = gateway
    posts: list[dict] = []
    replies = ['{"title": "제목", "sections": ["가", "나", "다"]}']
    patch.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(replies, posts, **kw))
    events = [e async for e in report.write(request="보고서", model="m", api_key="k")]
    plan = next(e["plan"] for e in events if e["type"] == "proposal")
    stored = plan[report_stages.PLAN_RESEARCH]
    assert [s["url"] for s in stored["sources"]] == ["https://comworld.co.kr/a"]
    assert "학생 수 추이" in stored["context"]
