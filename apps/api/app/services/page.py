"""Writing an HTML artifact, block by block.

Two-pass like the `report` and `deck` tracks: an outline call names the
blocks, then each block is written on its own call. The model writes only the
inside of a block; `design_templates.assemble` places it in the seed's
structure, so an off-vocabulary answer degrades to a plain paragraph.
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
from app.services import grounding, hangul, ratelimit, research, settings_store, thinking
from app.services import outline as plan_rules
from app.services.context import build_document_messages
from app.services.design_templates import DesignTemplate

log = logging.getLogger(__name__)

#: One model call per block, so this is the multiplier on the bill.
_MIN_BLOCKS = 4
_MAX_BLOCKS = 24
#: Deck templates get the slide track's ceiling.
_MAX_SLIDES = 50
#: Ceiling when no count was asked for.
_DEFAULT_MAX = 10

#: Waits between retries of a rate-limited call, in seconds.
_BACKOFF = (2.0, 6.0)

#: ```html … ``` — models fence markup even when told not to.
_FENCE = re.compile(r"^\s*```[A-Za-z]*\s*\n(.*?)\n?\s*```\s*$", re.S)

_OUTLINE_PROMPT = """다음 요청에 맞는 {noun}의 제목과 구성을 만들어라.

규칙:
- title 은 표지에 적힐 한 줄이다. 요청 문장을 그대로 옮기지 말고 주제를 가리키는
  명사구로 써라. 마침표와 "~에 대한 {noun}" 같은 군말은 빼라.
- **요청에 없는 소재를 지어내지 마라.** 요청이 문서의 쓰임만 말하고 무엇에 대한
  것인지는 말하지 않았으면 그 쓰임을 가리키는 제목을 쓰고, 그 쓰임이 요구하는
  뼈대로 구성을 잡아라. 요청에 없던 분야나 연도를 골라 채우지 마라.
- {unit} {lo}~{hi}개.
- 첫 {unit}은 반드시 layout "cover" 이고, 그 제목은 전체 제목과 같게 하라.
- layout 은 다음 중에서만 골라라: {layouts}
- 한 가지 layout 으로 끌고 가지 마라. 같은 layout 을 세 {unit} 연속으로 쓰지 말고,
  cover 를 뺀 나머지 중 최소 세 가지(있는 만큼)를 써라. 견주는 자리에는 표나 두 단,
  한 문장으로 남길 자리에는 인용을 쓴다.
- 각 {unit}의 제목은 거기서 말할 내용을 가리키는 짧은 구절로. 순서대로 읽으면
  하나가 되어야 한다.
- 내용은 쓰지 마라. 제목과 layout 만.
{ask_rule}

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

#: What each surface calls its parts, so the prompts read naturally.
_WORDS = {
    "deck": ("발표 자료", "장"),
    "document": ("문서", "절"),
}


def _text(payload: dict[str, Any]) -> str:
    return (payload["choices"][0]["message"].get("content") or "").strip()


async def _complete(
    model: str, messages: list[dict[str, str]], api_key: str, max_tokens: int
) -> tuple[str, dict[str, int]]:
    """One non-streaming call with 429 retry. Same contract as `deck._complete`."""
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
                    # See `thinking.NO_REASONING`; dropped by the proxy for
                    # providers that do not know it.
                    "reasoning": thinking.NO_REASONING,
                },
            )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            # A token-per-minute 429 names when its window resets; wait for that.
            delay = ratelimit.retry_delay(response.text, dict(response.headers), _BACKOFF[attempt])
            log.info("page call rate limited, retrying in %.0fs", delay)
            await asyncio.sleep(delay)
        response.raise_for_status()
        payload = response.json()

    # A reasoning model can spend the whole ceiling thinking and return an
    # empty answer; see `services/thinking.py`.
    if bigger := thinking.starved(payload, max_tokens):
        log.info("%s: answer starved by reasoning, re-asking with %s tokens", model, bigger)
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
        ) as client:
            again = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": bigger,
                    "reasoning": thinking.NO_REASONING,
                },
            )
            if again.status_code >= 400:
                # One retry of the re-ask.
                again = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": bigger,
                        "reasoning": thinking.NO_REASONING,
                    },
                )
        if again.status_code == 200:
            retried = again.json()
            spent = retried.get("usage") or {}
            first = payload.get("usage") or {}
            # Both calls are charged, so both are counted.
            payload = retried
            payload["usage"] = {
                "prompt_tokens": int(first.get("prompt_tokens") or 0)
                + int(spent.get("prompt_tokens") or 0),
                "completion_tokens": int(first.get("completion_tokens") or 0)
                + int(spent.get("completion_tokens") or 0),
            }

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


