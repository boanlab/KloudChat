"""Writing a report, section by section.

Two passes:

* **Outline.** One cheap call returning headings only. This is what makes the
  progress readout honest — six pending sections means six are coming.
* **Sections.** One call each, carrying the outline and everything written so
  far, so section four does not repeat section two.

A failed section is marked and the rest continues: five sections and a gap beat
nothing.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings
from app.services import settings_store

log = logging.getLogger(__name__)

#: Each section is its own model call, so this is the multiplier on the bill.
_MIN_SECTIONS = 3
_MAX_SECTIONS = 8

_OUTLINE_PROMPT = """다음 요청에 맞는 보고서의 제목과 목차를 만들어라.

규칙:
- 제목은 문서의 표지에 적힐 한 줄이다. 요청 문장을 그대로 옮기지 말고,
  주제를 가리키는 명사구로 써라. 마침표와 "~에 대한 보고서" 같은 군말은 빼라.
- 섹션 {lo}~{hi}개.
- 각 섹션은 서로 겹치지 않고, 순서대로 읽으면 하나의 글이 되어야 한다.
- 섹션은 제목만. 내용은 쓰지 마라.

JSON 객체로만 답하라.
예: {{"title": "전이학습의 소량 데이터 효율성", "sections": ["요약", "배경", "방법", "결과", "한계", "결론"]}}

요청: {request}"""

_SECTION_PROMPT = """너는 아래 보고서의 "{heading}" 섹션만 쓰고 있다.

전체 목차:
{outline}

앞 섹션에서 이미 쓴 내용:
{written}

