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
    design,
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
- style 은 이 발표가 어떤 자리에서 읽히는지에 맞는 인상이다. 셋 중 하나만 골라라:
  · 편집형 — 보고·검토·계획처럼 읽어서 판단하는 자리. 선과 넓은 여백.
  · 포스터형 — 홍보·설명회·발표회처럼 눈길을 먼저 잡아야 하는 자리. 강한 색면.
  · 미니멀 — 학술 발표·심사처럼 절제가 예의인 자리. 옅은 색과 작은 제목.
  요청에 인상이 적혀 있으면 그것을 따르고, 없으면 주제에서 골라라.
"""

#: 이름표와 렌더러가 아는 값. 프롬프트는 한국어로 묻고, 저장은 영어로 한다.
_STYLES = {"편집형": "editorial", "포스터형": "poster", "미니멀": "minimal"}

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
- **요청에 없는 소재를 지어내지 마라.** 요청이 문서의 쓰임만 말하고 무엇에 대한
  것인지는 말하지 않았으면 — "연구계획 발표자료", "제안 발표" 처럼 — 그 쓰임을
  가리키는 제목을 쓰고, 각 장은 그 쓰임이 요구하는 뼈대(배경·목표·방법·일정
  따위)로 잡아라. 요청에 없던 분야나 연도를 골라 채운 발표는 듣는 사람의 것이
  아니어서 그대로 쓸 수 없다.
- subtitle 은 표지에서 제목 아래 작게 붙는 한 줄이다. 40자 이내로, 이 발표가
  누구에게 무엇을 말하는지 적어라. 요청 문장을 그대로 옮기지 마라.
- 슬라이드 {lo}~{hi}장.
- 첫 장은 반드시 layout "title" 이고, 그 장의 제목은 발표 제목과 같게 하라.
- **"chart" 와 "metrics" 는 요청에 그 숫자가 있을 때만.** 요청에 수치가 없는데
  이 layout 을 고르면 그 장은 숫자를 지어내게 된다. 요청에 값이 여럿 있고 그
  **모양**을 봐야 하면 "chart", 기억시킬 숫자가 두셋 있으면 "metrics", 값을
  하나하나 읽어야 하면 "table".
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
- **"tiles" 는 요청이 머리글자·번호 묶음을 줄 때만**(4대 분야, P·H·A·S·E). 없는
  묶음을 표식으로 세우면 「V·C·U」 같은 뜻 없는 글자가 된다.
- **"timeline" 은 요청에 시점이나 절차의 순서가 있을 때만.** 연혁·일정·절차. 시점이
  없는 내용을 timeline 으로 잡으면 연도를 지어내게 된다. 절차라도 명령어 순서면
  bullets 가 낫다.
- 문의처·연락처·적용 시기·신청 방법처럼 **사실을 전하는 장은 "bullets"** 다. quote 로
  잡으면 내선 번호 대신 표어가 남는다.
- 확신이 없으면 "bullets" 다. 초안을 쓰는 단계에서 내용에 맞게 layout 을 바꿀 수
  있으니, 여기서 화려한 layout 을 미리 고르지 마라.
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

JSON 객체로만 답하라. "subject" 에는 이 발표가 무엇에 대한 것인지를 **요청에 적힌
말 그대로** 적어라 — 요청이 쓰임(중간발표, 학회 발표, 신청 발표)만 말하고 무엇에
대한 것인지 말하지 않았으면 빈 문자열.
예:
{{"title": "전이학습의 소량 데이터 효율성",
  "subtitle": "의료 영상 연구자를 위한 30분 개요",
  "subject": "전이학습",
  {theme_example}"slides": [{{"title": "전이학습의 소량 데이터 효율성", "layout": "title"}},
             {{"title": "왜 데이터가 부족한가", "layout": "bullets"}},
             {{"title": "사전학습과 미세조정 비교", "layout": "table"}}]}}

요청: {request}"""

