"""Writing an HTML artifact, block by block.

The third document track, beside `report` (markdown sections) and `deck` (JSON
slides). Same two-pass shape as both, and for the same reason: an outline call
names the blocks so the panel can show the whole thing before any of it
exists, then each block is written on its own call carrying what the earlier
ones said.

What is different is who owns the layout. The model never sees the seed and
never writes a `<style>`, a `class` or a `<section>`: it writes the *inside* of
one block, and `design_templates.assemble` puts that inside the structure the
seed styles. A model that ignores the vocabulary produces a plain paragraph in
the right place rather than a broken page.

The whole file is one artifact. There is no headless browser in this image, so
the way it becomes a PDF is the reader's own print dialogue — every seed
carries the `@media print` rules that make that work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings
from app.services import design_templates as templates
from app.services import settings_store
from app.services.context import build_document_messages
from app.services.design_templates import DesignTemplate

log = logging.getLogger(__name__)

#: One model call per block, so this is the multiplier on the bill.
_MIN_BLOCKS = 4
_MAX_BLOCKS = 24
#: Ceiling when no count was asked for, separate from the cap above.
_DEFAULT_MAX = 10

#: Waits between retries of a rate-limited call, in seconds.
_BACKOFF = (2.0, 6.0)

#: ```html … ``` — models fence markup even when told not to.
_FENCE = re.compile(r"^\s*```[A-Za-z]*\s*\n(.*?)\n?\s*```\s*$", re.S)

_OUTLINE_PROMPT = """다음 요청에 맞는 {noun}의 제목과 구성을 만들어라.

규칙:
- title 은 표지에 적힐 한 줄이다. 요청 문장을 그대로 옮기지 말고 주제를 가리키는
  명사구로 써라. 마침표와 "~에 대한 {noun}" 같은 군말은 빼라.
- {unit} {lo}~{hi}개.
- 첫 {unit}은 반드시 layout "cover" 이고, 그 제목은 전체 제목과 같게 하라.
- layout 은 다음 중에서만 골라라: {layouts}
- 각 {unit}의 제목은 거기서 말할 내용을 가리키는 짧은 구절로. 순서대로 읽으면
  하나가 되어야 한다.
- 내용은 쓰지 마라. 제목과 layout 만.
- 요청이 한 단어여도 되묻지 마라. 주제만 주어졌으면 그 주제를 처음 접하는
  사람에게 설명하는 것으로 네가 알아서 구성하라. 자료가 부족하다는 답은 하지 마라.

JSON 객체로만 답하라.
예: {{"title": "전이학습의 소량 데이터 효율성",
  "blocks": [{{"title": "전이학습의 소량 데이터 효율성", "layout": "cover"}},
             {{"title": "왜 데이터가 부족한가", "layout": "{second}"}}]}}

요청: {request}"""

_BLOCK_PROMPT = """너는 아래 {noun}의 "{heading}" {unit} 하나만 쓰고 있다.
이 {unit}의 layout 은 "{layout}" 이다.

전체 구성:
{outline}

앞에서 이미 말한 내용:
{written}

{guide}

HTML 조각만 답하라. 설명, 코드 울타리, `<html>`·`<body>`·`<style>` 은 쓰지 마라.
제목은 이미 따로 붙으므로 다시 쓰지 마라.

원래 요청: {request}"""

#: What each surface calls its parts, so the prompts read like the thing being
#: made rather than like a schema.
_WORDS = {
    "deck": ("발표 자료", "장"),
    "document": ("문서", "절"),
}


def _text(payload: dict[str, Any]) -> str:
    return (payload["choices"][0]["message"].get("content") or "").strip()


async def _complete(
    model: str, messages: list[dict[str, str]], api_key: str, max_tokens: int
) -> tuple[str, dict[str, int]]:
    """One non-streaming call. Same contract and retry as `deck._complete`.

    A page is one call per block in a row, so it meets a shared rate limit for
    the same reason a deck does.
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
                json={"model": model, "messages": messages, "max_tokens": max_tokens},
            )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            log.info("page call rate limited, retrying in %ss", _BACKOFF[attempt])
            await asyncio.sleep(_BACKOFF[attempt])
        response.raise_for_status()
        payload = response.json()

    raw = payload.get("usage") or {}
    return _text(payload), {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }


