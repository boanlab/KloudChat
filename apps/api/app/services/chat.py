"""Chat stream helpers: tool step labels, SSE framing, session titles and media-turn messages."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

import httpx

from app.core.config import settings
from app.models.chat import Message, Role, TurnFailure
from app.services import settings_store

log = logging.getLogger(__name__)


class ChatStreamError(RuntimeError):
    pass


# tool → running progress label
_STEP_LABELS: dict[str, str] = {
    "web_search": "웹 검색 중",
    "search": "웹 검색 중",
    "weather": "날씨 확인 중",
    "fetch_url": "문서 읽는 중",
    "fetch": "문서 읽는 중",
    "execute_code": "코드 실행 중",
    "deep_research": "심층 조사 중",
    "get_current_time": "현재 시각 확인 중",
    "my_usage": "사용량 조회 중",
    "use_skill": "스킬 적용 중",
}


def step_label(tool_name: str) -> str:
    base = tool_name.split("__")[-1]
    return _STEP_LABELS.get(base, base.replace("_", " "))


# tool → finished-step noun
_STEP_TITLES: dict[str, str] = {
    "web_search": "웹 검색",
    "search": "웹 검색",
    "weather": "날씨",
    "fetch_url": "문서 읽기",
    "fetch": "문서 읽기",
    "execute_code": "코드 실행",
    "deep_research": "심층 조사",
    "get_current_time": "현재 시각",
    "my_usage": "사용량 조회",
    "use_skill": "스킬 적용",
}


def step_title(tool_name: str) -> str:
    base = tool_name.split("__")[-1]
    return _STEP_TITLES.get(base, base.replace("_", " "))


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


TITLE_CHARS = 40


#: subject the prompt names (「주제: …」, 「제목: …」, 「주제는 '…'」)
_NAMED_SUBJECT = re.compile(
    r"(?:^|(?<=[\s.,;(]))(?:발표\s{0,2}|논문\s{0,2}|보고서\s{0,2})?(?:제목|주제)\s{0,2}[:：]"
    r"(?P<named>[^\n]*)"
    r"|주제는\s{0,2}['\"‘“「『](?P<quoted>[^'\"’”」』\n]{2,})['\"’”」』]"
)
#: a named subject ends at the first sentence stop
_SENTENCE_END = re.compile(r"[.。](?=\s|$)")


def provisional_title(prompt: str) -> str:
    """Session title from the prompt; final on media surfaces, else replaced by `generate_title`."""
    named = _NAMED_SUBJECT.search(prompt or "")
    subject = (named.group("named") or named.group("quoted") or "") if named else ""
    subject = _SENTENCE_END.split(subject, 1)[0]
    subject = " ".join(subject.strip(" '\"‘’“”「」『』").split())
    if len(subject) >= 2:
        return subject[:TITLE_CHARS]
    return " ".join((prompt or "").split())[:TITLE_CHARS]


def media_prompt(session_id: str, prompt: str, *, unanswered: bool = False) -> Message:
    """User turn of a media session; `unanswered` = produced nothing."""
    return Message(
        session_id=session_id,
        role=Role.user,
        content=prompt,
        failure=TurnFailure.no_answer if unanswered else None,
    )


def media_answer(
    session_id: str,
    artifact_ids: list[str],
    *,
    model: str = "",
    credits: int = 0,
    partial: bool = False,
) -> Message:
    """Assistant turn of a media session: artifact ids, empty `content`."""
    return Message(
        session_id=session_id,
        role=Role.assistant,
        content="",
        artifact_ids=list(artifact_ids),
        model=model or None,
        usage={"credits": credits},
        failure=TurnFailure.interrupted if partial else None,
    )


async def generate_title(
    model: str,
    first_user_message: str,
    first_reply: str,
    api_key: str,
    *,
    masker: Callable[[str], tuple[str, int]] | None = None,
    strict_local: bool = False,
    disable_fallbacks: bool = False,
    redact_logging: bool = False,
) -> tuple[str | None, dict[str, int]]:
    """`(title, usage)`; title None on failure."""
    spent = {"inputTokens": 0, "outputTokens": 0}
    if masker is not None:
        first_user_message, user_hits = masker(first_user_message)
        first_reply, reply_hits = masker(first_reply)
        redact_logging = redact_logging or bool(user_hits + reply_hits)

    prompt = (
        "다음 대화의 핵심 주제를 나타내는 제목을 작성하세요. "
        "반드시 한국어로, 5단어 이내, 따옴표나 문장부호 없이 제목 텍스트만 출력하세요.\n\n"
        f"사용자: {first_user_message[:1500]}\n"
        f"어시스턴트: {first_reply[:1500]}"
    )
    try:
        base, _ = await settings_store.litellm_config()
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                **({"x-litellm-enable-message-redaction": "true"} if redact_logging else {}),
            },
            timeout=settings.title_timeout_sec,
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 40,
                    **({"disable_fallbacks": True} if strict_local or disable_fallbacks else {}),
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.info("title generation skipped: %s", exc)
        return None, spent

    raw = data.get("usage") or {}
    spent = {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }
    choices = data.get("choices") or []
    if not choices:
        return None, spent
    title = (choices[0].get("message") or {}).get("content") or ""
    title = title.strip().strip("\"'").splitlines()[0].strip() if title.strip() else ""
    if masker is not None:
        title = masker(title)[0]
    return title[:80] or None, spent