_DRAFT_PROMPT = """아래 구성대로 발표 전체를 한 번에 써라. 장마다 JSON 객체 하나.

구성(이 순서, 이 제목, 이 layout 그대로):
{outline}

{facts}

장의 내용 필드 — layout 마다 하나만 채운다:
- bullets: "bullets": ["항목", ...] {count}개. 각 항목은 한 줄 40자 이내, 마침표 없이.
  **제목을 되풀이하는 항목(「requirements.txt 활용 방법」)이 아니라 그 장에서 실제로
  말할 사실·명령·규칙**을 적는다. 명령어·파일 이름은 그대로 쓴다(`python -m venv .venv`).
- two-column: "bullets": [...] {count_two}개. 앞 절반이 왼쪽, 뒤 절반이 오른쪽.
- table: "rows": [["기준", "A", "B"], ["행", "값", "값"], ...] 첫 줄이 머리글. 3~5행,
  2~4열. 칸은 짧게(15자 안쪽).
- timeline: "timeline": [["시점 또는 단계", "일"], ...] 3~6개. 일은 한 줄. **시점과 단계는
  요청에 있는 것만** — 요청에 날짜가 하나뿐이면 timeline 이 아니라 bullets 다. 「매주
  월요일 제출」「분기별 점검」처럼 요청에 없는 절차를 만들어 칸을 채우지 마라.
- bands: "bands": [["이름", "내용"], ...] 3~4개. 이름은 낱말 하나둘, 내용은 한 줄.
- tiles: "tiles": [["표식", "이름"], ...] 3~6개. 표식은 머리글자·번호 한두 글자. **요청에
  그런 묶음이 없으면 이 layout 을 쓰지 말고 bullets 로 바꿔라.**
- metrics: "metrics": [["값", "이름"], ...] 2~4개. **값은 「쓸 수 있는 수치」에 있는
  것만.** 없으면 이 layout 을 쓰지 말고 bullets 로 바꿔라.
- chart: "chart": {{"kind": "bar"|"line", "unit": "단위", "categories": [...],
  "series": [{{"name": "이름", "values": [...]}}]}}. 값은 「쓸 수 있는 수치」에 있는
  것만. 없으면 bullets 로.
- quote: "body": "한 문장" (60자 안쪽). 남길 만한 한 문장이 없으면 bullets 로. **요청에
  있는 문장이거나 발표자가 직접 하는 한 문장 요약만.** 직원·고객·전문가의 소감이나
  「직원들의 목소리」 같은 남의 말을 지어내지 마라 — 안내 자료에 없는 사람의 말을
  실으면 그 자료는 거짓말을 한 것이다.
- title, section: 내용 없이 "notes" 만.

모든 장에 "notes": 발표자가 이 장에서 **실제로 말할 문장** 3~5개. 「이 장에서는 ~를
설명합니다」처럼 장을 소개하는 문장이 아니라, 청중에게 하는 말 그대로. 개념은 보기
하나로 설명하고, 명령어가 있으면 무엇을 하는지 말한다.

규칙:
- 장마다 그 장에서만 하는 말. 앞 장을 다른 낱말로 되풀이하지 마라.
- **수치는 「쓸 수 있는 수치」에 있는 것과 그것으로 계산한 값만.** 없는 수치(비용,
  퍼센트, 초, 명)를 만들지 마라. 겁을 주거나 재촉하는 장을 만들지 마라.
- 영어 낱말을 한국어 문장에 섞지 말고, 중국어 한자를 쓰지 마라.
- 구성의 layout 은 제안이다. **내용이 그 모양이 아니면 바꿔라** — 이름 규칙을
  timeline 으로, 요약을 metrics 로 쓰지 마라. 기본은 bullets 이고, 두셋을 같은
  기준으로 견주면 table, 항목마다 이름이 붙으면 bands, 시간 순서가 요청에 있을
  때만 timeline, 남길 한 문장이 있을 때만 quote(전체 1장), 「쓸 수 있는 수치」에 값이
  있을 때만 metrics·chart, 요청이 머리글자 묶음을 줄 때만 tiles. 제목과 순서는
  바꾸지 마라.
- 규칙·명령어를 가르치는 장이면 명령어 자체를 항목으로 쓴다: `python -m venv .venv`,
  `pip freeze > requirements.txt`, `conda env create -f environment.yml`. **확신하는
  명령어와 옵션만 쓴다.** 기억이 흐린 플래그(`--with-env`, `-s /archive`)를 만들어
  넣으면 신입생이 그대로 쳐 보고 실패한다. 모르면 명령어 이름까지만 쓰고 옵션은
  「(문서 참고)」로 둔다. 틀린 관행(`.venv` 를 git 에 올리기)을 가르치지 마라.
- 요청이 규칙의 **이름만** 주고 내용을 주지 않았으면(「이름 규칙」, 「requirements
  고정」) 내용을 지어내지 마라. 무엇을 정해야 하는지를 적는다 — 「환경 이름:
  프로젝트명-연도 꼴로 통일(연구실에서 정함)」처럼. 「4~12자」 「밑줄 불가」 같은
  세부는 요청에 없으면 없는 것이다.
- 이모지(1️⃣ ✅ 🚀)를 쓰지 마라. 번호가 필요하면 timeline 이나 「1.」 을 쓴다.
- 요청에 없는 주제로 장을 채우지 마라. 구성에 그런 제목이 있으면 요청이 말한 것으로
  좁혀서 쓴다.

JSON 객체로만 답하라: {{"slides": [{{"title": "...", "layout": "...", ...}}, ...]}}

원래 요청: {request}{tail}"""


