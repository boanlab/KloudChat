"""Building a deck, slide by slide.

Two-pass, like `report`: an outline call names the slides so the panel can show
the whole deck before any of it exists, then each slide is filled in on its own
call carrying what the previous ones said.

Seven layouts, and only seven:

* `title`      — the cover, always first
* `bullets`    — the body of the deck
* `quote`      — one line, for a claim worth pausing on
* `two-column` — a long list split in two, so the deck is not one shape
* `table`      — values read against each other, as a real table
* `metrics`    — two or three figures, set large, for the numbers to remember
* `chart`      — a bar or line chart, drawn from real numbers

The rule is that a layout is offered only if all three renderers — the
preview, the .pptx and the .pdf — can draw it. `table` was drawable in two of
them for a long time and offered in none: the exporters had `rows` and the
model was never asked for one, so the commonest slide in a working deck came
out as six bullets the reader had to rebuild a table from in their head.

`image` is the one the frontend type still allows and nothing produces.
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
from app.services import (
    figures,
    grounding,
    hangul,
    imagegen,
    pictures,
    research,
    settings_store,
    thinking,
)
from app.services import outline as plan_rules
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
_LAYOUTS = (
    "title",
    "section",
    "bullets",
    "quote",
    "two-column",
    "table",
    "metrics",
    "chart",
    "bands",
    "tiles",
    "timeline",
)

#: The layouts that carry the argument. Named rather than sliced off the front
#: of `_LAYOUTS`: `_LAYOUTS[1:]` meant "everything but the cover" only for as
#: long as the cover was the only layout with no content in it, and adding the
#: section divider made the variety check demand that a deck use dividers.
#: A divider says where you are; it is not one of the shapes an argument takes.
_BODY_LAYOUTS = tuple(layout for layout in _LAYOUTS if layout not in ("title", "section"))

#: What a slide says when it did not get written.
#:
#: Shared with `deck_export`, which leaves such a slide out of the file. Two
#: copies of the sentence would be two chances for the file to carry it.
UNWRITTEN = "이 장을 쓰지 못했습니다."

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
- **수치의 모양이 요점인 장은 "chart" 로 잡아라.** 시간에 따른 추이, 항목별
  크기 비교처럼 값이 여럿이고 그 **모양**을 봐야 하는 내용이다. 값을 하나하나
  읽어야 하면 "table", 하나만 기억시키면 "metrics", 모양이면 "chart" 다.
- **기억시킬 숫자가 두셋인 장은 "metrics" 로 잡아라.** 성과, 규모, 목표치처럼
  값 자체가 요점인 내용이다. 표는 견주는 자리고 metrics 는 하나를 남기는
  자리다 — 같은 숫자를 두 장에 나눠 쓰지 마라.
- **같은 기준으로 두셋을 견주는 장은 "table" 로 잡아라.** 대안 비교, 전후 대비,
  단계별 조건처럼 값이 기준마다 갈리는 내용이다. 이런 내용을 bullets 로 늘어
  놓으면 읽는 사람이 머릿속에서 표를 다시 그려야 한다.
- 나머지 장은 말할 내용에 맞는 layout 을 골라라. 항목을 나열하면 "bullets",
  둘을 나란히 견주거나 항목이 6개 이상이면 "two-column", 한 문장으로 남길
  대목이면 "quote". quote 는 전체에서 최대 2장.
- **왼쪽에 이름표를 달고 오른쪽에 내용을 놓는 장은 "bands" 로 잡아라.** 항목마다
  이름이 붙는 내용이다 — 미션·배경·추진전략, 대상·기간·방식·수료, 학점·증명·연계
  처럼. 이런 장 제목은 대개 "~은 무엇인가", "~ 개요", "~ 체계", "혜택" 이다.
  같은 것을 bullets 로 쓰면 이름이 문장의 첫 낱말이 되고 이름이기를 그만둔다.
- **머리글자나 번호로 묶인 것을 보여 주는 장은 "tiles" 로 잡아라.** 4대 분야,
  3대 전략, P·H·A·S·E 처럼 표식을 크게 세우고 그 아래 이름을 붙인다. 제목에
  "N대", "핵심 N가지", "구성 요소" 가 있으면 거의 이 장이다.
- **시간 순서가 요점인 장은 "timeline" 으로 잡아라.** 연혁, 일정, 절차, 단계,
  로드맵. 제목에 "절차", "단계", "일정", "연혁", "로드맵" 이 있으면 이 장이다.
  이런 내용을 bullets 로 쓰면 순서가 우연처럼 읽힌다.
- **열 장을 넘는 발표에서 이야기가 갈리는 자리에는 "section" 을 한 장 넣어라.**
  그 뒤에 오는 묶음의 이름만 적는 간지다. number 에 "01." 처럼 순서를 적고,
  제목은 그 묶음의 이름으로 한다. 내용은 쓰지 마라 — 간지에 항목을 적으면
  그건 간지가 아니라 목차다. 짧은 발표에는 넣지 마라.
- **겁을 주거나 재촉하는 장은 만들지 마라.** "기회 손실", "마감 임박", "지금
  결정하지 않으면" 같은 장은 내용이 없을 때 분량을 채우려고 만드는 장이다.
  듣는 사람이 알아야 할 사실을 적고, 판단은 그 사람에게 맡겨라.
- **한 장에는 그 장에서만 하는 말을 담아라.** 앞 장을 다른 낱말로 다시 쓴 장은
  한 장이 아니라 여백이다.
- 같은 layout 을 세 장 연속으로 쓰지 마라. 표지를 뺀 나머지에서 최소 세 가지를
  써라. 한 가지로 끌고 간 발표는 넘겨도 넘긴 것 같지 않다.
{theme_rule}- 각 장 제목은 그 장에서 말할 내용을 가리키는 짧은 구절로. 순서대로 넘기면
  하나의 발표가 되어야 한다.
- 내용은 쓰지 마라. 제목과 layout 만.
{ask_rule}
- 참고할 자료에 발표 양식·서식 문서가 있으면 그 문서의 장 순서를 그대로 따라라.
  장수도 그 양식을 따르고, 일반적인 발표 구성으로 바꾸지 마라.

JSON 객체로만 답하라.
예:
{{"title": "전이학습의 소량 데이터 효율성",
  "subtitle": "의료 영상 연구자를 위한 30분 개요",
  {theme_example}"slides": [{{"title": "전이학습의 소량 데이터 효율성", "layout": "title"}},
             {{"title": "왜 데이터가 부족한가", "layout": "bullets"}},
             {{"title": "사전학습과 미세조정 비교", "layout": "table"}}]}}

요청: {request}"""