규칙:
- "{heading}" 에 해당하는 내용만 써라. 다른 섹션의 내용을 미리 쓰지 마라.
- 제목 줄은 쓰지 마라. 본문만.
- 마크다운을 쓰되 최상위 제목(#)은 쓰지 마라.
- 앞에서 한 말을 되풀이하지 마라.

원래 요청: {request}"""


async def _complete(model: str, prompt: str, api_key: str, max_tokens: int) -> tuple[str, dict]:
    """One non-streaming call. Returns `(text, usage)`."""
    base, _ = await settings_store.litellm_config()
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        payload = response.json()

    text = (payload["choices"][0]["message"]["content"] or "").strip()
    raw = payload.get("usage") or {}
    return text, {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }


def _parse_outline(text: str) -> tuple[str, list[str]]:
    """`(title, headings)` from whatever the model wrapped its JSON in.

    The title is optional throughout: a bare array, or an object missing the
    key, still yields a usable outline and the caller falls back to the request.
    """
    title = ""
    obj = re.search(r"\{.*\}", text, re.S)
    if obj:
        try:
            data = json.loads(obj.group(0))
            if isinstance(data, dict):
                title = str(data.get("title") or "").strip()
                items = data.get("sections") or []
                headings = [str(x).strip() for x in items if str(x).strip()]
                if headings:
                    return title, headings[:_MAX_SECTIONS]
        except json.JSONDecodeError:
            pass
    match = re.search(r"\[.*\]", text, re.S)
    if match:
        try:
            items = json.loads(match.group(0))
            headings = [str(x).strip() for x in items if str(x).strip()]
            if headings:
                return title, headings[:_MAX_SECTIONS]
        except json.JSONDecodeError:
            pass
    # A model that ignored the format usually still produced a list.
    lines = [
        re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line).strip(" #").strip()
        for line in text.splitlines()
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)
    ]
    return title, [line for line in lines if line][:_MAX_SECTIONS]


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `section` and one final `usage` event.

    The caller owns persistence, billing and the artifact — this only writes.
    """
    usage = {"inputTokens": 0, "outputTokens": 0}

    yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "running"}
    try:
        text, spent = await _complete(
            model,
            _OUTLINE_PROMPT.format(lo=_MIN_SECTIONS, hi=_MAX_SECTIONS, request=request[:2000]),
            api_key,
            400,
        )
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("report outline failed: %s", exc)
        yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
        yield {"type": "error", "message": "보고서 개요를 만들지 못했습니다."}
        yield {"type": "usage", **usage}
        return

    usage["inputTokens"] += spent["inputTokens"]
    usage["outputTokens"] += spent["outputTokens"]
    title, headings = _parse_outline(text)
    if len(headings) < _MIN_SECTIONS:
        yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
        yield {
            "type": "error",
            "message": "보고서 개요를 만들지 못했습니다. 요청을 조금 더 구체적으로 적어 주세요.",
        }
        yield {"type": "usage", **usage}
        return

    yield {
        "type": "step",
        "id": "outline",
        "label": f"개요 {len(headings)}개 섹션",
        "status": "done",
        "detail": " · ".join(headings),
    }
    # Emitted only when the model produced one, so the caller keeps its fallback.
    if title:
        yield {"type": "title", "title": title[:200]}

    sections = [
        {"id": f"s{i}_{uuid.uuid4().hex[:6]}", "heading": h, "level": 1}
        for i, h in enumerate(headings)
    ]
    # Announced up front so the panel can show the whole shape.
    for section in sections:
        yield {
            "type": "section",
            "sectionId": section["id"],
            "heading": section["heading"],
            "content": "",
            "done": False,
        }

    outline_text = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headings))
    written: list[str] = []

    for index, section in enumerate(sections):
        label = f"{index + 1}/{len(sections)} {section['heading']}"
        yield {"type": "step", "id": section["id"], "label": label, "status": "running"}
        try:
            body, spent = await _complete(
                model,
                _SECTION_PROMPT.format(
                    heading=section["heading"],
                    outline=outline_text,
                    # Tail only: the whole document would crowd out the
                    # instruction by section six.
                    written="\n\n".join(written)[-4000:] or "(아직 없음)",
                    request=request[:1500],
                ),
                api_key,
                1200,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("report section %r failed: %s", section["heading"], exc)
            yield {"type": "step", "id": section["id"], "label": label, "status": "error"}
            yield {
                "type": "section",
                "sectionId": section["id"],
                "heading": section["heading"],
                "content": "_이 섹션을 쓰지 못했습니다._",
                "done": True,
            }
            section["content"] = ""
            continue

        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        section["content"] = body
        written.append(f"## {section['heading']}\n{body}")
        yield {"type": "step", "id": section["id"], "label": label, "status": "done"}
        yield {
            "type": "section",
            "sectionId": section["id"],
            "heading": section["heading"],
            "content": body,
            "done": True,
        }

    yield {"type": "report", "sections": sections}
    yield {"type": "usage", **usage}


async def rewrite_section(
    *,
    request: str,
    heading: str,
    sections: list[dict],
    target_id: str,
    model: str,
    api_key: str,
    note: str = "",
) -> tuple[str, dict]:
    """Rewrites one section, with the rest of the document as context.

    Everything but the target is passed as written, so the new text does not
    repeat section two — the same guard the first pass uses.
    """
    outline = "\n".join(f"{i + 1}. {s.get('heading') or ''}" for i, s in enumerate(sections))
    written = "\n\n".join(
        f"## {s.get('heading')}\n{s.get('content') or ''}"
        for s in sections
        if s.get("id") != target_id and (s.get("content") or "").strip()
    )
    prompt = _SECTION_PROMPT.format(
        heading=heading,
        outline=outline,
        written=written[-4000:] or "(아직 없음)",
        request=request[:1500],
    )
    if note.strip():
        # Last and labelled: an unlabelled sentence appended to a prompt reads
        # as part of the original request.
        prompt += f"\n\n이번에 다시 쓰는 이유(반드시 반영):\n{note.strip()[:600]}"
    return await _complete(model, prompt, api_key, 1200)


def word_count(sections: list[dict]) -> int:
    return sum(len((s.get("content") or "").split()) for s in sections)


def to_markdown(title: str, sections: list[dict]) -> str:
    parts = [f"# {title}"]
    for section in sections:
        parts.append(f"\n## {section['heading']}\n\n{section.get('content') or ''}")
    return "\n".join(parts).strip() + "\n"
