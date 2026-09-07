"""An explicit short document is not enlarged to the default outline size."""

from __future__ import annotations

import io
import json
import subprocess
import sys

import httpx
import pytest
from pptx import Presentation
from pypdf import PdfReader

from app.services import deck, deck_export, report


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("반드시 4장: 표지, 신청 단계, 역할 비교, 요약", 4),
        ("표지 포함4장으로 만들어 줘", 4),
        ("세 장짜리 발표", 3),
        ("한 장으로 핵심만", 1),
        ("2026년 3분 발표", None),
        ("3~5장 발표", None),
        ("3 ~ 5장 발표", None),
        ("4장 이상으로 발표", None),
        ("4장소를 소개", None),
        ("제4장 내용을 발표", None),
        ("표지와 본문 2장으로 발표", None),
        ("표지와 본문 2장, 총 3장으로 발표", 3),
    ],
)
def test_explicit_slide_count_does_not_inherit_the_default_minimum(prompt, expected):
    assert deck.requested_slides(prompt) == expected


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("결과와 한계 두 절", 2),
        ("1쪽 보고서, 2개 섹션으로", 2),
        ("결과 한 절만", 1),
        ("배경과 결과를 2절로", 2),
        ("2026년 조사 결과를 1쪽으로, 3분 안에 읽게", None),
        ("제2절의 내용을 요약", None),
        ("1.2절을 참고해 보고서 작성", None),
        ("3~5개 섹션", None),
        ("3 ~ 5개 섹션", None),
        ("2절약 사례를 소개", None),
    ],
)
def test_only_section_counts_set_a_report_outline_size(prompt, expected):
    assert report.requested_sections(prompt) == expected