#: Which prompt each layout is written with. `title` never reaches here — the
#: cover is filled from the outline — and anything unknown falls back to
#: bullets, which is the shape that works for any content.
_PROMPTS: dict[str, str] = {}

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

_TABLE_PROMPT = """너는 아래 발표의 "{heading}" 슬라이드 한 장만 쓰고 있다.
이 장은 값을 나란히 놓고 견주는 표 한 장이다.

전체 구성:
{outline}

앞 장에서 이미 말한 내용:
{written}

규칙:
- rows 는 표의 줄이다. **첫 줄이 머리글**이고, 나머지가 값이다.
- **열은 2~4개, 줄은 머리글 포함 3~6줄.** 화면에 띄우는 표다. 그보다 크면
  뒷자리에서 읽히지 않는다.
- 칸 하나는 **12자 이내**. 문장을 넣지 마라 — 표는 읽는 글이 아니라 견주는
  자리다. 설명이 필요하면 notes 에 적어라.
- 첫 열은 견주는 **기준**이고, 나머지 열이 그 기준에 대한 값이다.
- 지어낸 수치를 쓰지 마라. 근거가 없으면 숫자 없이 써라.
- notes 는 이 표를 말로 설명할 때 할 이야기. 2~3문장.

JSON 객체로만 답하라.
예: {{"rows": [["기준", "대안 A", "대안 B"],
              ["초기 비용", "0원", "약 3억"],
              ["도입 기간", "2주", "4개월"]],
      "notes": "여기서는 ..."}}

원래 요청: {request}"""

_CHART_PROMPT = """너는 아래 발표의 "{heading}" 슬라이드 한 장만 쓰고 있다.
이 장은 수치를 그래프로 보여 주는 장이다.

전체 구성:
{outline}

앞 장에서 이미 말한 내용:
{written}

규칙:
- chart.kind 는 "bar" 또는 "line". 항목별 크기 비교는 bar, 시간에 따른 추이는
  line.
- chart.categories 는 가로축 항목. **3~8개.** 이름은 8자 이내.
- chart.series 는 계열 목록. **1~2개.** 각 계열의 values 는 categories 와
  **개수가 같아야 한다.**
- chart.unit 은 세로축 단위 한 마디 — `건`, `%`, `억 원`. 없으면 빈 문자열.
- **지어낸 수치를 쓰지 마라. 근거가 없으면 이 장을 쓰지 말고 bullets 로 답하라.**
  그래프는 숫자보다 더 사실처럼 읽힌다.
- notes 는 이 그래프가 무엇을 보여 주는지. 2~3문장.

JSON 객체로만 답하라.
예: {{"chart": {{"kind": "bar", "unit": "건",
                "categories": ["1분기", "2분기", "3분기", "4분기"],
                "series": [{{"name": "처리 건수", "values": [120, 210, 380, 460]}}]}},
      "notes": "여기서는 ..."}}

원래 요청: {request}"""

_METRICS_PROMPT = """너는 아래 발표의 "{heading}" 슬라이드 한 장만 쓰고 있다.
이 장은 숫자 두셋을 크게 띄우는 장이다.

전체 구성:
{outline}

앞 장에서 이미 말한 내용:
{written}

규칙:
- metrics 는 `[값, 이름]` 의 목록이다. **2~4개.**
- 값은 **짧게** — `32%`, `1.4초`, `3억 원`. 문장을 넣지 마라.
- 이름은 그 숫자가 무엇인지 한 마디로. **10자 이내.**
- 지어낸 수치를 쓰지 마라. **근거가 없으면 이 장을 쓰지 말고 bullets 로 답하라.**
  화면에 크게 띄운 숫자는 다른 어떤 것보다 사실처럼 읽힌다.
- notes 는 이 숫자들이 어디서 온 값이고 무엇을 뜻하는지. 2~3문장.

JSON 객체로만 답하라.
예: {{"metrics": [["32%", "오탐 감소"], ["1.4초", "평균 응답"]],
      "notes": "여기서는 ..."}}

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
                    "reasoning": thinking.REASONING_CAP,
                },
            )
            if again.status_code >= 400:
                # A gateway that does not know `reasoning` refuses the whole
                # call. The ceiling alone still helps every model that does not
                # scale its thinking to it.
                again = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": messages, "max_tokens": bigger},
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

    Stray ideographs are read back into Hangul here rather than field by field
    downstream: a slide is a title, a body, bullets, metric labels and table
    cells, and every one of them is a place `動的 엔드포인트` has turned up. The
    substitution is over the JSON text, which is safe because the keys are
    ASCII and the values are exactly what will be shown.
    """
    text, _ = hangul.read_back(text)
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


_PAIRED_PROMPT = """너는 아래 발표의 "{heading}" 슬라이드 한 장만 쓰고 있다.
{what}

전체 구성:
{outline}

앞 장에서 이미 말한 내용:
{written}

규칙:
{rules}
- 지어낸 내용을 쓰지 마라. 쓸 것이 없으면 이 장을 bullets 로 답하라.
- notes 는 발표자가 이 장에서 말할 내용. 2~3문장.

JSON 객체로만 답하라.
예: {example}

원래 요청: {request}"""

