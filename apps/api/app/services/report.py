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

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings
from app.models.chat import SessionKind
from app.services import grounding, settings_store
from app.services import outline as plan_rules
from app.services.context import build_document_messages

log = logging.getLogger(__name__)

#: Each section is its own model call, so this is the multiplier on the bill.
_MIN_SECTIONS = 3
_MAX_SECTIONS = 8

#: Search hits kept as the reference shelf — enough to cite, few enough to fit
#: in every section prompt.
_SOURCES = 6
_SEARCH_TIMEOUT = httpx.Timeout(12.0, connect=6.0)

_OUTLINE_PROMPT = """다음 요청에 맞는 보고서의 제목과 목차를 만들어라.

규칙:
- 제목은 문서의 표지에 적힐 한 줄이다. 요청 문장을 그대로 옮기지 말고,
  주제를 가리키는 명사구로 써라. 마침표와 "~에 대한 보고서" 같은 군말은 빼라.
- 섹션 {lo}~{hi}개.
- 각 섹션은 서로 겹치지 않고, 순서대로 읽으면 하나의 글이 되어야 한다.
- 섹션은 제목만. 내용은 쓰지 마라.
{ask_rule}
- 참고할 자료에 양식·서식 문서가 있으면 그 문서의 항목 순서를 그대로 목차로 써라.
  개수도 그 양식을 따르고, 일반적인 보고서 목차로 바꾸지 마라.

JSON 객체로만 답하라.
예: {{"title": "전이학습의 소량 데이터 효율성", "sections": ["요약", "배경", "방법", "결과", "한계", "결론"]}}

요청: {request}"""

_SECTION_PROMPT = """너는 아래 보고서의 "{heading}" 섹션만 쓰고 있다.

전체 목차:
{outline}

앞 섹션에서 이미 쓴 내용:
{written}

참고 자료:
{refs}

규칙:
- "{heading}" 에 해당하는 내용만 써라. 다른 섹션의 내용을 미리 쓰지 마라.
- 제목 줄은 쓰지 마라. 본문만.
- 마크다운을 쓰되 최상위 제목(#)은 쓰지 마라.
- 앞에서 한 말을 되풀이하지 마라.
- 참고 자료에서 가져온 사실은 그 자료의 번호를 문장 끝에 [1] 처럼 붙여라.
  목록에 없는 번호는 절대 쓰지 마라. 참고 자료가 없으면 번호도 쓰지 마라.

원래 요청: {request}"""

#: Placeholder for an empty shelf. An empty block reads as withheld material and
#: the model invents citations.
_NO_REFS = "(없음. 번호 인용을 쓰지 마라.)"


#: Waits between retries of a rate-limited call, in seconds.
_BACKOFF = (2.0, 6.0)


async def _complete(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int,
) -> tuple[str, dict]:
    """One non-streaming call. Returns `(text, usage)`. Retries a 429.

    One call per section against a shared limit; a transient refusal would leave
    a hole in the document.
    """
    base, _ = await settings_store.litellm_config()
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
    ) as client:
        for attempt in range(len(_BACKOFF) + 1):
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                },
            )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            log.info("report call rate limited, retrying in %ss", _BACKOFF[attempt])
            await asyncio.sleep(_BACKOFF[attempt])
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


def _publisher(url: str) -> str:
    """Host without `www.` — what a reader recognises."""
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return re.sub(r"^www\.", "", host)[:80]


async def gather_sources(request: str) -> list[dict[str, Any]]:
    """Reference shelf for one report, from the SearXNG the tools use.

    `[]` on failure or with no search backend, which is why the citation rule in
    the section prompt is conditional.
    """
    backends = await settings_store.tools_config()
    if not backends.search:
        return []
    try:
        async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
            response = await client.get(
                f"{backends.search.rstrip('/')}/search",
                params={"q": request[:300], "format": "json", "language": "ko"},
            )
            response.raise_for_status()
            results = (response.json() or {}).get("results") or []
    except (httpx.HTTPError, ValueError) as exc:
        log.info("report source search failed: %s", exc)
        return []

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in results:
        url = str(row.get("url") or "")
        title = str(row.get("title") or "").strip()
        if not url or not title:
            continue
        publisher = _publisher(url)
        # One entry per site: duplicates crowd out coverage on a short shelf.
        if publisher in seen:
            continue
        seen.add(publisher)
        sources.append(
            {
                "id": f"src{len(sources)}_{uuid.uuid4().hex[:6]}",
                "ordinal": len(sources) + 1,
                "title": title[:200],
                "publisher": publisher,
                "url": url,
                "origin": "web",
                "originLabel": "웹 검색",
                "quote": str(row.get("content") or "")[:300],
            }
        )
        if len(sources) >= _SOURCES:
            break
    return sources