#: Told to the writer when the person said 있는 자료로 진행 to 「어떤 연구입니까?」.
#: A form, and it has to look like one — see `report._FRAME_RULE`.
_FRAME_RULE = (
    "**이 발표는 자료 없이 틀만 쓴다.** 요청에 없는 연구·프로젝트·결과·수치·이름을 어떤 "
    "것도 지어내지 마라. 장마다 그 장에 무엇을 넣어야 하는지 항목 이름과 「(여기에: 연구 "
    "질문)」 같은 괄호 빈칸으로만 채우고, 노트는 그 장에서 무엇을 말해야 하는지 한두 "
    "문장으로 안내한다. metrics·chart·table 은 머리글과 빈 칸만."
)

#: The same rule again at the end of the draft prompt, with the shape shown.
#: Put once at the top, under thirty lines of layout rules, it lost to
#: 「그 장에서 실제로 말할 사실을 적는다」 and the deck came back as eight
#: slides of 기존 연구의 한계를 극복 about a thesis nobody described.
_FRAME_TAIL = (
    "\n\n" + _FRAME_RULE + '\n예: {"title": "연구 질문", "layout": "bullets", '
    '"bullets": ["(여기에: 연구 질문 1)", "(여기에: 연구 질문 2)", '
    '"(여기에: 왜 이 질문인가)"], "notes": "이 장에서는 연구 질문을 하나씩 읽고 '
    '왜 지금 이 질문인지 한 문장으로 말한다."}'
)


def _facts_line(request: str) -> str:
    """The numbers a deck may put on a slide, read off the request.

    A summary slide came back as `metrics` with 「2억 원 | 재현 실패 연간
    비용」 and 「3초 | 초기 설정 평균 시간」 — numbers nobody had said, on the
    layout that makes a number look most like a fact. See `report._facts_line`.
    """
    found: list[str] = []
    for match in re.finditer(
        r"\d[\d,]*(?:\.\d+)?\s*(?:억|만|천|백)?\s*(?:원|%|퍼센트|시간|분|초|일|주|개월|년|회|건|명|대|장)?",
        request,
    ):
        token = re.sub(r"\s+", "", match.group(0))
        if len(token) >= 2 and token not in found:
            found.append(token)
    if not found:
        return (
            "쓸 수 있는 수치: (요청에 수치가 없다 — metrics·chart 를 쓰지 말고 숫자를 만들지 마라)"
        )
    return "쓸 수 있는 수치(요청에 있는 것 전부): " + ", ".join(found[:30])


_CLAIM = re.compile(
    r"\d[\d,.]*\s*(?:억|만|천)?\s*(?:원|%|퍼센트|시간|분|초|일|주|개월|년|회|건|명|대|장|석)?"
)

#: Layouts that hold the same facts better, highest first. When a slide is a
#: retelling of an earlier one, the better shape survives.
_SHAPE_RANK = {"table": 3, "bands": 2, "two-column": 1, "bullets": 1, "metrics": 0}


def _claims(row: dict[str, Any]) -> set[str]:
    """The figures a slide asserts, as text — what makes two slides the same."""
    text = json.dumps(
        {k: v for k, v in row.items() if k not in ("notes", "layout", "title")}, ensure_ascii=False
    )
    # A year is context, not a claim: 「2026년 | 2027년」 as table headers must
    # not make the table a different slide from the bands above it.
    return {
        re.sub(r"\s+", "", m.group(0))
        for m in _CLAIM.finditer(text)
        if len(m.group(0)) >= 2 and not re.fullmatch(r"\d{4}\s*년", m.group(0))
    }


_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}|\d[\d,.]*")
_STOP = frozenset(
    "있습니다 합니다 위해 통해 대한 대해 경우 이를 위한 하는 하고 있는 됩니다 그리고 또한 다만 "
    "따라서 것을 것이 것은 수를 있어 하며 하되 이번 오늘 해당 관련 주요 현재 방안 문제 필요 "
    "가능 진행 확보 예정".split()
)


