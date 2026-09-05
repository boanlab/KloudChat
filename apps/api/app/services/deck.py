"""Deck generation: an outline call proposes the slides, an approved plan is drafted in one call,
and gaps are written per slide.

Layouts:

* `title`      — the cover, always first
* `agenda`     — the 목차, read back from the outline; no model call
* `section`    — a divider naming the part that follows
* `bullets`    — the body of the deck
* `quote`      — one line, for a claim worth pausing on
* `statement`  — the presenter's own conclusion, set large, at most once
* `two-column` — a long list split in two
* `table`      — values read against each other
* `metrics`    — two to four figures, set large
* `big-number` — one figure, very large, with a line saying what it means
* `chart`      — a bar or line chart, drawn from real numbers
* `bands`      — a name beside a band of text, down the slide
* `tiles`      — a letter or number set large over its name
* `timeline`   — dates beside what happened
* `steps`      — a procedure across the slide, numbered by position
* `cards`      — peers side by side as titled boxes
* `closing`    — the last slide: what to remember, and a line to end on

A layout is offered only if the preview, the .pptx and the .pdf can all draw it.
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
    deck_type,
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

#: Slide-count bounds for an explicit request.
_MIN_SLIDES = 5
_MAX_SLIDES = 50

#: Upper bound when no count was asked for.
_DEFAULT_MAX = 12

#: Layouts every renderer can draw.
_LAYOUTS = (
    "title",
    "section",
    "agenda",
    "bullets",
    "quote",
    "statement",
    "two-column",
    "table",
    "metrics",
    "big-number",
    "chart",
    "bands",
    "tiles",
    "timeline",
    "steps",
    "cards",
    "closing",
)

#: Slides filled from the outline without a model call.
_STRUCTURAL = ("title", "section", "agenda")

#: Layouts that carry the argument; the variety check is judged on these.
_BODY_LAYOUTS = tuple(layout for layout in _LAYOUTS if layout not in (*_STRUCTURAL, "closing"))

#: Body marker for a slide that did not get written; `deck_export` leaves such
#: a slide out of the file.
UNWRITTEN = "이 장을 쓰지 못했습니다."

#: Default accent, stored on every slide.
_ACCENT = "#5b5bd6"

#: Asked only when no design system fixes the accent.
_THEME_RULE = """- theme 은 주제에 맞는 색 이름 하나다. 다음 중에서만 골라라:
  {themes}
- style 은 이 발표가 어떤 자리에서 읽히는지에 맞는 인상이다. 일곱 중 하나만 골라라:
  · 편집형 — 보고·검토·계획처럼 읽어서 판단하는 자리. 선과 넓은 여백.
  · 포스터형 — 홍보·설명회·발표회처럼 눈길을 먼저 잡아야 하는 자리. 강한 색면.
  · 미니멀 — 학술 발표·심사처럼 절제가 예의인 자리. 옅은 색과 작은 제목.
  · 다크 — 기술·제품·데모처럼 화면을 어둡게 하고 보는 자리. 어두운 바탕에 빛나는 강조색.
  · 분할형 — 사업 보고·제안·기관 발표. 왼쪽 색면과 큰 번호, 선으로 그린 상자.
  · 따뜻한 — 교육·문화·복지·생활 주제. 크림색 종이 바탕과 둥근 상자.
  · 흑백 — 디자인·건축·연구·전시. 검정 선과 큰 제목, 색은 쓰지 않는다.
  요청에 인상이 적혀 있으면 그것을 따르고, 없으면 주제에서 골라라. 같은 주제라도
  자리가 다르면 다른 인상이다 — 늘 편집형으로 도망가지 마라.
- 이 요청에는 theme "{theme}", style "{style}" 이 어울린다. 요청이 다른 색이나 인상을
  말하지 않는 한 이 둘을 그대로 써라.
