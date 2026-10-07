"""Figures a document draws for itself: which parts want one, and the mermaid for each.

One planning call picks the parts where a figure says more than the words; each goes
through `diagram.draw`. A deck keeps the result in `slide["diagram"]`, a report appends a
mermaid fence. No image model and no approval card: the cost is the writer's own tokens.
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

#: Figures per document.
MAX_FIGURES = 3

Completion = Callable[[str, list[dict[str, str]], str, int], Awaitable[tuple[str, dict]]]
Wrap = Callable[[str], list[dict[str, str]]]

_STRICT_RULES = """규칙:
- 수치의 비교는 표나 차트지 도식이 아니다. 제안하지 마라.
- 「(표 있음)」이라고 적힌 부분은 이미 표로 비교한 곳이다. 거기에 비교도를 다시
  그리지 마라. 비교도는 구조나 흐름 자체가 다를 때만 그린다.
- 내용이 나열이나 서술뿐이고 구조·흐름·대비·층위가 없으면 제안하지 마라.
  **없는 것이 정상이다.** 억지로 채우지 마라. 이점·효과·특징·요구 사항의 나열은
  개념도가 아니다. 개념도는 상위 개념 아래 하위 개념이 놓이는 층위나 개념 사이의
  관계가 본문에 **적혀 있을 때만** 그린다.
"""

#: Report planner rules: every part is a candidate, and a table does not rule out a
#: comparison figure.
_REPORT_RULES = """규칙:
- 보고서는 그림이 있어야 읽힌다. 각 부분에서 **구성(요소와 관계)·흐름(단계·순서)·
  대비(두 쪽 또는 여러 유형)·층위(상위·하위 개념, 분류)** 가운데 하나라도 본문에
  적혀 있으면 그 부분에 도식을 제안하라. 분류(대상 환경 3갈래, 기능 4축 같은 것)는
  개념도, 배포·처리 경로는 구조도나 흐름도, 두 방식의 대비는 비교도다.
- 표가 있는 부분에도 비교도를 그릴 수 있다. 표의 숫자를 옮기지 말고, 두 쪽의
  구조·흐름·구성 요소가 어떻게 다른지를 그려라.
- 수치 자체(점유율, 금액, 비율)를 보여 주는 그림은 차트의 일이다. 제안하지 마라.
- 서론·요약·결론·참고문헌처럼 구조가 없는 부분은 건너뛴다.
"""

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

{rules}- 한 부분에 하나, 서로 다른 부분에, 최대 {limit}개.
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
    at_least: int = 0,
) -> tuple[list[Planned], dict[str, int]]:
    """Figures for these `(title, text)` parts; only `eligible` indices may get one. Never raises.

    `complete` and `wrap` are the caller's, so the call is priced like the writer's and sees
    the same sources.
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
        rules=_STRICT_RULES if slide else _REPORT_RULES,
        caption_rule=(
            "12자 안쪽의 이름이다. 「그림」이라는 말은 넣지 않는다."
            if slide
            else "무엇을 보여 주는 그림인지 말하는 한 문장이다."
        ),
    )
    messages = wrap(prompt) if wrap else [{"role": "user", "content": prompt}]
    usage = {"inputTokens": 0, "outputTokens": 0}
    try:
        text, usage = await complete(model, messages, api_key, 900)
    except Exception as exc:  # noqa: BLE001 — a document without figures is still a document
        log.info("figure planning failed: %s", exc)
        text = "[]"
    planned = parse(text, count=len(parts), limit=limit, eligible=allowed)
    asked_for = not planned and asks_for_diagrams(request)
    short = len(planned) < min(at_least, len(allowed))
    if asked_for or short:
        # Explicitly requested diagrams, or fewer than `at_least`: one retry, told why.

        if asked_for:
            asked = "·".join(dict.fromkeys(_ASKS_DIAGRAM.findall(request or ""))) or "도식"
            reason = (f"원래 요청이 {asked}를 명시적으로 요구한다. "
                      "요구한 종류마다 그것이 적힌 부분을")
        else:
            reason = (f"이 문서에는 도식이 적어도 {at_least}개 필요하다. 구성·흐름·대비·분류가 "
                      "가장 뚜렷하게 적힌 부분을")
        nudged = prompt + (
            f"\n\n{reason} 찾아 하나씩 제안하라(구조가 적힌 부분엔 method, 단계·순서가 적힌 "
            "부분엔 flow, 두 안의 대비가 적힌 부분엔 compare, 분류엔 concept). "
            "정말 없을 때만 [] 로 답하라."
        )
        try:
            text, more = await complete(
                model, wrap(nudged) if wrap else [{"role": "user", "content": nudged}], api_key, 900
            )
            usage = {k: usage.get(k, 0) + more.get(k, 0) for k in ("inputTokens", "outputTokens")}
            planned = parse(text, count=len(parts), limit=limit, eligible=allowed)
        except Exception as exc:  # noqa: BLE001
            log.info("figure planning retry failed: %s", exc)
    return planned, usage


_ASKS_DIAGRAM = re.compile(
    r"구조도|흐름도|비교도|개념도|다이어그램|도식|도해|아키텍처\s*(?:그림|도)|"
    # 「구조와 흐름은 그림으로」: a structure or flow asked for as a picture is a diagram.
    r"(?:구조|흐름|관계|과정|절차|단계)[^\n.]{0,12}?그림으로|그림으로\s*(?:그려|보여|표현|나타)|시각화|도식화|"
    r"\b(?:diagram|flowchart|architecture\s+figure)\b",
    re.I,
)


def asks_for_diagrams(request: str) -> bool:
    """Whether the request itself names a diagram it wants."""
    return bool(_ASKS_DIAGRAM.search(request or ""))


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