def _words(row: dict[str, Any]) -> set[str]:
    """A slide's content words, cut to two syllables so 「채택」 meets 「채택합니다」."""
    text = json.dumps(
        {k: v for k, v in row.items() if k not in ("notes", "layout", "title")}, ensure_ascii=False
    )
    return {
        w[:2] if len(w) > 2 and re.match(r"[가-힣]", w) else w
        for w in _WORD.findall(text)
        if w not in _STOP
    }


#: Share of a slide's words an earlier slide already used before it is the
#: same slide again. Measured on a live deck: the pairs that said the same
#: thing twice (논의 bullets, then 방안 bands) sat at 0.56–0.67, and the pairs
#: that did not at 0.36–0.46.
_RETOLD_SHARE = 0.55
_RETOLD_WORDS = 15


def _retold(slides: list[dict[str, Any]], drafted: dict[int, dict[str, Any]]) -> set[int]:
    """Indices of drafted slides that only repeat figures earlier slides carry.

    A 복지제도 개편 deck put the same four changes — 500만→700만, 격년→매년,
    주 1일→2일, 100만→150만 — on a two-column, then metrics, then bands, then
    a table: four slides, one fact set, and 「한 장에는 그 장에서만 하는 말」
    in the prompt the whole time. A slide with three or more figures all of
    which earlier kept slides already state is dropped; when the newcomer is a
    table and the earlier one is not, the earlier one goes instead, because
    the table is where those figures read best.
    """
    kept: list[tuple[int, set[str], set[str], str]] = []
    dropped: set[int] = set()
    for index, slide in enumerate(slides):
        row = drafted.get(index)
        if row is None or slide["layout"] in ("title", "section"):
            continue
        claims, words = _claims(row), _words(row)
        layout = str(row.get("layout") or slide["layout"])
        said = set().union(*(c for _, c, _, _ in kept)) if kept else set()
        by_figures = len(claims) >= 3 and claims <= said
        twins = [
            item
            for item in kept
            if len(words) >= _RETOLD_WORDS and len(words & item[2]) / len(words) >= _RETOLD_SHARE
        ]
        if not by_figures and not twins:
            kept.append((index, claims, words, layout))
            continue
        weaker = [
            item
            for item in kept
            if (item in twins or (len(item[1]) >= 3 and item[1] <= claims))
            and _SHAPE_RANK.get(item[3], 1) < _SHAPE_RANK.get(layout, 1)
        ]
        if weaker:
            for item in weaker:
                kept.remove(item)
                dropped.add(item[0])
            kept.append((index, claims, words, layout))
        else:
            dropped.add(index)
    return dropped