"""

#: Stored `visualStyle` value → prompt label, for the suggestion the outline is shown.
_STYLE_LABELS = {
    "editorial": "편집형",
    "poster": "포스터형",
    "minimal": "미니멀",
    "dark": "다크",
    "split": "분할형",
    "warm": "따뜻한",
    "mono": "흑백",
}

#: Topic words → accent name. Checked in order; the first topic named wins. A deck
#: about nothing on this list takes a colour keyed off its request, so two decks on
#: different subjects do not come out the same colour.
_TOPIC_THEMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("남색", ("보안", "금융", "법", "정책", "경영진", "이사회", "투자", "은행")),
    (
        "파랑",
        (
            "기술",
            "시스템",
            "소프트웨어",
            "개발",
            "데이터",
            "인공지능",
            "ai",
            "클라우드",
            "네트워크",
        ),
    ),
    ("초록", ("환경", "에너지", "농업", "생태", "탄소", "지속가능", "친환경")),
    ("청록", ("의료", "건강", "병원", "바이오", "제약", "간호")),
    ("주황", ("교육", "수업", "강의", "학생", "청소년", "학습")),
    ("자주", ("문화", "예술", "디자인", "패션", "공연", "미디어")),
    ("빨강", ("홍보", "행사", "축제", "캠페인", "모집", "마케팅")),
)
#: What a request naming no topic draws from; the product's own purple stays out so a
#: deck does not look like the app's chrome.
_ROTATION = ("파랑", "청록", "남색", "초록", "주황", "자주")


def suggest_look(request: str) -> tuple[str, str]:
    """`(theme name, style label)` the outline is shown as this request's default.

    The room decides the style (`design.venue_style_for`), the subject decides the
    colour; a request naming neither gets a colour keyed off its own words, never the
    same one every time. The outline may still override both when the request says so.
    """
    text = (request or "").lower()
    style = design.visual_style_for(request)
    if style == "editorial":
        style = design.venue_style_for(request) or "editorial"
    theme = next((name for name, words in _TOPIC_THEMES if any(w in text for w in words)), "")
    if not theme:
        digest = sum(ord(ch) for ch in re.sub(r"\s+", "", text)[:200])
        theme = _ROTATION[digest % len(_ROTATION)]
    return theme, _STYLE_LABELS[style]


#: Prompt label → stored `visualStyle` value.
_STYLES = {
    "편집형": "editorial",
    "포스터형": "poster",
    "미니멀": "minimal",
    "다크": "dark",
    "분할형": "split",
    "따뜻한": "warm",
    "흑백": "mono",
}

#: Accent palette the outline picks from by name; each carries white text.
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
- **여섯 장이 넘는 발표는 둘째 장에 "agenda"(목차) 를 넣어라.** 내용은 쓰지 마라 —
  구성에서 채운다. 제목은 "목차" 또는 "발표 순서".
- **마지막 장은 "closing"** — 기억할 것 두셋과 마무리 한 줄. 여섯 장이 넘는 발표에만.
- 나머지 장은 말할 내용에 맞는 layout 을 골라라. 항목을 나열하면 "bullets",
  둘을 나란히 견주거나 항목이 6개 이상이면 "two-column", 한 문장으로 남길
  대목이면 "quote". quote 는 전체에서 최대 2장. **발표의 결론 한 마디를 크게 세우는
  장은 "statement"** — 남의 말이 아니라 발표자가 말하려는 것, 전체에서 최대 1장.
- **절차·단계·과정을 차례로 놓되 날짜가 없으면 "steps"** (3~5단계, 가로로 번호가
  매겨진다). 접수→심사→선정, 조사→설계→구현→평가 처럼. 날짜가 있으면 "timeline".
- **같은 급의 항목 셋넷에 각각 이름과 한두 줄 설명이 붙으면 "cards"** — 분야·전략·역할·
  선택지처럼 나란히 놓고 보는 것. bands 는 이름표가 줄 앞에 서는 세로 목록이고,
  cards 는 상자를 옆으로 세운다. 순서가 뜻을 가지면 steps 다.
- **요청에 수치가 하나뿐인데 그 수치가 발표의 핵심이면 "big-number"** — 숫자 하나를
  크게, 그 뜻을 한 줄로. 두셋이면 "metrics".
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
  잡으면 내선 번호 대신 표어가 남는다. **퀴즈·연습 문제 장도 "bullets"** — 문제
  자체를 항목으로 적는다. 상태 전이·구조·흐름처럼 그림으로 그릴 것은 chart 가
  아니라 bands 나 bullets 다(chart 는 수치 계열만 그린다).
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
- **같은 layout 을 세 장 연속으로 쓰지 마라. bullets 는 연속 두 장까지.** 표지·목차·
  간지·마무리를 뺀 나머지에서 최소 네 가지를 써라. 여덟 장 중 여섯이 bullets 인
  발표는 넘겨도 넘긴 것 같지 않다 — 이름이 붙는 내용은 bands 나 cards 로, 절차는
  steps 로, 비교는 table 로, 결론은 statement 로 모양을 주어라.
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
- steps: "steps": [["단계", "내용"], ...] 3~5개. 단계는 이름 한 마디(번호 없이), 내용은
  한 줄. 요청에 있는 절차만.
- cards: "cards": [["이름", "내용"], ...] 3~4개. 이름은 한 마디, 내용은 한두 문장 80자
  안쪽. 같은 급의 항목만.
- statement: "title": 핵심 한 마디(12자 안쪽), "body": 그것을 푸는 한 문장(60자 안쪽).
- big-number: "metrics": [["값", "이름"]] 하나, "body": 그 숫자의 뜻 한 줄. 값은 「쓸 수
  있는 수치」에 있는 것만.
- closing: "bullets": 기억할 것 2~3개(각 30자 안쪽), "body": 마무리 한 줄(「질문을
  환영합니다」). 앞 장에서 말한 것만.
- agenda: 내용 없이 "notes" 만 — 목차는 구성에서 채운다.
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
  기준으로 견주면 table, 항목마다 이름이 붙으면 bands(세로) 나 cards(가로 상자),
  날짜 없는 절차는 steps, 시간 순서가 요청에 있을 때만 timeline, 남길 한 문장이
  있을 때만 quote(전체 1장), 결론 한 마디는 statement, 「쓸 수 있는 수치」에 값이
  있을 때만 metrics·chart·big-number, 요청이 머리글자 묶음을 줄 때만 tiles.
  제목과 순서는 바꾸지 마라. 표지·목차·간지·마무리의 layout 은 바꾸지 마라.
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


#: Writer rule when the person chose 있는 자료로 진행 with no subject: write a
#: form with blanks. See `report._FRAME_RULE`.
_FRAME_RULE = (
    "**이 발표는 자료 없이 틀만 쓴다.** 요청에 없는 연구·프로젝트·결과·수치·이름을 어떤 "
    "것도 지어내지 마라. 장마다 그 장에 무엇을 넣어야 하는지 항목 이름과 「(여기에: 연구 "
    "질문)」 같은 괄호 빈칸으로만 채우고, 노트는 그 장에서 무엇을 말해야 하는지 한두 "
    "문장으로 안내한다. metrics·chart·table 은 머리글과 빈 칸만."
)

#: `_FRAME_RULE` repeated at the end of the draft prompt with an example.
_FRAME_TAIL = (
    "\n\n" + _FRAME_RULE + '\n예: {"title": "연구 질문", "layout": "bullets", '
    '"bullets": ["(여기에: 연구 질문 1)", "(여기에: 연구 질문 2)", '
    '"(여기에: 왜 이 질문인가)"], "notes": "이 장에서는 연구 질문을 하나씩 읽고 '
    '왜 지금 이 질문인지 한 문장으로 말한다."}'
)


def _facts_line(request: str) -> str:
    """Prompt line listing the numbers found in the request. See `report._facts_line`."""
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

#: Preference between layouts carrying the same facts; the higher survives.
_SHAPE_RANK = {"table": 3, "bands": 2, "two-column": 1, "bullets": 1, "metrics": 0}


def _claims(row: dict[str, Any]) -> set[str]:
    """The figures a slide asserts, as text; a bare year is not a claim."""
    text = json.dumps(
        {k: v for k, v in row.items() if k not in ("notes", "layout", "title")}, ensure_ascii=False
    )
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


#: Share of a slide's words an earlier slide already used before it counts as
#: a retelling. Genuine retellings measure 0.56–0.67, distinct slides 0.36–0.46.
_RETOLD_SHARE = 0.55
_RETOLD_WORDS = 15


def _retold(slides: list[dict[str, Any]], drafted: dict[int, dict[str, Any]]) -> set[int]:
    """Indices of drafted slides that repeat an earlier slide's figures or words.

    When the newcomer has the better `_SHAPE_RANK`, the earlier slide is
    dropped instead.
    """
    kept: list[tuple[int, set[str], set[str], str]] = []
    dropped: set[int] = set()
    for index, slide in enumerate(slides):
        row = drafted.get(index)
        if row is None or slide["layout"] in _STRUCTURAL:
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
    """Whether every digit run in `values` appears in `facts`; values without digits pass."""
    for value in values:
        digits = re.sub(r"[^\d.]", "", value)
        if digits and digits not in facts:
            return False
    return True


_QUANTITY = re.compile(
    r"\d[\d,.]*\s*(?:만|억|천)?\s*(?:개소|개월|시간|퍼센트|명|분|초|일|주|년|월|회|건|대|개|석|층|원|%|"
    r"km|kg|m|cm|mm|㎡|Hz|kHz|V|A|W)"
)


def _unrequested_quantity(text: str, request: str) -> bool:
    """Whether `text` carries a number-with-unit the request does not (「11개소」 vs 「11월」)."""
    compact = re.sub(r"\s+", "", request)
    for m in _QUANTITY.finditer(text):
        token = re.sub(r"\s+", "", m.group(0))
        if re.fullmatch(r"\d{4}년", token) or token in compact:
            continue
        # 「2,400만원」 in the text and 「2,400만 원」 in the request.
        if token.replace(",", "") in compact.replace(",", ""):
            continue
        return True
    return False


def _moment_in_request(moment: str, request: str) -> bool:
    """Whether a timeline moment appears in the request, verbatim or by its digits."""
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
    """`{index: data}` for draft slides matched to the outline by position, then by title.

    Absent indices (skipped, duplicated, or emptied by validation) are written
    per slide afterwards.
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
        # A slide copying an earlier one's bullets is left to the per-slide pass.
        bullets = [str(b).strip() for b in (row.get("bullets") or []) if str(b).strip()]
        if bullets and any(bullets == earlier for earlier in seen_bullets):
            continue
        if bullets:
            seen_bullets.append(bullets)
        # metrics/chart with numbers not in the request fall back to bullets.
        metric_rows = [m for m in (row.get("metrics") or []) if isinstance(m, list) and m]
        if row.get("metrics") and not _numbers_come_from([str(v) for v, *_ in metric_rows], facts):
            row = {k: v for k, v in row.items() if k != "metrics"}
            row["layout"] = "bullets"
        elif row.get("metrics") and len(metric_rows) == 1:
            row = {**row, "layout": "big-number"}
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
        # Bullets with unrequested quantities are dropped when two or more remain.
        if request_text and isinstance(row.get("bullets"), list):
            sure_bullets = [
                b for b in row["bullets"] if not _unrequested_quantity(str(b), request_text)
            ]
            if 2 <= len(sure_bullets) < len(row["bullets"]):
                row = {**row, "bullets": sure_bullets}
        # A timeline with fewer than two requested moments becomes bullets.
        if isinstance(row.get("timeline"), list):
            steps = [t for t in row["timeline"] if isinstance(t, list) and t]
            sure = [t for t in steps if _moment_in_request(str(t[0]), request_text)]
            if len(sure) < 2:
                row = {k: v for k, v in row.items() if k != "timeline"}
                row["bullets"] = [f"{t[0]} – {t[1]}" if len(t) > 1 else str(t[0]) for t in steps]
                row["layout"] = "bullets"
            elif len(sure) < len(steps):
                row = {**row, "timeline": sure}
        # The draft may change a non-structural layout.
        wanted = str(row.get("layout") or "")
        if (
            wanted
            and slide["layout"] not in _STRUCTURAL
            and wanted in _LAYOUTS
            and wanted not in _STRUCTURAL
        ):
            slide["layout"] = wanted
        if slide["layout"] not in _STRUCTURAL and not any(row.get(k) for k in _DRAFT_CONTENT):
            # Emptied by validation: written again per slide, as bullets.
            if slide["layout"] in ("chart", "metrics"):
                slide["layout"] = "bullets"
            continue
        if slide["layout"] in _STRUCTURAL or any(row.get(k) for k in _DRAFT_CONTENT):
            out[index] = row
    return out


