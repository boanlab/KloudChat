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

from app.core import logs
from app.core.config import settings
from app.models.chat import SessionKind
from app.services import (
    deck_type,
    design,
    diagrams,
    figures,
    grounding,
    hangul,
    imagegen,
    pictures,
    ratelimit,
    research,
    revise,
    settings_store,
    thinking,
)
from app.services import outline as plan_rules
from app.services.context import build_document_messages

log = logging.getLogger(__name__)

#: Default minimum; explicit short requests keep their count. All outlines share the ceiling.
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
    "pastel": "파스텔",
    "forest": "숲",
    "slate": "강철",
    "paper": "학술",
}

#: Topic words → accent name. Checked in order; the first topic named wins. A deck
#: about nothing on this list takes a colour keyed off its request, so two decks on
#: different subjects do not come out the same colour.
#: Topic words → the colours that suit the subject. Several per topic, so forty decks
#: about systems do not all wear the same blue: one is picked by a digest of the request,
#: the same request always getting the same one.
_TOPIC_THEMES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("남색", "먹", "청록"), ("보안", "금융", "법", "정책", "경영진", "이사회", "투자", "은행")),
    (
        ("파랑", "청록", "남색", "자주"),
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
    (("초록", "청록"), ("환경", "에너지", "농업", "생태", "탄소", "지속가능", "친환경")),
    (("청록", "초록", "파랑"), ("의료", "건강", "병원", "바이오", "제약", "간호")),
    (
        ("주황", "초록", "자주"),
        ("교육", "수업", "강의", "학생", "청소년", "학습", "온보딩", "입사"),
    ),
    (("자주", "빨강", "주황"), ("문화", "예술", "디자인", "패션", "공연", "미디어")),
    (("빨강", "주황", "자주"), ("홍보", "행사", "축제", "캠페인", "모집", "마케팅", "소개")),
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
    style = design.visual_style_for(request)
    if style == "editorial":
        style = design.venue_style_for(request) or "editorial"
    return topic_theme(request), _STYLE_LABELS[style]


def topic_theme(request: str) -> str:
    """The colour name for this request: one of its subject's colours, picked by a digest
    of the words so the same request keeps its colour and the next one may differ; a
    request naming no subject draws from the rotation."""
    text = (request or "").lower()
    digest = sum(ord(ch) for ch in re.sub(r"\s+", "", text)[:200])
    for names, words in _TOPIC_THEMES:
        if any(w in text for w in words):
            return names[digest % len(names)]
    return _ROTATION[digest % len(_ROTATION)]


def topic_accent(request: str) -> str:
    """The hex accent for `topic_theme`."""
    return _THEMES.get(topic_theme(request), _THEMES["파랑"])


#: Prompt label → stored `visualStyle` value.
_STYLES = {
    "편집형": "editorial",
    "포스터형": "poster",
    "미니멀": "minimal",
    "다크": "dark",
    "분할형": "split",
    "따뜻한": "warm",
    "흑백": "mono",
    "파스텔": "pastel",
    "숲": "forest",
    "강철": "slate",
    "학술": "paper",
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
- **자료의 이름은 원문 그대로.** 첨부 데이터의 항목 이름·열 머리글·식별자(nginx, redis 같은
  워크로드명, 제품명, 코드명)는 번역하거나 다른 말로 바꿔 부르지 않는다. 「웹 서버」가
  아니라 「nginx」다.
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
    line = (
        "쓸 수 있는 수치(요청에 있는 것 전부): "
        + ", ".join(found[:30])
        + " — 사용자가 적어 준 사실이므로 각각 어느 장에든 한 번은 나와야 한다. 빠뜨리지 마라."
    )
    # Arithmetic the request implies, done once here (see `report.derived_values`).
    from app.services.report import derived_values

    if derived := derived_values(request):
        line += (
            "\n계산된 값(식 그대로 옮겨 적고 다시 셈하지 마라; 그 값이 필요한 한 장에서만 쓴다): "
            + "; ".join(derived)
        )
    return line


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


_KOREAN_AMOUNT = re.compile(
    r"(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(억|만|천)(?:\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(만|천))?"
)
_UNIT_VALUE = {"억": 100_000_000, "만": 10_000, "천": 1_000}


def _expanded_amounts(request: str) -> set[str]:
    """「6.1만」→ 61000, 「4만 2천」→ 42000, 「1200만」→ 12000000: the plain digits a chart
    or metric writes for an amount the request spelled with Korean units."""
    out: set[str] = set()
    for match in _KOREAN_AMOUNT.finditer(request):
        try:
            total = float(match.group(1).replace(",", "")) * _UNIT_VALUE[match.group(2)]
            if match.group(3) and match.group(4):
                total += float(match.group(3).replace(",", "")) * _UNIT_VALUE[match.group(4)]
        except (TypeError, ValueError):
            continue
        text = f"{total:.2f}".rstrip("0").rstrip(".")
        out.add(text)
        if "." not in text:
            out.add(text + ".0")
    return out


def _facts_set(request: str) -> set[str]:
    """Every digit run in the request, so a number on a slide can be checked."""
    digits = {re.sub(r"[^\d.]", "", m) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", request)}
    return digits | _expanded_amounts(request)


#: Words that make a clause an instruction rather than a fact to keep.
_ASK_WORDS = re.compile(
    r"(?:줘|주세요|해라|하라|만들|써\s|쓰고|바꿔|줄여|줄이|늘려|넣어|빼|합쳐|합치|병합|나눠|맞춰|맞춘)"
)
#: A count that is about the document's shape (「6장」, 「세 절」) or a point in time, not
#: a fact of the subject.
_WHEN_UNIT = re.compile(
    r"(?:분|초|시간|개월|주|일|년|월|회|장|절|슬라이드|페이지|쪽|문장|개의?\s*장)\s*$"
)
_CLAUSE_BREAK = re.compile(r"[,，、.。!?;:()（）\[\]「」·]|\s(?:그리고|또한|및)\s")


def _slide_words(slide: dict) -> str:
    """Every word a slide shows or says, for checking what the deck already carries."""
    parts: list[str] = [str(slide.get("title") or ""), str(slide.get("subtitle") or "")]
    parts.append(_drafted_text(slide))
    for row in slide.get("rows") or []:
        if isinstance(row, list):
            parts.append(" ".join(str(cell) for cell in row))
    for metric in slide.get("metrics") or []:
        if isinstance(metric, list):
            parts.append(" ".join(str(cell) for cell in metric))
    chart = slide.get("chart")
    if isinstance(chart, dict):
        for series in chart.get("series") or []:
            if isinstance(series, dict):
                parts.append(" ".join(str(v) for v in series.get("values") or []))
    notes = slide.get("notes")
    parts.append(" ".join(str(n) for n in notes) if isinstance(notes, list) else str(notes or ""))
    return "\n".join(parts)


def _clause_around(text: str, start: int, end: int) -> str:
    """The comma-to-comma clause holding `text[start:end]`, trimmed to a bullet's length."""
    left = max((m.end() for m in _CLAUSE_BREAK.finditer(text, 0, start)), default=0)
    right_match = _CLAUSE_BREAK.search(text, end)
    right = right_match.start() if right_match else len(text)
    clause = text[left:right].strip(" -–—:")
    if len(clause) > 48:
        clause = text[start:right].strip(" -–—:")
    return clause.strip()


def required_totals(request: str) -> list[tuple[str, str]]:
    """`(line, result digits)` for each finished sum the request explicitly asked for — a
    36-month TCO, a yearly total against a yearly budget — so a deck that forgot the
    table it was asked for still carries the figure."""
    from app.services.report import derived_values

    asked_months = {int(m) for m in re.findall(r"(\d{1,3})\s*개월", request)}
    years = re.findall(r"(\d)\s*년\s*(?:TCO|총|치|간|동안|기준)", request)
    asked_months |= {int(m) * 12 for m in years}
    if re.search(r"연\s*(?:간\s*)?예산", request):
        asked_months.add(12)
    out: list[tuple[str, str]] = []
    for line in derived_values(request):
        match = re.search(r"× (\d{1,4})개월 = (.{1,200})$", line)
        if match and int(match.group(1)) in asked_months:
            digits = re.sub(r"[^\d]", "", match.group(2))
            out.append((line, digits))
    return out


def listed_items(request: str) -> list[tuple[str, list[str]]]:
    """`(part name, its listed sub-items)` for each enumerated part whose description is a
    comma-separated list of short items (「(2) 필수 계정 — 메일, 메신저, 위키, 문서검색」);
    `[]` when the request lists parts no such way."""
    if not enumerated_items(request):
        return []
    # Compiled here: `_ENUM_MARK` is defined further down the module.
    detail = re.compile(
        _ENUM_MARK + r"\s*(?P<name>[^—:\-–()\n]{2,30}?)\s*[—:\-–]\s*(?P<detail>[^()\n]+?)\s*(?="
        + _ENUM_MARK + r"|[.。]\s|$)"
    )
    out: list[tuple[str, list[str]]] = []
    for match in detail.finditer(request):
        pieces = [p.strip(" .") for p in re.split(r"[,，、]", match.group("detail"))]
        pieces = [p for p in pieces if p]
        if len(pieces) >= 2 and all(len(p) <= 14 for p in pieces):
            out.append((" ".join(match.group("name").split()), pieces))
    return out


def keep_listed_items(slides: list[dict], request: str) -> list[int]:
    """Every sub-item the request spelled out for a part appears, in the user's words, on
    the slide that covers that part; a missing one is added. Returns the slides changed.

    「메일, 메신저, 위키, 문서검색」 asked for and 「사내 메신저와 협업 도구」 written is
    a paraphrase the person did not ask for; the list is theirs."""
    changed: list[int] = []
    squeeze = lambda t: re.sub(r"\s+", "", t).lower()  # noqa: E731
    for name, pieces in listed_items(request):
        index = next(
            (i for i, s in enumerate(slides) if _covers(str(s.get("title") or ""), name)), None
        )
        if index is None:
            continue
        slide = slides[index]
        # What the slide shows, not what the notes say: an item spilled into the notes is
        # an item the audience does not see.
        shown = squeeze(_slide_words({**slide, "notes": ""}))
        # Missing only when none of the item's words appear: the device catches an item
        # that vanished (「목 장비 설정」 with neither word on the slide), not one the
        # writer phrased its own way (「USB: 개인 USB 꽂지 않기」 for 「USB 금지」).
        def present(piece: str, shown: str = shown) -> bool:
            tokens = [w for w in piece.split() if len(w) >= 2 or not re.search(r"[가-힣]", w)]
            return any(squeeze(w) in shown for w in tokens) if tokens else True

        missing = [p for p in pieces if not present(p)]
        if not missing:
            continue
        for key in ("steps", "cards", "bands", "tiles", "timeline"):
            rows = slide.get(key)
            if isinstance(rows, list) and rows and all(isinstance(r, list) for r in rows):
                width = len(rows[0])
                for p in missing:
                    rows.append([p] + [""] * (width - 1))
                break
        else:
            bullets = slide.get("bullets") if isinstance(slide.get("bullets"), list) else []
            slide["bullets"] = bullets + missing
            if slide.get("layout") in _STRUCTURAL:
                slide["layout"] = "bullets"
        changed.append(index)
    return changed


#: A farewell where a closing slide's title belongs (「안녕히 가십시오」, 「수고하셨습니다」):
#: the slide's heading names what it is, the farewell goes in its one-line body.
_FAREWELL_TITLE = re.compile(
    r"^\s*(안녕히\s*(?:가십시오|가세요|계세요|계십시오)|수고\s*하셨습니다|수고\s*많으셨습니다|"
    r"끝(?:입니다)?|이상입니다|the\s+end|good\s*bye)\s*[.!]*\s*$",
    re.I,
)


def closing_title(title: str) -> str:
    """A closing slide's title, with a bare farewell replaced by 「마무리」."""
    return "마무리" if _FAREWELL_TITLE.match(title or "") or not title.strip() else title


#: Where a restructure's request ends and the planner's own conditions begin.
_ADDED_CONDITIONS = "\n\n덧붙인 조건:\n"


#: 「비어 있는 값은 「측정 안 함」으로」 — what the person wants in a cell the data left empty.
_EMPTY_CELL_WORD = re.compile(
    r"(?:비어\s{0,8}있는|빈|없는|누락된?|결측)\s{0,8}(?:값|칸|셀|항목|데이터)?[은는이가]?\s{0,8}"
    r"[「\"\u201c']?([^」\"\u201d'\n]{1,12}?)[」\"\u201d']?\s{0,8}(?:으로|로)"
)


def _csv_tables(material: list[str]) -> list[list[list[str]]]:
    """Tab- or comma-separated tables in the material (an attached CSV arrives as tab-joined
    rows): header first, three rows or more, one width."""
    out = []
    for block in material:
        rows: list[list[str]] = []
        for line in str(block or "").splitlines():
            cells = [c.strip() for c in (line.split("\t") if "\t" in line else line.split(","))]
            if len(cells) >= 2 and (not rows or len(cells) == len(rows[0])):
                rows.append(cells)
            elif len(rows) >= 3:
                out.append(rows)
                rows = []
            else:
                rows = []
        if len(rows) >= 3:
            out.append(rows)
    return out


def fill_csv_categories(slides: list[dict], material: list[str], request: str) -> list[int]:
    """A table built from an attached CSV shows every category the CSV has.

    The writer, told 「CSV에 없는 값은 만들지 말고 비어 있는 값은 「측정 안 함」으로」, tends
    to drop the row whose cells are empty instead. When a table's first column names two or
    more categories of a CSV, the categories it skipped come back as rows: a cell takes the
    CSV's value where the table's column was learned from the rows already there, and the
    person's word for an empty value otherwise. Returns the indices of slides changed."""
    tables = _csv_tables(material)
    if not tables:
        return []
    placeholder_match = _EMPTY_CELL_WORD.search(request or "")
    placeholder = placeholder_match.group(1).strip() if placeholder_match else "—"
    changed: list[int] = []
    for index, slide in enumerate(slides):
        rows = slide.get("rows")
        if slide.get("layout") != "table" or not isinstance(rows, list) or len(rows) < 2:
            continue
        shown = {str(r[0]).strip().lower(): r for r in rows[1:] if r}
        for csv_rows in tables:
            header, body = csv_rows[0], csv_rows[1:]
            categories = {r[0].strip().lower(): r for r in body if r and r[0].strip()}
            matched = [c for c in shown if c in categories]
            if len(matched) < 2 or len(matched) < len(shown):
                continue
            # Which CSV column each table column shows, learned from the matched rows.
            width = len(rows[0])
            mapping: dict[int, int] = {}
            for col in range(1, width):
                for csv_col in range(1, len(header)):
                    if all(
                        str(shown[c][col]).strip().replace(",", "")
                        == categories[c][csv_col].strip().replace(",", "")
                        for c in matched
                        if col < len(shown[c]) and csv_col < len(categories[c])
                    ):
                        mapping[col] = csv_col
                        break
            missing = [c for c in categories if c not in shown]
            if not missing or len(rows) - 1 + len(missing) > _MAX_ROWS:
                continue
            for key in missing:
                source = categories[key]
                row = [source[0].strip()]
                for col in range(1, width):
                    known = col in mapping and mapping[col] < len(source)
                    value = source[mapping[col]].strip() if known else ""
                    row.append(value or placeholder)
                rows.append(row)
            # In the CSV's order.
            order = {k: i for i, k in enumerate(categories)}
            rows[1:] = sorted(
                rows[1:], key=lambda r: order.get(str(r[0]).strip().lower(), len(order))
            )
            changed.append(index)
            break
    return changed


def restate_missing_facts(slides: list[dict], request: str) -> list[int]:
    """Puts back, as a line on the most related slide, each quantity the request states
    that no slide carries; returns the indices of the slides changed.

    「파일럿 742명」 in the brief and nowhere in six slides is the kind of omission a
    reader notices first. The clause around the number is the line; it goes to the body
    slide sharing the most words with it, and the deck's own numbers are never touched.
    Instructions (「6장으로 줄여 줘」) are not facts and are left out."""
    # The planner's appended conditions (「장수는 6장으로 맞춘다」) are about the deck,
    # not facts of the subject: nothing in them is restated.
    text = " ".join((request or "").split(_ADDED_CONDITIONS)[0].split())
    # Dates and durations describe the occasion (「2026년 … 3분 발표」), not the subject.
    quantities = [m for m in _QUANTITY.finditer(text) if not _WHEN_UNIT.search(m.group(0))]
    if len(quantities) < 2:
        return []
    carried = _facts_set("\n".join(_slide_words(s) for s in slides))
    bodies = [
        i for i, s in enumerate(slides)
        if s.get("layout") not in (*_STRUCTURAL, "closing", "statement", "quote")
        and s.get("body") != UNWRITTEN
    ]
    if not bodies:
        return []
    changed: list[int] = []
    # Totals the request asked for outright (a 36-month TCO) go onto the cost slide when
    # the deck left them out, as the finished line of arithmetic.
    deck_text = "\n".join(_slide_words(s) for s in slides)
    deck_digits = {re.sub(r"[^\d]", "", w) for w in re.findall(r"\d[\d,]*", deck_text)}
    for line, digits in required_totals(request):
        result = line.split(" = ")[-1].strip()
        if result in deck_text or digits in deck_digits:
            continue
        cost_words = {"비용", "TCO", "지출", "예산", "원", "총액"}
        scored = sorted(
            bodies,
            key=lambda i: (
                -len(cost_words & set(re.findall(r"[가-힣A-Za-z]+", _slide_words(slides[i])))),
                0 if isinstance(slides[i].get("bullets"), list) else 1,
                i,
            ),
        )
        slide = slides[scored[0]]
        if isinstance(slide.get("bullets"), list):
            slide["bullets"].append(line)
        elif isinstance(slide.get("rows"), list) and slide["rows"]:
            width = max(0, len(slide["rows"][0]) - 2)
            slide["rows"].append([line.split(" = ")[0], result, *([""] * width)])
        else:
            slide["bullets"] = [line]
        deck_text += "\n" + result
        if scored[0] not in changed:
            changed.append(scored[0])
    for match in quantities:
        quantity = match.group(0)
        digits = re.sub(r"[^\d.]", "", quantity)
        if not digits or digits in carried or _expanded_amounts(quantity) & carried:
            continue
        clause = _clause_around(text, match.start(), match.end())
        if not clause or _ASK_WORDS.search(clause) or len(clause) < len(quantity) + 2:
            continue
        words = {w for w in re.findall(r"[가-힣A-Za-z]{2,}", clause)}
        scored = sorted(
            bodies,
            key=lambda i: (
                -len(words & set(re.findall(r"[가-힣A-Za-z]{2,}", _slide_words(slides[i])))),
                0 if isinstance(slides[i].get("bullets"), list) else 1,
                i,
            ),
        )
        target = scored[0]
        slide = slides[target]
        # One clause carries every number in it (「파일럿 4개 부서 742명」 answers both
        # 4 and 742), so the same line never lands twice.
        carried |= {digits} | _expanded_amounts(quantity) | _facts_set(clause)
        if clause in _slide_words(slide):
            continue
        if isinstance(slide.get("bullets"), list):
            slide["bullets"].append(clause)
        elif slide.get("body"):
            slide["body"] = f"{str(slide['body']).rstrip()} {clause}."
        else:
            slide["bullets"] = [clause]
        if target not in changed:
            changed.append(target)
    return changed


def _bullets_from_notes(notes: object, limit: int = 4) -> list[str]:
    """The speaker notes as bullets, for a slide whose chart or metrics the number guard
    took away: the notes carry the same content in sentences, and a slide with words
    beats 「이 장을 쓰지 못했습니다」."""
    text = " ".join(str(n) for n in notes) if isinstance(notes, list) else str(notes or "")
    parts = [p.strip() for p in re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s*", text) if p.strip()]
    return [p.rstrip(".。")[:60] for p in parts[:limit] if len(p) >= 4]


#: A counted amount (「95건」, 「11명」, 「45만 원」) the request never gave: not a figure a
#: writer may derive, unlike a share (「40%」) or a time.
#: Not a date's day (「9/11」), not the 「개」 of 「개선」: the unit ends the word.
_COUNTED = re.compile(
    r"(?<![\d./])\d[\d,]*(?:\.\d+)?\s*(?:만|억|천)?\s*(?:건|명|원|대|회|개|곳|석)(?![가-힣A-Za-z])"
)


def dedupe_rows(rows: list[list[str]]) -> list[list[str]]:
    """A table that says one thing twice in two rows (「인덱스 노드 | 1대 증설, 월 45만 원」 and
    「인프라 증설 | 인덱스 노드 1대(월 45만 원)」) keeps the first: a later row goes when every
    number in it is in one earlier row and the two share two words. Rows without numbers
    are compared by words alone (all of the later row's words in the earlier one)."""
    if len(rows) < 3:
        return rows

    def words(row: list[str]) -> set[str]:
        return {w for w in re.findall(r"[가-힣A-Za-z]{2,}", " ".join(map(str, row)))}

    def numbers(row: list[str]) -> set[str]:
        found = re.findall(r"\d[\d,]*(?:\.\d+)?", " ".join(map(str, row)))
        return {n.replace(",", "") for n in found}

    kept = [rows[0]]
    for row in rows[1:]:
        mine_n, mine_w = numbers(row), words(row)
        same = any(
            (mine_n and mine_n <= numbers(prev) and len(mine_w & words(prev)) >= 2)
            or (not mine_n and mine_w and mine_w <= words(prev))
            for prev in kept[1:]
        )
        if not same:
            kept.append(row)
    return kept if len(kept) >= 2 else rows


def ground_cells(
    rows: list[list[str]], facts: set[str], given: str = ""
) -> list[list[str]]:
    """Table rows with counted amounts the request never gave taken out of their cells
    (「크롤러에서 95건 오류 발생」 → 「크롤러에서 오류 발생」); the header row is kept whole.
    With `given`, the amount must appear there with its unit: the 95 of 「p95」 does not
    vouch for 「95건」."""
    if not facts:
        return rows
    squeezed = re.sub(r"\s+", "", given or "")
    out = [list(rows[0])] if rows else []
    for row in rows[1:]:
        cells = []
        for cell in row:
            text = str(cell)

            def keep(m: re.Match) -> str:
                digits = re.sub(r"[^\d.]", "", re.match(r"[\d,.]+", m.group(0)).group(0))
                amounts = _expanded_amounts(m.group(0))
                if squeezed:
                    unit = re.sub(r"[\d,.\s]", "", m.group(0))
                    number = re.match(r"[\d,.]+", m.group(0)).group(0)
                    shown = number.replace(",", "") + unit
                    return (
                        m.group(0)
                        if shown in squeezed.replace(",", "") or amounts & facts
                        else ""
                    )
                return m.group(0) if digits in facts or amounts & facts else ""

            cleaned = " ".join(_COUNTED.sub(keep, text).split()).strip(" ,·")
            cells.append(cleaned or ("—" if text.strip() else text))
        out.append(cells)
    return out


def _numbers_come_from(values: list[str], facts: set[str]) -> bool:
    """Whether every digit run in `values` appears in `facts`; values without digits pass."""
    for value in values:
        digits = re.sub(r"[^\d.]", "", value)
        if digits and digits not in facts:
            return False
    return True


_QUANTITY = re.compile(
    r"\d[\d,.]{0,15}\s{0,8}(?:만|억|천)?\s{0,8}(?:개소|개월|시간|퍼센트|명|분|초|일|주|년|월|회|건|대|개|석|층|원|%|"
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
            # Emptied by validation: the notes still say it, so the slide says it as bullets
            # rather than being left to a second pass that may also come back empty.
            if slide["layout"] in ("chart", "metrics", "big-number"):
                slide["layout"] = "bullets"
            if bullets := _bullets_from_notes(row.get("notes")):
                row = {**row, "layout": "bullets", "bullets": bullets}
                out[index] = row
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
- 자료의 항목 이름·식별자(워크로드명·제품명·열 머리글)는 원문 그대로 쓴다. 번역·의역하지 않는다.

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
- 자료의 항목 이름·식별자(워크로드명·제품명·열 머리글)는 원문 그대로 쓴다. 번역·의역하지 않는다.
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


#: Seconds between retries of a rate-limited call when the gateway names no reset time.
#: Five rounds, about seventy seconds in all: past a token-per-minute window.
_BACKOFF = (2.0, 6.0, 12.0, 20.0, 30.0)


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
                    # Off where the provider allows; `thinking.switch` learns where not.
                    **thinking.switch(model),
                },
            )
            if thinking.refused(model, response):
                response = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": messages, "max_tokens": max_tokens},
                )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            # A token-per-minute 429 names when its window resets; wait for that.
            delay = ratelimit.retry_delay(response.text, dict(response.headers), _BACKOFF[attempt])
            log.info("deck call rate limited, retrying in %.0fs", delay)
            await asyncio.sleep(delay)
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
                    **thinking.switch(model),
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
    block = match.group(0) if match else text[text.find("{") :] if "{" in text else ""
    if not block:
        return {}
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        data = _repair_json(block)
    return _read_back_values(data) if isinstance(data, dict) else {}


def _repair_json(block: str) -> Any:
    """A reply cut off mid-object (the token budget ran out inside the last row): cut
    back to the last complete value and close what is still open. `None` when hopeless."""
    for end in range(len(block), 0, -1):
        head = block[:end].rstrip().rstrip(",")
        if not head.endswith(("]", "}", '"')) and not re.search(r"\d$", head):
            continue
        depth: list[str] = []
        quoted = False
        escaped = False
        for ch in head:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = not quoted
            elif not quoted and ch in "[{":
                depth.append("]" if ch == "[" else "}")
            elif not quoted and ch in "]}":
                if depth:
                    depth.pop()
        if quoted:
            continue
        try:
            return json.loads(head + "".join(reversed(depth)))
        except json.JSONDecodeError:
            continue
    return None


def _rows_from_text(text: str) -> list[list[str]]:
    """A Markdown pipe table in a reply that did not use `rows`."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            rows.append(cells)
    return rows


def _rows_any(parsed: dict[str, Any], text: str) -> list[list[str]]:
    """Table rows however the writer gave them: `rows`, `table`, rows as objects, or a
    pipe table in the text."""
    for key in ("rows", "table", "cells"):
        value = parsed.get(key)
        if isinstance(value, dict):
            value = value.get("rows") or value.get("cells")
        if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
            keys = list(value[0].keys())
            value = [keys, *[[str(r.get(k, "")) for k in keys] for r in value]]
        if rows := _clean_rows(value):
            return rows
    return _clean_rows(_rows_from_text(text))


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
    """An explicit total may be shorter than the default five-slide outline."""
    return plan_rules.requested_count(
        request, ("슬라이드", "페이지", "장", "쪽"), maximum=_MAX_SLIDES
    )


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


_LEAKED_PLANNING = re.compile(
    r"^(?:slides?|first slide|layout|note|rule)s?\s*[:(]|\(integrating\b|\bis fine\)|"
    r"^\"?(?:chart|metrics|bullets|table)\"?\s+(?:and|only|if)\b|\d+\s*to\s*\d+\.|"
    r"^(?:slide|장|부분)\s*\d+\s*[:.]",
    re.I,
)


def sane_outline(plan: list[dict], request: str) -> bool:
    """Whether a parsed outline is a deck and not the planner's notes to itself.

    A salvaged list can be the model's reasoning — 「Slides: 5 to 12. (6 is fine)」,
    「First slide: layout "title"」 — repeated section names, or an English plan for a
    Korean request. Writing such a plan costs minutes and produces forty slides of
    nothing; it is refused here so the outline is asked for once more.
    """
    titles = [" ".join(str(item.get("title") or "").split()) for item in plan]
    if not titles:
        return False
    if any(_LEAKED_PLANNING.search(t) for t in titles):
        return False
    lowered = [t.lower() for t in titles if t]
    if len(lowered) - len(set(lowered)) >= 2:
        return False
    if re.search(r"[가-힣]", request or ""):
        korean = sum(1 for t in titles if re.search(r"[가-힣]", t))
        if len(titles) >= 3 and korean * 2 < len(titles):
            return False
    return True


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


_SENTENCE_OR_DROPPED_STOP = re.compile(r"(?<=[.!?。])\s+|\n+|(?<=습니다|입니다|합니다|됩니다)\s+")


def _salvaged_bullets(data: dict) -> list[str]:
    """Bullets from any non-metadata string or list-of-strings field, whatever it was named."""
    found: list[str] = []
    for key, value in data.items():
        if key.lower() in _NOT_CONTENT:
            continue
        if isinstance(value, str):
            # A paragraph is split on sentence ends — also on a Korean sentence ending
            # whose full stop the writer dropped (「정리했습니다 검색 API는」).
            found.extend(
                part
                for part in re.split(_SENTENCE_OR_DROPPED_STOP, value)
                if part.strip()
            )
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
        # An explicit total preserves every approved slot, including user edits.
        # Keep its valid draft rather than shortening the deck or adding model calls.
        if retold and requested_slides(request) is None:
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

    echo_reasks = 0
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
            slide_messages: list[dict] | None = None
            if index in drafted:
                body, spent = (
                    json.dumps(drafted[index], ensure_ascii=False),
                    {"inputTokens": 0, "outputTokens": 0},
                )
            else:
                slide_messages = build_document_messages(
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
                )
                body, spent = await _complete(model, slide_messages, api_key, 600)
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
        elif isinstance(raw_notes, dict):
            # Notes written as an object (`{"title": …, "content": "…"}`) are their text.
            raw_notes = _notes_in_json(json.dumps(raw_notes, ensure_ascii=False)) or ""
        notes = str(raw_notes or "").strip()
        if notes.startswith("{") and (inner := _notes_in_json(notes)):
            notes = inner

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
                slide["bullets"] = _clean_bullets(data.get("bullets")) or _bullets_from_notes(
                    data.get("notes")
                )
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
            slide["title"] = closing_title(str(slide.get("title") or ""))
        elif slide["layout"] in _PAIRED:
            if pairs := _clean_pairs(data.get(slide["layout"]), slide["layout"]):
                slide[slide["layout"]] = pairs
            else:
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
        elif slide["layout"] == "table":
            # Rows however the writer shaped them: `rows`, `table`, objects, a pipe table.
            if rows := _rows_any(data, str(data.get("body") or "")):
                given = "\n".join([request, *(untrusted_context or [])])
                slide["rows"] = dedupe_rows(ground_cells(rows, _facts_set(given), given))
            else:
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
        else:
            slide["bullets"] = _clean_bullets(data.get("bullets"))

        if not has_content(slide):
            slide["bullets"] = _salvaged_bullets(data)
        if not has_content(slide) and slide_messages is not None:
            # One more ask before the slide is given up: the first answer had no content
            # the layout could use (an empty object, a lone title).
            try:
                again, more = await _complete(
                    model,
                    [
                        *slide_messages,
                        {"role": "assistant", "content": body[:400]},
                        {
                            "role": "user",
                            "content": "앞 답에는 쓸 내용이 없었다. 이 장의 bullets 3~4개와 "
                            "notes를 채운 JSON 객체 하나만 다시 써라.",
                        },
                    ],
                    api_key,
                    600,
                )
                usage["inputTokens"] += more["inputTokens"]
                usage["outputTokens"] += more["outputTokens"]
                parsed_again = _json_object(again)
                bullets = _clean_bullets(parsed_again.get("bullets")) or _salvaged_bullets(
                    parsed_again
                )
                if bullets:
                    slide["layout"] = "bullets"
                    slide["bullets"] = bullets
                    notes = notes or str(parsed_again.get("notes") or "").strip()
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                log.warning("deck slide %r second ask failed: %s", slide["title"], exc)
        if not has_content(slide):
            # Marked as unwritten (shown on screen, left out of exports) and
            # reset to bullets so renderers do not draw an empty layout.
            if slide.get("layout") not in _STRUCTURAL:
                slide["layout"] = "bullets"
            slide["body"] = UNWRITTEN

        if notes:
            notes = notes_only_given(notes_without_echo(notes, slide), slide, request)
        if slide.get("layout") not in _STRUCTURAL:
            # A body slide speaks in at least two sentences. Notes that only read the slide
            # aloud, that the filters emptied, or that the draft never wrote are asked for
            # once more — what to say beyond what is shown — within a budget per deck.
            if _sentence_count(notes) < 2 and echo_reasks < 4:
                echo_reasks += 1
                try:
                    said, more = await _complete(
                        model,
                        build_document_messages(
                            SessionKind.slides,
                            _NOTES_PROMPT.format(
                                heading=slide["title"], shown=_slide_lines_text(slide),
                                request=request[:1200],
                            )
                            + (
                                "\n\n요청이 「적어 준 것만」을 조건으로 했다. 화면과 요청에 있는 "
                                "말로만 풀어 말하고, 새 사실·조언·제재 같은 내용을 더하지 마라."
                                if _ONLY_GIVEN.search(request or "")
                                else ""
                            ),
                            request=request,
                            trusted_context=trusted_context,
                            untrusted_context=untrusted_context,
                            research_rule=research_rule,
                        ),
                        api_key,
                        400,
                    )
                except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                    log.info("notes re-ask for %r failed: %s", slide["title"], exc)
                else:
                    usage["inputTokens"] += more["inputTokens"]
                    usage["outputTokens"] += more["outputTokens"]
                    plain = _plain_notes(said)
                    added = notes_only_given(notes_without_echo(plain, slide), slide, request)
                    if not added and not notes and _ONLY_GIVEN.search(request or ""):
                        added = notes_only_given(plain, slide, request)
                    notes = _join_notes(notes, added)
        if notes:
            given_facts = _facts_set("\n".join([request, *(untrusted_context or [])]))
            notes = notes_grounded(notes, given_facts)
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

    # Structure, flow, comparison and concept figures the deck draws for itself, beside the
    # words. Planned on the written slides, so the planner reads what is actually there; a
    # slide with an approved picture keeps the picture, an unwritten one gets nothing.
    async for event in _draw_figures(
        slides,
        request=request,
        model=model,
        api_key=api_key,
        usage=usage,
        wrap=lambda prompt: build_document_messages(
            SessionKind.slides,
            prompt,
            request=request,
            trusted_context=trusted_context,
            untrusted_context=untrusted_context,
            research_rule=research_rule,
        ),
    ):
        yield event

    if (wanted := requested_slides(request)) and len(slides) != wanted:
        log.warning(
            "deck written with %d slides against a requested %d: %s",
            len(slides), wanted, " · ".join(str(s.get("title") or "") for s in slides),
        )
    # A number the person wrote down belongs somewhere in the deck; one the draft dropped
    # comes back as a line on the slide that talks about the same thing.
    touched = (
        set(restate_missing_facts(slides, request))
        | set(keep_listed_items(slides, request))
        | set(fill_csv_categories(slides, list(untrusted_context or []), request))
    )
    for index in sorted(touched):
        yield {"type": "slide", "slide": auto_fit(slides[index]), "done": True}
    yield {"type": "deck", "slides": harmonize(slides)}
    yield {"type": "usage", **usage}


async def _draw_figures(
    slides: list[dict[str, Any]],
    *,
    request: str,
    model: str,
    api_key: str,
    usage: dict[str, int],
    wrap: diagrams.Wrap,
) -> AsyncIterator[dict[str, Any]]:
    """Plans and draws the deck's own figures; each figured slide is announced again."""
    # A request that asks for its flows as pictures (「구조와 흐름은 그림으로」) opens the
    # step slides to the planner too; otherwise a steps layout is its own drawing.
    asked = diagrams.asks_for_diagrams(request)
    layouts = (*_DIAGRAM_LAYOUTS, "steps") if asked else _DIAGRAM_LAYOUTS
    eligible = [
        i
        for i, slide in enumerate(slides)
        if slide["layout"] in layouts
        and not slide.get("image")
        and slide.get("body") != UNWRITTEN
    ]
    planned, spent = await diagrams.plan(
        parts=[(str(slide["title"]), _drafted_text(slide)) for slide in slides],
        eligible=eligible,
        request=request,
        model=model,
        api_key=api_key,
        complete=_complete,
        slide=True,
        wrap=wrap,
    )
    usage["inputTokens"] += spent["inputTokens"]
    usage["outputTokens"] += spent["outputTokens"]
    if not planned:
        log.info("deck figures: none planned for %d eligible slides", len(eligible))
    for row in planned:
        slide = slides[row.index]
        name = row.caption or diagrams.FIGURES[row.figure]
        progress = {"current": row.index + 1, "total": len(slides)}
        yield {
            "type": "step",
            "id": f"dia{row.index}",
            "label": f"{name} 그리는 중",
            "status": "running",
            "progress": progress,
        }
        try:
            made, spent = await diagrams.make(row, model=model, api_key=api_key, slide=True)
        except Exception as exc:  # noqa: BLE001 — a slide without its figure is still a slide
            log.warning("slide figure %r not drawn: %s", name, exc)
            yield {
                "type": "step",
                "id": f"dia{row.index}",
                "label": name,
                "status": "error",
                "progress": progress,
            }
            continue
        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        slide["diagram"] = made
        _words_under_figure(slide)
        yield {
            "type": "step",
            "id": f"dia{row.index}",
            "label": name,
            "status": "done",
            "progress": progress,
        }
        yield {"type": "slide", "slide": auto_fit(slide), "done": True}


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
        # What the search backend was asked; the router counts it as usage.
        yield {
            "type": "research",
            "research": {"searched": findings.searched, "queries": findings.queries},
        }
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
        if (wanted := requested_slides(request)) and len(plan) != wanted:
            log.warning(
                "approved deck plan has %d slides against a requested %d: %s",
                len(plan), wanted, " · ".join(item["title"] for item in plan),
            )
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
    if plan and not sane_outline(plan, request):
        log.info("deck outline rejected as unsound (%d items), asking once more", len(plan))
        plan = []
    accent = fixed_accent or _theme_accent(text, suggested_accent)
    # Grounded before the variety check, so it does not ask for layouts that
    # would then be stripped.
    plan = _named_dividers(_rationed_quotes(_grounded_layouts(plan, request, document_context)))
    plan = keep_enumerated(plan, request, requested_slides(request))
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
    # A stated duration is a minimum; an explicit count is checked exactly below.
    needed = None if wanted else slides_for_minutes(request)
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
        # What came back, so an unreadable outline can be read afterwards.
        head = " ".join((text or "").split())[:240]
        log.info("deck outline unreadable, asking once more; reply began: %r", head)
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
    # Whatever the model settled on: a long deck opens with an agenda, and three bullet
    # lists in a row become two and a shape.
    plan = vary_layouts(ensure_agenda(plan))
    plan = _named_dividers(_rationed_quotes(_grounded_layouts(plan, request, document_context)))
    # After every retry has had its say: the parts the request listed stay separate.
    plan = keep_enumerated(plan, request, wanted)
    plan = structure_as_drawable(plan)

    # Check after structural changes: inserting an agenda must not enlarge an exact total.
    if wanted and plan and len(plan) != wanted:
        try:
            retry_text, retry_spent = await ask(
                f"\n\n앞선 구성은 {len(plan)}장이었다. 표지와 목차를 포함해 전체가 정확히 "
                f"{wanted}장이어야 한다. 요청한 내용을 빠뜨리지 말고 그 수에 맞춰 다시 구성하라."
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("deck outline size retry failed: %s", exc)
        else:
            plan_rules.count(usage, retry_spent, planned_apart=bool(outline_model))
            retry_title, retry_subtitle, retry_plan = _parse_outline(retry_text)
            retry_plan = _named_dividers(
                _rationed_quotes(_grounded_layouts(retry_plan, request, document_context))
            )
            retry_plan = vary_layouts(ensure_agenda(retry_plan))
            if len(retry_plan) == wanted:
                title = retry_title or title
                subtitle = retry_subtitle or subtitle
                plan = retry_plan
                accent = fixed_accent or _theme_accent(retry_text, suggested_accent) or accent
        plan = keep_enumerated(plan, request, wanted)
        if len(plan) != wanted:
            plan = fit_count(plan, wanted)
        if len(plan) != wanted:
            yield {"type": "step", "id": "outline", "label": "구성 잡는 중", "status": "error"}
            yield {
                "type": "error",
                "message": (
                    f"요청한 {wanted}장에 맞는 구성을 만들지 못했습니다. 다시 시도해 주세요."
                ),
            }
            yield {"type": "usage", **usage}
            return

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


#: Layout words a person uses when asking for a slide 「…로」: the layout they mean.
_LAYOUT_WORDS: tuple[tuple[str, str], ...] = (
    (r"연표|타임\s*라인|\btimeline\b", "timeline"),
    (r"단계(?:별)?|스텝|\bsteps?\b", "steps"),
    (r"차트|그래프|막대|\bchart\b|\bgraph\b", "chart"),
    (r"큰\s*숫자|\bbig\s*number\b", "big-number"),
    (r"지표|\bmetrics?\b|\bkpi\b", "metrics"),
    (r"두\s*단|2\s*단|\btwo[- ]column\b", "two-column"),
    (r"카드|\bcards?\b", "cards"),
    (r"띠|\bbands?\b", "bands"),
    (r"타일|\btiles?\b", "tiles"),
    (r"인용|\bquote\b", "quote"),
    (r"한\s*문장|핵심\s*메시지|\bstatement\b", "statement"),
    (r"(?<![연도])표|\btable\b", "table"),
)
_LAYOUT_ASK = re.compile(
    r"(?:로|으로)\s{0,8}(?:바꿔|바꾸|만들|보여|해\s{0,8}줘|해줘|정리|세워|고쳐|써|다시|하나)|"
    r"\b(?:as|into|to)\s{1,8}an?\b",
    re.I,
)


def requested_layout(text: str) -> str | None:
    """The layout the words ask the slide to become (「연표로 바꿔 줘」), or None.

    A layout word alone is not a request — a slide about 「단계별 계획」 is not asked to be
    `steps` — so the word must be followed by 「…로 바꿔/만들/보여」 or stand as 「…로」.
    """
    text = text or ""
    for pattern, layout in _LAYOUT_WORDS:
        for match in re.finditer(pattern, text, re.I):
            tail = text[match.end() : match.end() + 12]
            asked = re.match(r"\s{0,8}(?:로|으로)(?:\b|[\s,.]|$)", tail) or _LAYOUT_ASK.match(tail)
            if asked:
                return layout
    return None



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
    notes_only: bool = False,
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
    # 「연표로 바꿔 줘」「차트로」 changes the layout, not just the words: write it with
    # that layout's prompt. The person's own words first, the planner's paraphrase second.
    layout = requested_layout(typed) or requested_layout(note) or layout
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
        900 if layout in ("table", "chart", "timeline", "two-column") else 600,
    )
    parsed = _json_object(text)
    rows = _rows_any(parsed, text)
    pairs = _clean_pairs(parsed.get(layout), layout) if layout in _PAIRED else []
    bullets = _clean_bullets(parsed.get("bullets"))
    body = str(parsed.get("body") or "").strip()
    notes = str(parsed.get("notes") or "").strip()
    chart = parsed.get("chart") if layout == "chart" else None
    if not (isinstance(chart, dict) and chart.get("categories") and chart.get("series")):
        chart = None
    metrics = _clean_metrics(parsed.get("metrics")) if layout in ("metrics", "big-number") else []
    # Whatever the layout's prompt asked for counts as content; a notes-only answer to a
    # notes-only instruction keeps the slide and changes the notes, and a rename answered
    # with the title alone keeps the slide and changes the title.
    asked_title = revise.requested_title(typed)
    if not (rows or pairs or bullets or body or notes or chart or metrics):
        if asked_title:
            return {**target, "title": asked_title}, usage
        log.warning(
            "slide rewrite empty for %s: %s", logs.safe(target.get("title")), logs.safe(text[:240])
        )
        raise ValueError("빈 슬라이드")

    # Merged, so the slide's id, accent and picture survive.
    result = {**target}
    if notes_only:
        # The ask was for the notes: the slide's words stay exactly as they were. The
        # notes get the same reading as a first draft's: nothing the screen already says,
        # nothing the request never gave a number for.
        plain = _plain_notes(notes)
        strict = notes_only_given(notes_without_echo(plain, result), result, request)
        if not strict and _ONLY_GIVEN.search(request or ""):
            # Under 「적어 준 것만」 a slide may have nothing to add but its own words:
            # reading them out beats leaving the slide without notes.
            strict = notes_only_given(plain, result, request)
        notes = notes_grounded(strict, _facts_set("\n".join([request, *(material or [])])))
        if not notes:
            raise ValueError("노트 없음")
        result["notes"] = notes
        return result, usage
    # A title the person asked to change (「제목을 …로 바꿔 줘」) is taken from the answer;
    # otherwise the slide keeps its name, whatever the model restated.
    new_title = str(parsed.get("title") or "").strip()
    if re.search(r"제목|타이틀|\btitle\b|헤드라인", f"{typed} {note}", re.I):
        # The person's quoted title wins outright; the model's own only when it gave one.
        # The parser tells the new name from the old when both are quoted.
        asked = revise.requested_title(typed)
        if asked:
            result["title"] = asked
        elif new_title:
            result["title"] = new_title
    if chart:
        result["chart"] = chart
        result["layout"] = "chart"
        for field in ("bullets", "rows", "metrics", *_PAIRED):
            result.pop(field, None)
        rows, pairs, bullets = [], [], []
    elif metrics:
        result["metrics"] = metrics
        result["layout"] = layout
        for field in ("bullets", "rows", "chart", *_PAIRED):
            result.pop(field, None)
        rows, pairs, bullets = [], [], []
        if layout == "metrics":
            body = ""
    if rows:
        result["rows"] = rows
        result["layout"] = "table"
        for field in ("bullets", "body", *_PAIRED):
            result.pop(field, None)
        bullets, body = [], ""
    elif pairs:
        result[layout] = pairs
        result["layout"] = layout
        others = (f for f in _PAIRED if f != layout)
        for field in ("bullets", "body", "rows", "chart", "metrics", *others):
            result.pop(field, None)
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
    fitted = auto_fit(result)
    # A rewritten slide keeps the deck's body size unless it needs a smaller step.
    own = float(fitted.get("textScale") or 1.0)
    scale = min(own, deck_scale(slides))
    if scale >= 1.0:
        fitted.pop("textScale", None)
    else:
        fitted["textScale"] = scale
    fitted = keep_listed_dates(fitted, typed)
    return fitted, usage


#: Every field a slide's content can arrive in. A layout that stores content
#: under its own name must be here; the paired ones come from `_PAIRED`.
#: A figure the deck drew for itself is content too: a figured slide carries no other words.
_CONTENT_FIELDS = ("bullets", "body", "rows", "metrics", "chart", "diagram", *_PAIRED)


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
#: reasons in its slide units, so what it decides holds in all three. A slide only ever
#: steps down the body ladder (22 → 18 → 16 → 14 → 12pt); nothing grows to fill a slide.
_SCALES = deck_type.SCALES
#: The deck-wide body size never drops below this step on account of one crowded slide;
#: that slide keeps its own smaller step instead.
_COMMON_FLOOR = _SCALES[1]

#: Slide layouts that must vary: a run of these gets every other one re-planned.
_MONOTONE = "bullets"


#: Layouts a drawn figure may replace. Cards, bands and tiles are where the planner puts
#: structures, so they are offered too. Steps and timelines already draw their flow.
_DIAGRAM_LAYOUTS = ("bullets", "two-column", "cards", "bands", "tiles")


_NOTES_PROMPT = """발표 "{heading}" 장의 화면에는 이미 이렇게 적혀 있다:
{shown}

발표자가 이 장에서 청중에게 실제로 입으로 말할 문장을 두 문장으로 써라. 화면의 문장을
되풀이하지 말고, 왜 중요한지·청중이 기억할 한 가지·다음 장으로 넘어가는 말 가운데 골라라.
「~을 강조합니다」「~을 설명합니다」처럼 무엇을 말할지 적는 지시문이 아니라, 그 말 자체를 써라.
(예: 「이 숫자가 중요한 이유는 …입니다.」) 요청에 없는 수치나 사실은 지어내지 마라. 문장만 출력하라.

원래 요청: {request}"""


def _slide_lines_text(slide: dict) -> str:
    return "\n".join(_slide_lines(slide))


def _slide_lines(slide: dict) -> list[str]:
    """Every line the slide shows, for checking what the notes merely read aloud."""
    lines: list[str] = [str(slide.get("title") or "")]
    lines += [str(b) for b in slide.get("bullets") or []]
    if slide.get("body"):
        lines.append(str(slide["body"]))
    for key in ("cards", "steps", "bands", "tiles", "timeline", "rows", "metrics"):
        for row in slide.get(key) or []:
            if isinstance(row, list):
                lines.append(" ".join(str(c) for c in row))
    return [line for line in lines if line.strip()]


def _bigrams(text: str) -> set[str]:
    plain = re.sub(r"[^가-힣A-Za-z0-9%]", "", text)
    return {plain[i : i + 2] for i in range(len(plain) - 1)}


_DATED_ITEM = re.compile(
    r"(?<![\d/])(\d{1,2}/\d{1,2}|\d{1,2}월\s*\d{1,2}일)\s*([^,，.。;\n]{2,40}?)(?=\s*(?:[,，.。;\n]|$))"
)


def keep_listed_dates(slide: dict, instruction: str) -> dict:
    """A timeline rewritten from an instruction that lists dated items (「9/8 배포, 9/11
    회신, 9/15 재색인, 9/22 오픈」) shows every one of them, in the order given."""
    if slide.get("layout") != "timeline" or not isinstance(slide.get("timeline"), list):
        return slide
    listed = [(d.strip(), what.strip()) for d, what in _DATED_ITEM.findall(instruction or "")]
    if len(listed) < 2:
        return slide

    def key(date: str) -> str:
        digits = re.findall(r"\d+", date)
        return "/".join(str(int(x)) for x in digits[:2])

    present = {key(str(pair[0])) for pair in slide["timeline"] if pair}
    missing = [(d, what) for d, what in listed if key(d) not in present]
    if not missing:
        return slide
    order = {key(d): i for i, (d, _) in enumerate(listed)}
    merged = [list(pair) for pair in slide["timeline"] if pair] + [[d, w] for d, w in missing]
    merged.sort(key=lambda pair: order.get(key(str(pair[0])), len(order)))
    count = _PAIRED["timeline"][0]
    return {**slide, "timeline": merged[:count]}


def _notes_in_json(text: str) -> str | None:
    """The notes string inside a JSON reply (`notes`, `speaker_notes`, `note`, at any
    depth), or `None` when the text is not a JSON object with one."""
    if "{" not in text or "}" not in text:
        return None
    block = text[text.find("{") : text.rfind("}") + 1]
    try:
        data = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return None

    def walk(node: object) -> str | None:
        if isinstance(node, dict):
            for key in ("notes", "speaker_notes", "note", "content", "text", "body", "script"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    return value
                if isinstance(value, list) and all(isinstance(v, str) for v in value):
                    return " ".join(v.strip() for v in value if v.strip())
            for value in node.values():
                if (found := walk(value)) is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                if (found := walk(item)) is not None:
                    return found
        return None

    return walk(data)


def _plain_notes(text: str) -> str:
    # A reply that is a JSON object is read for its notes, never shown as braces.
    if (inner := _notes_in_json(text)) is not None:
        text = inner
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*[-*•]\s*", "", text, flags=re.M)
    text = " ".join(text.replace("\\n", " ").split())
    # A reply wrapped in quotation marks is the sentence, not a quotation.
    return text.strip().strip('"“”\'')


def notes_grounded(notes: str, facts: set[str]) -> str:
    """The notes minus any sentence whose numbers are not the request's: 「개발자 10명
    기준 하루 1시간 20분」 is an effect the presenter never measured."""
    kept = []
    for sentence in re.split(r"(?<=[.!?。])\s+", (notes or "").strip()):
        if not sentence.strip():
            continue
        if _numbers_come_from(re.findall(r"\d[\d,.]*(?:\s*(?:만|억|천))?", sentence), facts):
            kept.append(sentence.strip())
    return " ".join(kept)


#: A note that describes what to say instead of saying it: stage directions, not speech.
_META_NOTE = re.compile(
    r"(강조합니다|강조한다|설명합니다|설명한다|언급합니다|소개합니다|안내합니다|짚어\s*줍니다|"
    r"넘어갑니다|전달합니다|이야기합니다|말씀드립니다|보여\s*줍니다|상기시킵니다)\s*[.。]?$"
)


_KO_ENDING = re.compile(
    r"(에는|으로|에서|입니다|합니다|습니다|했습니다|됩니다|있어요|해요|이에요|예요|은|는|이|가|을|를|의|에|로|도|과|와)$"
)


def _stems_ko(sentence: str) -> set[str]:
    """Content words of a sentence with their particles and endings cut, lowercased."""
    out = set()
    for word in re.findall(r"[가-힣A-Za-z0-9]{1,}", sentence):
        stem = _KO_ENDING.sub("", word) or word
        if len(stem) >= 2 or stem.isascii():
            out.add(stem.lower())
    return out


#: 「적어 준 것만」: the person wants nothing on the slides or in the notes they did not
#: write. In that mode a note sentence must be made of the slide's and the request's words.
_ONLY_GIVEN = re.compile(
    r"적어\s*준\s*것만|적은\s*것만|준\s*것만|없는\s*내용|지어내지|추가하지\s*마"
)
#: Words of speaking, not of claiming: a note may use them without adding anything.
_SPOKEN_WORDS = frozenset({
    "가지", "지켜", "주세요", "주시", "드립니다", "드리", "하세요", "보세요", "보시", "확인",
    "기억", "부탁", "먼저", "오늘", "이제", "다음", "함께", "같이", "설명", "말씀", "정리",
    "핵심", "바로", "꼭", "이렇게", "여기", "이번", "장에서", "장은", "살펴", "넘어", "마지막",
    "중요", "기본", "모두", "전부", "각각", "매일", "순서", "진행", "정해져", "있습니다",
})


def _claim_words(sentence: str, allowed: set[str]) -> set[str]:
    return {
        w for w in _stems_ko(sentence) - allowed
        if len(w) >= 2 and not w.isdigit() and w not in _SPOKEN_WORDS
    }


def notes_only_given(notes: str, slide: dict, request: str) -> str:
    """Notes under a 「적어 준 것만」 request: sentences whose content words all come from
    the slide or the request stay; a sentence that adds a claim (「위반 시 계정 정지 등
    제재가 따릅니다」) goes. Other requests are untouched."""
    if not notes or not _ONLY_GIVEN.search(request or ""):
        return notes
    allowed = _stems_ko(" ".join([request, *_slide_lines(slide), str(slide.get("title") or "")]))
    kept = []
    for sentence in re.split(r"(?<=[.!?。])\s+", notes.strip()):
        if not sentence.strip():
            continue
        if len(_claim_words(sentence, allowed)) <= 2:
            kept.append(sentence.strip())
    return " ".join(kept)


def _sentence_count(text: str) -> int:
    return len([x for x in re.split(r"(?<=[.!?。])\s+", (text or "").strip()) if x.strip()])


def _join_notes(existing: str, added: str) -> str:
    """Existing notes followed by the added sentences they do not already say."""
    have = [x.strip() for x in re.split(r"(?<=[.!?。])\s+", existing or "") if x.strip()]
    for sentence in re.split(r"(?<=[.!?。])\s+", added or ""):
        sentence = sentence.strip()
        said = any(sentence == h or (len(sentence) > 12 and sentence in h) for h in have)
        if sentence and not said:
            have.append(sentence)
    return " ".join(have)


def notes_without_echo(notes: str, slide: dict) -> str:
    """The speaker notes minus every sentence that only reads a line of the slide aloud.

    A note that repeats the bullets is the one thing a presenter never needs; what the
    slide does not say is what the notes are for. Sentences are compared by character
    bigrams so particles and spacing do not hide a repeat."""
    if not notes.strip():
        return ""
    lines = _slide_lines(slide)
    shown = [_bigrams(line) for line in lines]
    shown_all = set().union(*shown) if shown else set()
    short_lines = [
        [t for t in re.split(r"[\s|:·,]+", line) if t and len(t) <= 12]
        for line in lines
        if len(_bigrams(line)) < 8 and line != str(slide.get("title") or "")
    ]
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?。])\s+", notes.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        grams = _bigrams(sentence)
        if len(grams) < 6:
            kept.append(sentence)
            continue
        # The same line said again (Dice), a terse bullet spelled out in a sentence
        # (containment of the bullet in the sentence), or a sentence stitched from
        # several lines (coverage): all three only read the slide aloud.
        echo = any(
            2 * len(grams & g) / (len(grams) + len(g)) >= 0.6
            or (len(g) >= 5 and len(grams & g) / len(g) >= 0.5)
            for g in shown
            if g
        )
        covered = len(grams & shown_all) / len(grams)
        # A terse line (「월 | OT」) read out as a sentence (「월요일에는 OT로 시작합니다」):
        # every token of the line is in the sentence and the sentence adds little else.
        spoken = any(
            tokens
            and all(t.lower() in sentence.lower() for t in tokens)
            and len(_stems_ko(sentence) - {t.lower() for t in tokens}) <= 3
            for tokens in short_lines
        )
        if echo or covered >= 0.85 or spoken:
            continue
        if _META_NOTE.search(sentence) and not re.search(r"\d", sentence):
            # 「…을 강조합니다」 says nothing a presenter can read out.
            continue
        kept.append(sentence)
    return " ".join(kept)


def _drafted_text(data: dict | None) -> str:
    """The words a slide holds, for the figure planner to read."""
    if not data:
        return ""
    parts: list[str] = [str(b) for b in (data.get("bullets") or []) if str(b).strip()]
    if data.get("body"):
        parts.append(str(data["body"]))
    for key in ("cards", "steps", "bands", "tiles", "timeline"):
        for pair in data.get(key) or []:
            if isinstance(pair, list):
                parts.append(" — ".join(str(cell) for cell in pair))
    return "\n".join(parts)


#: Height of the figure band above a figured slide's words, in slide units.
_FIGURE_BAND = 70.0

#: Words that stay under a figure: cards (this many, this long) or bullets (this many lines).
_UNDER_CARDS = 4
_CARD_CHARS = 44
_UNDER_LINES = 3
_LINE_CHARS = 60
#: A list of this many short lines (each this long at most) is kept whole under a figure.
_UNDER_LINES_SHORT = 6
_SHORT_LINE_CHARS = 24


def _clipped(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _words_under_figure(slide: dict) -> None:
    """A figured slide keeps a few words under its figure and moves the rest to the notes.

    Cards stay cards (four at most, short); every other shape becomes up to three bullets.
    The figure takes the top of the body, the words the strip beneath, so nothing beside
    the figure narrows it.
    """
    layout = str(slide.get("layout") or "")
    spill: list[str] = []
    if layout == "cards":
        pairs = [p for p in (slide.get("cards") or []) if isinstance(p, list) and p]
        kept: list[list[str]] = []
        for pair in pairs:
            name = str(pair[0]).strip()
            text = " ".join(str(cell).strip() for cell in pair[1:] if str(cell).strip())
            if len(kept) < _UNDER_CARDS:
                kept.append([name, _clipped(text, _CARD_CHARS)])
                if len(text) > _CARD_CHARS:
                    spill.append(f"{name}: {text}")
            else:
                spill.append(f"{name}: {text}")
        slide["cards"] = kept
    else:
        lines: list[str] = []
        for key in _PAIRED:
            for pair in slide.pop(key, None) or []:
                if not isinstance(pair, list) or not pair:
                    continue
                name = str(pair[0]).strip()
                text = " ".join(str(cell).strip() for cell in pair[1:] if str(cell).strip())
                lines.append(f"{name}: {text}" if name and text else name or text)
        lines.extend(str(b).strip() for b in (slide.get("bullets") or []) if str(b).strip())
        if body := str(slide.get("body") or "").strip():
            lines.append(body)
        slide["layout"] = "bullets"
        # A short list the person wrote out (five weekdays, four accounts) stays whole
        # under the figure; the text shrinks instead of two items vanishing into notes.
        keep = _UNDER_LINES_SHORT if len(lines) <= _UNDER_LINES_SHORT and all(
            len(line) <= _SHORT_LINE_CHARS for line in lines
        ) else _UNDER_LINES
        slide["bullets"] = [_clipped(line, _LINE_CHARS) for line in lines[:keep]]
        slide["body"] = ""
        spill = [line for line in lines[:keep] if len(line) > _LINE_CHARS] + lines[keep:]
    if spill:
        notes = str(slide.get("notes") or "").strip()
        slide["notes"] = "\n".join(filter(None, [notes, *spill]))[:800]


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
    U, L = deck_type.units, deck_type.LEADING
    layout = str(slide.get("layout") or "bullets")
    width = _body_width(slide)
    title = str(slide.get("title") or "")
    title_size = deck_type.title_pt(title) / deck_type.K
    title_lines = deck_type.lines(title, title_size, deck_type.TITLE_WIDTH)
    need = max(0, title_lines - 1) * title_size * L["title"]
    bullets = [str(b) for b in (slide.get("bullets") or []) if str(b).strip()]
    if layout == "agenda":
        size = U("agenda") * scale
        columns = 2 if len(bullets) > 4 else 1
        column_width = (width - 20 * (columns - 1)) / columns - 22
        rows = [
            deck_type.lines(b, size, column_width) * size * L["agenda"] + 10 * scale + 1
            for b in bullets
        ]
        per_column = -(-len(rows) // columns)
        need += max(sum(rows[:per_column]), sum(rows[per_column:]))
    elif layout == "two-column" and len(bullets) >= 5:
        size = U("bodyNarrow") * scale
        column_width = (width - 20) / 2 - 12
        half = -(-len(bullets) // 2)
        cost = [deck_type.lines(b, size, column_width) * size * L["body"] for b in bullets]
        gap = size * deck_type.BULLET_GAP
        need += max(
            sum(cost[:half]) + gap * (half - 1), sum(cost[half:]) + gap * (len(bullets) - half - 1)
        )
    elif bullets:
        size = U("body") * scale
        need += sum(deck_type.lines(b, size, width - 12) * size * L["body"] for b in bullets)
        need += size * deck_type.BULLET_GAP * (len(bullets) - 1)
    if slide.get("body") and not bullets:
        size = U("paragraph") * scale
        need += deck_type.lines(str(slide["body"]), size, width) * size * L["paragraph"] + 2
    # A figure the deck drew for itself sits above the words as a band.
    figured = bool(slide.get("diagram"))
    if figured:
        need += _FIGURE_BAND + 6
    for key, fixed in (
        ("cards", 36.0 if figured else 104.0),
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
            text = max(deck_type.lines(str(p[1]), U("stepText") * scale, span) for p in pairs)
            need += (
                8
                + 22
                + 7
                + U("stepName") * scale * 1.3
                + 3
                + text * U("stepText") * scale * L["stepText"]
            )
        elif key == "tiles":
            need += 8 + 62 + 7 + U("tileName") * scale * 1.5 * 2
        else:
            # Bands and timelines divide the body height among themselves.
            need += fixed
    rows = slide.get("rows") or []
    if rows:
        # The table sizes itself from its row count; a wrapped long cell takes the reserve.
        need += deck_type.table_row_height(len(rows)) * len(rows)
    metrics = slide.get("metrics") or []
    if metrics and layout != "big-number":
        need += 6 + 14 + U("metric") * scale * 1.1 + 5 + U("metricLabel") * scale * 1.5 + 16
    return need


def auto_fit(slide: dict) -> dict:
    """Sets `textScale` to the first step of the body ladder at which the slide fits.

    The panel and both exporters read the same field and the same type scale, so a deck
    looks the same in the panel, the `.pptx` and the `.pdf`. The title stays at its size;
    the body starts at 22pt and steps down to 18, 16, 14 and 12 only when it would cross
    the footer. A slide that fits keeps no scale; a scale a person set by hand is left
    alone.
    """
    if (
        slide.get("textScale") not in (None, 1, 1.0)
        or slide.get("layout") in _STRUCTURAL
        and slide.get("layout") != "agenda"
    ):
        return slide
    room = deck_type.BODY_BOTTOM - deck_type.BODY_TOP
    for scale in _SCALES:
        if _need(slide, scale) <= room:
            break
    else:
        scale = _SCALES[-1]
    if scale == 1.0:
        slide.pop("textScale", None)
    else:
        slide["textScale"] = scale
    return slide


def _body_slides(slides: list[dict]) -> list[dict]:
    return [
        s
        for s in slides
        if (s.get("layout") not in _STRUCTURAL or s.get("layout") == "agenda")
        and s.get("layout") != "closing"
    ]


def harmonize(slides: list[dict]) -> list[dict]:
    """One body size for the whole deck, in place.

    Slides fitted one by one come out at different steps of the ladder, and a deck that
    changes size from slide to slide reads as careless. The deck takes the smallest step
    any body slide needed, but not below 18pt: a slide crowded enough to need 16 or less
    keeps its own step, and the rest stay at 18.
    """
    body = _body_slides(slides)
    needed = [float(s.get("textScale") or 1.0) for s in body]
    common = max(min(needed, default=1.0), _COMMON_FLOOR)
    for slide in body:
        own = float(slide.get("textScale") or 1.0)
        scale = min(own, common)
        if scale >= 1.0:
            slide.pop("textScale", None)
        else:
            slide["textScale"] = scale
    return slides


def deck_scale(slides: list[dict]) -> float:
    """The body size the deck settled on: the step most of its body slides use."""
    body = _body_slides(slides)
    if not body:
        return 1.0
    seen = [float(s.get("textScale") or 1.0) for s in body]
    return max(set(seen), key=lambda value: (seen.count(value), value))


#: What the outline calls its table of contents.
_AGENDA_TITLE = re.compile(r"목차|발표\s*순서|차례|순서|agenda|contents|overview", re.I)


#: 「(1) 첫 주 일정 — …  (2) 필수 계정 — …」: the items a request enumerates.
_ENUM_MARK = r"(?:\(\d{1,2}\)|[①-⑳]|(?<!\d)\d{1,2}\)|(?<![\d.])\d{1,2}\.\s)"
_ENUM_ITEM = re.compile(
    _ENUM_MARK + r"\s*([^—:\-–()\n]{2,30}?)\s*(?=[—:\-–]|" + _ENUM_MARK + r"|[.。]|$)"
)
_FILLER_LAYOUTS = {"statement", "quote", "big-number", "section", "agenda"}
#: An instruction to merge or shorten parts — not a part's own words (「결과 병합」).
_MERGE_ASK = re.compile(
    r"한\s{0,8}장(?:으로|에)\s{0,8}(?:합|묶|넣|정리)|합쳐\s{0,8}(?:줘|서|라|주)|합치고|묶어\s{0,8}(?:줘|서|라)|"
    r"줄여\s{0,8}(?:줘|서|라|주)|줄이고|\d{1,15}\s{0,8}장으로\s{0,8}줄"
)


#: The label before a list that makes its items parts of the deck (「내용: (1) …」), not
#: the issues or steps of one part (「이슈: (1) p95 미달 (2) …」).
_PART_LIST_LABEL = re.compile(
    r"(?:내용|구성|목차|순서|차례|장\s{0,8}구성|슬라이드|섹션|절|파트|chapters?|sections?|slides?)"
    r"\s{0,8}(?:은|는|이|가)?\s{0,8}[:：]\s{0,8}$"
)


#: 「한 줄 요약 → 진척(표) → 지표 → … 순서」: parts named in a row with arrows. A flow that
#: belongs to one part (「웹 → API → 결과 병합」 after 「검색 요청 흐름 —」) is not one.
_ARROW_CHAIN = re.compile(r"(?:[^→\n:：]{1,24}\s*→\s*){2,}[^→\n.。]{1,24}")
_ARROW_PARTS_TAIL = re.compile(r"^\s*(?:순서|순으로|차례|구성|흐름으로)")
_ARROW_PARTS_LEAD = re.compile(
    r"(?:장|발표|슬라이드|덱|deck|자료)[^\n]{0,20}(?:만들|구성|순서|짜)[^\n→]{0,12}[:：]\s*$"
)
_COUNT_TAIL = re.compile(r"\s*(?:셋|둘|넷|다섯|여섯|\d+\s*(?:건|개|가지))$")


def arrow_parts(request: str) -> list[str]:
    """Part names listed with arrows and marked as the deck's order, hints in
    parentheses dropped; `[]` when no such chain is the deck's."""
    text = " ".join((request or "").split())
    for match in _ARROW_CHAIN.finditer(text):
        chain = match.group(0)
        # The order word may sit inside the last item (「… → 다음 단계 순서」) or after it.
        inside = re.search(r"\s*(?:순서|순으로|차례|구성|흐름으로)\s*[.。]?$", chain)
        if inside:
            chain = chain[: inside.start()]
        tail = text[match.end() : match.end() + 8]
        lead = text[max(0, match.start() - 60) : match.start()]
        if not (inside or _ARROW_PARTS_TAIL.match(tail) or _ARROW_PARTS_LEAD.search(lead)):
            continue
        items = []
        for raw in chain.split("→"):
            # The chain starts mid-sentence (「장수 8장. 한 줄 요약 → …」): the first item is
            # what follows the last full stop.
            raw = re.split(r"[.。:：;]", raw)[-1]
            item = re.sub(r"[(（][^()（）]*[)）]", "", raw).strip(" ,.。:")
            item = _COUNT_TAIL.sub("", item).strip()
            if 2 <= len(item) <= 14 and not re.fullmatch(r"[\d\s,.]+", item):
                items.append(item)
        if len(items) >= 3:
            return items
    return []


def enumerated_items(request: str) -> list[str]:
    """The names of the parts a request lists one by one under a parts label, in
    order; `[]` under two or when the list belongs to one part."""
    text = request or ""
    matches = list(_ENUM_ITEM.finditer(text))
    if len(matches) < 2:
        return arrow_parts(text)
    lead = text[max(0, matches[0].start() - 24):matches[0].start()]
    if not _PART_LIST_LABEL.search(lead):
        return arrow_parts(text)
    items = [" ".join(m.group(1).split()) for m in matches]
    items = [i for i in items if i and not re.fullmatch(r"[\d\s,.]+", i)]
    return items if len(items) >= 2 else arrow_parts(text)


def _covers(title: str, item: str) -> bool:
    squeeze = lambda t: re.sub(r"\s+", "", t).lower()  # noqa: E731
    if squeeze(item) and squeeze(item) in squeeze(title):
        return True
    words = set(item.split())
    return bool(words) and words <= set(title.split())


def keep_enumerated(plan: list[dict], request: str, wanted: int | None) -> list[dict]:
    """An outline keeps every part the request listed, one slide each.

    A planner given 「(1) 첫 주 일정 (2) 필수 계정 (3) 보안 규칙 (4) 도움 받는 곳, 6장」
    sometimes merges two items into one slide and spends the spare slot on a slogan.
    A merged slide is split back into the items it covers; an item no slide covers
    gets one before the closing; and when the deck is then over the asked count, the
    slides that cover no item and carry no argument (a statement, a quote, an agenda)
    make room. Content slides the planner added on its own are kept when there is room.
    """
    items = enumerated_items(request)
    if not plan or not items:
        return plan
    # A request that asks for parts to be merged, or a count too small for one slide
    # per item beside a cover and a closing, is the planner's to resolve.
    if _MERGE_ASK.search(request) or (wanted and len(items) + 2 > wanted):
        return plan
    # 「한 줄 요약」 asks for a one-line slide: the planner's statement is that part,
    # under its own line; it is neither split nor renamed.
    one_liners = {i for i in items if re.search(r"한\s*줄|요약|메시지|핵심\s*문장", i)}
    if one_liners and any(s.get("layout") in ("statement", "quote") for s in plan):
        items = [i for i in items if i not in one_liners]
    out: list[dict] = []
    for slide in plan:
        title = str(slide.get("title") or "")
        covered = [item for item in items if _covers(title, item)]
        first_seen = (
            any(_covers(str(o.get("title") or ""), covered[0]) for o in out) if covered else True
        )
        if len(covered) >= 2 and not first_seen:
            out.append({**slide, "title": covered[0]})
            for item in covered[1:]:
                out.append({"title": item, "layout": "bullets"})
        else:
            out.append(slide)
    # An agenda the person asked for (「표지·목차 포함」) is not filler to be dropped.
    asked_agenda = bool(re.search(r"목차|차례|agenda", request, re.I))
    fillers = _FILLER_LAYOUTS - ({"agenda"} if asked_agenda else set())
    # A closing the person did not ask for gives way to a part they listed (「표지·목차
    # 포함 9장」 with seven parts leaves no slot for 「마무리」).
    if not re.search(r"마무리|맺음|끝인사|클로징|closing|감사\s*인사", request, re.I):
        fillers = fillers | {"closing"}
    for item in items:
        if any(_covers(str(o.get("title") or ""), item) for o in out):
            continue
        # A planner that renamed the part (「보안 규칙」 → 「외부 유출 금지」) left a body
        # slide covering no item: that slide is the part, under the name the person used.
        orphan = next(
            (
                o for o in out
                if o.get("layout") not in (*_STRUCTURAL, "closing", "statement", "quote")
                and not any(_covers(str(o.get("title") or ""), i) for i in items)
            ),
            None,
        )
        if orphan is not None:
            orphan["title"] = item
            continue
        droppable = sum(
            1 for o in out
            if o.get("layout") in fillers
            and not any(_covers(str(o.get("title") or ""), i) for i in items)
        )
        if wanted and len(out) + 1 > wanted and not droppable:
            # No room and nothing to make room with: the part stays merged where the
            # planner put it rather than the deck failing its count.
            log.info("deck outline: no room for listed part %r", item)
            continue
        at = len(out) - 1 if out and out[-1].get("layout") == "closing" else len(out)
        out.insert(at, {"title": item, "layout": "bullets"})
    if wanted and len(out) > wanted:
        for index in range(len(out) - 1, -1, -1):
            if len(out) <= wanted:
                break
            slide = out[index]
            title = str(slide.get("title") or "")
            if slide.get("layout") in fillers and not any(_covers(title, i) for i in items):
                del out[index]
    if [o.get("title") for o in out] != [o.get("title") for o in plan]:
        log.info(
            "deck outline realigned to the listed parts: %s",
            " · ".join(str(o.get("title") or "") for o in out),
        )
    return out


def fit_count(plan: list[dict], wanted: int) -> list[dict]:
    """A plan one or two over the asked count, trimmed of what carries no argument.

    The planner was asked twice for exactly `wanted` and came back over. Before the
    turn fails, the slides that say nothing on their own go: section dividers first,
    then the agenda — a six-slide deck needs no table of contents. Content slides are
    never dropped here; a plan still over, or under, is left for the error path.
    """
    if len(plan) <= wanted:
        return plan
    out = list(plan)
    for layout in ("section", "agenda"):
        while len(out) > wanted:
            idx = next((i for i, item in enumerate(out) if item.get("layout") == layout), None)
            if idx is None:
                break
            del out[idx]
    return out if len(out) == wanted else plan


def ensure_agenda(plan: list[dict]) -> list[dict]:
    """A deck of more than six slides opens with an `agenda` slide, in place.

    The outline rule asks for one, and the model usually writes the title — 「발표 순서」 —
    but now and then labels it `bullets`, which the writer then fills with four
    sentences instead of the section list. The title says what the slide is; the
    layout follows. A long deck with no such slide at all gets one after the cover.
    """
    if len(plan) <= 6 or any(item.get("layout") == "agenda" for item in plan):
        return plan
    for item in plan[1:4]:
        if _AGENDA_TITLE.search(str(item.get("title") or "")):
            item["layout"] = "agenda"
            return plan
    plan.insert(1, {"title": "발표 순서", "layout": "agenda"})
    return plan


#: A slide about how something is built: components and their relations, not a sequence.
_STRUCTURE_TITLE = re.compile(
    r"구조|아키텍처|구성\s*(?:요소|도)|시스템\s*구성|전체\s*구성|컴포넌트|모듈\s*구성|"
    r"\b(?:architecture|structure|components?|topology)\b",
    re.I,
)


def structure_as_drawable(plan: list[dict]) -> list[dict]:
    """A structure slide the outline put on `steps` goes to `bullets`.

    `steps` is a sequence and `chart` wants a number series; an architecture is parts and
    their relations, which the deck draws as a 구조도 — but only on a layout the figure
    planner may draw on (`bullets`, `cards`, `bands`, …). Left on `steps`, 「전체 구조」 came
    out as four numbered boxes; on `chart`, as a bar chart of made-up values.
    """
    out = []
    for slide in plan:
        structural = _STRUCTURE_TITLE.search(str(slide.get("title") or ""))
        if structural and slide.get("layout") in _NOT_FOR_STRUCTURE:
            slide = {**slide, "layout": "bullets"}
        out.append(slide)
    return out


#: Layouts an architecture slide cannot be: a sequence, or a shape that wants numbers.
_NOT_FOR_STRUCTURE = ("steps", "chart", "table", "metrics", "big-number", "timeline")


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