#: What each paired layout is for, and what its two halves hold. Written out
#: rather than generated: a model told "왼쪽과 오른쪽을 채워라" fills both with
#: sentences, and the whole point of the shape is that the left half is a name.
_PAIRED_RULES = {
    "bands": (
        "이 장은 왼쪽에 이름표, 오른쪽에 그 내용을 띠로 놓는 장이다.",
        "- bands 는 `[이름, 내용]` 의 목록이다. **3~4개.**\n"
        "- 이름은 그 줄이 무엇인지 가리키는 한 마디. **10자 이내** — 미션, 배경,"
        " 추진전략, 기대효과 처럼.\n"
        "- 내용은 한 문장. **90자 이내.** 문장을 둘 넣지 마라.\n"
        "- 이름은 서로 겹치지 않는 다른 것이어야 한다.",
        '{{"bands": [["미션", "선제적으로 AI 신기술에 대응하여 차세대 인재를 양성한다"],'
        ' ["배경", "AI 전환기에 기술수요와 인재 격차가 벌어지고 있다"]],'
        ' "notes": "여기서는 ..."}}',
    ),
    "tiles": (
        "이 장은 글자나 번호를 크게 세우고 그 아래 이름을 붙이는 장이다.",
        "- tiles 는 `[글자, 이름]` 의 목록이다. **3~6개.**\n"
        "- 글자는 **4자 이내** — P, H, A, 01, ① 처럼 한눈에 들어오는 표식.\n"
        "- 표식과 이름이 같은 말이면 안 된다. `[AI] AI 기본역량` 은 표식이"
        " 아니라 같은 낱말을 두 번 쓴 것이다.\n"
        "- 이름은 그 표식이 무엇인지. **24자 이내.**\n"
        "- 표식들이 하나의 묶음으로 읽혀야 한다. 머리글자를 모으거나 번호를"
        " 매기는 자리이지, 아무 글자나 크게 세우는 자리가 아니다.",
        '{{"tiles": [["P", "Physical AI"], ["H", "Human-centered AI"],'
        ' ["A", "Agentic AI"]], "notes": "여기서는 ..."}}',
    ),
    "timeline": (
        "이 장은 시점과 그때 일어난 일을 차례로 놓는 장이다.",
        "- timeline 은 `[시점, 일]` 의 목록이다. **3~7개.**\n"
        "- 시점은 **12자 이내** — 2024.05, 1학기, 3년차 처럼.\n"
        "- **자료에 실제 날짜가 없으면 날짜를 지어내지 마라.** 1단계·2단계,"
        " 1~4주, 학기 초 처럼 상대적인 시점을 써라. 지어낸 날짜는 그럴듯해서"
        " 사실로 읽히고, 발표장에서 되묻는 사람이 반드시 있다.\n"
        "- 일은 그때 무엇이 있었는지 한 마디. **60자 이내.**\n"
        "- 시간 순서대로 놓아라. 순서가 뒤섞이면 그건 연혁이 아니라 목록이다.",
        '{{"timeline": [["2024.05", "1기 개설, 수료생 32명"],'
        ' ["2025.03", "2기 개설과 기업 연계 시작"]], "notes": "여기서는 ..."}}',
    ),
}

_PROMPTS.update(
    {
        layout: _PAIRED_PROMPT.replace("{what}", what)
        .replace("{rules}", rules)
        .replace("{example}", example)
        for layout, (what, rules, example) in _PAIRED_RULES.items()
    }
)

_PROMPTS.update(
    {
        "quote": _QUOTE_PROMPT,
        "table": _TABLE_PROMPT,
        "metrics": _METRICS_PROMPT,
        "chart": _CHART_PROMPT,
    }
)


#: Keys that describe the slide rather than fill it. What is left is the answer,
#: whatever the model decided to call it.
_NOT_CONTENT = frozenset({"notes", "layout", "title", "heading", "id", "index", "n"})


def _salvaged_bullets(data: dict) -> list[str]:
    """Bullets out of an answer that put them somewhere else.

    Read structurally rather than by key name, because there is no key to know:
    the prompt names `bullets` and a small model answers with `points`, `items`,
    `content`, or one paragraph of prose. The same trade `design_templates`
    makes one level up — salvage what came back rather than refuse it, because
    the alternative here is a blank slide.

    Strings and lists of strings only. A number or an object is structure the
    model invented, and putting it on a slide would show the reader a shape
    nobody designed.
    """
    found: list[str] = []
    for key, value in data.items():
        if key.lower() in _NOT_CONTENT:
            continue
        if isinstance(value, str):
            # A paragraph is not a bullet. Split on sentence ends so what
            # reaches the slide is lines rather than a wall.
            found.extend(part for part in re.split(r"(?<=[.!?。])\s+|\n+", value) if part.strip())
        elif isinstance(value, list):
            found.extend(item for item in value if isinstance(item, str))
    return _clean_bullets(found)


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


#: A slide table's shape, and the reason for every bound. Four columns is what
#: fits a 16:9 slide at a size the back row can read; six rows is what fits
#: under a title without the cells closing up. A cell longer than this is a
#: sentence, and a sentence in a table cell is a paragraph nobody can read from
#: eight metres away.
_MAX_COLUMNS = 4
_MAX_ROWS = 6
_MAX_CELL = 24


#: Eight categories is what fits across a slide with labels the back row can
#: read; two series is what a legend can hold before it needs explaining.
_MAX_CATEGORIES = 8
_MAX_SERIES = 2


#: Layouts whose whole content is figures. A slide of prose can be written
#: from a topic; a slide of numbers cannot be written from anything.
_NUMERIC_LAYOUTS = ("chart", "metrics")


def _offered_layouts(request: str, context: list[str]) -> list[str]:
    """The body layouts this request can actually reach.

    What the variety check is judged against. Handed the whole list, it names
    `metrics` and `chart` as missing from a deck about a topic with no figures
    anywhere near it — and asking for those is asking for invented numbers,
    which is the one thing the plan is then rewritten to strip.
    """
    body = [layout for layout in _BODY_LAYOUTS if layout not in _NUMERIC_LAYOUTS]
    return list(_BODY_LAYOUTS) if has_numbers(request, context) else body


