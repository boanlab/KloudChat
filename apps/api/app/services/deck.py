"""Building a deck, slide by slide.

Two-pass, like `report`: an outline call names the slides so the panel can show
the whole deck before any of it exists, then each slide is filled in on its own
call carrying what the previous ones said.

Four layouts, and only four:

* `title`      — the cover, always first
* `bullets`    — the body of the deck
* `quote`      — one line, for a claim worth pausing on
* `two-column` — a long list split in two, so the deck is not one shape

The frontend `Slide` type allows six. `chart` renders five hard-coded bars,
which would put invented numbers into a .pptx, and `image` has no producer
behind it — the model is offered only the layouts that exist in all three
renderers (preview, .pptx, .pdf).
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
from app.services import settings_store
from app.services.context import build_document_messages

log = logging.getLogger(__name__)

#: Slide-count bounds for an explicit request. One model call per slide, so the
#: ceiling is a runtime ceiling.
_MIN_SLIDES = 5
_MAX_SLIDES = 50

#: Upper bound when no number was asked for. Separate from the cap: an
#: unrequested outline running to 50 would charge 50 calls for a bare topic.
_DEFAULT_MAX = 12

#: The only layouts with a renderer behind them. See the module docstring.
_LAYOUTS = ("title", "bullets", "quote", "two-column")

#: One accent for the whole deck, applied to every slide. The preview, the
#: .pptx and the .pdf read from the same field, so they cannot drift apart.
_ACCENT = "#5b5bd6"

#: The palette rule, asked only when nothing has already decided the colour.
#: With a design system attached the accent arrives from the project, and
#: leaving the rule in would spend tokens on an answer that is then discarded —
#: worse, it would show the model a choice it does not have.
_THEME_RULE = """- theme 은 주제에 맞는 색 이름 하나다. 다음 중에서만 골라라:
  {themes}
"""

#: Accent palette the outline picks from by name. Curated rather than free hex:
#: each is dark enough to carry white text and to print.
_THEMES = {
    "보라": "#5b5bd6",
    "파랑": "#1f6feb",
    "청록": "#0f766e",
    "초록": "#15803d",
    "주황": "#c2410c",
    "빨강": "#b91c1c",
    "자주": "#a21caf",
    "남색": "#1e3a8a",
    "먹": "#334155",
}

_OUTLINE_PROMPT = """다음 요청에 맞는 발표 슬라이드의 제목과 구성을 만들어라.

규칙:
- title 은 표지에 적힐 한 줄이다. 요청 문장을 그대로 옮기지 말고 주제를 가리키는
  명사구로 써라. 마침표와 "~에 대한 발표" 같은 군말은 빼라.
- subtitle 은 표지에서 제목 아래 작게 붙는 한 줄이다. 40자 이내로, 이 발표가
  누구에게 무엇을 말하는지 적어라. 요청 문장을 그대로 옮기지 마라.
- 슬라이드 {lo}~{hi}장.
- 첫 장은 반드시 layout "title" 이고, 그 장의 제목은 발표 제목과 같게 하라.
- 나머지 장의 layout 은 "bullets" 를 기본으로 쓰고, 한 문장으로 강조할 대목이
  있을 때만 "quote" 를 써라. quote 는 전체에서 최대 2장.
- 항목이 6개 이상으로 많거나 둘을 나란히 견주는 장이면 layout 에
  "two-column" 을 써라. 같은 장이 계속 이어지면 발표가 지루해진다.
{theme_rule}- 각 장 제목은 그 장에서 말할 내용을 가리키는 짧은 구절로. 순서대로 넘기면
  하나의 발표가 되어야 한다.
- 내용은 쓰지 마라. 제목과 layout 만.
- 요청이 한 단어여도 되묻지 마라. 주제만 주어졌으면 그 주제를 처음 접하는
  사람에게 설명하는 발표로 네가 알아서 구성하라. 자료가 부족하다는 답은 하지 마라.
- 참고할 자료에 발표 양식·서식 문서가 있으면 그 문서의 장 순서를 그대로 따라라.
  장수도 그 양식을 따르고, 일반적인 발표 구성으로 바꾸지 마라.

JSON 객체로만 답하라.
예:
{{"title": "전이학습의 소량 데이터 효율성",
  "subtitle": "의료 영상 연구자를 위한 30분 개요",
  {theme_example}"slides": [{{"title": "전이학습의 소량 데이터 효율성", "layout": "title"}},
             {{"title": "왜 데이터가 부족한가", "layout": "bullets"}},
             {{"title": "사전학습과 미세조정 비교", "layout": "two-column"}}]}}