#: Per-slide prompt by layout; unknown layouts use `_BULLETS_PROMPT`.
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


#: Seconds between retries of a rate-limited call. Four rounds, about forty seconds in
#: all: long enough to outlast a burst of parallel document turns on one key.
_BACKOFF = (2.0, 6.0, 12.0, 20.0)


async def _complete(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int,
) -> tuple[str, dict]:
    """One non-streaming call. Returns `(text, usage)`. Retries a 429."""
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
                    # The proxy runs `drop_params`, so unsupported providers ignore this.
                    "reasoning": thinking.NO_REASONING,
                },
            )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            log.info("deck call rate limited, retrying in %ss", _BACKOFF[attempt])
            await asyncio.sleep(_BACKOFF[attempt])
        response.raise_for_status()
        payload = response.json()

    # A reasoning model may spend the whole budget thinking; see `services/thinking.py`.
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
                # A gateway that rejects `reasoning`: retry with the ceiling alone.
                again = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": messages, "max_tokens": bigger},
                )
        if again.status_code == 200:
            retried = again.json()
            spent = retried.get("usage") or {}
            first = payload.get("usage") or {}
            # Both calls are charged, so both are counted.
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
    """The first JSON object in the reply, or `{}`, with every string value read back into Hangul.

    Hangul read-back runs on parsed values, not the JSON text: a JSON array is
    ideographs inside brackets and would be protected as a gloss.
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
        return {key: _read_back_values(item) for key, item in value.items()}
    return value


def requested_slides(request: str) -> int | None:
    """Slide count stated in the request, clamped to bounds. `None` if unstated."""
    match = re.search(r"(\d{1,3})\s*(?:장|페이지|슬라이드|쪽)", request)
    if not match:
        return None
    asked = int(match.group(1))
    return max(_MIN_SLIDES, min(asked, _MAX_SLIDES)) if asked > 0 else None


def slides_for_minutes(request: str) -> int | None:
    """The fewest slides a talk of the stated length needs — about one every two minutes,
    never above the default ceiling. `None` when no length is stated.

    A 20-minute seminar planned as six slides leaves the speaker three minutes a slide;
    the floor keeps the outline honest about the room's time without dictating the count.
    """
    match = re.search(r"(\d{1,3})\s*분(?!기|류|석|산|리|야|할|량|배|담|위|과)", request)
    if not match:
        return None
    minutes = int(match.group(1))
    if minutes < 4:
        return None
    return max(_MIN_SLIDES, min(minutes // 2, _DEFAULT_MAX))


def _theme_style(text: str) -> str:
    """The `style` the outline chose, or `""`. Regex, so a salvaged outline keeps it."""
    match = re.search(r'"style"\s*:\s*"([^"]+)"', text)
    return _STYLES.get((match.group(1).strip() if match else ""), "")


def _theme_accent(text: str, default: str = _ACCENT) -> str:
    """Accent named by the outline, or `default`. Regex, so a salvaged outline keeps it."""
    match = re.search(r'"theme"\s*:\s*"([^"]+)"', text)
    return _THEMES.get((match.group(1).strip() if match else ""), default)


def _parse_outline(text: str) -> tuple[str, str, list[dict[str, str]]]:
    """`(title, subtitle, plan)` where each plan entry is `{title, layout}`.

    Unknown layouts become `bullets`; truncated JSON and plain lists are
    salvaged.
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
        # Truncated JSON: every object before the cut is intact.
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
            # The document title precedes the slide array.
            title = plan[0]["title"]

    if not plan:
        # Plain bullet or numbered lines.
        for line in text.splitlines():
            if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line):
                heading = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line).strip(" #").strip()
                if heading:
                    plan.append({"title": heading, "layout": "bullets"})

    plan = plan[:_MAX_SLIDES]
    if plan:
        # Export and preview both key off slide one being the cover.
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