#: A figure somebody could cite, as opposed to a digit.
#:
#: `re.search(r"\d", request)` was the test, and `4대 신기술 분야` passed it —
#: so a deck about a topic with no measurements anywhere near it was allowed a
#: chart, and the writer produced one: AI 채용 증가율 15 · 30 · 55 · 82 · 110 ·
#: 135%, six invented numbers on a line, drawn large in front of a room. The 4
#: in 4대 counts categories. It is not evidence of anything.
#:
#: So: a decimal, a run of three or more digits, or a number carrying a unit
#: that measures. A bare year is deliberately not one — a date can ground a
#: timeline and cannot ground a chart.
_FIGURE = re.compile(
    # A year is not a measurement. `2026년 계획` matched the three-digit rule
    # and let a deck about next year's plan draw a chart of nothing.
    # The lookarounds pin the whole run. `\d{3,}(?!\s*년)` looks right and is
    # not: given 2026년 the engine backtracks to 202, sees 6 rather than 년,
    # and matches. A regex that can retreat inside the number it is judging is
    # judging a different number.
    r"\d+[.,]\d|(?<!\d)\d{3,}(?!\d)(?!\s*년)|"
    r"\d+\s*(?:%|퍼센트|원|명|건|배|억|만|천|시간|분|초|주|개월|점|개|회|위)"
)


def has_numbers(request: str, context: list[str]) -> bool:
    """Is there anywhere a figure could honestly have come from?

    The context is searched rather than counted. `bool(context)` was the test,
    and the context is every block of project knowledge in play — a saved
    memory saying who the user is, the project's own instructions. Two memories
    about a person's role were enough to tell the writer that figures were
    available, and it drew six years of invented AI 채용 증가율 on a chart.

    Having material and having numbers in it are different questions. This asks
    the second one.
    """
    return bool(_FIGURE.search(request)) or any(_FIGURE.search(block) for block in context)


def _grounded_layouts(
    plan: list[dict[str, str]], request: str, context: list[str]
) -> list[dict[str, str]]:
    """Numbers only where a number could have come from.

    A `chart` or `metrics` slide is nothing but figures. Asked for one with no
    material attached, no search results and no numbers in the request itself,
    the model has exactly one place to get them, and it takes it: a live run
    asked for a quarterly trend and got eight quarters of tidy invented data,
    on a chart, at the front of a room.

    The prompts say not to and one of the two obeyed — which is the reason this
    is here rather than in a prompt. A rule the writer keeps most of the time
    is not a rule for something that reads as fact the moment it is projected.

    The request counts as material. Somebody who writes "저장 비용이 32%
    줄었다" has given the figure, and refusing to chart it would be refusing
    the thing they asked for.
    """
    if has_numbers(request, context):
        return plan
    return [
        {**item, "layout": "bullets"} if item.get("layout") in _NUMERIC_LAYOUTS else item
        for item in plan
    ]


#: How many one-sentence slides a deck may hold. The prompt has said 최대 2장
#: for a long time and a live run came back with three, all of them selling:
#: 기회 손실 리스크, 마감 임박 경고, and a closing line. A rule the writer keeps
#: most of the time is not a rule — it is a suggestion with a number in it.
_MAX_QUOTES = 2


def _rationed_quotes(plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Extra one-sentence slides demoted to bullets, keeping the first ones.

    The first is the one the writer meant. What follows a full quota is a deck
    that has run out of things to say and is saying them louder.
    """
    out: list[dict[str, str]] = []
    spent = 0
    for item in plan:
        if item.get("layout") != "quote":
            out.append(item)
            continue
        spent += 1
        out.append(item if spent <= _MAX_QUOTES else {**item, "layout": "bullets"})
    return out


#: A divider title that says nothing: `01`, `2.`, `Part 3`, `섹션 1`.
_NUMBER_ONLY = re.compile(r"^\s*(?:part|섹션|section|장)?\s*[0-9IVX]+\s*[.)]?\s*$", re.I)


def _named_dividers(plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Dividers that name their part, and no others.

    The number is drawn by the renderer from where the divider falls, so a
    writer that puts it in the title spends the one line a divider has on
    something already on the slide: the reader is told they have reached part
    two without being told what part two is.

    Dropped rather than renamed. The obvious repair is to borrow the title of
    the slide after it — and that draws the same words twice in a row, once
    large and once as a heading, which is worse than the number was. What a
    part is called is the one thing only the writer knows, so a divider that
    did not bring a name is not a divider.

    Inserting them is left to the prompt for the same reason. Asked for, the
    writer produced four; invented here, every one of them would have been
    named after whatever happened to follow it.
    """
    return [
        item
        for item in plan
        if item.get("layout") != "section" or not _NUMBER_ONLY.match(item.get("title") or "")
    ]


def _clean_chart(value: Any) -> dict[str, Any] | None:
    """A chart, or `None` when what came back cannot be drawn.

    The length check is the one that matters. A series with fewer values than
    there are categories is not a chart with a gap in it — it is a chart whose
    bars line up under the wrong labels, and every reader of the slide takes
    away a fact that was never in the data. Short series are padded onto the
    front of the categories they do match and the rest of the categories are
    dropped, so what is drawn is the part that is actually paired.
    """
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "bar").strip().lower()
    if kind not in ("bar", "line"):
        kind = "bar"
    categories = [str(c).strip()[:10] for c in (value.get("categories") or [])]
    categories = [c for c in categories if c][:_MAX_CATEGORIES]

    series: list[dict[str, Any]] = []
    for item in (value.get("series") or [])[:_MAX_SERIES]:
        if not isinstance(item, dict):
            continue
        numbers: list[float] = []
        for raw in item.get("values") or []:
            try:
                numbers.append(float(raw))
            except (TypeError, ValueError):
                break
        if numbers:
            series.append({"name": str(item.get("name") or "").strip()[:16], "values": numbers})
    if not categories or not series:
        return None

    width = min(len(categories), min(len(s["values"]) for s in series))
    if width < 2:
        return None
    return {
        "kind": kind,
        "unit": str(value.get("unit") or "").strip()[:8],
        "categories": categories[:width],
        "series": [{"name": s["name"], "values": s["values"][:width]} for s in series],
    }


#: Four figures is what fits across a 16:9 slide at a size worth setting large;
#: past that they are narrow columns of text. A value longer than this is not a
#: figure, and a label longer than that is a bullet.
_MAX_METRICS = 4
_MAX_VALUE = 12
_MAX_LABEL = 16


#: A label that says its value is a date rather than a measurement.
#:
#: `2026 마감년도 · 8월 신청월 · 31일 마감일` came out of a metrics slide — one
#: deadline taken apart into three numbers and each set 44pt. Nothing there is
#: a quantity, and the slide that exists to make one figure memorable made three
#: unmemorable ones out of a date. A date has a layout of its own now.
_CALENDAR = re.compile(r"년도|연도|마감|기한|일자|신청월|개강|종강|^(년|월|일|요일)$")


