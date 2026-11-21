"""A summary written before sections written apart (an upper model's recommendation) is said
again from the finished body; kept only when its figures are the document's.

Rounds 2-3 r2r03, r3r03, r3r06, r3r11 (KC-SL): the summary recommended B (or no option, or only
restated the request) while the recommendation section chose A."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.pipeline import report_stages


def _bundle():
    request = SimpleNamespace(model="m", api_key="k", text="두 안 비교 보고서")
    return SimpleNamespace(request=request, usage={})


SECTIONS = [
    {"heading": "요약", "content": "B안 중심으로 검토하는 것을 권합니다. 비용은 2,700만 원입니다."},
    {"heading": "대안 비교", "content": "A안 2,300만 원, B안 2,700만 원(예산 2,500만 원 초과)."},
    {"heading": "권고안과 다음 단계", "content": "예산 안인 A안을 권고합니다."},
]


def _run(monkeypatch, said: str) -> str:
    async def written(*args, **kwargs):
        return said, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report_stages, "_written", written)
    monkeypatch.setattr(report_stages, "_messages", lambda bundle, prompt, model: [])
    monkeypatch.setattr(report_stages, "_count", lambda *a, **k: None)
    monkeypatch.setattr(report_stages, "_cleaned", lambda bundle, i, s, body: body)
    sections = [dict(s) for s in SECTIONS]
    asyncio.run(report_stages._summary_last(_bundle(), sections))
    return sections[0]["content"]


def test_a_summary_said_again_from_the_body_is_kept(monkeypatch):
    said = ("예산 2,500만 원 안인 A안(2,300만 원)을 권고합니다. "
            "B안은 2,700만 원으로 예산을 넘습니다.")
    assert _run(monkeypatch, said) == said


def test_a_summary_with_a_new_figure_is_not_kept(monkeypatch):
    said = "A안을 권고합니다. 연 3,100만 원을 아낄 수 있습니다."
    assert _run(monkeypatch, said) == SECTIONS[0]["content"]