#: Per paired layout: what it is for, its rules, and an example answer.
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

_PAIRED_RULES.update(
    {
        "steps": (
            "이 장은 절차의 단계를 차례대로 가로로 놓는 장이다. 번호는 자리가 매긴다.",
            "- steps 는 `[단계, 내용]` 의 목록이다. **3~5개.**\n"
            "- 단계는 그 단계의 이름 한 마디. **12자 이내** — 접수, 심사, 선정, 협약"
            " 처럼. 번호를 붙이지 마라.\n"
            "- 내용은 그 단계에서 무엇을 하는지 한 줄. **60자 이내.**\n"
            "- 순서대로 놓아라. 요청에 있는 절차만 — 없는 단계를 지어 칸을 채우지 마라.",
            '{{"steps": [["접수", "온라인으로 신청서와 계획서를 낸다"],'
            ' ["심사", "서류와 발표로 평가한다"], ["협약", "선정 뒤 2주 안에 협약을 맺는다"]],'
            ' "notes": "여기서는 ..."}}',
        ),
        "cards": (
            "이 장은 나란한 항목을 이름 붙은 상자로 놓는 장이다.",
            "- cards 는 `[이름, 내용]` 의 목록이다. **3~4개.**\n"
            "- 이름은 그 상자의 제목 한 마디. **14자 이내** — 교육, 연구, 산학, 국제화 처럼.\n"
            "- 내용은 한두 문장. **80자 이내.**\n"
            "- 항목은 서로 같은 급이어야 한다. 순서가 뜻을 가지면 steps, 이름표가"
            " 줄 앞에 서야 하면 bands 다.",
            '{{"cards": [["교육", "전교생 AI 기초 과목을 필수로 연다"],'
            ' ["연구", "학과별 AI 융합 연구 과제를 지원한다"],'
            ' ["산학", "지역 기업과 실습 프로젝트를 잇는다"]], "notes": "여기서는 ..."}}',
        ),
    }
)

_PROMPTS.update(
    {
        layout: _PAIRED_PROMPT.replace("{what}", what)
        .replace("{rules}", rules)
        .replace("{example}", example)
        for layout, (what, rules, example) in _PAIRED_RULES.items()
    }
)

_STATEMENT_PROMPT = """너는 아래 발표의 "{heading}" 슬라이드 한 장만 쓰고 있다.
이 장은 발표의 핵심 메시지 하나를 크게 세우는 장이다 — 남의 말이 아니라 발표자의 결론.

전체 구성:
{outline}

앞 장에서 이미 말한 내용:
{written}

규칙:
- title 은 크게 설 한 마디. **12자 이내** — 「전교생, AI 기초부터」 처럼.
- body 는 그 한 마디를 푸는 한 문장. **60자 이내.** 없는 사실을 넣지 마라.
- 겁을 주거나 재촉하는 말이 아니라, 발표가 말하려는 것.
- notes 는 발표자가 이 장에서 말할 내용. 2~3문장.

JSON 객체로만 답하라.
예: {{"title": "전교생, AI 기초부터", "body": "학과와 상관없이 같은 출발선에서 AI 를 쓰게 한다",
      "notes": "여기서는 ..."}}

원래 요청: {request}"""

_BIG_NUMBER_PROMPT = """너는 아래 발표의 "{heading}" 슬라이드 한 장만 쓰고 있다.
이 장은 숫자 하나를 아주 크게 띄우고 그 뜻을 한 줄로 붙이는 장이다.

전체 구성:
{outline}

앞 장에서 이미 말한 내용:
{written}

규칙:
- metrics 는 `[값, 이름]` **하나.** 값은 짧게 — `32%`, `1.4초`, `3억 원`.
- body 는 그 숫자가 무엇을 뜻하는지 한 문장. **60자 이내.**
- 지어낸 수치를 쓰지 마라. **근거가 없으면 이 장을 쓰지 말고 bullets 로 답하라.**
- notes 는 이 숫자가 어디서 온 값인지. 2~3문장.

JSON 객체로만 답하라.
예: {{"metrics": [["32%", "오탐 감소"]], "body": "새 규칙을 적용한 첫 달, 오탐 신고가 1/3 줄었다",
      "notes": "여기서는 ..."}}

원래 요청: {request}"""

_CLOSING_PROMPT = """너는 아래 발표의 "{heading}" 슬라이드 한 장만 쓰고 있다.
이 장은 마지막 장이다 — 기억할 것 두셋과 마무리 한 줄.

전체 구성:
{outline}

앞 장에서 이미 말한 내용:
{written}

규칙:
- bullets 는 이 발표에서 기억할 것. **2~3개**, 각 **30자 이내.** 앞 장에서 실제로 말한
  것만 — 새 주장을 여기서 꺼내지 마라.
- body 는 마무리 한 줄. **30자 이내** — 「질문을 환영합니다」, 「감사합니다」 처럼.
- notes 는 발표자가 마무리하며 할 말. 2~3문장.

JSON 객체로만 답하라.
예: {{"bullets": ["2027년부터 전교생 필수", "학과별 실습으로 이어진다"],
      "body": "질문을 환영합니다", "notes": "여기서는 ..."}}

원래 요청: {request}"""

_PROMPTS.update(
    {
        "quote": _QUOTE_PROMPT,
        "statement": _STATEMENT_PROMPT,
        "table": _TABLE_PROMPT,
        "metrics": _METRICS_PROMPT,
        "big-number": _BIG_NUMBER_PROMPT,
        "chart": _CHART_PROMPT,
        "closing": _CLOSING_PROMPT,
    }
)


