"""Writing a report, section by section.

Two passes:

* **Outline.** One cheap call returning headings only. This is what makes the
  progress readout honest — six pending sections means six are coming.
* **Sections.** One call each, carrying the outline and everything written so
  far, so section four does not repeat section two.

A failed section is marked and the rest continues: five sections and a gap beat
nothing.
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
from app.services import deck as deck_rules
from app.services import (
    figures,
    design,
    grounding,
    hangul,
    imagegen,
    pictures,
    research,
    richtext,
    settings_store,
    thinking,
)
from app.services import outline as plan_rules
from app.services.context import build_document_messages

log = logging.getLogger(__name__)

#: Each section is its own model call, so this is the multiplier on the bill.
_MIN_SECTIONS = 3
_MAX_SECTIONS = 8

_OUTLINE_PROMPT = """다음 요청에 맞는 보고서의 제목과 목차를 만들어라.

규칙:
- 제목은 문서의 표지에 적힐 한 줄이다. 요청 문장을 그대로 옮기지 말고,
  주제를 가리키는 명사구로 써라. 마침표와 "~에 대한 보고서" 같은 군말은 빼라.
- 섹션 {lo}~{hi}개.
- 각 섹션은 서로 겹치지 않고, 순서대로 읽으면 하나의 글이 되어야 한다.
- 섹션은 제목만. 내용은 쓰지 마라.
{ask_rule}
- 참고할 자료에 양식·서식 문서가 있으면 그 문서의 항목 순서를 그대로 목차로 써라.
  개수도 그 양식을 따르고, 일반적인 보고서 목차로 바꾸지 마라.

JSON 객체로만 답하라.
예: {{"title": "전이학습의 소량 데이터 효율성", "sections": ["요약", "배경", "방법", "결과", "한계", "결론"]}}

요청: {request}"""

_SECTION_PROMPT = """너는 아래 보고서의 "{heading}" 섹션만 쓰고 있다.

전체 목차:
{outline}

앞 섹션에서 이미 쓴 내용:
{written}

참고 자료:
{refs}