def _json_object(text: str) -> dict[str, Any]:
    """The first JSON object in a reply that may be fenced or prefaced."""
    body = _FENCE.sub(r"\1", text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return {}


def requested_blocks(request: str) -> int | None:
    """A count stated in the request, clamped. `None` if unstated."""
    match = re.search(r"(\d{1,3})\s*(?:장|페이지|절|쪽|개)", request)
    if not match:
        return None
    asked = int(match.group(1))
    return max(_MIN_BLOCKS, min(asked, _MAX_BLOCKS)) if asked > 0 else None


#: `{"title": "…", "layout": "…"}` even when a quote is missing from a key.
#: Small models drop one often enough that the difference is a whole turn:
#: the call is already paid for, and the plan inside it is legible to a human
#: reading the log — so it should be legible here too.
_SALVAGE = re.compile(
    r'\{[^{}]*?title"?\s*:\s*"([^"]+)"[^{}]*?layout"?\s*:\s*"([^"]+)"', re.S
)


def _salvaged(text: str) -> dict[str, Any]:
    """A plan pulled out of malformed JSON, or `{}`."""
    blocks = [
        {"title": title.strip(), "layout": layout.strip()}
        for title, layout in _SALVAGE.findall(text)
    ]
    if not blocks:
        return {}
    outer = re.search(r'"title"\s*:\s*"([^"]+)"', text)
    return {"title": outer.group(1) if outer else blocks[0]["title"], "blocks": blocks}


def _parse_outline(text: str, template: DesignTemplate) -> tuple[str, list[dict[str, str]]]:
    """`(title, blocks)`, with unknown layouts coerced to the first body one.

    Coerced rather than dropped: losing the block loses what it was going to
    say, while losing the layout only loses how it would have looked.
    """
    data = _json_object(text) or _salvaged(text)
    title = str(data.get("title") or "").strip()
    fallback = template.layouts[1] if len(template.layouts) > 1 else template.layouts[0]
    blocks: list[dict[str, str]] = []
    for raw in data.get("blocks") or []:
        if not isinstance(raw, dict):
            continue
        heading = str(raw.get("title") or "").strip()
        if not heading:
            continue
        layout = str(raw.get("layout") or "").strip()
        blocks.append(
            {
                "title": heading[:120],
                "layout": layout if layout in template.layouts else fallback,
            }
        )
    # The cover is the template's, not the model's: a body layout in the first
    # position gives a document with no title page and a heading twice.
    if blocks:
        blocks[0]["layout"] = template.layouts[0]
    return title, blocks[:_MAX_BLOCKS]


def _fragment(text: str) -> str:
    """One block's markup, unfenced and reduced to the seed's vocabulary."""
    return templates.sanitise(_FENCE.sub(r"\1", text.strip()))


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    template: DesignTemplate,
    tokens: dict[str, str] | None = None,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `title`, `block`, a final `page` and one `usage` event.

    The caller owns persistence, billing and the artifact — this only writes.
    A block that fails is left empty and the rest continues, which for a page
    means a gap rather than nothing.
    """
    usage = {"inputTokens": 0, "outputTokens": 0}
    noun, unit = _WORDS.get(template.kind, ("문서", "절"))
    wanted = requested_blocks(request)
    surface = template.surface

    yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "running"}
    try:
        text, spent = await _complete(
            model,
            build_document_messages(
                surface,
                _OUTLINE_PROMPT.format(
                    noun=noun,
                    unit=unit,
                    lo=wanted or _MIN_BLOCKS,
                    hi=wanted or _DEFAULT_MAX,
                    layouts=" / ".join(template.layouts),
                    second=template.layouts[1] if len(template.layouts) > 1 else "section",
                    request=request[:2000],
                ),
                trusted_context=[*(trusted_context or []), template.instructions],
                untrusted_context=untrusted_context,
            ),
            api_key,
            max(600, 70 * (wanted or _DEFAULT_MAX) + 300),
        )
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("page outline failed: %s", exc)
        yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "error"}
        yield {"type": "error", "message": "구성을 만들지 못했습니다."}
        yield {"type": "usage", **usage}
        return

    usage["inputTokens"] += spent["inputTokens"]
    usage["outputTokens"] += spent["outputTokens"]
    title, plan = _parse_outline(text, template)
    if not plan:
        # The one failure that used to leave nothing behind: the call
        # succeeded, so there is no exception, and the turn ends with no
        # artifact and no message. What the model actually said is the only
        # way to tell a refusal from a malformed answer.
        log.warning("page outline unparseable for %s: %s", template.id, text[:300])
        yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "error"}
        yield {
            "type": "error",
            "message": "구성을 만들지 못했습니다. 요청을 조금 더 구체적으로 적어 주세요.",
        }
        yield {"type": "usage", **usage}
        return

    yield {
        "type": "step",
        "id": "outline",
        "label": f"구성 {len(plan)}개",
        "status": "done",
        "detail": " · ".join(item["title"] for item in plan),
    }
    if title:
        yield {"type": "title", "title": title[:200]}

    blocks = [{**item, "html": ""} for item in plan]
    for block in blocks:
        yield {"type": "block", "block": {k: v for k, v in block.items()}, "done": False}

    outline_text = "\n".join(f"{i + 1}. {b['title']}" for i, b in enumerate(blocks))
    written: list[str] = []

    for index, block in enumerate(blocks):
        heading = str(block["title"])
        yield {
            "type": "step",
            "id": f"b{index}",
            "label": heading,
            "status": "running",
            "progress": {"done": index, "total": len(blocks)},
        }
        try:
            text, spent = await _complete(
                model,
                build_document_messages(
                    surface,
                    _BLOCK_PROMPT.format(
                        noun=noun,
                        unit=unit,
                        heading=heading,
                        layout=block["layout"],
                        outline=outline_text,
                        written="\n".join(written[-4:]) or "(없음)",
                        guide=template.instructions,
                        request=request[:1200],
                    ),
                    trusted_context=trusted_context,
                    untrusted_context=untrusted_context,
                ),
                api_key,
                1200,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("page block %s failed: %s", heading, exc)
            yield {
                "type": "step",
                "id": f"b{index}",
                "label": heading,
                "status": "error",
                "progress": {"done": index + 1, "total": len(blocks)},
            }
            continue

        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        block["html"] = _fragment(text)
        written.append(f"{heading}: {re.sub(r'<[^>]+>', ' ', block['html'])[:200]}")
        yield {"type": "block", "block": {k: v for k, v in block.items()}, "done": True}
        yield {
            "type": "step",
            "id": f"b{index}",
            "label": heading,
            "status": "done",
            "progress": {"done": index + 1, "total": len(blocks)},
        }

    html = templates.render(
        template,
        title=title or request.strip()[:60],
        tokens=tokens or {},
        body=templates.assemble(template, blocks),
    )
    yield {"type": "page", "html": html, "blocks": blocks, "templateId": template.id}
    yield {"type": "usage", **usage}


async def rewrite_block(
    *,
    request: str,
    blocks: list[dict[str, Any]],
    index: int,
    template: DesignTemplate,
    model: str,
    api_key: str,
    note: str = "",
) -> tuple[str, dict[str, int]]:
    """Rewrites one block's markup, with the rest of the document as context.

    Same prompt the first pass used, so a rewritten block is written under the
    rules its neighbours were — and everything but the target is passed as
    written, so it does not repeat what the block before it already said.
    """
    target = blocks[index]
    noun, unit = _WORDS.get(template.kind, ("문서", "절"))
    outline = "\n".join(f"{i + 1}. {b.get('title') or ''}" for i, b in enumerate(blocks))
    written = "\n".join(
        f"{b.get('title')}: {re.sub(r'<[^>]+>', ' ', b.get('html') or '')[:200]}"
        for i, b in enumerate(blocks)
        if i != index and (b.get("html") or "").strip()
    )
    prompt = _BLOCK_PROMPT.format(
        noun=noun,
        unit=unit,
        heading=target.get("title") or "",
        layout=target.get("layout") or template.layouts[0],
        outline=outline,
        written=written[-3000:] or "(없음)",
        guide=template.instructions,
        request=request[:1200],
    )
    if note.strip():
        # Last and labelled: an unlabelled sentence appended to a prompt reads
        # as part of the original request.
        prompt += f"\n\n이번에 다시 쓰는 이유(반드시 반영):\n{note.strip()[:600]}"

    text, usage = await _complete(
        model,
        build_document_messages(template.surface, prompt),
        api_key,
        1200,
    )
    return _fragment(text), usage


def filled(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The blocks that actually say something — what billing and the message count."""
    return [b for b in blocks if (b.get("html") or "").strip()]


__all__ = ["filled", "requested_blocks", "rewrite_block", "write"]