def _facts_set(request: str) -> set[str]:
    """Every digit run in the request, so a number on a slide can be checked."""
    return {re.sub(r"[^\d.]", "", m) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", request)}


def _numbers_come_from(values: list[str], facts: set[str]) -> bool:
    """Whether every number in `values` appears in the request.

    A value with no digits at all (「높음」) passes; a value with digits the
    request never had (「2억」, 「100%」, 「30MB」) fails the slide.
    """
    for value in values:
        digits = re.sub(r"[^\d.]", "", value)
        if digits and digits not in facts:
            return False
    return True


def _moment_in_request(moment: str, request: str) -> bool:
    """Whether a timeline's 'when' was given — a date the request has, or its words."""
    compact = re.sub(r"\s+", "", request)
    cell = re.sub(r"\s+", "", moment)
    if not cell:
        return False
    if cell in compact:
        return True
    digits = re.findall(r"\d+", cell)
    return bool(digits) and all(d in compact for d in digits)


def _split_deck_draft(
    text: str, slides: list[dict[str, Any]], facts: set[str], request_text: str = ""
) -> dict[int, dict[str, Any]]:
    """The draft's slides matched to the outline's, by position then by title.

    Returns `{index: data}`. A slide the draft skipped is simply absent and is
    written on its own below. A slide whose content is empty is absent too —
    the per-slide pass has a better chance than an empty object does.
    """
    data = _json_object(text)
    rows = data.get("slides") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}

    def key(t: Any) -> str:
        return re.sub(r"[\s:：.]+", "", str(t or "")).lower()

    by_title = {key(r.get("title")): r for r in rows if isinstance(r, dict)}
    out: dict[int, dict[str, Any]] = {}
    seen_bullets: list[list[str]] = []
    for index, slide in enumerate(slides):
        row = rows[index] if index < len(rows) and isinstance(rows[index], dict) else None
        if row is None or key(row.get("title")) != key(slide["title"]):
            row = by_title.get(key(slide["title"]), row)
        if not isinstance(row, dict):
            continue
        # 앞 장을 그대로 베낀 장은 초안에서 빼고 따로 쓴다 — 그쪽은 앞 장을
        # 「이미 말한 내용」으로 받으므로 되풀이할 수 없다.
        bullets = [str(b).strip() for b in (row.get("bullets") or []) if str(b).strip()]
        if bullets and any(bullets == earlier for earlier in seen_bullets):
            continue
        if bullets:
            seen_bullets.append(bullets)
        # 요청에 없는 숫자로 채운 metrics·chart 는 버린다. 그 layout 은 숫자를
        # 가장 사실처럼 보이게 하는 자리라, 지어낸 숫자가 가장 해로운 자리다.
        if row.get("metrics") and not _numbers_come_from(
            [str(v) for v, *_ in (m for m in row["metrics"] if isinstance(m, list) and m)], facts
        ):
            row = {k: v for k, v in row.items() if k != "metrics"}
            row["layout"] = "bullets"
        if isinstance(row.get("chart"), dict) and not _numbers_come_from(
            [
                str(v)
                for series in (row["chart"].get("series") or [])
                if isinstance(series, dict)
                for v in (series.get("values") or [])
            ],
            facts,
        ):
            row = {k: v for k, v in row.items() if k != "chart"}
            row["layout"] = "bullets"
        # 요청에 없는 시점으로 채운 timeline 은 bullets 로 내린다. 「2027년 1월
        # 1일 발효」 하나가 요청에 있었고, 「매주 월요일 제출」「분기별 점검」 넷이
        # 그 뒤에 따라왔다 — 절차를 지어내 칸을 채운 것이다.
        if isinstance(row.get("timeline"), list):
            steps = [t for t in row["timeline"] if isinstance(t, list) and t]
            sure = [t for t in steps if _moment_in_request(str(t[0]), request_text)]
            if len(sure) < 2:
                row = {k: v for k, v in row.items() if k != "timeline"}
                row["bullets"] = [f"{t[0]} – {t[1]}" if len(t) > 1 else str(t[0]) for t in steps]
                row["layout"] = "bullets"
            elif len(sure) < len(steps):
                row = {**row, "timeline": sure}
        # 초안이 layout 을 바꿨으면 따른다 — 내용에 맞지 않는 layout 을 고집한 장이
        # 빈 사각형이 되는 것보다 낫다. 표지와 간지는 그대로.
        wanted = str(row.get("layout") or "")
        if wanted and slide["layout"] not in ("title", "section") and wanted in _LAYOUTS:
            slide["layout"] = wanted
        if slide["layout"] in ("title", "section") or any(
            row.get(k)
            for k in ("bullets", "rows", "timeline", "bands", "tiles", "metrics", "chart", "body")
        ):
            out[index] = row
    return out


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
                    # No thinking on a call whose whole answer is one JSON
                    # object — see `thinking.NO_REASONING` for the measurements.
                    # Safe to send everywhere: the proxy runs `drop_params`, so
                    # a provider that has never heard of it never sees it.
                    "reasoning": thinking.NO_REASONING,
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


def _json_object(text: str) -> dict[str, Any]:
    """The first JSON object in whatever the model wrapped it in.

    Models fence their JSON, prefix it with a sentence, or both. Returning `{}`
    rather than raising lets each caller decide what a miss costs — for one
    slide it is a gap, for the outline it is the whole deck.

    Stray ideographs are read back into Hangul here rather than field by field
    downstream: a slide is a title, a body, bullets, metric labels and table
    cells, and every one of them is a place `動的 엔드포인트` has turned up.

    Over the parsed values, not over the JSON text. Reading it back from the
    text looked simpler and was wrong: a gloss is ideographs inside brackets —
    `분산(分散)` — and a JSON array is ideographs inside brackets too, so
    `[{"title": "대학生的 역량 격차"}]` was protected as if somebody had written
    it as an aside. It reached the proposal card and then the slide.
    """
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return _read_back_values(data) if isinstance(data, dict) else {}


def _read_back_values(value: Any) -> Any:
    """The same structure with every string read back into Hangul."""
    if isinstance(value, str):
        return hangul.tidy_spacing(hangul.read_back(value)[0])
    if isinstance(value, list):
        return [_read_back_values(item) for item in value]
    if isinstance(value, dict):
        # Keys are the schema's and are ASCII; only the values are shown.
        return {key: _read_back_values(item) for key, item in value.items()}
    return value


def requested_slides(request: str) -> int | None:
    """Slide count stated in the request, clamped to bounds. `None` if unstated.

    Parsed here rather than left to the model, which rounds it down quietly.
    """
    match = re.search(r"(\d{1,3})\s*(?:장|페이지|슬라이드|쪽)", request)
    if not match:
        return None
    asked = int(match.group(1))
    return max(_MIN_SLIDES, min(asked, _MAX_SLIDES)) if asked > 0 else None