요청: {request}"""

_BULLETS_PROMPT = """너는 아래 발표의 "{heading}" 슬라이드 한 장만 쓰고 있다.

전체 구성:
{outline}

앞 장에서 이미 말한 내용:
{written}

규칙:
- bullets 는 {count}개. 각 항목은 한 줄, 40자 이내. 문장 부호로 끝내지 마라.
- 슬라이드는 읽는 글이 아니라 보는 화면이다. 문단을 넣지 마라.
- notes 는 이 장을 말로 설명할 때 할 이야기. 2~3문장.
- 앞 장에서 한 말을 되풀이하지 마라.
- 지어낸 수치를 쓰지 마라. 근거가 없으면 숫자 없이 써라.

JSON 객체로만 답하라.
예: {{"bullets": ["학습 데이터 확보 비용", "라벨링 품질 편차"], "notes": "여기서는 ..."}}

원래 요청: {request}"""

_QUOTE_PROMPT = """너는 아래 발표의 "{heading}" 슬라이드 한 장만 쓰고 있다.
이 장은 한 문장만 크게 띄우는 장이다.

전체 구성:
{outline}

앞 장에서 이미 말한 내용:
{written}

규칙:
- body 는 한 문장. 60자 이내. 이 발표에서 가장 남길 만한 한 줄.
- 실존 인물의 말을 인용하지 마라. 이 발표가 하는 주장을 써라.
- notes 는 이 장을 말로 설명할 때 할 이야기. 2~3문장.

JSON 객체로만 답하라.
예: {{"body": "데이터가 아니라 전이가 병목이다", "notes": "여기서는 ..."}}

원래 요청: {request}"""


class DeckError(RuntimeError):
    """The message is written for the person who asked."""


#: Waits between retries of a rate-limited call, in seconds.
_BACKOFF = (2.0, 6.0)


async def _complete(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int,
) -> tuple[str, dict]:
    """One non-streaming call. Returns `(text, usage)`. Retries a 429.

    A deck is dozens of calls in a row, the request most likely to meet a shared
    rate limit.
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
            log.info("deck call rate limited, retrying in %ss", _BACKOFF[attempt])
            await asyncio.sleep(_BACKOFF[attempt])
        response.raise_for_status()
        payload = response.json()

    text = (payload["choices"][0]["message"]["content"] or "").strip()
    raw = payload.get("usage") or {}
    return text, {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }


def _json_object(text: str) -> dict[str, Any]:
    """The first JSON object in whatever the model wrapped it in.

    Models fence their JSON, prefix it with a sentence, or both. Returning `{}`
    rather than raising lets each caller decide what a miss costs — for one
    slide it is a gap, for the outline it is the whole deck.
    """
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def requested_slides(request: str) -> int | None:
    """Slide count stated in the request, clamped to bounds. `None` if unstated.

    Parsed here rather than left to the model, which rounds it down quietly.
    """
    match = re.search(r"(\d{1,3})\s*(?:장|페이지|슬라이드|쪽)", request)
    if not match:
        return None
    asked = int(match.group(1))
    return max(_MIN_SLIDES, min(asked, _MAX_SLIDES)) if asked > 0 else None


def _theme_accent(text: str) -> str:
    """Accent named by the outline, or the default.

    Own regex rather than the parsed object, so a salvaged outline keeps colour.
    """
    match = re.search(r'"theme"\s*:\s*"([^"]+)"', text)
    return _THEMES.get((match.group(1).strip() if match else ""), _ACCENT)