def _ceiling(template: DesignTemplate | None) -> int:
    """How many blocks one document may run to: slides get the deck's room."""
    return _MAX_SLIDES if template is not None and template.kind == "deck" else _MAX_BLOCKS


def requested_blocks(request: str, template: DesignTemplate | None = None) -> int | None:
    """A count stated in the request, clamped. `None` if unstated."""
    match = re.search(r"(\d{1,3})\s*(?:장|페이지|절|쪽|개)", request)
    if not match:
        return None
    asked = int(match.group(1))
    return max(_MIN_BLOCKS, min(asked, _ceiling(template))) if asked > 0 else None


#: `{"title": "…", "layout": "…"}` even when a quote is missing from a key.
_SALVAGE = re.compile(r'\{[^{}]*?title"?\s*:\s*"([^"]+)"[^{}]*?layout"?\s*:\s*"([^"]+)"', re.S)


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
    """`(title, blocks)`, with unknown layouts coerced to the first body layout."""
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
    # The first block is always the template's cover layout.
    if blocks:
        blocks[0]["layout"] = template.layouts[0]
    return title, blocks[: _ceiling(template)]


def _guide(template: DesignTemplate) -> str:
    """The template's rules, its layout vocabulary, and its blank form when it ships one.

    The form's guidance lines are addressed to a person filling it in, so the
    form arrives labelled as a shape rather than as text to keep.
    """
    rules = template.instructions
    if template.markup:
        rules = f"{rules}\n\n{template.markup}" if rules else template.markup
    form = templates.form_text(template)
    if not form:
        return rules
    return (
        f"{rules}\n\n"
        "## 이 서식의 빈 양식\n\n"
        "아래는 사람이 손으로 채우는 빈 양식에서 뽑은 글이다. 제목과 그 차례,\n"
        "표의 열 이름은 이대로 따른다. 각 제목 아래의 한 줄은 무엇을 적으라는\n"
        "안내이지 문서에 남길 문장이 아니므로, 그 자리에는 실제 내용을 쓴다.\n\n"
        f"{form}"
    )


def _citation_rule(findings: research.Findings, kind: str) -> str:
    """Numbered citation markers, documents only."""
    if kind != "document" or not findings.sources:
        return ""
    available = ", ".join(f"[{source['ordinal']}]" for source in findings.sources)
    return (
        "## 출처 표시\n"
        "웹 자료에서 가져온 사실·수치·주장은 해당 문장 끝에 자료 번호를 "
        f"붙인다. 사용할 수 있는 번호는 {available}뿐이다. 목록에 없는 번호를 "
        "만들지 말고, 자료를 쓰지 않은 문장에는 번호를 붙이지 않는다."
    )