def _agenda_lines(slides: list[dict]) -> list[str]:
    """Agenda lines: the dividers when there are two or more, else the body slides.

    At most eight.
    """
    names = [s["title"] for s in slides if s.get("layout") == "section"]
    if len(names) < 2:
        names = [s["title"] for s in slides if s.get("layout") not in (*_STRUCTURAL, "closing")]
    return [str(n).strip() for n in names if str(n).strip()][:8]


#: Keys that describe a slide rather than fill it.
_NOT_CONTENT = frozenset({"notes", "layout", "title", "heading", "id", "index", "n"})

#: The fields a drafted slide can carry its content in.
_DRAFT_CONTENT = (
    "bullets",
    "rows",
    "timeline",
    "bands",
    "tiles",
    "steps",
    "cards",
    "metrics",
    "chart",
    "body",
)


def _salvaged_bullets(data: dict) -> list[str]:
    """Bullets from any non-metadata string or list-of-strings field, whatever it was named."""
    found: list[str] = []
    for key, value in data.items():
        if key.lower() in _NOT_CONTENT:
            continue
        if isinstance(value, str):
            # A paragraph is split on sentence ends.
            found.extend(part for part in re.split(r"(?<=[.!?。])\s+|\n+", value) if part.strip())
        elif isinstance(value, list):
            found.extend(item for item in value if isinstance(item, str))
    return _clean_bullets(found)


def _clean_bullets(value: Any) -> list[str]:
    """Bullets as short single lines, however the model formatted them."""
    items = value if isinstance(value, list) else []
    out: list[str] = []
    for item in items:
        # An object or list item is joined into one line.
        if isinstance(item, dict):
            item = " – ".join(str(v).strip() for v in item.values() if str(v).strip())
        elif isinstance(item, list):
            item = " – ".join(str(v).strip() for v in item if str(v).strip())
        text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", str(item)).strip()
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("`", "").strip()
        # First line only.
        text = text.splitlines()[0].strip() if text else ""
        if text:
            out.append(text[:80])
    return out[:6]


#: Slide table bounds: what a 16:9 slide fits at a readable size.
_MAX_COLUMNS = 4
_MAX_ROWS = 6
_MAX_CELL = 24


_MAX_CATEGORIES = 8
_MAX_SERIES = 2


#: Layouts whose whole content is figures.
_NUMERIC_LAYOUTS = ("chart", "metrics", "big-number")


def _offered_layouts(request: str, context: list[str]) -> list[str]:
    """Body layouts the variety check may ask for; numeric ones only when figures exist."""
    body = [layout for layout in _BODY_LAYOUTS if layout not in _NUMERIC_LAYOUTS]
    return list(_BODY_LAYOUTS) if has_numbers(request, context) else body


#: A request about the person's own work, which cannot be written without material.
_OWN_WORK = re.compile(
    r"학위논문|연구계획|과제 신청|사업 신청|신청 발표|녹취|캡스톤|산학|과제 제안|제안 발표"
)

#: A citable figure: a decimal, three or more digits (not a year), or a number
#: with a measuring unit. `(?<!\d)` pins each match to the start of a digit
#: run; every quantifier is bounded because user text reaches this.
_FIGURE = re.compile(
    r"(?<!\d)(?:"
    r"\d{1,12}[.,]\d"
    r"|\d{3,12}(?!\d)(?!\s{0,4}년)"
    r"|\d{1,12}\s{0,4}(?:%|퍼센트|원|명|건|배|억|만|천|시간|분|초|주|개월|점|개|회|위)"
    r")"
)


def has_numbers(request: str, context: list[str]) -> bool:
    """Whether the request or any context block contains a citable figure."""
    return bool(_FIGURE.search(request)) or any(_FIGURE.search(block) for block in context)


def _grounded_layouts(
    plan: list[dict[str, str]], request: str, context: list[str]
) -> list[dict[str, str]]:
    """Numeric layouts demoted to `bullets` unless the request or context carries figures."""
    if has_numbers(request, context):
        return plan
    return [
        {**item, "layout": "bullets"} if item.get("layout") in _NUMERIC_LAYOUTS else item
        for item in plan
    ]


_MAX_QUOTES = 2


