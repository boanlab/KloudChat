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
from app.services import grounding, hangul, research, settings_store, thinking
from app.services import outline as plan_rules
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
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    # See `thinking.NO_REASONING`: a reasoning model asked for
                    # one block of markup spends the whole ceiling thinking and
                    # answers with nothing. Dropped by the proxy for providers
                    # that do not know it.
                    "reasoning": thinking.NO_REASONING,
                },
            )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            log.info("page call rate limited, retrying in %ss", _BACKOFF[attempt])
            await asyncio.sleep(_BACKOFF[attempt])
        response.raise_for_status()
        payload = response.json()

    # A reasoning model can spend the whole ceiling thinking and return an
    # empty answer with `finish_reason: "length"`. See `services/thinking.py` —
    # this is the one place that can tell that apart from a model with nothing
    # to say, because it is the only place holding the raw payload.
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
                # A gateway that does not know `reasoning` refuses the whole
                # call. The ceiling alone still helps every model that does not
                # scale its thinking to it.
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
            # Both calls are charged, so both are counted. A budget that hid
            # the first attempt would under-report what the turn cost.
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


def _guide(template: DesignTemplate) -> str:
    """The 서식's rules, and the form they are the rules of.

    The instructions say what belongs where; the form shows it — the headings
    in order, the columns of each table, the line under each heading naming
    what goes there. A 서식 ships that file already, so describing it a second
    time in prose would be a second copy to keep in step with the first.

    Short by construction: a blank form is headings and column names, so 회의록
    comes to 257 characters. This is not a document being stuffed into the
    context, it is a table of contents with the blanks named.

    The guidance lines inside a form are addressed to a person filling it in by
    hand, and a model handed them without warning writes them into the document
    as though they were the text. So the form arrives labelled — this is the
    shape, and the lines in it are directions rather than sentences to keep.
    """
    # The seed's vocabulary under the 서식's own rules. Under, not over: a
    # 서식 that describes its own layouts has the more specific thing to say,
    # and this is the floor for the eight that describe none.
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
    """Numbered markers that keep the prose connected to its stored shelf."""
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
    """One block's markup, unfenced and reduced to the seed's vocabulary.

    The template comes along for its layout names: a model that answers with
    the layout it was handed before it writes anything would otherwise have
    that word printed on the slide.

    Stray ideographs are read back into Hangul on the way through — see
    `services/hangul.py`. This is the door the model's own markup comes in by,
    and it is the last place the text is still only the model's, so a `傳統的인
    방화벽` never becomes something a person has to notice and press a button
    about.
    """
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
    #: The model that plans, when an administrator has named one. The outline
    #: is one call and decides the shape of every call after it, so it is the
    #: one place where a stronger model changes the result out of proportion
    #: to what it costs. Empty means the same model writes and plans.
    outline_model: str = "",
    #: The shape somebody has already seen and approved.
    #:
    #: Absent, this plans and stops: it emits `proposal` — or `needs`, when the
    #: material cannot carry the request — and writes nothing. Present, it
    #: skips planning and writes exactly what was approved, because planning
    #: again would produce a different document from the one agreed to.
    approved_plan: dict[str, Any] | None = None,
    #: Whether this pass may stop to ask.
    #:
    #: False on the pass that follows "있는 자료로 진행" — the button whose whole
    #: promise is that it will not be asked again. Without it the answer folds
    #: back into a request identical to the one that raised the question, the
    #: planner asks it again, and the button loops for as long as somebody
    #: keeps pressing it. Only this one pass is silenced; a later request that
    #: genuinely cannot be grounded is still allowed to say so.
    may_ask: bool = True,
    #: Whether to research this document before writing it. Same pass reports
    #: and decks run: queries planned off the request, top pages read in full,
    #: ahead of the outline so the shape is chosen from what is true.
    web_search: bool = True,
    project_sources: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `title`, `block`, a final `page` and one `usage` event.

    The caller owns persistence, billing and the artifact — this only writes.
    A block that fails is left empty and the rest continues, which for a page
    means a gap rather than nothing.
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
    noun, unit = _WORDS.get(template.kind, ("문서", "절"))
    wanted = requested_blocks(request)
    surface = template.surface

    findings = research.Findings()
    # Checked before the step is drawn, not inside `run`. A deployment with no
    # search backend would otherwise open every document with 자료 찾는 중 and
    # close it with 참고할 자료 없음 — a step that reports the deployment's
    # configuration as though it were this document's result.
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
    # Three states, and the writer is told which one it is in. A toggle
    # somebody switched off is a choice and needs no disclaimer; a search that
    # could not run and a search that found nothing are both worth saying, and
    # they do not mean the same thing to a reader.
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
        # A question instead of a plan — see `grounding.ASK_RULE`. Only when the
        # request names material the sources do not carry.
        if may_ask and (asked := grounding.parse_needs(text)):
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {"type": "needs", "questions": [q.wire() for q in asked]}
            yield {"type": "usage", **usage}
            return
        title, plan = _parse_outline(text, template)

        # A flat plan is the one thing a small model gets wrong that costs nothing
        # to notice and one call to fix: the seed styles five layouts, and a deck
        # that uses one of them is the seed's fault only in the sense that nobody
        # asked for the others. Asked once more, naming exactly what is missing —
        # and the second answer is kept only if it is actually less flat.
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
            # The failure with no exception behind it: the call succeeded and the
            # turn ends with no artifact. What the model actually said is the only
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
        # Planned, and that is where this stops. The shape is offered rather
        # than written into: the caller stores it, shows it, and calls back
        # with it approved. Nothing is written here, which is what keeps the
        # document already on screen safe from a run nobody confirmed.
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
                "progress": {"done": index + 1, "total": len(blocks)},
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
        guide=_guide(template),
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
    return _fragment(text, template), usage


def filled(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The blocks that actually say something — what billing and the message count."""
    return [b for b in blocks if (b.get("html") or "").strip()]


__all__ = ["filled", "requested_blocks", "rewrite_block", "write"]