규칙:
- "{heading}" 에 해당하는 내용만 써라. 다른 섹션의 내용을 미리 쓰지 마라.
- 제목 줄은 쓰지 마라. 본문만.
- 마크다운을 쓰되 최상위 제목(#)은 쓰지 마라.
- 앞에서 한 말을 되풀이하지 마라.
- **비교·수치·일정은 표로 써라.** 두 가지 이상을 같은 기준으로 견주거나, 항목별
  값이 나열되거나, 날짜와 담당이 붙는 대목은 줄글보다 표가 읽힌다. 줄글로 늘어
  놓은 비교는 읽는 사람이 머릿속에서 표로 다시 그려야 한다.
- **다만 한 항목이 시점에 따라 변하는 값은 표가 아니라 아래의 ```chart 다.**
  1월·2월·3월…처럼 시점이 열로 늘어서는 표는 단 폭을 넘기고, 읽는 사람은 결국
  그 숫자들을 머릿속에서 선으로 잇는다. **시점이 4개를 넘으면 표를 쓰지 마라.**
- **같은 숫자를 두 번 쓰지 마라.** 표나 차트에 넣은 값을 문단에서 다시 읊지
  마라. 본문은 그 숫자가 무엇을 뜻하는지 말하는 자리다.
- 표는 이 형식으로 쓴다. **행 사이에 빈 줄을 넣지 마라** — 빈 줄이 들어가면
  표가 아니라 문장으로 그려진다.

      | 기준 | 대안 A | 대안 B |
      | --- | --- | --- |
      | 초기 비용 | 0원 | 약 3억 원 |
      | 도입 기간 | 2주 | 4개월 |

- **핵심 수치는 강조 블록으로 뽑아라.** 절의 결론이 되는 숫자가 두셋 있으면
  ```kpi 로 감싼 블록에 `값 | 이름` 을 한 줄씩 쓴다. 화면과 내보낸 파일 양쪽에
  큰 숫자로 나온다. 최대 4개.

      ```kpi
      32% | 오탐 감소
      1.4초 | 평균 응답 시간
      99.2% | 가용성
      ```

  - **한 절에 하나까지.** 매 절마다 붙이면 강조가 아니라 배경이 된다.
  - 값은 짧게 — `32%`, `1.4초`, `3억 원`. 문장을 넣지 마라.
  - 표에 있는 수치를 그대로 옮기지 마라. 표는 견주는 자리고, 이 블록은
    **하나를 기억시키는** 자리다.
  - 블록만 두지 마라. 그 숫자가 무엇을 뜻하는지는 본문이 말해야 한다.
- **훑어 읽는 대목은 카드로 써라.** 서너 갈래를 같은 무게로 나란히 놓아야 할
  때 — 산출물·목표·이해관계자·성공 기준처럼 — ```cards 로 감싸고 `## 카드 제목`
  아래에 `- 줄` 을 붙인다. 화면에서는 두 단 격자로, 내보낸 파일에서는 두 단 표로
  나온다. 최대 6장.

      ```cards
      ## 산출물
      - 네트워크 전면 교체
      - 클라우드 이전
      ## 목표
      - 8개월 안에 완료
      - 성능 40% 개선
      ```

  - **두 장이면 그것이 비교다.** 현행과 제안, 지금과 이후를 나란히 놓을 때 따로
    쓰는 문법은 없다 — 카드 둘이 곧 두 단이다.
  - 카드 제목은 명사로 짧게. 카드마다 줄은 **다섯 줄 안쪽**으로.
  - **줄글로 쓸 것을 카드에 넣지 마라.** 카드는 훑는 자리다. 이어서 읽어야 이해
    되는 문장은 본문에 둔다.
  - 한 절에 하나까지. 절마다 격자가 있으면 격자가 배경이 된다.
- **지나치면 안 되는 한 줄은 강조 상자로 써라.** ```callout 로 감싸고 첫 줄에
  제목, 다음 줄부터 내용을 쓴다. 왼쪽에 색 막대가 붙어 나온다.

      ```callout
      승인 없이는 시작하지 않는다
      9월 교무회의 승인 전까지는 계약도 발주도 하지 않는다.
      ```

  - **문서 전체에 하나까지.** 둘이면 둘 다 지나치게 된다.
  - 경고·전제·기한처럼 **틀리면 뒤가 다 무너지는 것**만 넣는다. 요약은 여기가
    아니라 첫 절이다.
- **차례대로 하는 일은 절차 블록으로 써라.** ```steps 로 감싸고 `이름 | 설명` 을
  한 줄씩 쓴다. 번호가 붙어 나온다. 최대 8단계.

      ```steps
      자료 수집 | 공개 데이터와 내부 로그를 모은다
      정제 | 중복과 결측을 걸러낸다
      분석 | 세 가지 기준으로 견준다
      ```

  - 번호를 직접 쓰지 마라. 번호는 자동으로 붙는다.
  - 이름은 짧게, 설명은 한 줄로. 두 문장이 필요하면 그건 절차가 아니라 본문이다.
  - **갈라지는 흐름은 이걸로 쓰지 마라.** 조건에 따라 길이 나뉘거나 되돌아가면
    아래의 mermaid 를 써라. 반대로 곧게 이어지는 순서를 mermaid 로 그리면
    목록을 그림으로 만든 것뿐이고, 파일에서는 글자를 잃는다.
- **구조·흐름·관계는 mermaid 로 그려라.** ```mermaid 로 감싼 블록을 쓰면 화면과
  내보낸 파일 양쪽에 도해로 나온다. 아키텍처, 절차, 조직, 상태 변화가 대상이다.
  글로 세 문단 걸릴 관계도를 열 줄로 적을 수 있다.
- 도해는 **가로로 긴 직사각형**이어야 한다. 종이에 인쇄되는 그림이고, 세로로
  길면 한 쪽을 통째로 먹는다. 폭에 맞춰 줄이면 이번에는 글자가 읽을 수 없게
  작아진다. 아래 규칙은 전부 그 모양 하나를 위한 것이다.
  - **층은 3개까지.** `graph TD` 에서 층의 수가 곧 세로 길이다. `A --> B --> C
    --> D --> E` 처럼 한 줄로 이어지면 다섯 층이 되어 좁고 길어진다.
  - **한 줄로 이어지면 도해가 아니다.** 그건 절차이므로 위의 ```steps 로 써라.
    도해는 **갈라질 때만** 쓴다.
  - 한 층에 **2~4개를 나란히** 놓아라. 형제 노드가 가로로 퍼지면서 그림이
    납작해진다. 이것이 폭을 쓰는 유일한 방법이다.
  - 노드는 **8개 이하**, 이름은 **10자 안쪽**. 긴 이름은 칸을 넓히는 게 아니라
    줄바꿈되어 칸을 높이고, 그만큼 그림이 세로로 자란다. `/` 로 두 가지를 한
    칸에 넣지 마라 — `서버리스/컨테이너` 는 칸 하나가 아니라 노드 둘이다.
  - **화살표에 글을 붙이는 문법은 `A -->|성공| B` 하나뿐이다.** 파이프는 화살표
    **바로 뒤**에 오고 그 뒤에 도착 노드가 온다. `A --> B|성공| C` 처럼 쓰면
    mermaid 가 그리지 못하고, 읽는 사람은 도해 대신 소스를 보게 된다.
  - 화살표에 붙이는 글은 **6자 안쪽.** 노드 이름보다 더 잘 줄바꿈되고, 줄바꿈된
    화살표 글씨는 선 옆에 두 줄로 떠서 그림을 어지럽게 만든다. 길어질 것 같으면
    아예 붙이지 마라.
  - `subgraph` 는 **하나까지**. 둘을 나란히 놓으면 각각이 절반으로 줄어든다.
    비교가 목적이면 도해 두 개로 나누거나 표로 써라.
  - `style`·`classDef`·`linkStyle` 로 색을 칠하지 마라. 색은 서식이 정한다.
    써도 그리기 전에 지워진다.
- **수치의 모양이 요점이면 ```chart 로 그려라.** 표는 값을 **읽는** 자리고
  차트는 값의 **모양**을 보는 자리다. 항목별 크기 비교는 `bar`, 시간에 따른
  추이는 `line`. **시점이 4개 이상이면 표가 아니라 언제나 차트다.**

      ```chart
      bar | 건
      분기 | 처리 건수 | 반려 건수
      1분기 | 120 | 8
      2분기 | 210 | 11
      3분기 | 380 | 9
      ```

  - 첫 줄은 `종류 | 단위`. 둘째 줄은 `가로축 이름 | 계열 이름들`. 나머지가 값이다.
  - 가로축 항목은 **8개까지**, 계열은 **2개까지**.
  - **모든 줄의 값 개수가 계열 수와 같아야 한다.** 하나라도 비면 그 줄은 통째로
    빠진다 — 빈칸을 0으로 채우면 그리지 않은 0이 그래프에 주장으로 남는다.
  - 값이 서넛뿐이면 차트 대신 표나 강조 수치를 써라. 막대 세 개짜리 그림은
    표보다 자리만 넓게 차지한다.
  - **지어낸 수치를 쓰지 마라.** 그래프는 숫자보다 더 사실처럼 읽힌다.
  - 색과 축 눈금은 정하지 마라. 서식이 정하고, 세로축은 언제나 0에서 시작한다.
- 표는 3~5행이 적당하다. 그보다 길면 본문에서 요점을 먼저 말하고 표는 근거로
  둔다. 표 하나로 절을 대신하지 마라 — 표 앞에 무엇을 비교하는지, 뒤에 그래서
  무엇인지 한 문장씩은 있어야 한다.
- 참고 자료에서 가져온 사실은 그 자료의 번호를 문장 끝에 [1] 처럼 붙여라.
  목록에 없는 번호는 절대 쓰지 마라. 참고 자료가 없으면 번호도 쓰지 마라.
- **자료에 없는 고유한 값을 지어내지 마라.** 금액, 날짜, 기관 이름, 사람 이름,
  계약 상대가 그렇다. 결정해야 할 자리라면 값을 채우지 말고 무엇을 정해야
  하는지를 적어라 — "예산 2억 원" 이 아니라 "예산 규모(미정)", "A社·B社" 가
  아니라 "협약 기업(선정 필요)" 이다. 지어낸 고유값은 읽는 사람이 그대로
  옮겨 적고, 그 뒤에 아무도 그것이 어디서 왔는지 묻지 않는다.

원래 요청: {request}"""

#: Placeholder for an empty shelf. An empty block reads as withheld material and
#: the model invents citations.
_NO_REFS = "(없음. 번호 인용을 쓰지 마라.)"


#: Waits between retries of a rate-limited call, in seconds.
_BACKOFF = (2.0, 6.0)


async def _complete(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int,
) -> tuple[str, dict]:
    """One non-streaming call. Returns `(text, usage)`. Retries a 429.

    One call per section against a shared limit; a transient refusal would leave
    a hole in the document.
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
                    # No thinking on a call whose whole answer is one JSON
                    # object — see `thinking.NO_REASONING` for the measurements.
                    # Safe to send everywhere: the proxy runs `drop_params`, so
                    # a provider that has never heard of it never sees it.
                    "reasoning": thinking.NO_REASONING,
                },
            )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            log.info("report call rate limited, retrying in %ss", _BACKOFF[attempt])
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


def _parse_outline(text: str) -> tuple[str, list[str]]:
    """`(title, headings)` from whatever the model wrapped its JSON in.

    The title is optional throughout: a bare array, or an object missing the
    key, still yields a usable outline and the caller falls back to the request.
    """
    title = ""
    obj = re.search(r"\{.*\}", text, re.S)
    if obj:
        try:
            data = json.loads(obj.group(0))
            if isinstance(data, dict):
                title = str(data.get("title") or "").strip()
                items = data.get("sections") or []
                headings = [str(x).strip() for x in items if str(x).strip()]
                if headings:
                    return title, headings[:_MAX_SECTIONS]
        except json.JSONDecodeError:
            pass
    match = re.search(r"\[.*\]", text, re.S)
    if match:
        try:
            items = json.loads(match.group(0))
            headings = [str(x).strip() for x in items if str(x).strip()]
            if headings:
                return title, headings[:_MAX_SECTIONS]
        except json.JSONDecodeError:
            pass
    # A model that ignored the format usually still produced a list.
    lines = [
        re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line).strip(" #").strip()
        for line in text.splitlines()
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)
    ]
    return title, [line for line in lines if line][:_MAX_SECTIONS]


def _refs_block(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return _NO_REFS
    return "\n".join(
        f"[{s['ordinal']}] {s['title']} ({s['publisher']})\n{s.get('quote') or ''}"
        for s in sources
    )


async def _draw(figure: dict, image_model: dict | None, api_key: str) -> dict | None:
    """One picture as a stored figure, or `None` when it could not be drawn.

    Embedded as a `data:` URI rather than a file reference. A report is
    exported and mailed, and a picture that lives at a URL is a picture that is
    missing by the time somebody opens the attachment — the same reason the
    document editor embeds what a person pastes in.

    Never raises. A drawing that fails leaves the section without a figure,
    which the reader can see; a turn that dies takes the whole document.
    """
    if not image_model:
        return None
    base, _ = await settings_store.litellm_config()
    try:
        made = await imagegen.generate(
            base_url=base,
            api_key=api_key,
            model=str(image_model.get("id") or ""),
            prompt=imagegen.compose_prompt(
                str(figure.get("prompt") or ""), aspect="4:3", style=""
            ),
        )
    except Exception as exc:  # noqa: BLE001 — a missing figure is not a failed report
        log.warning("figure could not be drawn: %s", exc)
        return None
    return {
        # `encode` already returns the whole `data:` address; wrapping it
        # in `data_uri` again produced `data:image/png;base64,data:…`,
        # which every reader of it silently refused.
        "src": pictures.encode(made.mime, made.data),
        "caption": str(figure.get("caption") or ""),
        "width": made.width,
        "height": made.height,
        # Popped by the caller into the turn's usage. Carried on the dict
        # because the drawing is billed to the same turn the prose is.
        "_in": made.input_tokens,
        "_out": made.output_tokens,
    }


#: The two blocks that are nothing but figures, drawn large.
_FIGURE_FENCE = re.compile(r"^```(?:kpi|chart)\b.*?^```\s*$", re.S | re.M)


def _grounded_figures(text: str, grounded: bool) -> str:
    """Figure blocks removed from a section with nothing to draw them from.

    The same rule the deck applies to its `chart` and `metrics` slides, and for
    the same reason. Asked for a 검토 보고서 on a topic with no material, the
    writer filled three sections with `kpi` blocks — 6개월, 80%, 90%, 4명, 30%,
    100%, 0일, 8주, 40명, 80점 — every one of them invented, every one of them
    set large on the page where a figure is read as the most factual thing in
    the section.

    The prose around them survives. A sentence saying the programme runs in a
    compressed cycle is a claim somebody can weigh; the same claim as `8주`
    beside a heading is a measurement, and there was no measuring.
    """
    if grounded:
        return text
    return _FIGURE_FENCE.sub("", text).strip()


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    #: The model that plans, when an administrator has named one. A report's
    #: 목차 is the same kind of decision a deck's layouts are: one call that
    #: every call after it is written against. Empty plans with `model`.
    outline_model: str = "",
    #: The 목차 somebody has already seen and approved.
    #:
    #: Absent, this plans and stops: it emits `proposal` — or `needs`, when the
    #: material cannot carry the request — and writes nothing. Present, it
    #: skips planning and writes exactly what was approved, because planning
    #: again would produce a different report from the one agreed to.
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
    #: The pictures somebody agreed to on the second card, ready to draw.
    #:
    #: `None` on the planning pass, which is where they are *proposed*. `[]`
    #: means the card was answered with 그림 없이, and the difference matters:
    #: a section told a figure is coming writes 아래 그림과 같이, and one told
    #: nothing does not. That is why the question is asked before the writing
    #: rather than after it.
    figures_plan: list[dict] | None = None,
    #: Model that draws them, and the key to draw with. Empty disables the
    #: proposal entirely — no image model configured, no card.
    image_model: dict | None = None,
    #: Whether to research this report before writing it.
    #:
    #: The shelf this used to build was six titles and six 300-character
    #: snippets, from one search on the request typed verbatim. That is enough
    #: to print a reference list and not enough to correct a single thing the
    #: model misremembers — which is how a report cites four real sources
    #: underneath a paragraph none of them support. With this on, the pass runs
    #: through `services.research`: the queries are planned off the request,
    #: and the top pages are read in full before a heading is chosen.
    web_search: bool = True,
    project_sources: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `section` and one final `usage` event.

    The caller owns persistence, billing and the artifact — this only writes.
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

    # Before the outline, not after it. A 목차 chosen from memory commits the
    # whole document to that memory's shape — every section after it is written
    # to fill a heading that was already wrong. Researching first costs one
    # planning call and buys an outline that knows what it is about.
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
    # `research.run` normally owns this list, but test doubles and connector
    # adapters may reuse a Findings object. Project citations belong to this
    # run only, so never append into the caller's shelf in place.
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
                    or ("전체 내용 전달됨" if item.get("state") == "included" else "일부 내용만 전달됨")
                ),
            }
        )
        project_reference_lines.append(f"- [{ordinal}] {title}")
        project_selected += 1

    # Keep the investigation legible after the progress row disappears.  A
    # source shelf proves what was cited; it does not prove what was searched,
    # whether search actually ran, or how much irrelevant material was
    # rejected.  The report artifact stores this event as its research log.
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
    # The pages read, as their own reference block. Appended rather than
    # substituted: an attached file is still the better source for what it
    # covers, and the two are labelled so the writer can tell them apart.
    document_context = list(untrusted_context or [])
    if project_reference_lines:
        trusted_context = list(trusted_context or []) + [
            "# 프로젝트 자료 인용 번호\n"
            "프로젝트 자료에서 가져온 사실을 사용한 문장 끝에는 아래 번호를 정확히 붙이세요. "
            "목록에 없는 번호를 만들지 마세요.\n" + "\n".join(project_reference_lines)
        ]
    #: Whether a figure could honestly have come from anywhere. Judged once for
    #: the run, by the same test the deck uses — a saved memory about who the
    #: user is is material and is not a measurement.
    grounded = deck_rules.has_numbers(request, document_context)
    if block := research.context_block(findings):
        document_context.append(block)

    if approved_plan is None:
        yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "running"}
        try:
            text, spent = await _complete(
                outline_model or model,
                build_document_messages(
                    SessionKind.report,
                    _OUTLINE_PROMPT.format(
                        ask_rule=grounding.ASK_RULE if may_ask else grounding.PROCEED_RULE,
                        lo=_MIN_SECTIONS,
                        hi=_MAX_SECTIONS,
                        request=request[:2000],
                    ),
                    trusted_context=trusted_context,
                    untrusted_context=document_context,
                    research_rule=research_rule,
                ),
                api_key,
                400,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("report outline failed: %s", exc)
            yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
            yield {"type": "error", "message": "보고서 개요를 만들지 못했습니다."}
            yield {"type": "usage", **usage}
            return

        plan_rules.count(usage, spent, planned_apart=bool(outline_model))
        # A question instead of a 목차 — see `grounding.ASK_RULE`. Only when the
        # request names material the sources do not carry; a bare topic is still
        # planned without anybody being asked about it.
        if may_ask and (asked := grounding.parse_needs(text)):
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {"type": "needs", "questions": [q.wire() for q in asked]}
            yield {"type": "usage", **usage}
            return
        title, headings = _parse_outline(text)
        if len(headings) < _MIN_SECTIONS:
            yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
            yield {
                "type": "error",
                "message": "보고서 개요를 만들지 못했습니다. 요청을 조금 더 구체적으로 적어 주세요.",
            }
            yield {"type": "usage", **usage}
            return

        yield {
            "type": "step",
            "id": "outline",
            "label": f"개요 {len(headings)}개 섹션",
            "status": "done",
            "detail": " · ".join(headings),
        }
        # Planned, and that is where this stops. The 목차 is offered rather
        # than written against: the caller stores it, shows it, and calls back
        # with it approved. Nothing has been written, which is what keeps the
        # report already on screen safe from a run nobody confirmed.
        #
        # The pictures are proposed here too and asked about separately, once
        # the outline is agreed — see `services.figures`. Proposed now because
        # the planner has the outline in front of it; asked later because two
        # decisions on one card is how somebody approves an expensive one by
        # accident.
        plan: dict[str, Any] = {
            "title": title[:200],
            "sections": headings,
            "visualStyle": design.visual_style_for(request),
        }
        if image_model:
            drawn = await figures.propose(
                request=request,
                title=title,
                parts=headings,
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
                plan["figures"] = drawn.wire()
        yield {"type": "proposal", "plan": plan}
        yield {"type": "usage", **usage}
        return

    title = str(approved_plan.get("title") or "")
    headings = [str(h).strip() for h in (approved_plan.get("sections") or []) if str(h).strip()]
    if not headings:
        yield {"type": "error", "message": "승인된 개요가 비어 있습니다."}
        yield {"type": "usage", **usage}
        return
    # Emitted only when the model produced one, so the caller keeps its fallback.
    if title:
        yield {"type": "title", "title": title[:200]}

    # One shelf for every section, and the same one the outline was chosen
    # from. Researched above — before this, the search ran here, which meant
    # the 목차 was planned with nothing under it.
    sources = findings.sources
    yield {"type": "sources", "sources": sources}
    refs = _refs_block(sources)

    sections = [
        {"id": f"s{i}_{uuid.uuid4().hex[:6]}", "heading": h, "level": 1}
        for i, h in enumerate(headings)
    ]
    # Announced up front so the panel can show the whole shape.
    for section in sections:
        yield {
            "type": "section",
            "sectionId": section["id"],
            "heading": section["heading"],
            "content": "",
            "done": False,
        }

    outline_text = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headings))
    written: list[str] = []
    #: Approved pictures by the index of the section they belong to. Empty when
    #: the figure card was answered 그림 없이, and then nothing below mentions a
    #: figure — which is the whole point of asking before the writing.
    wanted_figures = {
        int(f.get("section", -1)): f for f in (figures_plan or []) if f.get("prompt")
    }

    for index, section in enumerate(sections):
        # The position lives in `progress`, not in the text: spelled into both,
        # the surface renders "3/9 도입 (3/9)".
        label = str(section["heading"])
        # The outline lands before any section is written, so each step can say
        # where it sits in it — which is the only figure that answers "how much
        # of this is left" while the document builds.
        progress = {"current": index + 1, "total": len(sections)}
        yield {
            "type": "step",
            "id": section["id"],
            "label": label,
            "status": "running",
            "progress": progress,
        }
        try:
            body, spent = await _complete(
                model,
                build_document_messages(
                    SessionKind.report,
                    _SECTION_PROMPT.format(
                        heading=section["heading"],
                        outline=outline_text,
                        # Tail only: the whole document would crowd out the
                        # instruction by section six.
                        written="\n\n".join(written)[-4000:] or "(아직 없음)",
                        refs=refs,
                        request=request[:1500],
                    )
                    + (
                        # Told before the prose is written, so the section can
                        # refer to its figure. A picture added afterwards is a
                        # picture nobody mentioned.
                        "\n\n" + figures.note_for(figures.Figure(
                            section=index,
                            caption=str(wanted_figures[index].get("caption") or ""),
                            prompt="",
                        ))
                        if index in wanted_figures
                        else ""
                    ),
                    trusted_context=trusted_context,
                    untrusted_context=document_context,
                    research_rule=research_rule,
                ),
                api_key,
                1200,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("report section %r failed: %s", section["heading"], exc)
            yield {
                "type": "step",
                "id": section["id"],
                "label": label,
                "status": "error",
                "progress": progress,
            }
            yield {
                "type": "section",
                "sectionId": section["id"],
                "heading": section["heading"],
                "content": "_이 섹션을 쓰지 못했습니다._",
                "done": True,
            }
            section["content"] = ""
            continue

        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        # Models write a table with a blank line between every row, which is
        # not a table to any renderer. Closed here, once, so the panel, the
        # page view and the three exporters all read the same thing.
        # Stray ideographs read back into Hangul before anything is stored —
        # `services/hangul.py`. The deck and the page tracks did this at their
        # own doors and the report did not, so a 보고서 came out carrying 培育,
        # 劣势 and 書類 while a deck on the same subject did not. One product,
        # one answer.
        clean, _ = hangul.read_back(body)
        clean = hangul.tidy_spacing(clean)
        section["content"] = richtext.tidy_tables(_grounded_figures(clean, grounded))

        # The picture, if this section is one of the ones somebody paid for.
        # Drawn after the prose rather than before it so a failed drawing
        # leaves a section with no figure rather than a section that refers to
        # one — the prompt already told the writer a figure was coming, and
        # that sentence is the thing a missing picture makes wrong.
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
                # Into the prose, not onto the section.
                #
                # `section["images"]` was the writer's own channel and the
                # exporters read it — but nothing on screen did, so a figure
                # somebody paid for sat in the downloaded file and was
                # invisible in the panel. The body is the one place every
                # reader looks.
                #
                # As Markdown rather than as HTML, because the body *is*
                # Markdown here: the web view renders it, the page view turns
                # it into an `<img>` the editor can move, and the exporters
                # read it through the same `![…](…)` the document editor
                # produces when somebody pastes a picture in themselves.
                caption = str(picture.get("caption") or "").replace("]", " ")
                section["content"] = (
                    f"{section['content'].rstrip()}\n\n![{caption}]({picture['src']})"
                )
                yield {
                    "type": "step",
                    "id": f"fig{index}",
                    "label": drawing.get("caption") or "그림",
                    "status": "done",
                    "progress": progress,
                }
        written.append(f"## {section['heading']}\n{body}")
        yield {
            "type": "step",
            "id": section["id"],
            "label": label,
            "status": "done",
            "progress": progress,
        }
        yield {
            "type": "section",
            "sectionId": section["id"],
            "heading": section["heading"],
            "content": body,
            "done": True,
        }

    yield {"type": "report", "sections": sections}
    yield {"type": "usage", **usage}


async def rewrite_section(
    *,
    request: str,
    heading: str,
    sections: list[dict],
    target_id: str,
    model: str,
    api_key: str,
    note: str = "",
    sources: list[dict] | None = None,
) -> tuple[str, dict]:
    """Rewrites one section, with the rest of the document as context.

    Everything but the target is passed as written, so the new text does not
    repeat section two — the same guard the first pass uses.
    """
    outline = "\n".join(f"{i + 1}. {s.get('heading') or ''}" for i, s in enumerate(sections))
    written = "\n\n".join(
        f"## {s.get('heading')}\n{s.get('content') or ''}"
        for s in sections
        if s.get("id") != target_id and (s.get("content") or "").strip()
    )
    prompt = _SECTION_PROMPT.format(
        heading=heading,
        outline=outline,
        written=written[-4000:] or "(아직 없음)",
        # The document already carries numbered citations, so a rewrite without
        # the shelf would renumber them against nothing.
        refs=_refs_block(sources or []),
        request=request[:1500],
    )
    if note.strip():
        # Last and labelled: an unlabelled sentence appended to a prompt reads
        # as part of the original request.
        prompt += f"\n\n이번에 다시 쓰는 이유(반드시 반영):\n{note.strip()[:600]}"
    return await _complete(
        model,
        build_document_messages(SessionKind.report, prompt),
        api_key,
        1200,
    )


def word_count(sections: list[dict]) -> int:
    return sum(len((s.get("content") or "").split()) for s in sections)


def to_markdown(title: str, sections: list[dict]) -> str:
    parts = [f"# {title}"]
    for section in sections:
        parts.append(f"\n## {section['heading']}\n\n{section.get('content') or ''}")
    return "\n".join(parts).strip() + "\n"
