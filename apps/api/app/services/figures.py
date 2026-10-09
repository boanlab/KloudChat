"""Proposes and prices document figures at outline time, before the prose is written.

Nothing here draws; approved prompts go to `imagegen`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from math import ceil
from typing import Any

import httpx

from app.services import settings_store

log = logging.getLogger(__name__)

MAX_FIGURES = 4

#: Assumed completion tokens per picture, for the approval-card estimate only;
#: the ledger charges actual usage.
_TOKENS_PER_IMAGE = 1000

_PROMPT = """다음 문서에 그림을 넣는다면 어디에 무엇을 넣어야 하는지 판정하라.

문서 제목: {title}

구성:
{outline}

원래 요청:
{request}

규칙:
- **구조도·흐름도·관계도는 제안하지 마라.** 그런 것은 도식으로 따로 그린다
  — 이미지 모델보다 정확하고, 글자가 깨지지 않으며, 값이 들지 않는다.
- 수치 비교는 그림이 아니라 표다. 표로 될 것은 제안하지 마라.
- 여기서 제안할 것은 **그림으로만 되는 것**이다. 개념을 은유로 보여 주는 삽화,
  실물의 모습, 분위기를 전달해야 하는 표지 같은 것.
- 장식은 제안하지 마라. 표지 그림, 분위기 사진, 아이콘은 보고서에 필요 없다.
- 넣을 자리가 없으면 빈 배열로 답하라. **없는 것이 정상이다** — 대부분의
  업무 문서에는 그림이 한 장도 필요 없다.
- 최대 {limit}장.

각 항목:
- section: 그림이 들어갈 절/장의 번호 (1부터)
- caption: 그림 아래에 붙을 한 줄 설명. 한국어.
- prompt: 이미지 모델에 줄 영어 지시. 글자는 최소로.

JSON 배열로만 답하라.
예: [{{"section": 2, "caption": "3계층 구성도", "prompt": "clean technical \
diagram of a three-tier architecture, flat vector style, labelled boxes, \
white background"}}]"""


#: For the picker on one section: always proposes, unlike `_PROMPT`.
_ONE_PROMPT = """다음 자리에 넣을 그림 하나를 제안하라.

문서 제목: {title}
문서의 인상: {look}

이 자리의 제목: {about}

이 자리의 내용:
{context}

그림의 종류 — 하나를 골라 "figure" 에 적어라:
- "flow": 흐름도. 단계·절차·순서가 있을 때.
- "method": 구조도. 구성 요소와 그 관계(아키텍처, 방법의 짜임)가 있을 때.
- "concept": 개념도. 개념의 층위와 관계일 때.
- "": 그림(삽화). 장면·은유·실물·화면처럼 이름표 없이 보여 줄 것일 때.

규칙:
- 구조·흐름·개념은 그림 모델이 아니라 도식으로 그린다 — 이름표가 깨지지 않는다.
- 수치 비교는 그림이 아니라 표다. 표로 될 것은 제안하지 마라.
- 이 자리에 이미 있는 말을 그림으로 옮겨 적지 마라. 글이 못 하는 것을 그린다.
- 도식("figure" 가 비어 있지 않음)이면 "description" 에 그릴 내용을 **한국어로
  구체적으로** 적는다 — 구성 요소 이름, 단계 이름, 관계. 이름표가 그대로 그림에
  들어간다. "prompt" 는 비운다.
- 그림이면 "prompt" 에 이미지 모델에 줄 영어 지시 한 문장을 적는다. 문서의 인상에
  어울리게. 글자·숫자·표를 넣지 마라 — 들어간 글자는 깨져서 나온다. "description" 은
  비운다.
- caption: 그림 아래에 붙을 한 줄 설명. 한국어. 20자 안쪽.

