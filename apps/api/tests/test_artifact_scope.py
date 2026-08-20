"""What earns a panel, and what the answer still owes the reader.

Someone asked for a short mail to a professor and got three paragraphs filed
behind a preview tab, a source tab, an export menu and a version history, while
the chat said only "메일 초안을 작성했습니다." The rule these tests pin is not
length: a four-line compose file is a document because a program reads it back,
and three paragraphs of a letter are an answer because reading them is the whole
of what they are for.
"""

from __future__ import annotations

import pytest

from app.services.tools import builtin as builtin_tools
from app.services.tools.base import ToolContext

#: The artifact that started this, verbatim from the row it was stored as.
EMAIL_DRAFT = (
    "<p>안녕하세요, Professor. OOO입니다.<br>\n오늘 수업과 관련하여 여쭙습니다.</p>"
    "<p>혹시 오늘 수업이 휴강으로 진행되나요?<br>공지를 찾지 못해 확인 부탁드립니다.</p>"
    "<p>감사합니다.<br>\nOOO 드림</p>"
)

COMPOSE = "services:\n  api:\n    image: kloudchat/api:local\n    ports: ['8100:8100']\n"


def ctx() -> ToolContext:
    return ToolContext(user_id="u", session_id="s")


async def call(**args) -> tuple[ToolContext, object]:
    context = ctx()
    return context, await builtin_tools.create_artifact(args, context)


@pytest.mark.asyncio
async def test_a_short_letter_is_an_answer_and_stays_in_the_transcript():
    context, result = await call(kind="html", title="휴강 문의 메일", content=EMAIL_DRAFT)
    assert context.pending_artifacts == []
    # Not an error: the turn ends with the answer the reader wanted, and a red
    # step would report a failure to someone who is looking at a good reply.
    assert result.failed is False
    assert "답변에 그대로" in result.content


@pytest.mark.asyncio
async def test_four_lines_of_compose_are_a_document():
    context, result = await call(
        kind="code", title="docker-compose.yml", content=COMPOSE, language="yaml"
    )
    assert len(context.pending_artifacts) == 1
    assert result.failed is False


@pytest.mark.asyncio
async def test_a_page_is_a_document_even_when_it_is_barely_a_page():
    context, _result = await call(
        kind="html",
        title="배지",
        content="<div class='badge'><style>.badge{color:red}</style>합격</div>",
    )
    assert len(context.pending_artifacts) == 1


@pytest.mark.asyncio
async def test_a_script_that_forgot_to_name_its_language_is_still_a_script():
    """`language` falls back to "text" on the way into storage, so an absent
    value must not be read as a claim that the payload is prose."""
    context, _result = await call(
        kind="code", title="backup.sh", content="#!/bin/sh\nrsync -a /data /backup\n"
    )
    assert len(context.pending_artifacts) == 1


@pytest.mark.asyncio
async def test_prose_dressed_as_a_code_artifact_is_still_prose():
    context, _result = await call(
        kind="code", title="사과문", content="죄송합니다. 다시 확인하겠습니다.", language="markdown"
    )
    assert context.pending_artifacts == []


@pytest.mark.asyncio
async def test_prose_long_enough_to_lose_in_a_transcript_earns_the_panel():
    context, _result = await call(
        kind="html", title="분기 보고서", content="<p>" + "결과를 정리하면 " * 120 + "</p>"
    )
    assert len(context.pending_artifacts) == 1


@pytest.mark.asyncio
async def test_the_person_asking_for_a_file_beats_the_guess():
    context, _result = await call(
        kind="html", title="휴강 문의 메일", content=EMAIL_DRAFT, userRequested=True
    )
    assert len(context.pending_artifacts) == 1


@pytest.mark.asyncio
async def test_a_short_document_is_still_carried_by_the_answer():
    _context, result = await call(
        kind="code", title="docker-compose.yml", content=COMPOSE, language="yaml"
    )
    assert "본문을 그대로 옮겨" in result.content


@pytest.mark.asyncio
async def test_a_long_document_may_stay_in_the_panel_but_not_go_unannounced():
    _context, result = await call(
        kind="code",
        title="seed.sql",
        content="".join(f"insert into note values ({n}, 'row {n}');\n" for n in range(80)),
        language="sql",
    )
    assert "다시 옮길 필요는 없지만" in result.content
    assert "'만들었습니다' 한 줄로 끝내지" in result.content


def test_the_rule_the_model_reads_names_both_sides():
    description = builtin_tools.CREATE_ARTIFACT.description
    assert "길이가 아니라 쓰임새" in description
    assert "메일 초안" in description
    assert "docker-compose.yml" in description
