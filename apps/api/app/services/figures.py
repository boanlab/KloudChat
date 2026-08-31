"""Pictures a document asks for, proposed before it is written.

A report of nothing but prose is the complaint this answers. The writers could
never make a figure — the surfaces that produce a document leave the chat
pipeline at submit and are handed no tools, the image model is a different
model on a different endpoint, and nothing joined the two.

Two things decide the shape of this.

**Asked before the writing, not after.** The obvious place is at the end: write
the document, then offer to illustrate it. It is the wrong place. The prose
refers to what is beside it — 아래 그림과 같이, 표 1에서 보듯 — so a document
written expecting figures and then declined reads as broken, and one written
without them and illustrated afterwards has pictures nobody referred to. The
figures belong in the outline, where the person is already being asked to
approve a shape.

**Priced in the question.** A picture costs multiples of what the whole report
costs to write, and on a per-image model the charge is per picture. An approval
that does not say how many and how much is not an approval.

Nothing here draws anything. It proposes, prices, and — once somebody has said
yes — hands each prompt to `imagegen` and the results back to the writer.
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

#: Pictures one document may propose. Past this the deck is illustration with
#: prose between it, and the bill stops being something anybody skims.
MAX_FIGURES = 4

#: What one picture is assumed to cost, in output tokens, for the estimate
#: shown on the approval card.
#:
#: The models bill a picture as completion tokens and the count varies with
#: size and detail; measured across the catalogue's image models it lands near
#: a thousand. The card says 약, and the ledger charges what actually ran —
#: this number only has to be close enough that nobody is surprised by the
#: order of magnitude.
_TOKENS_PER_IMAGE = 1000

_PROMPT = """다음 문서에 그림을 넣는다면 어디에 무엇을 넣어야 하는지 판정하라.

문서 제목: {title}

구성:
{outline}

원래 요청:
{request}

규칙:
- **구조도·흐름도·관계도는 제안하지 마라.** 그런 것은 본문에 mermaid 로 그린다
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


@dataclass(slots=True)
class Figure:
    """One proposed picture, before anybody has agreed to pay for it."""

    #: Zero-based index into the document's parts.
    section: int
    caption: str
    prompt: str


@dataclass(slots=True)
class Proposal:
    """What was proposed, and what it will cost to say yes."""

    figures: list[Figure] = field(default_factory=list)
    #: Credits, as shown on the approval card. Approximate on purpose.
    credits: int = 0
    #: The image model's display name, so the card names what will draw them.
    model: str = ""
    usage: dict[str, int] = field(default_factory=lambda: {"inputTokens": 0, "outputTokens": 0})

    def wire(self) -> dict[str, Any]:
        # Keys the pending row carries straight through to the card, so the
        # names are the card's rather than this module's.
        return {
            "figures": [
                {"section": f.section, "caption": f.caption, "prompt": f.prompt}
                for f in self.figures
            ],
            "figureCredits": self.credits,
            "figureModel": self.model,
        }


def estimate(model: dict, count: int) -> int:
    """Credits for `count` pictures on this model. Never understates.

    Rounded up per picture rather than over the total: somebody reading `약 2`
    beside two pictures and being charged 3 has been told the wrong thing, and
    the rounding that produces that is the one worth avoiding.
    """
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
    """Where this document wants pictures, if anywhere. Never raises.

    An empty proposal is the common answer and the right one. Most work
    documents need no figure at all, and a planner that finds one every time is
    a planner nobody will read the card of.
    """
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
        # One picture per part. Two figures in one section is a section that
        # wanted a diagram, not two.
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
    """What the writer is told about the picture beside its section.

    Given to the section prompt so the prose can refer to the figure — which is
    the whole reason this is asked before the writing rather than after it.
    """
    return (
        f"이 절에는 그림이 한 장 들어갑니다: {figure.caption or '도해'}. "
        "본문에서 그 그림을 한 번 언급하되, 그림이 말하는 것을 글로 다시 "
        "설명하지는 마세요."
    )


__all__ = ["MAX_FIGURES", "Figure", "Proposal", "estimate", "note_for", "propose"]