JSON 객체로만 답하라.
예: {{"figure": "", "caption": "무균실 작업 장면", "prompt": "photorealistic \
view of a cleanroom technician in white coveralls handling equipment, soft even \
lighting, shallow depth of field", "description": ""}}
예: {{"figure": "method", "caption": "수집·분석·집행 구성", "prompt": "", \
"description": "이벤트 수집기가 커널 이벤트를 받아 분석 엔진으로 보내고, 분석 엔진이 \
정책 엔진에 판정을 넘기며, 집행기가 컨테이너에 정책을 적용한다. 대시보드는 분석 \
엔진에서 상태를 읽는다."}}"""


#: Style chip per document look; looks not listed leave the choice to the planner.
_LOOK_STYLE = {"minimal": "미니멀"}

#: Mermaid figure kinds the suggestion may name; `diagrams.FIGURES` draws them.
_KINDS = ("flow", "method", "concept")
_LOOK_NAMES = {
    "editorial": "편집형",
    "poster": "포스터형",
    "minimal": "미니멀",
    "dark": "다크",
    "split": "분할형",
    "warm": "따뜻한",
    "mono": "흑백",
}


@dataclass(slots=True)
class Figure:
    """One proposed picture."""

    #: Zero-based index into the document's parts.
    section: int
    caption: str
    prompt: str
    #: `flow` / `method` / `concept` for a figure drawn as shapes; empty for a picture.
    figure: str = ""
    #: Diagram description, for the figure path.
    description: str = ""
    style: str = ""


@dataclass(slots=True)
class Proposal:
    """Proposed figures and their estimated cost."""

    figures: list[Figure] = field(default_factory=list)
    #: Approximate credits shown on the approval card.
    credits: int = 0
    #: The image model's display name.
    model: str = ""
    usage: dict[str, int] = field(default_factory=lambda: {"inputTokens": 0, "outputTokens": 0})

    def wire(self) -> dict[str, Any]:
        # Key names are the approval card's.
        return {
            "figures": [
                {"section": f.section, "caption": f.caption, "prompt": f.prompt}
                for f in self.figures
            ],
            "figureCredits": self.credits,
            "figureModel": self.model,
        }


def estimate(model: dict, count: int) -> int:
    """Credits for `count` pictures on this model, rounded up per picture."""
    per_out = model.get("creditCost") or 0
    if per_out == 0:
        return 0  # self-hosted
    return count * max(1, ceil(per_out * _TOKENS_PER_IMAGE / 1000))


async def propose(
    *,
    request: str,
    title: str,
    parts: list[str],
    model: str,
    api_key: str,
    image_model: dict | None,
    limit: int = MAX_FIGURES,
) -> Proposal:
    """Figures for this document's outline; empty is the common answer. Never raises."""
    if not parts or not image_model:
        return Proposal()

    outline = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(parts))
    base, _ = await settings_store.litellm_config()
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _PROMPT.format(
                                title=title[:200],
                                outline=outline[:4000],
                                request=request[:1500],
                                limit=limit,
                            ),
                        }
                    ],
                    "max_tokens": 600,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.info("figure planning failed: %s", exc)
        return Proposal()

    raw = payload.get("usage") or {}
    figures = _parse((payload["choices"][0]["message"]["content"] or "").strip(), len(parts), limit)
    return Proposal(
        figures=figures,
        credits=estimate(image_model, len(figures)),
        model=str(image_model.get("label") or image_model.get("id") or ""),
        usage={
            "inputTokens": int(raw.get("prompt_tokens") or 0),
            "outputTokens": int(raw.get("completion_tokens") or 0),
        },
    )


async def suggest(
    *,
    title: str,
    about: str,
    context: str,
    model: str,
    api_key: str,
    look: str = "",
) -> Figure | None:
    """One picture for one place, pre-filled for the picker. `None` on any failure, never raises."""
    base, _ = await settings_store.litellm_config()
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _ONE_PROMPT.format(
                                title=title[:200],
                                look=_LOOK_NAMES.get(look, "정해지지 않음"),
                                about=about[:200],
                                context=context[:2000],
                            ),
                        }
                    ],
                    "max_tokens": 400,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.info("figure suggestion failed: %s", exc)
        return None

    text = (payload["choices"][0]["message"]["content"] or "").strip()
    block = text[text.find("{") : text.rfind("}") + 1] if "{" in text and "}" in text else ""
    try:
        parsed = json.loads(block)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    prompt = str(parsed.get("prompt") or "").strip()
    description = str(parsed.get("description") or "").strip()
    figure = str(parsed.get("figure") or "").strip().lower()
    if figure not in _KINDS or not description:  # nothing to diagram; fall back to a picture
        figure = ""
    if not figure and not prompt:
        return None
    style = _LOOK_STYLE.get(look, "")
    return Figure(
        section=0,
        caption=str(parsed.get("caption") or "").strip()[:120],
        prompt="" if figure else prompt[:600],
        figure=figure,
        description=description[:3000],
        style=style,
    )


def _parse(text: str, count: int, limit: int) -> list[Figure]:
    block = text[text.find("[") : text.rfind("]") + 1] if "[" in text and "]" in text else ""
    try:
        parsed = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []

    out: list[Figure] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            section = int(item.get("section", 0)) - 1
        except (TypeError, ValueError):
            continue
        prompt = str(item.get("prompt") or "").strip()
        if not (0 <= section < count) or not prompt:
            continue
        # One picture per part.
        if any(f.section == section for f in out):
            continue
        out.append(
            Figure(
                section=section,
                caption=str(item.get("caption") or "").strip()[:200],
                prompt=prompt[:600],
            )
        )
        if len(out) >= limit:
            break
    return out


def note_for(figure: Figure) -> str:
    """Section-prompt note so the prose can refer to the figure."""
    return (
        f"이 절에는 그림이 한 장 들어갑니다: {figure.caption or '도해'}. "
        "본문에서 그 그림을 한 번 언급하되, 그림이 말하는 것을 글로 다시 "
        "설명하지는 마세요."
    )


__all__ = ["MAX_FIGURES", "Figure", "Proposal", "estimate", "note_for", "propose"]
