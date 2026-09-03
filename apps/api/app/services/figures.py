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


#: For a picture somebody has already decided to put here.
#:
#: `_PROMPT` judges whether a document wants pictures at all and is right to
#: answer "none" most of the time. This is the other question: the person has
#: opened the picker on one 장 and the only thing left to decide is what to
#: draw. Answering "nothing" there would be answering a question nobody asked,
#: so this one always proposes — and says so in the caption, which the person
#: reads before spending a credit on it.
_ONE_PROMPT = """다음 자리에 넣을 그림 하나를 제안하라.

문서 제목: {title}
문서의 인상: {look}

이 자리의 제목: {about}

이 자리의 내용:
{context}

쓸 수 있는 그림 서식 — 하나를 골라라:
{catalogue}

규칙:
- 단계·절차·순서가 있으면 흐름도(infographic·pipeline), 구성 요소와 관계가
  있으면 아키텍처·구조도(architecture·method), 개념의 층위와 관계면 개념도
  (diagram), 기존과 제안의 대비면 티저(teaser), 장면·은유·실물이면 삽화
  (scene), 화면이면 목업(mockup). 문서의 인상에 어울리는 것을 고른다.
- 수치 비교는 그림이 아니라 표다. 표로 될 것은 제안하지 마라.
- 이 자리에 이미 있는 말을 그림으로 옮겨 적지 마라. 글이 못 하는 것을 그린다.
- 도식(figure 가 있는 서식)을 골랐으면 "description" 에 그릴 내용을 **한국어로
  구체적으로** 적는다 — 구성 요소 이름, 단계 이름, 관계. 이름표가 그대로 그림에
  들어간다. "prompt" 는 비운다.
- 그림 서식을 골랐으면 "prompt" 에 이미지 모델에 줄 영어 지시 한 문장을 적는다.
  글자·숫자·표를 넣지 마라 — 들어간 글자는 깨져서 나온다. "description" 은 비운다.
- caption: 그림 아래에 붙을 한 줄 설명. 한국어. 20자 안쪽.

JSON 객체로만 답하라.
예: {{"template": "image-scene", "caption": "무균실 작업 장면", "prompt": "photorealistic \
view of a cleanroom technician in white coveralls handling equipment, soft even \
lighting, shallow depth of field", "description": ""}}
예: {{"template": "image-architecture", "caption": "수집·분석·집행 구성", "prompt": "", \
"description": "이벤트 수집기가 커널 이벤트를 받아 분석 엔진으로 보내고, 분석 엔진이 \
정책 엔진에 판정을 넘기며, 집행기가 컨테이너에 정책을 적용한다. 대시보드는 분석 \
엔진에서 상태를 읽는다."}}"""


#: What the picker draws with, per document look. A minimal document wants a
#: minimal picture; the others take the 서식's own default.
_LOOK_STYLE = {"minimal": "미니멀"}
_LOOK_NAMES = {"editorial": "편집형", "poster": "포스터형", "minimal": "미니멀"}


def _catalogue_lines() -> str:
    """The image 서식 on offer, one per line, for the suggestion prompt."""
    from app.services import design_templates

    rows = []
    for template in design_templates.all_templates():
        if template.kind != "image" or template.id in ("image-cover", "image-poster-bg"):
            continue
        kind = f"도식({template.figure})" if template.figure else "그림"
        rows.append(f"- {template.id}: {template.name} — {template.description} [{kind}]")
    return "\n".join(rows)


@dataclass(slots=True)
class Figure:
    """One proposed picture, before anybody has agreed to pay for it."""

    #: Zero-based index into the document's parts.
    section: int
    caption: str
    prompt: str
    #: The image 서식 chosen for this place, when the suggestion chose one.
    template_id: str = ""
    #: `flow` / `method` / `concept` when that 서식 draws a mermaid figure.
    figure: str = ""
    #: The figure's description, for the diagram path.
    description: str = ""
    #: The style chip to draw a picture with.
    style: str = ""


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


async def suggest(
    *,
    title: str,
    about: str,
    context: str,
    model: str,
    api_key: str,
    look: str = "",
) -> Figure | None:
    """One picture for one place. `None` on any failure, never raises.

    The picker used to open on an empty box with the 장 title in the
    placeholder, which asks somebody who wanted a picture to first become the
    person who can describe one. The suggestion arrives filled in and editable:
    the decision left is whether to spend the credit, which is the decision
    that was theirs to make in the first place.
    """
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
                                catalogue=_catalogue_lines(),
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
    template_id = str(parsed.get("template") or "").strip()
    # 서식은 카탈로그에 있는 것만. A name the model made up draws nothing.
    from app.services import design_templates

    template = design_templates.get(template_id)
    if template is None or template.kind != "image":
        template, template_id = None, ""
    figure = template.figure if template else ""
    if figure and not description:
        # A figure with nothing to draw: fall back to a picture of it.
        figure = ""
    if not figure and not prompt:
        return None
    style = _LOOK_STYLE.get(look) or (
        str((template.defaults or {}).get("style") or "") if template else ""
    )
    return Figure(
        section=0,
        caption=str(parsed.get("caption") or "").strip()[:120],
        prompt=prompt[:600],
        template_id=template_id,
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