def _refs_block(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return _NO_REFS
    return "\n".join(
        f"[{s['ordinal']}] {s['title']} ({s['publisher']})\n{s.get('quote') or ''}"
        for s in sources
    )


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    #: The model that plans, when an administrator has named one. A report's
    #: 목차 is the same kind of decision a deck's layouts are: one call that
    #: every call after it is written against. Empty plans with `model`.
    outline_model: str = "",
    #: The 목차 somebody has already seen and approved.
    #:
    #: Absent, this plans and stops: it emits `proposal` — or `needs`, when the
    #: material cannot carry the request — and writes nothing. Present, it
    #: skips planning and writes exactly what was approved, because planning
    #: again would produce a different report from the one agreed to.
    approved_plan: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `section` and one final `usage` event.

    The caller owns persistence, billing and the artifact — this only writes.
    """
    # Planning is counted apart from writing, because it can run on another
    # model — and a call billed at the wrong model's price is a ledger that
    # says the wrong thing about where the money went. Empty when the same
    # model does both, which is the shape every caller already handles.
    usage = {
        "inputTokens": 0,
        "outputTokens": 0,
        "outlineInputTokens": 0,
        "outlineOutputTokens": 0,
    }

    if approved_plan is None:
        yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "running"}
        try:
            text, spent = await _complete(
                outline_model or model,
                build_document_messages(
                    SessionKind.report,
                    _OUTLINE_PROMPT.format(
                        ask_rule=grounding.ASK_RULE,
                        lo=_MIN_SECTIONS,
                        hi=_MAX_SECTIONS,
                        request=request[:2000],
                    ),
                    trusted_context=trusted_context,
                    untrusted_context=untrusted_context,
                ),
                api_key,
                400,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("report outline failed: %s", exc)
            yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
            yield {"type": "error", "message": "보고서 개요를 만들지 못했습니다."}
            yield {"type": "usage", **usage}
            return

        plan_rules.count(usage, spent, planned_apart=bool(outline_model))
        # A question instead of a 목차 — see `grounding.ASK_RULE`. Only when the
        # request names material the sources do not carry; a bare topic is still
        # planned without anybody being asked about it.
        if asked := grounding.parse_needs(text):
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {"type": "needs", "questions": [q.wire() for q in asked]}
            yield {"type": "usage", **usage}
            return
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
        # Planned, and that is where this stops. The 목차 is offered rather
        # than written against: the caller stores it, shows it, and calls back
        # with it approved. Nothing has been written, which is what keeps the
        # report already on screen safe from a run nobody confirmed.
        yield {"type": "proposal", "plan": {"title": title[:200], "sections": headings}}
        yield {"type": "usage", **usage}
        return

    title = str(approved_plan.get("title") or "")
    headings = [str(h).strip() for h in (approved_plan.get("sections") or []) if str(h).strip()]
    if not headings:
        yield {"type": "error", "message": "승인된 개요가 비어 있습니다."}
        yield {"type": "usage", **usage}
        return
    # Emitted only when the model produced one, so the caller keeps its fallback.
    if title:
        yield {"type": "title", "title": title[:200]}

    # Before any section, so all of them cite from one shelf.
    yield {"type": "step", "id": "sources", "label": "자료 찾는 중", "status": "running"}
    sources = await gather_sources(request)
    yield {
        "type": "step",
        "id": "sources",
        "label": f"자료 {len(sources)}건" if sources else "참고할 자료 없음",
        "status": "done",
        "detail": " · ".join(str(s["publisher"]) for s in sources),
    }
    yield {"type": "sources", "sources": sources}
    refs = _refs_block(sources)

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
        # The position lives in `progress`, not in the text: spelled into both,
        # the surface renders "3/9 도입 (3/9)".
        label = str(section["heading"])
        # The outline lands before any section is written, so each step can say
        # where it sits in it — which is the only figure that answers "how much
        # of this is left" while the document builds.
        progress = {"current": index + 1, "total": len(sections)}
        yield {
            "type": "step",
            "id": section["id"],
            "label": label,
            "status": "running",
            "progress": progress,
        }
        try:
            body, spent = await _complete(
                model,
                build_document_messages(
                    SessionKind.report,
                    _SECTION_PROMPT.format(
                        heading=section["heading"],
                        outline=outline_text,
                        # Tail only: the whole document would crowd out the
                        # instruction by section six.
                        written="\n\n".join(written)[-4000:] or "(아직 없음)",
                        refs=refs,
                        request=request[:1500],
                    ),
                    trusted_context=trusted_context,
                    untrusted_context=untrusted_context,
                ),
                api_key,
                1200,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("report section %r failed: %s", section["heading"], exc)
            yield {
                "type": "step",
                "id": section["id"],
                "label": label,
                "status": "error",
                "progress": progress,
            }
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
        yield {
            "type": "step",
            "id": section["id"],
            "label": label,
            "status": "done",
            "progress": progress,
        }
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
    sources: list[dict] | None = None,
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
        # The document already carries numbered citations, so a rewrite without
        # the shelf would renumber them against nothing.
        refs=_refs_block(sources or []),
        request=request[:1500],
    )
    if note.strip():
        # Last and labelled: an unlabelled sentence appended to a prompt reads
        # as part of the original request.
        prompt += f"\n\n이번에 다시 쓰는 이유(반드시 반영):\n{note.strip()[:600]}"
    return await _complete(
        model,
        build_document_messages(SessionKind.report, prompt),
        api_key,
        1200,
    )


def word_count(sections: list[dict]) -> int:
    return sum(len((s.get("content") or "").split()) for s in sections)


def to_markdown(title: str, sections: list[dict]) -> str:
    parts = [f"# {title}"]
    for section in sections:
        parts.append(f"\n## {section['heading']}\n\n{section.get('content') or ''}")
    return "\n".join(parts).strip() + "\n"