def _fragment(text: str, template: DesignTemplate) -> str:
    """One block's markup: unfenced, stray ideographs read back into Hangul, sanitised."""
    clean, _ = hangul.read_back(text.strip())
    return templates.sanitise(_FENCE.sub(r"\1", hangul.tidy_spacing(clean)), template.layouts)


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    template: DesignTemplate,
    tokens: dict[str, str] | None = None,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    #: The model that plans; empty means the writer plans too.
    outline_model: str = "",
    #: A plan somebody approved. Absent, this plans and stops with `proposal`
    #: (or `needs`); present, it writes exactly what was approved.
    approved_plan: dict[str, Any] | None = None,
    #: Whether this pass may stop to ask. False on the pass after "있는 자료로 진행".
    may_ask: bool = True,
    #: Whether to research the request before the outline.
    web_search: bool = True,
    project_sources: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `title`, `block`, a final `page` and one `usage` event.

    The caller owns persistence, billing and the artifact. A block that fails
    is left empty and the rest continues.
    """
    # Planning is counted apart from writing because it can run on another model.
    usage = {
        "inputTokens": 0,
        "outputTokens": 0,
        "outlineInputTokens": 0,
        "outlineOutputTokens": 0,
    }
    noun, unit = _WORDS.get(template.kind, ("문서", "절"))
    wanted = requested_blocks(request, template)
    surface = template.surface

    findings = research.Findings()
    # Availability is checked before the step is drawn, so a deployment with no
    # search backend does not report "no sources" on every document.
    if web_search and await research.available():
        yield {"type": "step", "id": "sources", "label": "자료 찾는 중", "status": "running"}
        findings = await research.run(request, model=outline_model or model, api_key=api_key)
        usage["outlineInputTokens" if outline_model else "inputTokens"] += findings.usage[
            "inputTokens"
        ]
        usage["outlineOutputTokens" if outline_model else "outputTokens"] += findings.usage[
            "outputTokens"
        ]
        yield {
            "type": "step",
            "id": "sources",
            "label": f"자료 {len(findings.sources)}건" if findings.sources else "참고할 자료 없음",
            "status": "done",
            "detail": findings.detail,
        }
    findings.sources = list(findings.sources)
    web_selected = len(findings.sources)
    project_selected = 0
    project_excluded = 0
    project_reference_lines: list[str] = []
    for item in project_sources or []:
        if item.get("state") not in ("included", "truncated"):
            project_excluded += 1
            continue
        ordinal = len(findings.sources) + 1
        title = str(item.get("name") or "프로젝트 자료")[:200]
        url = str(item.get("sourceUrl") or "")
        findings.sources.append(
            {
                "id": str(item.get("id") or f"project-{ordinal}"),
                "ordinal": ordinal,
                "title": title,
                "publisher": research._publisher(url) if url else "프로젝트 파일",
                "url": url,
                "origin": "web" if url else "file",
                "originLabel": "프로젝트 웹 자료" if url else "프로젝트 파일",
                "quote": (
                    " · ".join(str(v) for v in (item.get("locations") or []))
                    or (
                        "전체 내용 전달됨"
                        if item.get("state") == "included"
                        else "일부 내용만 전달됨"
                    )
                ),
            }
        )
        project_reference_lines.append(f"- [{ordinal}] {title}")
        project_selected += 1
    if findings.sources:
        yield {"type": "sources", "sources": findings.sources}
    yield {
        "type": "research",
        "research": {
            "enabled": web_search,
            "searched": findings.searched,
            "queries": findings.queries,
            "selected": len(findings.sources),
            "excluded": findings.dropped,
            "webSelected": web_selected,
            "projectSelected": project_selected,
            "projectExcluded": project_excluded,
        },
    }
    # Search switched off needs no disclaimer; a search that could not run and
    # one that found nothing are told apart.
    research_rule = ""
    if web_search and not findings.searched:
        research_rule = research.UNRESEARCHED_RULE
    elif web_search and web_selected == 0:
        research_rule = research.EMPTY_RULE
    document_context = list(untrusted_context or [])
    if project_reference_lines:
        trusted_context = list(trusted_context or []) + [
            "# 프로젝트 자료 인용 번호\n"
            "프로젝트 자료에서 가져온 사실을 사용한 문장 끝에는 아래 번호를 정확히 붙이세요. "
            "목록에 없는 번호를 만들지 마세요.\n" + "\n".join(project_reference_lines)
        ]
    if block := research.context_block(findings):
        document_context.append(block)

    async def ask(nudge: str = "") -> tuple[str, dict[str, int]]:
        return await _complete(
            outline_model or model,
            build_document_messages(
                surface,
                _OUTLINE_PROMPT.format(
                    ask_rule=grounding.ASK_RULE if may_ask else grounding.PROCEED_RULE,
                    noun=noun,
                    unit=unit,
                    lo=wanted or _MIN_BLOCKS,
                    hi=wanted or _DEFAULT_MAX,
                    layouts=" / ".join(template.layouts),
                    second=template.layouts[1] if len(template.layouts) > 1 else "section",
                    request=request[:2000],
                )
                + nudge,
                trusted_context=[*(trusted_context or []), _guide(template)],
                untrusted_context=document_context,
                research_rule=research_rule,
            ),
            api_key,
            max(600, 70 * (wanted or _DEFAULT_MAX) + 300),
        )

    if approved_plan is None:
        yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "running"}
        try:
            text, spent = await ask()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("page outline failed: %s", exc)
            yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "error"}
            yield {"type": "error", "message": "구성을 만들지 못했습니다."}
            yield {"type": "usage", **usage}
            return

        plan_rules.count(usage, spent, planned_apart=bool(outline_model))
        # A question instead of a plan — see `grounding.ASK_RULE`.
        if may_ask and (asked := grounding.parse_needs(text)):
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {"type": "needs", "questions": [q.wire() for q in asked]}
            yield {"type": "usage", **usage}
            return
        title, plan = _parse_outline(text, template)

        # A flat plan is re-asked once, naming the missing layouts; the second
        # answer is kept only if it is less flat.
        missing = plan_rules.flat_layouts(plan, template.layouts[1:]) if plan else []
        if missing:
            log.info("page outline flat for %s, unused: %s", template.id, ",".join(missing))
            try:
                retry_text, retry_spent = await ask(
                    f"\n\n앞선 구성이 한 layout 에 몰렸다. 다시 짜라. "
                    f"다음 layout 을 최소 한 번씩 쓰고, "
                    f"같은 layout 을 세 {unit} 연속으로 쓰지 마라: " + " / ".join(missing)
                )
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                log.warning("page outline retry failed: %s", exc)
            else:
                plan_rules.count(usage, retry_spent, planned_apart=bool(outline_model))
                retry_title, retry_plan = _parse_outline(retry_text, template)
                if retry_plan and not plan_rules.flat_layouts(retry_plan, template.layouts[1:]):
                    title, plan = retry_title or title, retry_plan
                else:
                    log.info("page outline still flat for %s, keeping the first", template.id)
        if not plan:
            # The raw answer is logged: it is the only way to tell a refusal from malformed JSON.
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
        # Planning stops here; the caller stores the proposal and calls back with it approved.
        yield {
            "type": "proposal",
            "plan": {
                "title": title[:200],
                "blocks": [{"title": item["title"], "layout": item["layout"]} for item in plan],
            },
        }
        yield {"type": "usage", **usage}
        return

    title = str(approved_plan.get("title") or "")
    fallback_layout = template.layouts[0] if template.layouts else "section"
    plan = [
        {
            "title": str(item.get("title") or "").strip(),
            "layout": str(item.get("layout") or fallback_layout),
        }
        for item in (approved_plan.get("blocks") or [])
        if str(item.get("title") or "").strip()
    ]
    if not plan:
        yield {"type": "error", "message": "승인된 구성이 비어 있습니다."}
        yield {"type": "usage", **usage}
        return
    if title:
        yield {"type": "title", "title": title[:200]}

    blocks = [{**item, "html": ""} for item in plan]
    for block in blocks:
        yield {"type": "block", "block": {k: v for k, v in block.items()}, "done": False}

    outline_text = "\n".join(f"{i + 1}. {b['title']}" for i, b in enumerate(blocks))
    written: list[str] = []

    for index, block in enumerate(blocks):
        heading = str(block["title"])
        # Same progress shape as the slide and report tracks.
        progress = {"current": index + 1, "total": len(blocks)}
        yield {
            "type": "step",
            "id": f"b{index}",
            "label": heading,
            "status": "running",
            "progress": progress,
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
                        guide="\n\n".join(
                            part
                            for part in (_guide(template), _citation_rule(findings, template.kind))
                            if part
                        ),
                        request=request[:1200],
                    ),
                    trusted_context=trusted_context,
                    untrusted_context=document_context,
                    research_rule=research_rule,
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
                "progress": progress,
            }
            continue

        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        block["html"] = _fragment(text, template)
        written.append(f"{heading}: {re.sub(r'<[^>]+>', ' ', block['html'])[:200]}")
        yield {"type": "block", "block": {k: v for k, v in block.items()}, "done": True}
        yield {
            "type": "step",
            "id": f"b{index}",
            "label": heading,
            "status": "done",
            "progress": progress,
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
    """Rewrites one block's markup under the first pass's prompt, with the rest as context."""
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
        guide=_guide(template),
        request=request[:1200],
    )
    if note.strip():
        # Last and labelled, so it does not read as part of the original request.
        prompt += f"\n\n이번에 다시 쓰는 이유(반드시 반영):\n{note.strip()[:600]}"

    text, usage = await _complete(
        model,
        build_document_messages(template.surface, prompt),
        api_key,
        1200,
    )
    return _fragment(text, template), usage


def filled(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The blocks that say something; what billing and the message count."""
    return [b for b in blocks if (b.get("html") or "").strip()]


__all__ = ["filled", "requested_blocks", "rewrite_block", "write"]