def _parse_outline(text: str) -> tuple[str, str, list[dict[str, str]]]:
    """`(title, subtitle, plan)` where each plan entry is `{title, layout}`.

    A layout the renderer does not have is coerced to `bullets` rather than
    dropped: the model occasionally answers `chart` for a slide that is really
    a list, and losing the slide is worse than losing the layout.
    """
    data = _json_object(text)
    title = str(data.get("title") or "").strip()
    subtitle = str(data.get("subtitle") or "").strip()

    raw_slides = data.get("slides")
    plan: list[dict[str, str]] = []
    if isinstance(raw_slides, list):
        for item in raw_slides:
            if isinstance(item, dict):
                heading = str(item.get("title") or "").strip()
                layout = str(item.get("layout") or "bullets").strip().lower()
            else:
                heading, layout = str(item).strip(), "bullets"
            if not heading:
                continue
            plan.append(
                {"title": heading, "layout": layout if layout in _LAYOUTS else "bullets"}
            )

    if not plan:
        # Truncated JSON: a model that stops mid-array leaves text `json.loads`
        # rejects, though every object before the cut is intact.
        for match in re.finditer(r"\{[^{}]*?\}", text):
            item = re.search(r'"title"\s*:\s*"([^"]+)"', match.group(0))
            if not item:
                continue
            layout_match = re.search(r'"layout"\s*:\s*"([^"]+)"', match.group(0))
            layout = (layout_match.group(1) if layout_match else "bullets").strip().lower()
            plan.append(
                {
                    "title": item.group(1).strip(),
                    "layout": layout if layout in _LAYOUTS else "bullets",
                }
            )
        if plan and not title:
            # Document title precedes the slide array, so it is the first match.
            title = plan[0]["title"]

    if not plan:
        # A model that ignored the format still usually produced a list. Take the
        # bullet or numbered lines rather than failing the whole deck.
        for line in text.splitlines():
            if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line):
                heading = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line).strip(" #").strip()
                if heading:
                    plan.append({"title": heading, "layout": "bullets"})

    plan = plan[:_MAX_SLIDES]
    if plan:
        # The cover is structural, not the model's choice: the export and the
        # preview both key off slide one being the title card.
        plan[0]["layout"] = "title"
        if not title:
            title = plan[0]["title"]
        else:
            plan[0]["title"] = title
    return title, subtitle, plan


