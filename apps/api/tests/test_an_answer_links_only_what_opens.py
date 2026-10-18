"""Chat answer links and pictures limited to what a browser can open."""

from __future__ import annotations

import pytest

from app.pipeline import chat_stages
from app.pipeline.stage import Context
from app.services.chat_format import unlink_local_targets


def test_sandbox_pictures_and_relative_files_point_to_the_files_under_the_answer():
    made = {"chart_1791814189_1.png", "budget_2026_09_집행분석.xlsx"}
    text = (
        "부서별 집행률입니다.\n\n![부서별 집행률](sandbox:chart_1791814189_1.png)\n\n"
        "상세 분석 결과는 [budget_2026_09_집행분석.xlsx](budget_2026_09_집행분석.xlsx)에 정리해 "
        "두었습니다. 산점도는 [산점도: Study Hours vs Score by Group](artifact:산점도: Study Hours "
        "vs Score by Group)에 있습니다. 원 그림 ![x](/mnt/data/없는그림.png)."
    )
    out = unlink_local_targets(text, made)
    assert "*그림 「부서별 집행률」: 답 아래 파일 chart_1791814189_1.png*" in out
    assert "상세 분석 결과는 budget_2026_09_집행분석.xlsx에" in out
    assert "산점도는 산점도: Study Hours vs Score by Group에" in out
    # A picture of a file nobody made is not promised: only its words stay.
    assert "원 그림 x." in out
    assert "](" not in out


def test_web_links_inline_pictures_and_code_are_left_alone():
    text = (
        "[출처](https://example.org/a b) ![그림](data:image/png;base64,AAAA) [위](#top)\n"
        "```\n![코드 속](sandbox:x.png)\n```"
    )
    assert unlink_local_targets(text, {"x.png"}) == text


@pytest.mark.asyncio
async def test_the_answer_normalize_stage_unlinks_against_the_turns_made_files():
    bundle = chat_stages.bundle_for(
        answer="![차트](sandbox:/mnt/data/chart.png)", request="차트", model={"id": "m"},
        actual_model=None, api_key="k", session_id="s", today=None, first_message="차트",
        messages=[], material="", check_keys=(), check_material="", tool_outputs=[], judge="",
        kept_steps=[{"id": "h1_0", "outputs": [{"id": "f1", "name": "chart.png", "size": 9}]}],
        usage={},
    )
    await chat_stages.Normalize().apply(bundle, Context())
    assert bundle.document.answer == "*그림 「차트」: 답 아래 파일 chart.png*"
