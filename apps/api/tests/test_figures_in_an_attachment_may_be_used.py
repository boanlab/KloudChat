"""Figures from an attached transcript or table usable in the document."""

from __future__ import annotations

from app.services import deck, report

TRANSCRIPT = (
    "한지수: 파일럿 4개 부서 742명 중에 지난주 실제 검색한 사람이 518명이에요. 하루 평균 검색 "
    "2,340건이고, p95 응답시간이 2.8초에서 1.9초로 내려왔습니다."
)


def test_a_report_may_use_the_figures_an_attachment_gives() -> None:
    line = report._facts_line("첨부한 녹취로 주간 업무 보고서를 써 줘.", [], TRANSCRIPT)
    assert "none" not in line
    assert "742명" in line and "518명" in line and "2,340건" in line and "1.9초" in line


def test_a_report_with_no_figures_anywhere_still_writes_none() -> None:
    assert "write no amounts" in report._facts_line("회의록을 써 주세요.", [], "참석자 의견 정리")


async def test_the_report_writer_is_told_the_transcripts_figures(monkeypatch):
    async def litellm_config():
        return "http://mock-litellm", "unused"

    async def unavailable():
        return False

    prompts: list[str] = []

    async def complete(_model, messages, _key, max_tokens=0, **_):
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        prompts.append(prompt)
        return "파일럿 742명 중 518명이 검색했습니다.", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(report.research, "available", unavailable)
    monkeypatch.setattr(report, "_complete", complete)
    approved = {"title": "TF 주간 업무 보고", "sections": ["이번 주 성과", "다음 주 계획"]}
    [e async for e in report.write(request="첨부한 회의 녹취로 주간 업무 보고서를 써 줘.",
                                   model="m", api_key="k", approved_plan=approved,
                                   untrusted_context=[TRANSCRIPT], web_search=False)]
    told = [line for p in prompts for line in p.splitlines() if line.startswith("쓸 수 있는 수치")]
    assert told, "the writer got a figures line"
    assert all("518명" in line and "none" not in line for line in told)


def test_a_deck_may_use_the_figures_an_attachment_gives() -> None:
    line = deck._facts_line("첨부한 지표로 본부장 보고 슬라이드 만들어 줘.", [TRANSCRIPT])
    assert "do not use metrics" not in line
    assert "518명" in line and "2,340건" in line
    both = deck._facts_line("오픈 10/28로 변경 승인 요청 슬라이드 8장", [TRANSCRIPT])
    assert "every one in the request" in both and "518명" in both.split("attached material")[1]
    assert "do not use metrics" in deck._facts_line("발표 슬라이드 만들어 줘.", ["참석자 의견"])