def _rationed_quotes(plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Quote slides beyond `_MAX_QUOTES` demoted to bullets."""
    out: list[dict[str, str]] = []
    spent = 0
    for item in plan:
        if item.get("layout") != "quote":
            out.append(item)
            continue
        spent += 1
        out.append(item if spent <= _MAX_QUOTES else {**item, "layout": "bullets"})
    return out


#: A divider title that is only a number: `01`, `2.`, `Part 3`, `섹션 1`.
#: Multi-letter Roman numerals only; a lone I/V/X may be a word.
_NUMBER_ONLY = re.compile(r"^\s*(?:part|섹션|section|장)?\s*(?:[0-9]+|[IVX]{2,})\s*[.)]?\s*$", re.I)


def _named_dividers(plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Dividers whose title is only a number are dropped; the renderer draws the number."""
    return [
        item
        for item in plan
        if item.get("layout") != "section" or not _NUMBER_ONLY.match(item.get("title") or "")
    ]


def _clean_chart(value: Any) -> dict[str, Any] | None:
    """A drawable chart, or `None`. Categories and series are cut to the shortest paired length."""
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


_MAX_METRICS = 4
_MAX_VALUE = 12
_MAX_LABEL = 16


#: A metric label naming a date part rather than a measurement. Anchored on
#: the whole word: `일자` is inside 일자리.
_CALENDAR = re.compile(
    r"^(년도|연도|마감|기한|일자|신청월|개강|종강|년|월|일|요일"
    r"|마감일|마감월|마감년도|신청일|시작일|종료일|개강일|종강일|접수일|발표일)$"
)


def _clean_metrics(value: Any) -> list[list[str]]:
    """`[[값, 이름]]` pairs; half-empty pairs and date-part labels are dropped."""
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


#: Paired layouts → (max pairs, left max chars, right max chars).
_PAIRED = {
    "bands": (4, 10, 90),
    "tiles": (6, 4, 24),
    "timeline": (7, 12, 60),
    "steps": (5, 12, 60),
    "cards": (4, 14, 80),
}


def _clean_pairs(value: Any, layout: str) -> list[list[str]]:
    """`[[왼쪽, 오른쪽]]` pairs within `_PAIRED` bounds; half-empty pairs are dropped."""
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
    """Table rows within bounds, ragged rows padded; empty when fewer than two rows."""
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
    """One slide picture as a `data:` URI (slides live in JSONB), or `None`. Never raises."""
    if not image_model:
        return None
    base, _ = await settings_store.litellm_config()
    try:
        made = await imagegen.generate(
            base_url=base,
            api_key=api_key,
            model=str(image_model.get("id") or ""),
            prompt=imagegen.compose_prompt(
                str(figure.get("prompt") or ""), aspect="16:9", style=""
            ),
            aspect="16:9",
        )
    except Exception as exc:  # noqa: BLE001 — a missing figure is not a failed deck
        log.warning("slide figure could not be drawn: %s", exc)
        return None
    return {
        # `encode` returns the whole `data:` address.
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
    """Writes slide bodies for an approved outline.

    One draft call, then per-slide calls for the gaps it left.
    """
    #: Approved pictures by slide index.
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
    # Announced up front so the panel can show the unwritten slides.
    for slide in slides:
        yield {"type": "slide", "slide": slide, "done": False}

    outline_text = "\n".join(f"{i + 1}. {s['title']}" for i, s in enumerate(slides))
    written: list[str] = []
    #: Dividers seen so far; a divider's number counts dividers, not slides.
    divider = 0

    # The whole deck in one call keeps the thread; the per-slide pass below
    # writes whatever the draft skipped.
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
        # Numbers in the request and its attachments are the ones a slide may use.
        given = "\n".join([request, *(untrusted_context or [])])
        drafted = _split_deck_draft(draft_text, slides, _facts_set(given), given)
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
        # Position goes in `progress`, not in the label.
        label = str(slide["title"])
        progress = {"current": index + 1, "total": len(slides)}

        if slide["layout"] in _STRUCTURAL:
            # Filled from the outline; no model call.
            if slide["layout"] == "section":
                divider += 1
                slide["number"] = f"{divider:02d}."
                slide["body"] = ""
            elif slide["layout"] == "agenda":
                slide["bullets"] = _agenda_lines(slides)
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
            yield {"type": "slide", "slide": auto_fit(slide), "done": True}
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
                            written="\n".join(written)[-3000:] or "(아직 없음)",
                            # Prompts without `{count}` ignore the extra field.
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
            # Reset to bullets so renderers do not draw an empty layout.
            if slide.get("layout") not in _STRUCTURAL:
                slide["layout"] = "bullets"
            slide["body"] = UNWRITTEN
            yield {"type": "slide", "slide": slide, "done": True}
            continue

        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]

        # Drawn after the text, so a failed drawing still leaves a written slide.
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
        # Same number check as the draft path.
        if index not in drafted:
            facts = _facts_set("\n".join([request, *(untrusted_context or [])]))
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
        if isinstance(raw_notes, list):
            raw_notes = " ".join(str(n).strip() for n in raw_notes if str(n).strip())
        notes = str(raw_notes or "").strip()

        if slide["layout"] in ("quote", "statement"):
            line = str(data.get("body") or "").strip().strip('"“”')
            if slide["layout"] == "statement" and (word := str(data.get("title") or "").strip()):
                slide["title"] = word[:24]
            if not line:
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
        elif slide["layout"] in ("metrics", "big-number"):
            if metrics := _clean_metrics(data.get("metrics")):
                slide["metrics"] = metrics[:1] if slide["layout"] == "big-number" else metrics
                if slide["layout"] == "big-number":
                    slide["body"] = " ".join(str(data.get("body") or "").split())[:90]
            else:
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
        elif slide["layout"] == "closing":
            slide["bullets"] = _clean_bullets(data.get("bullets"))[:3]
            slide["body"] = " ".join(str(data.get("body") or "").split())[:60]
            if not slide["bullets"] and not slide["body"]:
                slide["body"] = "감사합니다"
        elif slide["layout"] in _PAIRED:
            if pairs := _clean_pairs(data.get(slide["layout"]), slide["layout"]):
                slide[slide["layout"]] = pairs
            else:
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
        elif slide["layout"] == "table":
            if rows := _clean_rows(data.get("rows")):
                slide["rows"] = rows
            else:
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
        else:
            slide["bullets"] = _clean_bullets(data.get("bullets"))

        if not has_content(slide):
            slide["bullets"] = _salvaged_bullets(data)
        if not has_content(slide):
            # Marked as unwritten (shown on screen, left out of exports) and
            # reset to bullets so renderers do not draw an empty layout.
            if slide.get("layout") not in _STRUCTURAL:
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
        yield {"type": "slide", "slide": auto_fit(slide), "done": True}

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
    #: Model for the outline call; empty means the writing model.
    outline_model: str = "",
    #: An approved outline. Absent: plan and emit `proposal` (or `needs`),
    #: writing nothing. Present: write exactly this, with no planning call.
    approved_plan: dict[str, Any] | None = None,
    #: False on the pass after "있는 자료로 진행", so the planner cannot re-ask.
    may_ask: bool = True,
    #: Approved pictures to draw; `None` on the planning pass, `[]` for 그림 없이.
    figures_plan: list[dict] | None = None,
    #: The image model that draws them.
    image_model: dict | None = None,
    #: Research before the outline, as reports do.
    web_search: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `title`, `slide`, a final `deck` and one `usage` event.

    Two passes: the planning pass emits a `proposal` and writes nothing; the
    approved pass writes. The caller owns persistence, billing and the
    artifact. `tokens` is the project's design system; its accent overrides
    the model's colour choice.
    """
    # Outline usage is counted apart because it may run on another model.
    usage = {
        "inputTokens": 0,
        "outputTokens": 0,
        "outlineInputTokens": 0,
        "outlineOutputTokens": 0,
    }
    wanted = requested_slides(request)
    fixed_accent = (tokens or {}).get("accent") or ""
    # The look this request would get on its own; the outline is shown it and may override.
    suggested_theme, suggested_style = suggest_look(request)
    suggested_accent = _THEMES[suggested_theme]

    # Both passes research: the approved outline names the slides, not their facts.
    findings = research.Findings()
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
    # Search off needs no rule; unavailable and empty are told apart.
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
        # Re-checked: an approved plan may have been edited.
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
                    lo=wanted or slides_for_minutes(request) or _MIN_SLIDES,
                    hi=wanted or _DEFAULT_MAX,
                    theme_rule=(
                        ""
                        if fixed_accent
                        else _THEME_RULE.format(
                            themes=" / ".join(_THEMES), theme=suggested_theme, style=suggested_style
                        )
                    ),
                    theme_example=(
                        ""
                        if fixed_accent
                        else f'"theme": "{suggested_theme}",\n  "style": "{suggested_style}",\n  '
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
            # Scaled with the slide count so the JSON is not truncated.
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
    # The outline call may answer with questions; see `grounding.ASK_RULE`.
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
        # No subject to write about: ask, as the report does.
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
    accent = fixed_accent or _theme_accent(text, suggested_accent)
    # Grounded before the variety check, so it does not ask for layouts that
    # would then be stripped.
    plan = _named_dividers(_rationed_quotes(_grounded_layouts(plan, request, document_context)))
    offered = _offered_layouts(request, document_context)

    # A flat outline gets one retry naming the layouts it skipped.
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
                accent = fixed_accent or _theme_accent(retry_text, suggested_accent) or accent
            else:
                log.info("deck outline still flat, keeping the first")
    # A talk with a stated length or count planned too short gets one retry, no shorter.
    needed = wanted or slides_for_minutes(request)
    if plan and needed and len(plan) < needed:
        log.info("deck outline short: %d of %d slides, asking once more", len(plan), needed)
        try:
            retry_text, retry_spent = await ask(
                f"\n\n앞선 구성은 {len(plan)}장이었다. 이 발표에는 최소 {needed}장이 필요하다. "
                "다시 짜라 — 있는 장을 둘로 쪼개지 말고, 요청이 말한 흐름에서 아직 장이 없는 "
                "대목(사례, 비교, 남은 문제, 정리)에 장을 주어라."
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("deck outline retry failed: %s", exc)
        else:
            plan_rules.count(usage, retry_spent, planned_apart=bool(outline_model))
            retry_title, retry_subtitle, retry_plan = _parse_outline(retry_text)
            retry_plan = _named_dividers(
                _rationed_quotes(_grounded_layouts(retry_plan, request, document_context))
            )
            if len(retry_plan) > len(plan):
                title = retry_title or title
                subtitle = retry_subtitle or subtitle
                plan = retry_plan
                accent = fixed_accent or _theme_accent(retry_text, suggested_accent) or accent
            else:
                log.info("deck outline still short, keeping the first")
    # An unreadable outline gets one retry.
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
                accent = fixed_accent or _theme_accent(retry_text, suggested_accent) or accent
    # Whatever the model settled on, three bullet lists in a row become two and a shape.
    plan = vary_layouts(plan)

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
    # The planning pass stops here; the caller stores the proposal and calls
    # back with it approved.
    plan = _named_dividers(_rationed_quotes(_grounded_layouts(plan, request, document_context)))
    proposal: dict[str, Any] = {
        "title": title[:200],
        "subtitle": subtitle[:200],
        "accent": accent,
        # A style the request names wins; otherwise the outline's choice.
        # A style the request names wins; then the outline's choice; then the room's.
        "visualStyle": (
            design.visual_style_for(request)
            if design.visual_style_for(request) != "editorial"
            else (_theme_style(text) or _STYLES[suggested_style])
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
    # Pictures are proposed with the outline and approved on a second card.
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
        # The writing pass writes a form; see `_FRAME_RULE`.
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
    material: list[str] | None = None,
    typed: str = "",
) -> tuple[dict, dict]:
    """Rewrites one slide with the rest of the deck as context. Returns `(slide, usage)`; same shape
    as the report's. `material` is the request's own data, carried again so the numbers come
    from where the original did; `typed` is the instruction as the person wrote it, read for a
    layout change the planner's paraphrase may have dropped.
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
    # 「표로 바꿔 줘」 changes the layout, not just the words: write it with the table prompt.
    if re.search(r"표(로|를|가| 하나)|\btable\b", f"{note} {typed}", re.I):
        layout = "table"
    template = _PROMPTS.get(layout) or (_TABLE_PROMPT if layout == "table" else _BULLETS_PROMPT)
    prompt = template.format(
        heading=target.get("title") or "",
        outline=outline,
        written=written[-3000:] or "(아직 없음)",
        count="6~8" if layout == "two-column" else "3~5",
        request=request[:1500],
    )
    if note.strip():
        # Labelled, or it reads as part of the request.
        prompt += f"\n\n이번에 다시 쓰는 이유(반드시 반영):\n{note.strip()[:600]}"

    text, usage = await _complete(
        model,
        build_document_messages(
            SessionKind.slides, prompt, request=request, untrusted_context=material
        ),
        api_key,
        600,
    )
    parsed = _json_object(text)
    rows = _clean_rows(parsed.get("rows"))
    pairs = _clean_pairs(parsed.get(layout), layout) if layout in _PAIRED else []
    bullets = _clean_bullets(parsed.get("bullets"))
    body = str(parsed.get("body") or "").strip()
    notes = str(parsed.get("notes") or "").strip()
    # Whatever the layout's prompt asked for counts as content; a notes-only answer to a
    # notes-only instruction keeps the slide and changes the notes.
    if not (rows or pairs or bullets or body or notes):
        raise ValueError("빈 슬라이드")

    # Merged, so the slide's id, accent and picture survive.
    result = {**target}
    if rows:
        result["rows"] = rows
        result["layout"] = "table"
        for field in ("bullets", "body", *_PAIRED):
            result.pop(field, None)
        bullets, body = [], ""
    elif pairs:
        result[layout] = pairs
        result.pop("bullets", None)
        result.pop("body", None)
        bullets, body = [], ""
    if bullets:
        result["bullets"] = bullets
        # The old body (possibly UNWRITTEN) must not survive beside the rewrite.
        result.pop("body", None)
    if body:
        result["body"] = body
        result.pop("bullets", None)
    if notes:
        result["notes"] = notes
    # The verdicts belonged to the old text.
    result.pop("factCheck", None)
    result.pop("textScale", None)
    return auto_fit(result), usage


#: Every field a slide's content can arrive in. A layout that stores content
#: under its own name must be here; the paired ones come from `_PAIRED`.
_CONTENT_FIELDS = ("bullets", "body", "rows", "metrics", "chart", *_PAIRED)


def has_content(slide: dict) -> bool:
    """Whether the slide carries content; structural slides count on their title alone."""
    if slide.get("layout") in _STRUCTURAL:
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


#: The type scale is shared with the panel and the exporters (`deck_type`); the fit below
#: reasons in its slide units, so what it decides holds in all three.
_FIT_FLOOR = 0.65
_FIT_CEILING = 1.25
#: Layouts whose body may grow when the slide is sparse. Paired shapes and tables size
#: themselves from their count; growing their text would overflow their boxes.
_GROWABLE = ("bullets", "two-column")

#: Slide layouts that must vary: a run of these gets every other one re-planned.
_MONOTONE = "bullets"


def _body_width(slide: dict) -> float:
    """The text column's width in slide units, narrowed by a picture beside it."""
    width = float(deck_type.TITLE_WIDTH)
    image = slide.get("image") or {}
    if image.get("src") and slide.get("layout") != "title":
        share = {"small": 0.32, "medium": 0.42, "large": 0.54}.get(
            str(image.get("size") or ""), 0.42
        )
        width = width * (1 - share) - 16
    return width


def _need(slide: dict, scale: float) -> float:
    """Slide units of height the body asks for at `scale`, plus the title's extra lines.

    Mirrors `SlideView`: bullets wrap in the text column at the body size, the agenda
    lays two ruled columns above four entries, cards and steps keep fixed boxes.
    """
    T, L = deck_type.TYPE, deck_type.LEADING
    layout = str(slide.get("layout") or "bullets")
    width = _body_width(slide)
    title_size = T["title"] * min(1.0, scale)
    title_lines = deck_type.lines(str(slide.get("title") or ""), title_size, deck_type.TITLE_WIDTH)
    need = max(0, title_lines - 1) * title_size * L["title"]
    bullets = [str(b) for b in (slide.get("bullets") or []) if str(b).strip()]
    if layout == "agenda":
        size = T["agenda"] * scale
        columns = 2 if len(bullets) > 4 else 1
        column_width = (width - 20 * (columns - 1)) / columns - 22
        rows = [
            deck_type.lines(b, size, column_width) * size * L["agenda"] + 10 * scale + 1
            for b in bullets
        ]
        per_column = -(-len(rows) // columns)
        need += max(sum(rows[:per_column]), sum(rows[per_column:]))
    elif layout == "two-column" and len(bullets) >= 5:
        size = T["bodyNarrow"] * scale
        column_width = (width - 20) / 2 - 12
        half = -(-len(bullets) // 2)
        cost = [deck_type.lines(b, size, column_width) * size * L["body"] for b in bullets]
        gap = size * deck_type.BULLET_GAP
        need += max(
            sum(cost[:half]) + gap * (half - 1), sum(cost[half:]) + gap * (len(bullets) - half - 1)
        )
    elif bullets:
        size = T["body"] * scale
        need += sum(deck_type.lines(b, size, width - 12) * size * L["body"] for b in bullets)
        need += size * deck_type.BULLET_GAP * (len(bullets) - 1)
    if slide.get("body") and not bullets:
        size = T["paragraph"] * scale
        need += deck_type.lines(str(slide["body"]), size, width) * size * L["paragraph"] + 2
    for key, fixed in (
        ("cards", 104.0),
        ("steps", 0.0),
        ("tiles", 0.0),
        ("bands", 0.0),
        ("timeline", 0.0),
    ):
        pairs = [p for p in (slide.get(key) or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
        if not pairs:
            continue
        if key == "steps":
            span = (width - 8 * (len(pairs) - 1)) / len(pairs)
            text = max(deck_type.lines(str(p[1]), T["stepText"] * scale, span) for p in pairs)
            need += (
                8
                + 22
                + 7
                + T["stepName"] * scale * 1.3
                + 3
                + text * T["stepText"] * scale * L["stepText"]
            )
        elif key == "tiles":
            need += 8 + 62 + 7 + T["tileName"] * scale * 1.5 * 2
        else:
            # Bands and timelines divide the body height among themselves.
            need += fixed
    rows = slide.get("rows") or []
    if rows:
        # The table sizes itself from its row count; a wrapped long cell takes the reserve.
        need += deck_type.table_row_height(len(rows)) * len(rows)
    metrics = slide.get("metrics") or []
    if metrics and layout != "big-number":
        need += 6 + 14 + T["metric"] * scale * 1.1 + 5 + T["metricLabel"] * scale * 1.5 + 16
    return need


def auto_fit(slide: dict) -> dict:
    """Sets `textScale` so the slide's body fills its box without crossing the footer.

    The panel and both exporters read the same field and the same type scale, so a deck
    looks the same in the panel, the `.pptx` and the `.pdf`. A sparse list grows in steps
    of 0.05 up to 1.25 (the title stays put); a crowded slide shrinks down to 0.65, below
    which splitting the slide is the answer. A scale a person set by hand is left alone.
    """
    if (
        slide.get("textScale") not in (None, 1, 1.0)
        or slide.get("layout") in _STRUCTURAL
        and slide.get("layout") != "agenda"
    ):
        return slide
    room = deck_type.BODY_BOTTOM - deck_type.BODY_TOP
    layout = str(slide.get("layout") or "bullets")
    ceiling = _FIT_CEILING if layout in _GROWABLE and slide.get("bullets") else 1.0
    steps = int(round((ceiling - _FIT_FLOOR) / 0.05))
    for step in range(steps + 1):
        scale = round(ceiling - step * 0.05, 2)
        if _need(slide, scale) <= room:
            break
    else:
        scale = _FIT_FLOOR
    if scale == 1.0:
        slide.pop("textScale", None)
    else:
        slide["textScale"] = scale
    return slide


def vary_layouts(plan: list[dict]) -> list[dict]:
    """Breaks runs of three or more bullet slides: every other one becomes bands or cards.

    The outline model reaches for `bullets` by habit; a deck of nine bullet lists reads
    as one slide repeated. Bands and cards carry the same content as a labelled list,
    and the writer falls back to bullets when a slide has nothing to pair.
    """
    run: list[int] = []

    def close() -> None:
        if len(run) >= 3:
            for position, index in enumerate(run):
                if position % 2 == 1:
                    plan[index]["layout"] = "bands" if position % 4 == 1 else "cards"
        run.clear()

    for index, item in enumerate(plan):
        if item.get("layout") == _MONOTONE:
            run.append(index)
        else:
            close()
    close()
    return plan


def to_markdown(title: str, slides: list[dict]) -> str:
    """The deck as Markdown."""
    parts = [f"# {title}", ""]
    for index, slide in enumerate(slides):
        if slide.get("layout") == "title" and index == 0:
            continue
        parts.append(f"## {slide.get('title') or ''}")
        if slide.get("body"):
            parts.append(f"\n> {slide['body']}")
        for bullet in slide.get("bullets") or []:
            parts.append(f"- {bullet}")
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


__all__ = ["filled", "has_content", "to_markdown", "write"]