def _theme_style(text: str) -> str:
    """The visual impression the outline chose, or `""` when it named none.

    Read with its own regex for the same reason `_theme_accent` is: a salvaged
    outline — one whose JSON did not parse whole — should still keep the look
    the model picked for it.
    """
    match = re.search(r'"style"\s*:\s*"([^"]+)"', text)
    return _STYLES.get((match.group(1).strip() if match else ""), "")


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
            plan.append({"title": heading, "layout": layout if layout in _LAYOUTS else "bullets"})

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
        # 객체로 온 항목(`{"left": "명령", "right": "설명"}`, `{"text": …}`)은 그
        # 값들을 한 줄로 잇는다. `str(dict)` 를 항목으로 쓰면 화면에 중괄호가
        # 찍히고, 버리면 그 장이 「쓰지 못했습니다」가 된다.
        if isinstance(item, dict):
            item = " – ".join(str(v).strip() for v in item.values() if str(v).strip())
        elif isinstance(item, list):
            item = " – ".join(str(v).strip() for v in item if str(v).strip())
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
#: A deck about the person's own work — nothing to say without it. 「학위논문
#: 연구계획 발표자료」 came back as 새로운 알고리즘으로 정확도를 높임 over eight
#: slides, for a thesis nobody described; the planner's `subject` was the
#: request's own words, so the subject check let it through.
_OWN_WORK = re.compile(r"학위논문|연구계획|과제 신청|사업 신청|신청 발표|녹취")

