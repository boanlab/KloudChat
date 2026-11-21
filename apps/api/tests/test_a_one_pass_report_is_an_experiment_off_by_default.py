"""The one-pass report path (the whole document in one call from the person's own material) is
an experiment: off unless `REPORT_ONE_PASS=1`, and never for a report with web sources."""

from __future__ import annotations

from types import SimpleNamespace

from app.pipeline import report_stages


def _bundle(sources):
    return SimpleNamespace(material=SimpleNamespace(sources=sources))


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(report_stages.ONE_PASS_ENV, raising=False)
    assert not report_stages.one_pass(_bundle([]))


def test_on_only_without_web_sources(monkeypatch):
    monkeypatch.setenv(report_stages.ONE_PASS_ENV, "1")
    assert report_stages.one_pass(_bundle([{"origin": "file", "attached": True}]))
    assert not report_stages.one_pass(_bundle([{"origin": "web", "url": "https://a.kr"}]))
