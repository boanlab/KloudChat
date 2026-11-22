"""The one-pass report path (the whole document in one call from the person's own material) is on
by default for a self-hosted writer, off for external ones, and never for a report with web
sources. `REPORT_ONE_PASS` forces it on (1) or off (0)."""

from __future__ import annotations

import pytest

from app.pipeline import report_stages
from app.pipeline.bundle import Bundle, Material, Request


def _bundle(model, sources=()):
    """A real bundle: the writer's group is read from the request, as in a live run."""
    return Bundle(request=Request(text="보고서 써 줘", model=model, api_key="k"),
                  material=Material(sources=list(sources)))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(report_stages.ONE_PASS_ENV, raising=False)


def test_self_hosted_writer_by_default():
    assert report_stages.one_pass(_bundle("strict-local/qwen3.8-flash-next"))
    assert report_stages.one_pass(_bundle("local/qwen3.8-flash-next"))


def test_external_writer_keeps_the_staged_path():
    assert not report_stages.one_pass(_bundle("openai/gpt-5.4"))


def test_never_with_web_sources():
    web = [{"origin": "web", "url": "https://a.kr"}]
    assert not report_stages.one_pass(_bundle("strict-local/x", web))


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv(report_stages.ONE_PASS_ENV, "1")
    assert report_stages.one_pass(_bundle("openai/gpt-5.4"))
    monkeypatch.setenv(report_stages.ONE_PASS_ENV, "0")
    assert not report_stages.one_pass(_bundle("strict-local/x"))
