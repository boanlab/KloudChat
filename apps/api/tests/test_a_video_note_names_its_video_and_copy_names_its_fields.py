"""Video notes headed with title, channel and length; copy tables headed with field words."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from app.services import skill_scripts

_MCP = Path(__file__).resolve().parents[3] / "mcp" / "youtube.py"


def _youtube(monkeypatch):
    """mcp/youtube.py with its third-party imports stood in for (not installed for the API)."""
    fastmcp = types.SimpleNamespace(
        FastMCP=lambda name: types.SimpleNamespace(tool=lambda: (lambda f: f), run=None))
    errors = types.SimpleNamespace(NoTranscriptFound=LookupError, TranscriptsDisabled=LookupError,
                                   VideoUnavailable=LookupError)
    for name, module in {
        "mcp": types.SimpleNamespace(), "mcp.server": types.SimpleNamespace(),
        "mcp.server.fastmcp": fastmcp,
        "youtube_transcript_api": types.SimpleNamespace(YouTubeTranscriptApi=object),
        "youtube_transcript_api._errors": errors,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("youtube_mcp_under_test", _MCP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_transcript_comes_under_the_video_s_title_channel_and_length(monkeypatch):
    yt = _youtube(monkeypatch)
    captions = "자 그러면 이제 HTML이 무엇인지 이제 한번 살펴볼까요"

    async def meta(vid):
        return {"title": "HTML - 기술소개", "channel": "생활코딩", "duration": 371,
                "published": "20151205"}

    monkeypatch.setattr(yt, "_metadata", meta)
    monkeypatch.setattr(yt, "_try_captions", lambda vid, language: captions)
    out = asyncio.run(yt.transcript("https://www.youtube.com/watch?v=33DjsANwlJk"))
    assert out == (
        "Title: HTML - 기술소개\nChannel: 생활코딩\nLength: 6분 11초\nPublished: 2015-12-05\n"
        "URL: https://www.youtube.com/watch?v=33DjsANwlJk\n\nTranscript:\n" + captions)


def test_a_value_youtube_did_not_give_is_left_out(monkeypatch):
    yt = _youtube(monkeypatch)
    # oEmbed answers title and channel only
    assert yt._header("EAs8qDciTak", {"title": "금리 개념 진짜 쉽게 알려드림",
                                      "channel": "쿼빗 경제상식"}) == (
        "Title: 금리 개념 진짜 쉽게 알려드림\nChannel: 쿼빗 경제상식\n"
        "URL: https://www.youtube.com/watch?v=EAs8qDciTak")
    assert yt._header("EAs8qDciTak", {}) == "URL: https://www.youtube.com/watch?v=EAs8qDciTak"
    assert [yt._length(s) for s in (204, 3723, 45, None, 0)] == [
        "3분 24초", "1시간 2분 3초", "45초", "", ""]


def test_a_failed_transcript_is_still_an_error_not_a_header(monkeypatch):
    yt = _youtube(monkeypatch)

    async def meta(vid):
        return {"title": "HTML - 기술소개"}

    def no_audio(vid, tmp):
        raise RuntimeError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(yt, "_metadata", meta)
    monkeypatch.setattr(yt, "_try_captions", lambda vid, language: None)
    monkeypatch.setattr(yt, "_download_audio", no_audio)
    out = asyncio.run(yt.transcript("33DjsANwlJk"))
    assert out.startswith("Error: audio download failed") and "Title" not in out


#: tasks/A13/T1/prompt.md as sent
_AD_REQUEST = (
    "「한빛워크」 무료 체험 캠페인 광고 소재 만들어 줘. 채널은 네이버 검색광고, "
    "메타(페이스북·인스타그램) 피드, 링크드인 스폰서드 콘텐츠.\n"
    "캠페인 정보(한빛소프트, 가상 — 아래가 전부임):\n"
    "- 기간 11/1~11/30, 목표: 30일 무료 체험 신청\n"
    "- 혜택: 11월 중 신청하면 체험 기간 30일 → 45일\n"
    "채널별 글자 수 제한을 지켜서 각각 2~3안씩, A/B 테스트 아이디어도."
)
#: A LinkedIn 3안 table headed with the field words the script gives
_LINKEDIN = (
    "| # | 각도 | 헤드라인 | 글자 | 설명 | 글자 | 도입 문구 | 글자 |\n"
    "|---|------|------------|------|---------------|------|---------|------|\n"
    "| 3 | 대상 호명 (정보화 담당자) | 11월, 업무 플랫폼은 45일간 무료 | 20 | "
    "11월 신청 시 45일 무료 체험. 설치형·클라우드형, 망분리, 전자결재. | 41 | "
    "정보화 담당자라면 아실 겁니다. 새 업무 플랫폼을 도입할 때 망분리 대응과 "
    "전자결재 연동이 중요합니다. 한빛워크는 설치형·클라우드형 선택, 망분리 지원, "
    "전자결재 연동을 모두 갖추고 있고, 한빛시청 1,200명이 실제로 쓰고 있습니다. "
    "11월 중 신청 시 45일 무료 체험합니다. | 148 |"
)


def test_the_script_names_fields_in_the_person_s_words(capsys):
    length = skill_scripts.module("copy-check", "length")
    assert length.main(["length", '{"list": true, "channel": "linkedin"}']) == 0
    printed = capsys.readouterr().out
    assert "링크드인 도입 문구: 150 (chars, 권장) — 부를 때 field=ad_intro" in printed
    row = length.measure({"label": "링크드인 3안", "channel": "linkedin", "field": "ad_intro",
                          "text": "정보화 담당자라면 아실 겁니다."})
    assert row["word"] == "도입 문구"
    # every field a channel has carries a word of its own
    assert all(field in length.FIELD_WORDS
               for spec in length.SPECS.values() for field in spec["fields"])


def test_a_sponsored_linkedin_intro_gets_its_measured_count():
    length = skill_scripts.module("copy-check", "length")
    rows = length.targets(_AD_REQUEST)
    assert {r["key"] for r in rows} >= {"linkedin.ad_intro", "linkedin.ad_headline",
                                        "linkedin.ad_description"}
    measured = length.measured(length.table_items(_LINKEDIN, rows), rows)
    intro = next(r for r in measured if r["key"] == "linkedin.ad_intro")
    assert intro["count"] == 156 and not intro["hard"]
    assert length.restate_counts(_LINKEDIN, measured).endswith("| 156 |")
    # a member's own post is not an ad
    assert not any(r["key"].startswith("linkedin.")
                   for r in length.targets("링크드인에 올릴 회사 소개 글 써 줘"))


@pytest.mark.parametrize("skill", ["youtube-summary", "copy-check"])
def test_the_skill_tells_the_writer_where_the_words_come_from(skill):
    text = (Path(__file__).resolve().parents[1] / "app" / "skills" / skill / "SKILL.md").read_text()
    assert ("<Title>" in text) if skill == "youtube-summary" else ("`word`" in text)
