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


def ctx(request: str = "") -> ToolContext:
    return ToolContext(user_id="u", session_id="s", request=request)


async def call(request: str = "", **args) -> tuple[ToolContext, object]:
    context = ctx(request)
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
    """여전히 사람이 이긴다 — 다만 사람이 실제로 파일을 말했을 때."""
    context, _result = await call(
        request="이 메일 txt 파일로 만들어 줘",
        kind="html",
        title="휴강 문의 메일",
        content=EMAIL_DRAFT,
        userRequested=True,
    )
    assert len(context.pending_artifacts) == 1


@pytest.mark.asyncio
async def test_the_flag_cannot_speak_for_a_person_who_only_asked_for_writing():
    """모델이 켠 깃발 하나로 짧은 글이 문서가 되지는 않는다.

    화면에서 그대로 일어난 일이다. "내일 회의가 30분 미뤄졌다고 알리는 짧은 메일
    초안 세 문장만 써줘" 에 모델은 "사용자가 메일 초안을 요청했으므로" 라며
    `userRequested` 를 켰고, 세 문장짜리 메일이 미리보기 탭·소스 탭·버전 기록을
    거느린 문서가 되어 옆 패널에 앉았다. 사람이 부탁한 것은 글이었지 파일이
    아니었고, 그 차이는 payload 가 아니라 사람이 쓴 말에만 남아 있다.
    """
    context, result = await call(
        request="내일 회의가 30분 미뤄졌다고 알리는 짧은 메일 초안 세 문장만 써줘.",
        kind="code",
        title="회의 연기 알림 메일 초안",
        content="제목: 내일 회의 시간 변경 안내\n\n내일 회의가 30분 미뤄졌습니다.",
        language="text",
        userRequested=True,
    )
    assert context.pending_artifacts == []
    assert "답변에 그대로" in result.content


@pytest.mark.asyncio
async def test_a_file_asked_for_in_english_is_still_a_file():
    context, _result = await call(
        request="make that a .md file I can download",
        kind="code",
        title="notes.md",
        content="# 회의\n- 30분 연기",
        language="markdown",
        userRequested=True,
    )
    assert len(context.pending_artifacts) == 1


@pytest.mark.asyncio
async def test_a_real_document_never_needed_the_flag():
    """이 관문은 글에만 선다. 프로그램이 읽어 갈 파일은 사람이 무엇이라 했든
    문서이고, 그래서 이 시험은 요청을 비워 둔 채로 통과해야 한다."""
    context, _result = await call(
        kind="code", title="docker-compose.yml", content=COMPOSE, language="yaml"
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


def test_the_flag_the_model_sets_is_described_as_the_person_s_words():
    """설명이 관문과 다른 말을 하면, 모델은 되돌아오는 호출을 계속 만든다."""
    flag = builtin_tools.CREATE_ARTIFACT.parameters["properties"]["userRequested"]
    assert "파일이나 문서 자체를" in flag["description"]
    assert "메일 초안" in flag["description"]