def test_count_scan_handles_long_spacing_and_many_candidates():
    script = """
from app.services.outline import requested_count
padding = " \\t\\n\\u3000" * 50_000
assert requested_count("4장," * 20_000, ("장",), maximum=30) == 4
spaced = "표지" + padding + "포함" + padding + "4" + padding + "장"
assert requested_count(spaced, ("장",), maximum=30) == 4
assert requested_count("4장" + padding + "이상", ("장",), maximum=30) is None
assert requested_count("4" + padding + "대", ("장",), maximum=30) is None
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=5)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("표지\t포함\n4\u3000장으로", 4),
        ("본문\t2장,\n총\u30003장으로", 3),
        ("3\t~\n5장 발표", None),
        ("4장\u3000이상으로", None),
        ("4장, 3장 중 선택", None),
        ("4장, 3장 말고 표지\t포함\n5장으로", 5),
    ],
)
def test_count_context_preserves_whitespace_and_conflicting_totals(prompt, expected):
    assert deck.requested_slides(prompt) == expected


def _outline(service, count, *, agenda=False):
    if service is report:
        return {
            "title": "동아리 조사",
            "subject": "동아리",
            "sections": [f"항목 {i}" for i in range(count)],
        }
    layouts = ["title", "agenda" if agenda else "steps", "table", "statement"]
    return {
        "title": "동아리 안내",
        "subject": "동아리",
        "slides": [
            {"title": "동아리 안내" if i == 0 else f"항목 {i}", "layout": layouts[i % len(layouts)]}
            for i in range(count)
        ],
    }


def _planner(monkeypatch, service, replies):
    calls = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        calls.append(messages)
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return text, {"inputTokens": 2, "outputTokens": 1}

    monkeypatch.setattr(service, "_complete", complete)
    return calls


async def _plan(service, prompt):
    return [
        event
        async for event in service.write(
            request=prompt,
            model="synthetic",
            api_key="synthetic",
            web_search=False,
            may_ask=False,
        )
    ]


@pytest.mark.parametrize(
    ("service", "prompt", "count"),
    [
        (report, "동아리 조사 결과와 한계 두 절", 2),
        (deck, "동아리 안내, 표지 포함 4장", 4),
    ],
)
async def test_short_outline_reaches_approval_with_its_exact_requested_size(
    monkeypatch, service, prompt, count
):
    calls = _planner(monkeypatch, service, [_outline(service, count)])
    events = await _plan(service, prompt)
    proposal = next(event["plan"] for event in events if event["type"] == "proposal")
    assert len(proposal["sections" if service is report else "slides"]) == count
    assert len(calls) == 1
    assert f"{count}~{count}" in str(calls[0])


@pytest.mark.parametrize(
    ("service", "prompt", "count"),
    [
        (report, "동아리 조사 결과와 한계 두 절", 2),
        (deck, "동아리 안내, 표지 포함 4장", 4),
    ],
)
@pytest.mark.parametrize("offset", [-1, 1])
async def test_wrong_size_is_replanned_once_without_slicing_content(
    monkeypatch, service, prompt, count, offset
):
    corrected = _outline(service, count)
    corrected["title"] = "수정된 전체 구성"
    calls = _planner(monkeypatch, service, [_outline(service, count + offset), corrected])
    events = await _plan(service, prompt)
    proposal = next(event["plan"] for event in events if event["type"] == "proposal")
    assert proposal["title"] == "수정된 전체 구성"
    assert len(proposal["sections" if service is report else "slides"]) == count
    assert len(calls) == 2
    usage = next(event for event in events if event["type"] == "usage")
    assert (usage["inputTokens"], usage["outputTokens"]) == (4, 2)


@pytest.mark.parametrize(
    ("service", "prompt", "count"),
    [
        (report, "동아리 결과와 한계 두 절", 2),
        (deck, "동아리 안내, 표지 포함 4장", 4),
    ],
)
@pytest.mark.parametrize("retry_fails", [False, True])
async def test_unresolved_size_stops_before_approval_after_one_retry(
    monkeypatch, service, prompt, count, retry_fails
):
    wrong = _outline(service, count + 1)
    calls = _planner(
        monkeypatch, service, [wrong, httpx.ConnectError("offline") if retry_fails else wrong]
    )
    events = await _plan(service, prompt)
    assert len(calls) == 2
    assert not any(event["type"] == "proposal" for event in events)
    error = next(event["message"] for event in events if event["type"] == "error")
    assert str(count) in error and ("절" in error or "장" in error)


async def test_added_agenda_is_checked_against_explicit_total(monkeypatch):
    # Eight body/cover rows become nine if the missing agenda is appended.
    calls = _planner(monkeypatch, deck, [_outline(deck, 8), _outline(deck, 8, agenda=True)])
    events = await _plan(deck, "동아리 안내 발표자료 총 8장")
    slides = next(event["plan"]["slides"] for event in events if event["type"] == "proposal")
    assert len(slides) == 8
    assert any(slide["layout"] == "agenda" for slide in slides)
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("service", "prompt", "count", "bounds"),
    [
        (report, "동아리 조사 보고서", 4, "3~12"),
        (deck, "동아리 소개 발표", 5, "5~12"),
    ],
)
async def test_unspecified_size_keeps_the_existing_defaults(
    monkeypatch, service, prompt, count, bounds
):
    calls = _planner(monkeypatch, service, [_outline(service, count)])
    events = await _plan(service, prompt)
    assert any(event["type"] == "proposal" for event in events)
    assert bounds in str(calls[0])


async def test_approved_report_is_not_replanned_or_trimmed(monkeypatch):
    calls = _planner(
        monkeypatch,
        report,
        ["## 배경\n동아리 조사다.\n## 결과\n참여가 늘었다.\n## 한계\n원인은 알 수 없다."],
    )
    events = [
        event
        async for event in report.write(
            request="동아리 결과 두 절",
            model="synthetic",
            api_key="synthetic",
            web_search=False,
            approved_plan={"title": "직접 수정한 목차", "sections": ["배경", "결과", "한계"]},
        )
    ]
    headings = [
        event["heading"] for event in events if event["type"] == "section" and not event.get("done")
    ]
    assert headings == ["배경", "결과", "한계"]
    result = next(event["sections"] for event in events if event["type"] == "report")
    assert [section["heading"] for section in result] == headings
    assert not any("보고서의 제목과 목차" in str(call) for call in calls)


async def test_approved_deck_is_not_replanned_or_trimmed(monkeypatch):
    written = []

    async def write_slides(**kwargs):
        written.extend(kwargs["plan"])
        yield {"type": "deck", "slides": kwargs["plan"]}

    monkeypatch.setattr(deck, "_write_slides", write_slides)
    calls = _planner(monkeypatch, deck, [])
    events = [
        event
        async for event in deck.write(
            request="동아리 소개 4장",
            model="synthetic",
            api_key="synthetic",
            web_search=False,
            approved_plan=_outline(deck, 5),
        )
    ]
    assert len(written) == 5 and events[-1]["type"] == "deck"
    assert calls == []


@pytest.mark.parametrize(
    ("prompt", "approved_count", "expected_count"),
    [
        ("동아리 신청 안내, 표지 포함 4장", 4, 4),
        ("동아리 신청 안내, 표지 포함 4장", 5, 5),
        ("동아리 신청 안내", 4, 3),
        ("2026년 동아리 신청 안내, 3분 발표", 4, 3),
    ],
)
async def test_explicit_total_preserves_retold_slots_through_writing_and_export(
    monkeypatch, prompt, approved_count, expected_count
):
    rows = [
        {"title": "Application guide", "layout": "title"},
        {
            "title": "Application steps",
            "layout": "steps",
            "steps": [
                ["Apply", "Students submit registration forms and event descriptions"],
                ["Confirm", "Staff verify receipt attendance venue location and guidance"],
            ],
        },
        {
            "title": "Roles",
            "layout": "table",
            "rows": [
                ["Role", "Students", "Staff"],
                [
                    "Action",
                    "Submit registration forms and event descriptions",
                    "Verify receipt attendance venue location and guidance",
                ],
            ],
        },
        {"title": "Summary", "layout": "bullets", "bullets": ["Bring the completed form"]},
    ]
    if approved_count == 5:
        rows.append({"title": "Questions", "layout": "closing", "body": "Thank you"})
    plan = [{"title": row["title"], "layout": row["layout"]} for row in rows]
    draft = json.dumps({"slides": rows})
    drafted = deck._split_deck_draft(draft, plan, set(), prompt)
    # The real overlap detector prefers the table and would remove the earlier steps.
    assert deck._retold(plan, drafted) == {1}

    calls = _planner(monkeypatch, deck, [draft])

    async def no_figures(**kwargs):
        return [], {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(deck.diagrams, "plan", no_figures)
    events = [
        event
        async for event in deck.write(
            request=prompt,
            model="synthetic",
            api_key="synthetic",
            web_search=False,
            approved_plan={"title": "Application guide", "slides": plan},
        )
    ]
    slides = next(event["slides"] for event in events if event["type"] == "deck")
    expected_titles = [row["title"] for row in plan]
    if expected_count != approved_count:
        expected_titles.pop(1)
    assert [slide["title"] for slide in slides] == expected_titles
    assert len(slides) == expected_count
    assert len(calls) == 1
    assert len([e for e in events if e["type"] == "slide" and e.get("done")]) == expected_count
    assert all(e["progress"]["total"] == expected_count for e in events if "progress" in e)
    assert all(deck.has_content(slide) or slide["layout"] in deck._STRUCTURAL for slide in slides)
    pptx = Presentation(io.BytesIO(deck_export.to_pptx("Guide", slides)))
    pdf = PdfReader(io.BytesIO(deck_export.to_pdf("Guide", slides)))
    assert len(pptx.slides) == len(pdf.pages) == expected_count
