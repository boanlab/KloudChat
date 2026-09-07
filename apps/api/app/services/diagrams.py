"""Figures a document draws for itself: which parts want one, and the mermaid for each.

One planning call reads the drafted parts and names the places where a structure, flow,
comparison or concept figure says more than the words. Each chosen part then goes through
`diagram.draw`, which writes labelled mermaid in the house style. A deck keeps the result
beside the slide's words (`slide["diagram"]`, rasterised by the browser); a report appends a
mermaid fence its editor renders and stores. No image model and no approval card: the only
cost is the writer's own tokens, and a person can still replace any figure with a drawn
picture afterwards.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from app.services.report_export import diagram_key

log = logging.getLogger(__name__)

#: Figure kinds a document may draw for itself, with the name a reader sees.
FIGURES = {"method": "구조도", "flow": "흐름도", "compare": "비교도", "concept": "개념도"}

#: Figures per document; more than this and the deck reads as a picture book.
MAX_FIGURES = 3

Completion = Callable[[str, list[dict[str, str]], str, int], Awaitable[tuple[str, dict]]]
Wrap = Callable[[str], list[dict[str, str]]]

_PROMPT = """아래는 {what}의 부분들이다. 각 부분의 제목과 내용을 읽고, **글보다 도식이 더
잘 전달하는 곳**에만 도식을 하나씩 제안하라.

원래 요청:
{request}

부분:
{parts}

도식의 종류 — 넷 중 하나:
- method: 구조도. 구성 요소와 그 사이의 관계·데이터 흐름이 내용일 때.
- flow: 흐름도. 단계·절차·순서, 입력이 결과가 되기까지가 내용일 때.
- compare: 비교도. 기존과 제안, 또는 두 안의 대비가 내용일 때.
- concept: 개념도. 개념들의 층위와 관계가 내용일 때.

규칙:
- 수치의 비교는 표나 차트지 도식이 아니다. 제안하지 마라.
- 「(표 있음)」이라고 적힌 부분은 이미 표로 비교한 곳이다. 거기에 비교도를 다시
  그리지 마라. 비교도는 구조나 흐름 자체가 다를 때만 그린다.
- 내용이 나열이나 서술뿐이고 구조·흐름·대비·층위가 없으면 제안하지 마라.
  **없는 것이 정상이다.** 억지로 채우지 마라. 이점·효과·특징·요구 사항의 나열은
  개념도가 아니다. 개념도는 상위 개념 아래 하위 개념이 놓이는 층위나 개념 사이의
  관계가 본문에 **적혀 있을 때만** 그린다.
- 한 부분에 하나, 서로 다른 부분에, 최대 {limit}개.
- description 에는 그릴 내용을 **한국어로 구체적으로** 적는다: 구성 요소나 단계의
  이름, 그 사이의 관계와 방향, 비교도라면 양쪽의 이름과 마주 볼 항목. 이름은 그
  부분의 본문에 쓰인 용어 그대로. 본문에 없는 요소를 지어내지 마라.
- caption 은 {caption_rule}