def _clean_bullets(value: Any) -> list[str]:
    """Bullets as short single lines, however the model formatted them."""
    items = value if isinstance(value, list) else []
    out: list[str] = []
    for item in items:
        text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", str(item)).strip()
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("`", "").strip()
        # A model that answered with a paragraph gets its first line used; the
        # rest would overflow the slide and be invisible in the .pptx anyway.
        text = text.splitlines()[0].strip() if text else ""
        if text:
            out.append(text[:80])
    return out[:6]


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    tokens: dict[str, str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `title`, `slide`, a final `deck` and one `usage` event.

    The caller owns persistence, billing and the artifact — this only writes.
    A slide that fails is marked and the rest continues, because eight slides
    and a gap is worth more than nothing.

    `tokens` is the project's design system, when it wears one. Its accent
    replaces the model's colour choice outright rather than being offered as a
    default: a deck that is nearly the project's colour is worse than one that
    is plainly not.
    """
    usage = {"inputTokens": 0, "outputTokens": 0}
    wanted = requested_slides(request)
    fixed_accent = (tokens or {}).get("accent") or ""

    yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "running"}
    try:
        text, spent = await _complete(
            model,
            build_document_messages(
                SessionKind.slides,
                _OUTLINE_PROMPT.format(
                    lo=wanted or _MIN_SLIDES,
                    hi=wanted or _DEFAULT_MAX,
                    theme_rule=(
                        "" if fixed_accent else _THEME_RULE.format(themes=" / ".join(_THEMES))
                    ),
                    theme_example="" if fixed_accent else '"theme": "청록",\n  ',
                    request=request[:2000],
                ),
                trusted_context=trusted_context,
                untrusted_context=untrusted_context,
            ),
            api_key,
            # Scaled with the slide count: a fixed ceiling truncates the JSON
            # on a long deck and the parse fails.
            max(600, 70 * (wanted or _DEFAULT_MAX) + 300),
        )
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("deck outline failed: %s", exc)
        yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "error"}
        yield {"type": "error", "message": "슬라이드 구성을 만들지 못했습니다."}
        yield {"type": "usage", **usage}
        return

    usage["inputTokens"] += spent["inputTokens"]
    usage["outputTokens"] += spent["outputTokens"]
    title, subtitle, plan = _parse_outline(text)
    accent = fixed_accent or _theme_accent(text)
    # Only an empty outline is a failure; a short one is a narrow topic.
    if not plan:
        yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "error"}
        yield {
            "type": "error",
            "message": "슬라이드 구성을 만들지 못했습니다. 요청을 조금 더 구체적으로 적어 주세요.",
        }
        yield {"type": "usage", **usage}
        return

    yield {
        "type": "step",
        "id": "outline",
        "label": f"구성 {len(plan)}장",
        "status": "done",
        "detail": " · ".join(item["title"] for item in plan),
    }
    if title:
        yield {"type": "title", "title": title[:200]}

    slides: list[dict[str, Any]] = [
        {
            "id": f"sl{i}_{uuid.uuid4().hex[:6]}",
            "layout": item["layout"],
            "title": item["title"],
            "accent": accent,
        }
        for i, item in enumerate(plan)
    ]
    # Announced up front: the panel greys out what is not written yet.
    for slide in slides:
        yield {"type": "slide", "slide": slide, "done": False}

    outline_text = "\n".join(f"{i + 1}. {s['title']}" for i, s in enumerate(slides))
    written: list[str] = []

    for index, slide in enumerate(slides):
        # The position lives in `progress`, not in the text: spelled into both,
        # the surface renders "3/9 도입 (3/9)".
        label = str(slide["title"])
        # The deck is planned before any of it is written, so every step can
        # say where it sits in that plan. Without it the reader watching a
        # ten-slide run has only the step in front of them and no idea how
        # many are behind it.
        progress = {"current": index + 1, "total": len(slides)}

        if slide["layout"] == "title":
            # The title slide needs no model call — both lines arrive with the
            # outline. The subtitle is trimmed rather than copied verbatim:
            # pasting the request in puts its question mark on the opening
            # slide of a presentation.
            slide["body"] = subtitle[:80]
            slide["notes"] = ""
            yield {
                "type": "step",
                "id": slide["id"],
                "label": label,
                "status": "done",
                "progress": progress,
            }
            yield {"type": "slide", "slide": slide, "done": True}
            continue

        yield {
            "type": "step",
            "id": slide["id"],
            "label": label,
            "status": "running",
            "progress": progress,
        }
        template = _QUOTE_PROMPT if slide["layout"] == "quote" else _BULLETS_PROMPT
        try:
            body, spent = await _complete(
                model,
                build_document_messages(
                    SessionKind.slides,
                    template.format(
                        heading=slide["title"],
                        outline=outline_text,
                        # Tail only: the whole deck would crowd out the rules.
                        written="\n".join(written)[-3000:] or "(아직 없음)",
                        # Fuller list for two columns; four bullets would leave
                        # one empty. `_QUOTE_PROMPT` ignores the extra field.
                        count="6~8" if slide["layout"] == "two-column" else "3~5",
                        request=request[:1500],
                    ),
                    trusted_context=trusted_context,
                    untrusted_context=untrusted_context,
                ),
                api_key,
                600,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("deck slide %r failed: %s", slide["title"], exc)
            yield {
                "type": "step",
                "id": slide["id"],
                "label": label,
                "status": "error",
                "progress": progress,
            }
            slide["body"] = "이 장을 쓰지 못했습니다."
            yield {"type": "slide", "slide": slide, "done": True}
            continue

        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        data = _json_object(body)
        notes = str(data.get("notes") or "").strip()

        if slide["layout"] == "quote":
            line = str(data.get("body") or "").strip().strip('"“”')
            if not line:
                # No usable quote — a slide with an empty headline is a blank
                # screen in the middle of a talk, so it becomes a bullets slide.
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
            else:
                slide["body"] = line[:120]
        else:
            slide["bullets"] = _clean_bullets(data.get("bullets"))

        if notes:
            slide["notes"] = notes[:800]

        summary = " / ".join(slide.get("bullets") or []) or slide.get("body") or ""
        written.append(f"{slide['title']}: {summary}")
        yield {
            "type": "step",
            "id": slide["id"],
            "label": label,
            "status": "done",
            "progress": progress,
        }
        yield {"type": "slide", "slide": slide, "done": True}

    yield {"type": "deck", "slides": slides}
    yield {"type": "usage", **usage}


def filled(slides: list[dict]) -> list[dict]:
    """The slides that actually have something on them.

    A cover counts: it carries the title. Anything else needs bullets or a line,
    otherwise it is a slide the model failed to write.
    """
    return [
        s
        for s in slides
        if s.get("layout") == "title" or (s.get("bullets") or (s.get("body") or "").strip())
    ]


def to_markdown(title: str, slides: list[dict]) -> str:
    """The deck as text, for the person who wants to paste it somewhere else."""
    parts = [f"# {title}", ""]
    for index, slide in enumerate(slides):
        if slide.get("layout") == "title" and index == 0:
            continue
        parts.append(f"## {slide.get('title') or ''}")
        if slide.get("body"):
            parts.append(f"\n> {slide['body']}")
        for bullet in slide.get("bullets") or []:
            parts.append(f"- {bullet}")
        if slide.get("notes"):
            parts.append(f"\n발표 노트: {slide['notes']}")
        parts.append("")
    return "\n".join(parts).strip() + "\n"


__all__ = ["DeckError", "filled", "to_markdown", "write"]