def _clean_metrics(value: Any) -> list[list[str]]:
    """`[[값, 이름]]`, however the model formatted it.

    A pair with only one half is dropped. A number with nothing saying what it
    counts is a number nobody can use, and a label with no number is a bullet
    that has wandered into the wrong slide. A label that names a part of a date
    goes too — see `_CALENDAR`.
    """
    items = value if isinstance(value, list) else []
    out: list[list[str]] = []
    for item in items[:_MAX_METRICS]:
        pair = item if isinstance(item, list) else []
        if len(pair) < 2:
            continue
        figure = str(pair[0]).strip()[:_MAX_VALUE]
        label = str(pair[1]).strip()[:_MAX_LABEL]
        if figure and label and not _CALENDAR.search(label):
            out.append([figure, label])
    return out


#: The three that are a left thing and a right thing, and their bounds.
#:
#: One cleaner for all of them, because they are the same data wearing three
#: designs: a label beside a band of text, a letter over a caption, a date
#: beside what happened. Three cleaners would be three places to disagree about
#: what an empty half means.
_PAIRED = {
    #: `[[라벨, 내용]]` — 미션 · 배경 · 추진전략, the row-label shape every
    #: Korean 사업 발표 opens with.
    "bands": (4, 10, 90),
    #: `[[글자, 설명]]` — P · H · A · S · E, a letter or a number set large.
    "tiles": (6, 4, 24),
    #: `[[시점, 일어난 일]]` — 연혁, a date beside what happened.
    "timeline": (7, 12, 60),
}


def _clean_pairs(value: Any, layout: str) -> list[list[str]]:
    """`[[왼쪽, 오른쪽]]` for the three paired layouts, however it was formatted.

    A pair with only one half is dropped. A label with nothing beside it is a
    heading for a band that is not there, and a band with no label is a
    paragraph that has wandered onto the wrong slide.
    """
    count, left_max, right_max = _PAIRED[layout]
    items = value if isinstance(value, list) else []
    out: list[list[str]] = []
    for item in items[:count]:
        pair = item if isinstance(item, list) else []
        if len(pair) < 2:
            continue
        left = " ".join(str(pair[0]).split())[:left_max]
        right = " ".join(str(pair[1]).split())[:right_max]
        if left and right:
            out.append([left, right])
    return out


def _clean_rows(value: Any) -> list[list[str]]:
    """A slide table, however the model formatted it.

    Ragged rows are padded rather than dropped. A model that gives four
    headings and then a row of three has made a mistake in one cell, and
    throwing the whole table away over it costs the reader the other eleven.

    A table of one row is not a table — it is a heading with nothing under it —
    and comes back empty so the caller can fall back to a list.
    """
    rows = value if isinstance(value, list) else []
    out: list[list[str]] = []
    for row in rows[:_MAX_ROWS]:
        if not isinstance(row, list):
            continue
        cells = [
            re.sub(r"\*\*(.+?)\*\*", r"\1", str(cell)).replace("`", "").strip()[:_MAX_CELL]
            for cell in row[:_MAX_COLUMNS]
        ]
        if any(cells):
            out.append(cells)
    if len(out) < 2:
        return []
    width = max(len(row) for row in out)
    return [row + [""] * (width - len(row)) for row in out]


async def _draw(figure: dict, image_model: dict | None, api_key: str) -> dict | None:
    """One picture for a slide, or `None` when it could not be drawn.

    Embedded as a `data:` URI, which is the shape `deck_export` already reads
    off a JSON deck — its slides live in a JSONB column and bytes do not.

    Never raises: a slide without its diagram is a slide; a turn that dies takes
    the deck.
    """
    if not image_model:
        return None
    base, _ = await settings_store.litellm_config()
    try:
        made = await imagegen.generate(
            base_url=base,
            api_key=api_key,
            model=str(image_model.get("id") or ""),
            # 16:9, because a slide is. A square diagram on a widescreen slide
            # leaves two columns of empty deck.
            prompt=imagegen.compose_prompt(
                str(figure.get("prompt") or ""), aspect="16:9", style=""
            ),
        )
    except Exception as exc:  # noqa: BLE001 — a missing figure is not a failed deck
        log.warning("slide figure could not be drawn: %s", exc)
        return None
    return {
        # `encode` already returns the whole `data:` address; wrapping it
        # in `data_uri` again produced `data:image/png;base64,data:…`,
        # which every reader of it silently refused.
        "src": pictures.encode(made.mime, made.data),
        "caption": str(figure.get("caption") or ""),
        "_in": made.input_tokens,
        "_out": made.output_tokens,
    }