JSON 배열로만 답하라. 없으면 [].
예: [{{"part": 3, "figure": "flow", "description": "입력 문서가 검색기 → 계획기 → \
스타일 조정기를 차례로 거쳐 초안이 되고, 검토기의 의견이 계획기로 되돌아간다", \
"caption": "생성 흐름"}}]"""


_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}", re.M)


def _has_table(text: str) -> bool:
    """Whether a part already carries a Markdown table (a rule row under a head)."""
    return bool(_TABLE_RULE.search(text or ""))


@dataclass(frozen=True, slots=True)
class Planned:
    """One figure the planner asked for, on part `index` (0-based)."""

    index: int
    figure: str
    description: str
    caption: str


async def plan(
    *,
    parts: list[tuple[str, str]],
    eligible: Iterable[int],
    request: str,
    model: str,
    api_key: str,
    complete: Completion,
    slide: bool,
    wrap: Wrap | None = None,
    limit: int = MAX_FIGURES,
) -> tuple[list[Planned], dict[str, int]]:
    """Figures for these `(title, text)` parts; only `eligible` indices may get one.

    `complete` is the caller's completion function (the deck's or the report's), so the
    call is priced and retried like the writer's own; `wrap` puts the prompt into the
    caller's messages (system rules, reference blocks), so the planner sees the same
    sources as the writer and nothing else. Never raises: an unusable answer is an
    empty plan.
    """
    allowed = sorted(set(eligible))
    if not allowed:
        return [], {"inputTokens": 0, "outputTokens": 0}
    listed = "\n\n".join(
        f"[{index + 1}] {parts[index][0]}{' (표 있음)' if _has_table(parts[index][1]) else ''}\n"
        f"{parts[index][1][:1200] or '(내용 없음)'}"
        for index in allowed
    )
    prompt = _PROMPT.format(
        what="발표 슬라이드" if slide else "보고서",
        request=request[:1500],
        parts=listed[:9000],
        limit=limit,
        caption_rule=(
            "12자 안쪽의 이름이다. 「그림」이라는 말은 넣지 않는다."
            if slide
            else "무엇을 보여 주는 그림인지 말하는 한 문장이다."
        ),
    )
    messages = wrap(prompt) if wrap else [{"role": "user", "content": prompt}]
    try:
        text, usage = await complete(model, messages, api_key, 900)
    except Exception as exc:  # noqa: BLE001 — a document without figures is still a document
        log.info("figure planning failed: %s", exc)
        return [], {"inputTokens": 0, "outputTokens": 0}
    return parse(text, count=len(parts), limit=limit, eligible=allowed), usage


def parse(text: str, *, count: int, limit: int, eligible: Iterable[int]) -> list[Planned]:
    """The planner's answer as `Planned` rows: known kinds, eligible parts, one per part."""
    block = text[text.find("[") : text.rfind("]") + 1] if "[" in text and "]" in text else ""
    try:
        parsed = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    allowed = set(eligible)
    out: list[Planned] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("part", 0)) - 1
        except (TypeError, ValueError):
            continue
        figure = str(item.get("figure") or "").strip().lower()
        description = " ".join(str(item.get("description") or "").split())
        if index not in allowed or figure not in FIGURES or len(description) < 8:
            continue
        if any(row.index == index for row in out):
            continue
        out.append(
            Planned(
                index=index,
                figure=figure,
                description=description[:1200],
                caption=" ".join(str(item.get("caption") or "").split())[:120],
            )
        )
        if len(out) >= limit:
            break
    return out


async def make(
    planned: Planned, *, model: str, api_key: str, slide: bool
) -> tuple[dict[str, Any], dict[str, int]]:
    """The mermaid for one planned figure, with its key for the browser's raster.

    Raises when the model does not produce a diagram; callers leave the part as words.
    """
    # Imported here: `diagram` reaches the report writer, which reaches the deck rules.
    from app.services import diagram

    source, caption, usage = await diagram.draw(
        description=planned.description,
        figure=planned.figure,
        model=model,
        api_key=api_key,
        slide=slide,
    )
    return {
        "figure": planned.figure,
        "description": planned.description,
        "source": source,
        "caption": (planned.caption or caption)[:120],
        "key": diagram_key(source),
    }, {
        "inputTokens": int(usage.get("inputTokens") or 0),
        "outputTokens": int(usage.get("outputTokens") or 0),
    }


def fence(made: dict[str, Any]) -> str:
    """The figure as the Markdown a report section carries: a mermaid fence and its caption."""
    caption = str(made.get("caption") or "").strip()
    block = f"```mermaid\n{str(made.get('source') or '').strip()}\n```"
    return f"{block}\n\n*그림: {caption}*" if caption else block


__all__ = ["FIGURES", "MAX_FIGURES", "Planned", "fence", "make", "parse", "plan"]