_FIGURE = re.compile(
    # A year is not a measurement. `2026년 계획` matched the three-digit rule
    # and let a deck about next year's plan draw a chart of nothing.
    #
    # `(?<!\d)` pins every match to the start of a digit run — 2026년 cannot
    # be salvaged by backtracking into 202, because a regex that can retreat
    # inside the number it is judging is judging a different number.
    #
    # Every quantifier is bounded. Static analysis flags an unbounded `\d+`
    # beside an overlapping alternative as polynomial on adversarial digit
    # strings, and user-typed text reaches this — a twelve-digit cap loses
    # nothing anybody has ever put on a slide and closes the question by
    # construction rather than by argument.
    r"(?<!\d)(?:"
    r"\d{1,12}[.,]\d"
    r"|\d{3,12}(?!\d)(?!\s{0,4}년)"
    r"|\d{1,12}\s{0,4}(?:%|퍼센트|원|명|건|배|억|만|천|시간|분|초|주|개월|점|개|회|위)"
    r")"
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
#: Digits and *multi-letter* Roman numerals. A lone I/V/X is as likely a word
#: or a product name as a number — and with `re.I`, a lone x too — so a divider
#: somebody typed into the proposal card could be silently deleted for it.
_NUMBER_ONLY = re.compile(r"^\s*(?:part|섹션|section|장)?\s*(?:[0-9]+|[IVX]{2,})\s*[.)]?\s*$", re.I)


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
#: Whole labels, not substrings: `일자` is inside 일자리 — the commonest metric
#: label in a Korean 사업 발표 — and the unanchored version deleted 신규 일자리,
#: 마감률 and 연도별 추이 from metrics slides while it was catching deadlines.
_CALENDAR = re.compile(
    r"^(년도|연도|마감|기한|일자|신청월|개강|종강|년|월|일|요일"
    r"|마감일|마감월|마감년도|신청일|시작일|종료일|개강일|종강일|접수일|발표일)$"
)


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
        last = label.split()[-1] if label.split() else ""
        if figure and label and not _CALENDAR.match(last):
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
    density: str = "speaker",
    frame: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Writes the bodies for an outline that has already been agreed to.

    Lifted out of `write` so the approved-plan path and the plan-it-now path
    reach exactly the same code. Two copies of this loop would be two decks
    that differ in ways nobody chose.
    """
    #: Approved pictures by the index of the slide they belong to.
    wanted_figures = {int(f.get("section", -1)): f for f in (figures_plan or []) if f.get("prompt")}
    yield {
        "type": "step",
        "id": "outline",
        "label": f"구성 {len(plan)}장",
        "status": "done",
        "detail": " · ".join(item["title"] for item in plan),
    }
    if title:
        yield {"type": "title", "title": hangul.tidy_spacing(title)[:200]}

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

    # 한 번에 쓴다 — `report.write` 와 같은 이유로.
    #
    # Slide by slide, each call saw the outline and a 3,000-character tail of
    # what came before, and wrote a one-bullet slide with garbled notes, a
    # bullets slide whose bullets were the titles of other slides, and a
    # metrics slide with 「2억 원」 nobody had said. The whole deck in one call
    # keeps the thread; the per-slide pass below writes whatever the draft
    # skipped, and stays for rewrites.
    drafted: dict[int, dict[str, Any]] = {}
    yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "running"}
    try:
        draft_text, spent = await _complete(
            model,
            build_document_messages(
                SessionKind.slides,
                _DRAFT_PROMPT.format(
                    outline="\n".join(
                        f"{i + 1}. {s['title']}  (layout: {s['layout']})"
                        for i, s in enumerate(slides)
                    ),
                    facts=_FRAME_RULE if frame else _facts_line(request),
                    count="4~6" if density == "reading" else "3~4",
                    count_two="6~8" if density == "reading" else "4~6",
                    request=request[:1500],
                    tail=_FRAME_TAIL if frame else "",
                ),
                request=request,
                trusted_context=trusted_context,
                untrusted_context=untrusted_context,
                research_rule=research_rule,
            ),
            api_key,
            min(9000, 500 * len(slides) + 600),
        )
        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        drafted = _split_deck_draft(draft_text, slides, _facts_set(request), request)
        # 같은 사실을 다른 모양으로 되풀이한 장은 뺀다.
        retold = _retold(slides, drafted)
        if retold:
            log.info("deck retold slides dropped: %s", ",".join(str(i) for i in sorted(retold)))
            kept = [i for i in range(len(slides)) if i not in retold]
            slides[:] = [slides[i] for i in kept]
            drafted = {new: drafted[old] for new, old in enumerate(kept) if old in drafted}
            wanted_figures = {
                new: wanted_figures[old] for new, old in enumerate(kept) if old in wanted_figures
            }
        yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "done"}
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("deck draft failed, writing slide by slide: %s", exc)
        yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "error"}

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
        density_rule = (
            "\n\n이 자료는 발표자 없이 전달해 읽는 자료다. 표·근거·맥락을 한 장 안에서 "
            "이해할 수 있게 쓰고, notes에만 핵심 설명을 숨기지 마라. 글자를 줄여 억지로 "
            "채우지 말고 현재 layout의 읽기 쉬운 한도를 지켜라."
            if density == "reading"
            else "\n\n이 자료는 발표자가 설명하는 자료다. 한 장에는 한 가지 핵심만 두고, "
            "짧은 문구와 넓은 여백을 우선하며 자세한 설명은 notes에 둬라."
        )
        try:
            if index in drafted:
                body, spent = (
                    json.dumps(drafted[index], ensure_ascii=False),
                    {"inputTokens": 0, "outputTokens": 0},
                )
            else:
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
                            count=(
                                ("6~8" if slide["layout"] == "two-column" else "4~6")
                                if density == "reading"
                                else ("4~6" if slide["layout"] == "two-column" else "2~4")
                            ),
                            request=request[:1500],
                        )
                        + density_rule,
                        request=request,
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
            # 마커와 함께 layout 도 평문으로 되돌린다. timeline 인 채로 남으면
            # 화면과 파일이 "항목 없는 연혁" 을 그리려 들고, 검사는 빈 장으로
            # 읽는다 — 표·차트가 빈 답에서 bullets 로 내려가는 것과 같은 규칙.
            if slide.get("layout") not in ("title", "section"):
                slide["layout"] = "bullets"
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
        # 장별 경로에도 같은 검증. 초안이 빠뜨린 장을 이쪽이 쓰는데, 여기서
        # 지어낸 「0.8초 | 파일 파싱 속도」가 그대로 화면에 올라갔다.
        if index not in drafted:
            facts = _facts_set(request)
            if data.get("metrics") and not _numbers_come_from(
                [str(m[0]) for m in data["metrics"] if isinstance(m, list) and m], facts
            ):
                data.pop("metrics", None)
                slide["layout"] = "bullets"
            if isinstance(data.get("chart"), dict) and not _numbers_come_from(
                [
                    str(v)
                    for series in (data["chart"].get("series") or [])
                    if isinstance(series, dict)
                    for v in (series.get("values") or [])
                ],
                facts,
            ):
                data.pop("chart", None)
                slide["layout"] = "bullets"
        raw_notes = data.get("notes")
        # 배열로 온 노트는 문장으로 잇는다 — 화면에 `['…', '…']` 가 그대로 찍혔다.
        if isinstance(raw_notes, list):
            raw_notes = " ".join(str(n).strip() for n in raw_notes if str(n).strip())
        notes = str(raw_notes or "").strip()

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
            # 마커와 함께 layout 도 평문으로 되돌린다. timeline 인 채로 남으면
            # 화면과 파일이 "항목 없는 연혁" 을 그리려 들고, 검사는 빈 장으로
            # 읽는다 — 표·차트가 빈 답에서 bullets 로 내려가는 것과 같은 규칙.
            if slide.get("layout") not in ("title", "section"):
                slide["layout"] = "bullets"
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
            density=str(approved_plan.get("density") or "speaker"),
            frame=bool(approved_plan.get("frame")),
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
                    theme_example=(
                        "" if fixed_accent else '"theme": "청록",\n  "style": "편집형",\n  '
                    ),
                    request=request[:2000],
                )
                + nudge,
                request=request,
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
    unmaterial = grounding.subject_missing(text, request, "\n".join(untrusted_context or [])) or (
        _OWN_WORK.search(request)
        and not has_numbers(request, [])
        and len(request) < 300
        and not any(block.strip() for block in (untrusted_context or []))
    )
    if may_ask and unmaterial:
        # 주제가 없는 요청은 묻는다. 「캡스톤 중간발표 10분」 was planned as an
        # AI 맞춤 학습 플랫폼 nobody is building, and 「학회 구두 발표 15분,
        # 수치를 크게」 as seven slides of 500회 and 20% about nothing — the
        # rule against invented 소재 was in the prompt both times. Same check
        # as the report's, on the same field.
        yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
        yield {
            "type": "needs",
            "questions": [
                grounding.Question(
                    id="subject",
                    question=(
                        "어떤 연구입니까? 주제, 연구 질문, 방법과 지금까지의 결과를 "
                        "적거나 파일을 붙여 주세요."
                        if _OWN_WORK.search(request)
                        else "무엇에 대한 발표입니까? 주제와, 보여 줄 결과·수치가 있으면 "
                        "함께 적어 주세요."
                    ),
                    options=[],
                ).wire()
            ],
        }
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
        # 말한 사람이 먼저다. `visual_style_for` only answers when the request
        # actually says so — 「포스터처럼」, 「담백하게」 — and returns the
        # editorial default otherwise. That default used to reach every deck,
        # so a 학술 심사 발표 and a 홍보 설명회 came out wearing the same face.
        # The outline picks for the ones nobody described.
        "visualStyle": (
            design.visual_style_for(request)
            if design.visual_style_for(request) != "editorial"
            else (_theme_style(text) or "editorial")
        ),
        "density": (
            "reading"
            if any(
                word in request
                for word in (
                    "읽기용",
                    "배포용",
                    "공유용",
                    "회의 자료",
                    "검토 자료",
                    "보고 자료",
                )
            )
            else "speaker"
        ),
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
    if unmaterial:
        # 있는 자료로 진행 — the writing pass writes a form. See `_FRAME_RULE`.
        proposal["frame"] = True
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
        model, build_document_messages(SessionKind.slides, prompt, request=request), api_key, 600
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
        # Content from the old attempt must not survive beside the rewrite.
        # In particular, a failed slide carries UNWRITTEN as its old body or
        # bullet; keeping the opposite field makes a successful retry still
        # look failed in previews and exports.
        result.pop("body", None)
    if body:
        result["body"] = body
        result.pop("bullets", None)
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
    if slide.get("layout") in ("title", "section"):
        # A divider says the name of its part and nothing else — that is the
        # whole of what a divider is, so its title is its content. Read as
        # empty, `filled` dropped approved dividers from finished decks.
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
        # Everything else a slide can hold, or "텍스트로 복사" hands over six
        # bare headings out of eleven layouts and calls it the deck.
        for row in slide.get("rows") or []:
            parts.append("| " + " | ".join(str(cell) for cell in row) + " |")
        for pair in slide.get("metrics") or []:
            if isinstance(pair, list) and len(pair) >= 2:
                parts.append(f"- **{pair[0]}** {pair[1]}")
        for key in _PAIRED:
            for pair in slide.get(key) or []:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    parts.append(f"- {pair[0]} — {pair[1]}")
        if chart := slide.get("chart"):
            for item in chart.get("series") or []:
                values = " · ".join(str(v) for v in item.get("values") or [])
                parts.append(f"- {item.get('name') or '계열'}: {values}")
        if slide.get("notes"):
            parts.append(f"\n발표 노트: {slide['notes']}")
        parts.append("")
    return "\n".join(parts).strip() + "\n"


__all__ = ["DeckError", "filled", "has_content", "to_markdown", "write"]