async def _write_slides(
    *,
    plan: list[dict[str, Any]],
    title: str,
    subtitle: str,
    accent: str,
    request: str,
    model: str,
    api_key: str,
    trusted_context: list[str] | None,
    untrusted_context: list[str] | None,
    usage: dict[str, int],
    research_rule: str = "",
    figures_plan: list[dict] | None = None,
    image_model: dict | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Writes the bodies for an outline that has already been agreed to.

    Lifted out of `write` so the approved-plan path and the plan-it-now path
    reach exactly the same code. Two copies of this loop would be two decks
    that differ in ways nobody chose.
    """
    #: Approved pictures by the index of the slide they belong to.
    wanted_figures = {
        int(f.get("section", -1)): f for f in (figures_plan or []) if f.get("prompt")
    }
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
    #: How many dividers have gone by. A divider's number counts dividers, not
    #: slides — `01.` over the first one whether it is slide 2 or slide 6, which
    #: is what a reader asking "which part are we in" wants to know.
    divider = 0

    for index, slide in enumerate(slides):
        # The position lives in `progress`, not in the text: spelled into both,
        # the surface renders "3/9 도입 (3/9)".
        label = str(slide["title"])
        # The deck is planned before any of it is written, so every step can
        # say where it sits in that plan. Without it the reader watching a
        # ten-slide run has only the step in front of them and no idea how
        # many are behind it.
        progress = {"current": index + 1, "total": len(slides)}

        if slide["layout"] in ("title", "section"):
            # Neither needs a model call. The cover's two lines arrive with the
            # outline — the subtitle trimmed rather than copied verbatim, since
            # pasting the request in puts its question mark on the opening
            # slide of a presentation. A divider has only its own name, which
            # the outline already chose, and a number, which is where it falls
            # among the dividers rather than among the slides.
            if slide["layout"] == "section":
                divider += 1
                slide["number"] = f"{divider:02d}."
                slide["body"] = ""
            else:
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
        template = _PROMPTS.get(slide["layout"], _BULLETS_PROMPT)
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
                    research_rule=research_rule,
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
            slide["body"] = UNWRITTEN
            yield {"type": "slide", "slide": slide, "done": True}
            continue

        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]

        # The picture, when this slide is one somebody paid for. Drawn after
        # the bullets so a failed drawing leaves a slide with words on it
        # rather than a slide that promised a diagram.
        if (drawing := wanted_figures.get(index)) is not None:
            yield {
                "type": "step",
                "id": f"fig{index}",
                "label": drawing.get("caption") or "그림 그리는 중",
                "status": "running",
                "progress": progress,
            }
            picture = await _draw(drawing, image_model, api_key)
            if picture is None:
                yield {
                    "type": "step",
                    "id": f"fig{index}",
                    "label": drawing.get("caption") or "그림",
                    "status": "error",
                    "progress": progress,
                }
            else:
                usage["inputTokens"] += picture.pop("_in", 0)
                usage["outputTokens"] += picture.pop("_out", 0)
                slide["image"] = picture
                yield {
                    "type": "step",
                    "id": f"fig{index}",
                    "label": drawing.get("caption") or "그림",
                    "status": "done",
                    "progress": progress,
                }
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
        elif slide["layout"] == "chart":
            if chart := _clean_chart(data.get("chart")):
                slide["chart"] = chart
            else:
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
        elif slide["layout"] == "metrics":
            if metrics := _clean_metrics(data.get("metrics")):
                slide["metrics"] = metrics
            else:
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
        elif slide["layout"] in _PAIRED:
            if pairs := _clean_pairs(data.get(slide["layout"]), slide["layout"]):
                slide[slide["layout"]] = pairs
            else:
                # An empty band is a coloured rectangle with nothing in it.
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
        elif slide["layout"] == "table":
            if rows := _clean_rows(data.get("rows")):
                slide["rows"] = rows
            else:
                # An empty table is a blank rectangle in the middle of a talk.
                # Whatever the model did give back is shown as a list, which is
                # the shape this slide would have had anyway.
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
        else:
            slide["bullets"] = _clean_bullets(data.get("bullets"))

        # Every branch above falls back to bullets, and until now that fallback
        # had none of its own: a model that answered a bullets slide with prose,
        # or with its list under a key nobody specified, left the slide empty —
        # and one empty slide locked 내보내기, 발표 and 텍스트 수정 for the whole
        # deck. A blank rectangle in the middle of a talk is the visible half of
        # that; the deck nobody can export is the worse half.
        if not has_content(slide):
            slide["bullets"] = _salvaged_bullets(data)
        if not has_content(slide):
            # Nothing to salvage either. Said the same way a call that threw is
            # said, because to the reader it is the same event: this slide did
            # not get written. Marked rather than left blank so the deck stays
            # a deck — every control on this panel waits for the run to end and
            # not for the result to be good, and 텍스트 수정 is then right there
            # to write the slide by hand.
            #
            # It says so on the screen and nowhere else. `deck_export` leaves
            # the slide out of the file: a panel is a workbench and a file is
            # what goes into a room, and a live run put "이 장을 쓰지
            # 못했습니다." on slide three of a deck somebody was about to
            # present. The lint already files this P0, so nobody is exporting
            # it without having been told.
            slide["body"] = UNWRITTEN

        if notes:
            slide["notes"] = notes[:800]

        summary = (
            " / ".join(slide.get("bullets") or [])
            or " / ".join(f"{v} {n}" for v, n in (slide.get("metrics") or []))
            or " / ".join((slide.get("chart") or {}).get("categories") or [])
            or " / ".join(" ".join(row) for row in (slide.get("rows") or []))
            or slide.get("body")
            or ""
        )
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

async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    tokens: dict[str, str] | None = None,
    #: The model that plans, when an administrator has named one. The outline
    #: is one call and decides the shape of every call after it, so it is the
    #: one place where a stronger model changes the result out of proportion
    #: to what it costs. Empty means the same model writes and plans.
    outline_model: str = "",
    #: The outline somebody has already seen and approved.
    #:
    #: Absent, this plans and stops: it emits `proposal` — or `needs`, when the
    #: material cannot carry the request — and writes nothing. Present, it
    #: skips planning entirely and writes exactly what was approved, because a
    #: second planning call would produce a different deck from the one that
    #: was agreed to and quietly replace it.
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
    #: Pictures somebody agreed to on the second card, ready to draw.
    #:
    #: `None` on the planning pass, where they are proposed. `[]` means the
    #: card was answered 그림 없이. A slide's picture goes on the slide rather
    #: than under it — `deck_export` already reads `slide["image"]` and puts it
    #: in the .pptx — so this is the same two-step the report uses landing in a
    #: different place.
    figures_plan: list[dict] | None = None,
    #: The model that draws them — the image default, not the writer's.
    image_model: dict | None = None,
    #: Whether to research this deck before writing it.
    #:
    #: A deck is the surface where an unchecked fact travels furthest: it gets
    #: presented, and nobody in the room can see where a bullet came from. The
    #: pass is the same one reports run — queries planned off the request, top
    #: pages read in full — and it lands before the outline, so the slide list
    #: is chosen from what is true rather than from what was remembered.
    web_search: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `title`, `slide`, a final `deck` and one `usage` event.

    Two passes over two requests. The first plans and offers; the second writes
    what came back approved. Nothing is written on the first, which is what
    keeps a deck already on screen safe from a run nobody confirmed.

    The caller owns persistence, billing and the artifact — this only writes.
    A slide that fails is marked and the rest continues, because eight slides
    and a gap is worth more than nothing.

    `tokens` is the project's design system, when it wears one. Its accent
    replaces the model's colour choice outright rather than being offered as a
    default: a deck that is nearly the project's colour is worse than one that
    is plainly not.
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
    wanted = requested_slides(request)
    fixed_accent = (tokens or {}).get("accent") or ""

    # Ahead of both paths. The approved-plan pass researches too: the outline
    # it was handed named the slides, not what goes on them, and the bullets
    # are where the facts actually are.
    findings = research.Findings()
    # Checked before the step is drawn, not inside `run`. A deployment with no
    # search backend would otherwise open every document with 자료 찾는 중 and
    # close it with 참고할 자료 없음 — a step that reports the deployment's
    # configuration as though it were this document's result.
    if web_search and await research.available():
        yield {"type": "step", "id": "sources", "label": "자료 찾는 중", "status": "running"}
        findings = await research.run(
            request, model=outline_model or model, api_key=api_key
        )
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
        if findings.sources:
            yield {"type": "sources", "sources": findings.sources}
    # Three states, and the writer is told which one it is in. A toggle
    # somebody switched off is a choice and needs no disclaimer; a search that
    # could not run and a search that found nothing are both worth saying, and
    # they do not mean the same thing to a reader.
    research_rule = ""
    if web_search and not findings.searched:
        research_rule = research.UNRESEARCHED_RULE
    elif web_search and not findings.sources:
        research_rule = research.EMPTY_RULE
    document_context = list(untrusted_context or [])
    if block := research.context_block(findings):
        document_context.append(block)

    if approved_plan is not None:
        title = str(approved_plan.get("title") or "")
        subtitle = str(approved_plan.get("subtitle") or "")
        accent = fixed_accent or str(approved_plan.get("accent") or "")
        plan = [
            {"title": str(item.get("title") or ""), "layout": str(item.get("layout") or "bullets")}
            for item in (approved_plan.get("slides") or [])
            if str(item.get("title") or "").strip()
        ]
        if not plan:
            yield {"type": "error", "message": "승인된 구성이 비어 있습니다."}
            yield {"type": "usage", **usage}
            return
        # Again on the way back in. A plan can be approved days after it was
        # proposed, and it can be edited before it is — neither path went past
        # the check above.
        plan = _named_dividers(_rationed_quotes(_grounded_layouts(plan, request, document_context)))
        async for event in _write_slides(
            plan=plan,
            title=title,
            subtitle=subtitle,
            accent=accent,
            request=request,
            model=model,
            api_key=api_key,
            trusted_context=trusted_context,
            untrusted_context=document_context,
            usage=usage,
            research_rule=research_rule,
            figures_plan=figures_plan,
            image_model=image_model,
        ):
            yield event
        return

    async def ask(nudge: str = "") -> tuple[str, dict[str, int]]:
        return await _complete(
            outline_model or model,
            build_document_messages(
                SessionKind.slides,
                _OUTLINE_PROMPT.format(
                    ask_rule=grounding.ASK_RULE if may_ask else grounding.PROCEED_RULE,
                    lo=wanted or _MIN_SLIDES,
                    hi=wanted or _DEFAULT_MAX,
                    theme_rule=(
                        "" if fixed_accent else _THEME_RULE.format(themes=" / ".join(_THEMES))
                    ),
                    theme_example="" if fixed_accent else '"theme": "청록",\n  ',
                    request=request[:2000],
                )
                + nudge,
                trusted_context=trusted_context,
                untrusted_context=document_context,
                research_rule=research_rule,
            ),
            api_key,
            # Scaled with the slide count: a fixed ceiling truncates the JSON
            # on a long deck and the parse fails.
            max(600, 70 * (wanted or _DEFAULT_MAX) + 300),
        )

    yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "running"}
    try:
        text, spent = await ask()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("deck outline failed: %s", exc)
        yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "error"}
        yield {"type": "error", "message": "슬라이드 구성을 만들지 못했습니다."}
        yield {"type": "usage", **usage}
        return

    plan_rules.count(usage, spent, planned_apart=bool(outline_model))
    # The model may answer the outline call with a question instead. Only when
    # the request names material it cannot find — see `grounding.ASK_RULE`; a
    # bare topic is still planned without being asked about.
    if may_ask and (asked := grounding.parse_needs(text)):
        yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
        yield {"type": "needs", "questions": [q.wire() for q in asked]}
        yield {"type": "usage", **usage}
        return
    title, subtitle, plan = _parse_outline(text)
    accent = fixed_accent or _theme_accent(text)
    # Before the variety check, not after it. A plan judged against layouts it
    # is not allowed to use asks for them by name, pays a model call for the
    # answer, and then has them stripped out again — the deck comes back as
    # flat as it started and one call poorer.
    plan = _named_dividers(_rationed_quotes(_grounded_layouts(plan, request, document_context)))
    offered = _offered_layouts(request, document_context)

    # Four layouts on offer, and the answer is usually `bullets` all the way
    # down. One more call, naming the ones it skipped, is the cheapest place
    # to fix that — the slides themselves have not been written yet.
    missing = plan_rules.flat_layouts(plan, offered) if plan else []
    if missing:
        log.info("deck outline flat, unused: %s", ",".join(missing))
        try:
            retry_text, retry_spent = await ask(
                "\n\n앞선 구성이 한 layout 에 몰렸다. 다시 짜라. "
                "다음 layout 을 최소 한 번씩 쓰고, 같은 layout 을 세 장 연속으로 쓰지 마라: "
                + " / ".join(missing)
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("deck outline retry failed: %s", exc)
        else:
            plan_rules.count(usage, retry_spent, planned_apart=bool(outline_model))
            retry_title, retry_subtitle, retry_plan = _parse_outline(retry_text)
            retry_plan = _grounded_layouts(retry_plan, request, document_context)
            if retry_plan and not plan_rules.flat_layouts(retry_plan, offered):
                title = retry_title or title
                subtitle = retry_subtitle or subtitle
                plan = retry_plan
                accent = fixed_accent or _theme_accent(retry_text) or accent
            else:
                log.info("deck outline still flat, keeping the first")
    # One more call before calling it a failure. What the parse trips over is a
    # shape, not the request — a fenced block, a sentence of preamble, a list
    # where an object belongs — and the same prompt usually lands it the second
    # time. The machinery is already here for the flat-layout retry above, and
    # the alternative is charging for a call and showing nothing for it.
    if not plan:
        log.info("deck outline unreadable, asking once more")
        try:
            retry_text, retry_spent = await ask(
                "\n\n앞선 답을 읽을 수 없었다. 설명도 머리말도 코드펜스도 없이 "
                "JSON 객체 하나만 출력하라."
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("deck outline retry failed: %s", exc)
        else:
            plan_rules.count(usage, retry_spent, planned_apart=bool(outline_model))
            retry_title, retry_subtitle, retry_plan = _parse_outline(retry_text)
            if retry_plan:
                title = retry_title or title
                subtitle = retry_subtitle or subtitle
                plan = retry_plan
                accent = fixed_accent or _theme_accent(retry_text) or accent

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
    # Planned, and that is where this stops. The deck is offered rather than
    # written: the caller stores it, shows it, and calls back with it approved.
    plan = _named_dividers(_rationed_quotes(_grounded_layouts(plan, request, document_context)))
    proposal: dict[str, Any] = {
        "title": title[:200],
        "subtitle": subtitle[:200],
        "accent": accent,
        "slides": [{"title": item["title"], "layout": item["layout"]} for item in plan],
    }
    # The pictures are proposed here and asked about on a second card, once the
    # outline is agreed — the report's two-step, landing on slides. Proposed now
    # because the planner has the outline in front of it; asked separately
    # because a picture costs multiples of the prose and should not be bought
    # by a button somebody pressed for the shape.
    if image_model:
        drawn = await figures.propose(
            request=request,
            title=title,
            parts=[item["title"] for item in plan],
            model=outline_model or model,
            api_key=api_key,
            image_model=image_model,
        )
        usage["outlineInputTokens" if outline_model else "inputTokens"] += drawn.usage[
            "inputTokens"
        ]
        usage["outlineOutputTokens" if outline_model else "outputTokens"] += drawn.usage[
            "outputTokens"
        ]
        if drawn.figures:
            proposal["figures"] = drawn.wire()
    yield {"type": "proposal", "plan": proposal}
    yield {"type": "usage", **usage}


async def rewrite_slide(
    *,
    request: str,
    slides: list[dict],
    target_id: str,
    model: str,
    api_key: str,
    note: str = "",
) -> tuple[dict, dict]:
    """Rewrites one slide, with the rest of the deck as context.

    The report surface has had this since it shipped and the deck has not,
    which is why "5번 장에 근거를 붙여" planned an entire new presentation. The
    shape is the report's on purpose: same arguments, same `(result, usage)`,
    so the caller that drives a revision does not need to know which surface it
    is on.

    Everything but the target is passed as written, so the new slide does not
    repeat what slide two already said — the same guard the first pass uses.
    """
    target = next((s for s in slides if s.get("id") == target_id), None)
    if target is None:
        raise KeyError(target_id)

    outline = "\n".join(f"{i + 1}. {s.get('title') or ''}" for i, s in enumerate(slides))
    written = "\n".join(
        f"{s.get('title')}: {' / '.join(s.get('bullets') or []) or (s.get('body') or '')}"
        for s in slides
        if s.get("id") != target_id
    )
    layout = str(target.get("layout") or "bullets")
    template = _PROMPTS.get(layout, _BULLETS_PROMPT)
    prompt = template.format(
        heading=target.get("title") or "",
        outline=outline,
        written=written[-3000:] or "(아직 없음)",
        count="6~8" if layout == "two-column" else "3~5",
        request=request[:1500],
    )
    if note.strip():
        # Last and labelled: an unlabelled sentence appended to a prompt reads
        # as part of the original request.
        prompt += f"\n\n이번에 다시 쓰는 이유(반드시 반영):\n{note.strip()[:600]}"

    text, usage = await _complete(
        model, build_document_messages(SessionKind.slides, prompt), api_key, 600
    )
    parsed = _json_object(text)
    bullets = _clean_bullets(parsed.get("bullets"))
    body = str(parsed.get("body") or "").strip()
    if not bullets and not body:
        raise ValueError("빈 슬라이드")

    #: Merged rather than replaced. A picture somebody put on this slide, its
    #: accent, its id — none of that is the model's to drop, and a rewrite that
    #: silently removed a chart would be indistinguishable from one that failed.
    result = {**target}
    if bullets:
        result["bullets"] = bullets
    if body:
        result["body"] = body
    if notes := str(parsed.get("notes") or "").strip():
        result["notes"] = notes
    # The verdicts belonged to the old text.
    result.pop("factCheck", None)
    return result, usage


#: Every field a slide can carry its content in. `bullets` and `body` were the
#: whole list once, and the two that were added after — a table's `rows`, a
#: strip's `metrics`, a chart's numbers — live nowhere near them.
#: Every field a slide's content can arrive in.
#:
#: Derived from `_PAIRED` rather than typed out again beside it. The three
#: paired layouts were added and this list was not, so a finished 참여 혜택
#: slide — four filled bands on it — was read as a slide the writer failed to
#: write, and the words "이 장을 쓰지 못했습니다." were set on top of it. The
#: same list is why a table slide was once dropped from finished decks. A
#: layout that stores its content under its own name has to be in here, and the
#: only way to guarantee that is to not maintain two lists.
_CONTENT_FIELDS = ("bullets", "body", "rows", "metrics", "chart", *_PAIRED)


def has_content(slide: dict) -> bool:
    """Is there anything on this slide?

    The cover counts on its title alone. Everything else needs one of the
    fields above — and reading only `bullets` and `body`, as this did, meant a
    finished table slide was indistinguishable from a slide the model failed to
    write. `filled` then dropped it from the deck, and the panel's own copy of
    the same test left 내보내기, 발표 and 텍스트 수정 disabled forever on a deck
    that was complete.
    """
    if slide.get("layout") == "title":
        return True
    for field in _CONTENT_FIELDS:
        value = slide.get(field)
        if isinstance(value, str):
            if value.strip():
                return True
        elif value:
            return True
    return False


def filled(slides: list[dict]) -> list[dict]:
    """The slides that actually have something on them."""
    return [s for s in slides if has_content(s)]


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


__all__ = ["DeckError", "filled", "has_content", "to_markdown", "write"]
