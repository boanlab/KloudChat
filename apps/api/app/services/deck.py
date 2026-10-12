"""Deck generation: outline call, one-call draft of the approved plan, per-slide gap fill."""

from __future__ import annotations

import asyncio
import dataclasses
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
    calc,
    calc_check,
    deck_type,
    design,
    diagram_render,
    diagrams,
    figures,
    grounding,
    hangul,
    imagegen,
    key_figures,
    pictures,
    placeholders,
    quality_gate,
    ratelimit,
    research,
    revise,
    settings_store,
    slide_patterns,
    thinking,
    units,
)
from app.services import outline as plan_rules
from app.services.context import build_document_messages, pasted_material, prompt_request

log = logging.getLogger(__name__)

#: Default minimum; explicit short requests keep their count.
_MIN_SLIDES = 5
_MAX_SLIDES = 50

#: Upper bound when no count was asked for.
_DEFAULT_MAX = 12

#: Layouts every renderer (preview, .pptx, .pdf) can draw.
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
    # patterns (`slide_patterns`)
    *slide_patterns.NAMES,
)

#: Filled from the outline, no model call.
_STRUCTURAL = ("title", "section", "agenda")

#: Argument-carrying layouts; variety check judged on these.
_BODY_LAYOUTS = tuple(layout for layout in _LAYOUTS if layout not in (*_STRUCTURAL, "closing"))

#: Unwritten-slide body marker; `deck_export` omits such slides.
UNWRITTEN = "이 장을 쓰지 못했습니다."

#: Default accent, stored on every slide.
_ACCENT = "#5b5bd6"

#: Only when no design system fixes the accent.
_THEME_RULE = """- theme is one colour name that suits the topic. Pick only from:
  {themes}
- style is the impression that suits the occasion where this deck is read. Pick exactly one
  of seven:
  · 편집형 — occasions for reading and judging: reports, reviews, plans. Lines and wide margins.
  · 포스터형 — occasions that must catch the eye first: promotion, info sessions, showcases.
    Strong colour fields.
  · 미니멀 — occasions where restraint is courtesy: academic talks, examinations. Pale colours
    and small titles.
  · 다크 — occasions watched on a darkened screen: technology, products, demos. A dark ground
    with a glowing accent.
  · 분할형 — business reports, proposals, institutional talks. A colour field on the left, large
    numbers, boxes drawn in line.
  · 따뜻한 — education, culture, welfare, everyday topics. A cream paper ground and rounded boxes.
  · 흑백 — design, architecture, research, exhibitions. Black lines and large titles; no colour.
  If the request states an impression, follow it; otherwise pick from the topic. The same topic
  on a different occasion is a different impression — do not always fall back to 편집형.
- For this request, theme "{theme}" and style "{style}" fit. Use both as given unless the
  request names another colour or impression.
"""

#: Stored `visualStyle` → prompt label (outline suggestion).
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

#: (accent names, topic words); first topic wins, colour picked by request digest.
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
#: No-topic rotation; app purple excluded (deck ≠ app chrome).
_ROTATION = ("파랑", "청록", "남색", "초록", "주황", "자주")


def suggest_look(request: str) -> tuple[str, str]:
    """`(theme name, style label)` default for the outline (venue → style, subject → colour)."""
    style = design.visual_style_for(request)
    if style == "editorial":
        style = design.venue_style_for(request) or "editorial"
    return topic_theme(request), _STYLE_LABELS[style]


def topic_theme(request: str) -> str:
    """Colour name for the request: subject colour by digest (stable per request), else rotation."""
    text = (request or "").lower()
    digest = sum(ord(ch) for ch in re.sub(r"\s+", "", text)[:200])
    for names, words in _TOPIC_THEMES:
        if any(w in text for w in words):
            return names[digest % len(names)]
    return _ROTATION[digest % len(_ROTATION)]


def topic_accent(request: str) -> str:
    """Hex accent for `topic_theme`."""
    return _THEMES.get(topic_theme(request), _THEMES["파랑"])


#: Prompt label → stored `visualStyle`.
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

#: Accent palette by name; each carries white text.
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

_OUTLINE_PROMPT = """Make the title and slide structure of a presentation for the request below.

Rules:
- title is the one line printed on the cover. Do not copy the request sentence; write a noun
  phrase that names the topic. Drop the full stop and filler such as "~에 대한 발표".
- **Do not invent subject matter the request does not give.** If the request only says what
  the document is for and not what it is about — like "연구계획 발표자료", "제안 발표" —
  write a title that names that use, and build each slide on the skeleton that use calls for
  (배경·목표·방법·일정 and the like). A deck filled with a field or year the request never
  named is not the audience's own and cannot be used as is.
- subtitle is the one small line under the title on the cover. Within 40 characters, say who
  this talk is for and what it tells them. Do not copy the request sentence.
- {lo}~{hi} slides.
- The first slide must have layout "title", and its title must be the same as the deck title.
- **"chart" and "metrics" only when the request has those numbers.** Choosing these layouts
  when the request has no figures makes the slide invent numbers. If the request has several
  values whose **shape** must be seen, "chart"; two or three numbers to remember, "metrics";
  values to be read one by one, "table".
- **Make a slide that compares two or three things on the same criteria a "table".**
  Comparing alternatives, before and after, conditions by stage — content whose values differ
  by criterion. Laid out as bullets, the reader has to redraw the table in their head.
- **If the request says to split the talk into parts (「시장 구조, 핵심 취약점, 거버넌스
  세 부분」), give each part a "section" slide with that name, followed by that part's
  slides.** The agenda follows those part names.
- **When a title promises subjects such as 「주요 기업별」 or 「국내외」, put real names
  (companies, products, documents) on that slide.** Do not fill it with category names only.
- **A deck of more than six slides puts an "agenda" (목차) as its second slide.** Write no
  content — it is filled from the structure. Title it "목차" or "발표 순서".
- **The last slide is "closing"** — two or three things to remember and one closing line.
  Only in decks of more than six slides.
- For the other slides pick the layout that fits what will be said. A list of items is
  "bullets"; two things side by side or six or more items, "two-column"; a passage to leave
  as one sentence, "quote". At most 2 quote slides in all. **A slide that stands the talk's
  one-line conclusion up large is "statement"** — what the presenter means to say, not
  someone else's words; at most 1 in all.
- **Procedures, stages or processes in order but without dates are "steps"** (3~5 stages,
  numbered across). Like 접수→심사→선정, 조사→설계→구현→평가. With dates, "timeline".
- **Three or four items of equal rank, each with a name and a line or two of description,
  are "cards"** — fields, strategies, roles, options seen side by side. bands is a vertical
  list with the label at the head of each row; cards stands boxes side by side. If the order
  carries meaning, it is steps.
- **If the request has only one figure and that figure is the heart of the talk,
  "big-number"** — the one number large, its meaning in one line. Two or three, "metrics".
- **Make a slide with a label on the left and its content on the right "bands".** Content
  where each item has a name — 미션·배경·추진전략, 대상·기간·방식·수료, 학점·증명·연계.
  Such slides are usually titled "~은 무엇인가", "~ 개요", "~ 체계", "혜택". Written as
  bullets, the name becomes the first word of a sentence and stops being a name.
- **"tiles" only when the request gives a set of initials or numbers** (4대 분야,
  P·H·A·S·E). Markers for a set that does not exist become meaningless letters like
  「V·C·U」.
- **"timeline" only when the request has points in time or the order of a procedure.**
  History, schedule, procedure. Content with no points in time laid out as a timeline makes
  the slide invent years. Even for a procedure, a sequence of commands is better as bullets.
- **A slide that conveys facts — contacts, when it applies, how to apply — is "bullets".**
  As a quote, a slogan is left where the extension number should be. **Quiz and exercise
  slides are "bullets" too** — write the questions themselves as items. Things to draw as a
  picture — state transitions, structures, flows — are bands or bullets, not chart (chart only
  draws numeric series).
- The patterns below can also be used as layouts. **When a slide's content fits a pattern's
  use, choose that pattern instead of bullets** — a deck of nothing but bullet lists is
  monotonous. pros-cons for pros and cons, swot for strengths·weaknesses·opportunities·threats,
  glossary for explaining terms, faq for questions and answers, process for a procedure flow,
  roadmap for a plan by period, and so on. Numeric patterns (kpi-grid, stat-bars,
  number-compare, chart-*) only when the request has those figures.
  - cards-2: two things side by side, large — 두 방안, 두 축, 두 대상.
  - cards-3: three items of equal rank — 세 전략, 세 원칙, 세 기능.
  - feature-grid: 4~6 features or traits in a grid — 제품 기능, 서비스 구성.
  - team: people and roles — 팀원 소개, 역할 분담, 담당자.
  - checklist: things to check, things to bring, requirements — 점검 목록.
  - numbered: items with a rank or order — 우선순위, 상위 N개, 요점 번호.
  - faq: expected questions and answers — Q&A, 자주 묻는 질문.
  - glossary: terms and definitions — 개념 정리, 약어 풀이.
  - takeaways: two or three things to remember — 결론 직전의 요약, 시사점.
  - objectives: goals and outcome indicators — what this project sets out to achieve.
  - references: list of sources — the talk's references and data sources. Near the last slide.
  - process: a processing flow from input to output — 데이터 흐름, 업무 절차.
  - chevron: progress through stages with short names — 추진 단계, 성숙도 단계.
  - roadmap: a plan by period, laid across — 1~4주, 분기별, 학기 일정.
  - milestones: key points in time running down the slide — 연혁, 진행 이정표.
  - pyramid: a hierarchy that narrows toward the top — 욕구 단계, 우선순위 층.
  - funnel: a quantity that shrinks at each stage — 전환 깔때기, 선별 과정.
  - cycle: a repetition whose end returns to the start — PDCA, 개선 주기.
  - swot: analysis of strengths, weaknesses, opportunities and threats.
  - matrix: four cells split by two axes — 중요도×긴급도, 비용×효과.
  - compare-2: two alternatives matched item by item — A안 대 B안, 기존 대 제안.
  - compare-3: comparing three alternatives — 세 기술, 세 정책안.
  - pros-cons: the pros and cons of one subject.
  - before-after: before and after a change — 개선 전후, 도입 전후.
  - problem-solution: problems paired with their solutions.
  - myth-fact: common misconceptions and the facts — 통념 바로잡기.
  - do-dont: guidelines — what to do and what not to do.
  - three-column: content split three ways — 세 영역, 세 이해관계자.
  - kpi-grid: three or four numbers at a glance — 현황 지표.
  - stat-bars: ratios or attainment rates as bars — 퍼센트 지표.
  - number-compare: one indicator's two numbers, before and after — 14분 → 3분.
  - chart-pie: shares of a whole — 합이 100%인 비중.
  - chart-donut: shares with the total in the centre.
  - chart-hbar: comparing the sizes of categories with long names — 순위.
  - chart-stacked: a comparison where each category stacks several parts.
  - question: a question put to the audience — 문제 제기, 토론 질문.
  - definition: the definition of one key concept — the title is the term.
  - hypothesis: one sentence of a research hypothesis or claim.
- If neither a pattern nor a basic layout fits, it is "bullets". Do not use the same pattern
  twice in one deck, and do not force a pattern onto a slide whose use is unclear.
- **In a deck of more than ten slides, put one "section" slide where the story turns.** It is
  a divider that only names the group that follows. Put the order in number, like "01.", and
  title it with the group's name. Write no content — a divider that lists items is not a
  divider but an agenda. Do not add one to a short deck.
- **Do not make slides that frighten or rush.** Slides like "기회 손실", "마감 임박",
  "지금 결정하지 않으면" are made to fill space when there is no content. State the facts the
  audience needs to know and leave the judgement to them.
- **Put on each slide only what that slide says.** A slide that restates the previous one in
  other words is not a slide but blank space.
- **Do not use the same layout on three slides in a row; bullets at most two in a row.** Use
  at least four different layouts among the slides other than the cover, agenda, dividers and
  closing. A deck where six of eight slides are bullets does not feel like it moves on — give
  content a shape: named content as bands or cards, procedures as steps, comparisons as table,
  conclusions as statement.
{theme_rule}- Each slide title is a short phrase naming what that slide will say. Read in
  order, they must make one talk.
- Write no content. Only titles and layouts.
{ask_rule}
- If the reference material includes a presentation template or form document, follow that
  document's slide order exactly. Follow its slide count too; do not change it into a generic
  talk structure.
- Write title, subtitle, subject and slide titles in Korean unless the request itself is
  written in English.

Answer with a JSON object only. In "subject" write what this talk is about **in the request's
own words** — if the request only names the use (중간발표, 학회 발표, 신청 발표) and not what
it is about, an empty string.
Example:
{{"title": "전이학습의 소량 데이터 효율성",
  "subtitle": "의료 영상 연구자를 위한 30분 개요",
  "subject": "전이학습",
  {theme_example}"slides": [{{"title": "전이학습의 소량 데이터 효율성", "layout": "title"}},
             {{"title": "왜 데이터가 부족한가", "layout": "bullets"}},
             {{"title": "사전학습과 미세조정 비교", "layout": "table"}}]}}

Request: {request}"""

_DRAFT_PROMPT = """Write the whole deck in one pass, following the structure below. One JSON
object per slide.

Structure (this order, these titles, these layouts):
{outline}

{facts}

Content fields of a slide — fill only the one for its layout:
- bullets: "bullets": ["항목", ...], {count} items. Each item one line within 40 characters,
  no full stop. **Not items that repeat the title (「requirements.txt 활용 방법」) but the
  facts, commands and rules the slide actually says.** Write commands and file names as they
  are (`python -m venv .venv`).
- two-column: "bullets": [...], {count_two} items. The first half is the left column, the
  second half the right.
- table: "rows": [["기준", "A", "B"], ["행", "값", "값"], ...]; the first row is the header.
  3~5 rows, 2~4 columns. Keep cells short (within 15 characters).
- timeline: "timeline": [["시점 또는 단계", "일"], ...], 3~6 entries. Each event one line.
  **Only points in time and stages the request has** — if the request has only one date, it
  is not a timeline but bullets. Do not fill cells with procedures the request lacks, like
  「매주 월요일 제출」「분기별 점검」.
- bands: "bands": [["이름", "내용"], ...], 3~4 entries. The name one or two words, the
  content one line.
- steps: "steps": [["단계", "내용"], ...], 3~5 entries. The stage a one-word name (no number),
  the content one line. Only procedures the request has.
- cards: "cards": [["이름", "내용"], ...], 3~4 entries. The name one word, the content one or
  two sentences within 80 characters. Only items of equal rank.
- statement: "title": the key phrase (within 12 characters), "body": one sentence unpacking it
  (within 60 characters).
- big-number: "metrics": one [["값", "이름"]], "body": one line on what the number means. The
  value only from the usable figures.
- closing: "bullets": 2~3 things to remember (each within 30 characters), "body": one closing
  line (「질문을 환영합니다」). Only what earlier slides said.
- agenda: no content, only "notes" — the agenda is filled from the structure.
- tiles: "tiles": [["표식", "이름"], ...], 3~6 entries. The marker one or two characters of
  initials or numbers. **If the request has no such set, do not use this layout; change it to
  bullets.**
- metrics: "metrics": [["값", "이름"], ...], 2~4 entries. **Values only from the usable
  figures.** If there are none, do not use this layout; change it to bullets.
- chart: "chart": {{"kind": "bar"|"line", "unit": "단위", "categories": [...],
  "series": [{{"name": "이름", "values": [...]}}]}}. Values only from the usable figures. If
  there are none, use bullets.
- quote: "body": "한 문장" (within 60 characters). If there is no sentence worth keeping, use
  bullets. **Only a sentence from the request or a one-sentence summary in the presenter's own
  voice.** Do not invent other people's words, such as remarks by staff, customers or experts or
  「직원들의 목소리」 — material that quotes people who are not in it is lying.
- Pattern layouts: fill only one, by shape — 「items」 `[[이름, 내용], ...]` (list, grid, flow
  and quadrant families), 「columns」 `[{{"title": 이름, "items": [...]}},
  ...]`(compare-2·compare-3·pros-cons·before-after·problem-solution·myth-fact·do-dont·three-column),
  「metrics」 (kpi-grid·stat-bars·number-compare),
  「chart」 (chart-pie·chart-donut·chart-hbar·chart-stacked, kind as named),
  「body」 (question·definition·hypothesis). Counts fit the use given at the structure stage;
  keep each cell short. swot keeps its four cells 강점·약점·기회·위협 as they are; pros-cons has
  the two columns 「장점」「단점」.
- title, section: no content, only "notes".

Every slide has "notes": 3~5 sentences the presenter **will actually say** on that slide. Not
sentences that introduce the slide, like 「이 장에서는 ~를 설명합니다」, but the very words
said to the audience. Explain a concept with one example; if there is a command, say what it
does.

Rules:
- Each slide says only its own thing. Do not repeat an earlier slide in other words.
- **Names from the material stay as in the original.** Item names, column headers and
  identifiers in attached data (workload names like nginx and redis, product names, code
  names) are not translated or called by another name. It is 「nginx」, not 「웹 서버」.
- **Figures only from the usable figures and values computed from them.** Do not make up
  figures that are not there (costs, percentages, seconds, headcounts). Do not make slides
  that frighten or rush.
- Do not mix English words into Korean sentences, and do not use Chinese characters.
- The structure's layouts are suggestions. **If the content is not that shape, change it** —
  do not write naming rules as a timeline or a summary as metrics. The default is bullets;
  two or three things compared on the same criteria are table; items that each carry a name
  are bands (vertical) or cards (side-by-side boxes); a procedure without dates is steps;
  timeline only when the request has a time order; quote only when there is a sentence to keep
  (1 in all); a one-line conclusion is statement; metrics, chart and big-number only when the
  usable figures have values; tiles only when the request gives a set of initials.
  Do not change titles or order. Do not change the layouts of the cover, agenda, dividers and
  closing.
- On a slide that teaches rules or commands, write the commands themselves as items:
  `python -m venv .venv`, `pip freeze > requirements.txt`, `conda env create -f
  environment.yml`. **Write only commands and options you are sure of.** A half-remembered
  flag (`--with-env`, `-s /archive`) gets typed in by a new student as is and fails. If unsure,
  write only the command name and leave the options as 「(문서 참고)」. Do not teach wrong
  practices (committing `.venv` to git).
- If the request gives only the **name** of a rule and not its content (「이름 규칙」,
  「requirements 고정」), do not invent the content. Write what has to be decided — like
  「환경 이름: 프로젝트명-연도 꼴로 통일(연구실에서 정함)」. Details like 「4~12자」 or
  「밑줄 불가」 do not exist unless the request has them.
- Do not invent names, dates, amounts or figures absent from the request and material — no
  ratios, latencies or counts that are not there. Write the slide so it holds without them
  (「일정은 확정 후 공유」); a bracket only for what the presenter fills in themselves
  (「[발표자]」).
- No quiz, O/X or exercise slide unless the request asks for one. Every slide carries body
  text, the closing or takeaway slide too.
- Do not use emoji (1️⃣ ✅ 🚀). If numbering is needed, use timeline or 「1.」.
- Do not fill slides with topics the request lacks. If the structure has such a title, narrow
  it to what the request said.
- Write the slide text and notes in Korean unless the request itself is written in English.

Answer with a JSON object only: {{"slides": [{{"title": "...", "layout": "...", ...}}, ...]}}

Original request: {request}{tail}"""


#: 있는 자료로 진행 with no subject: form with blanks (see `report._FRAME_RULE`).
_FRAME_RULE = (
    "**This deck writes only the frame, with no material.** Do not invent any research, "
    "project, result, figure or name the request does not give. Fill each slide only with the "
    "names of the items that belong on it and bracketed blanks like 「[연구 질문]」, and in the "
    "notes say in one or two sentences what that slide should say. For metrics, chart and "
    "table, only headers and empty cells."
)

#: `_FRAME_RULE` repeated at the draft prompt's end with an example.
_FRAME_TAIL = (
    "\n\n" + _FRAME_RULE + '\nExample: {"title": "연구 질문", "layout": "bullets", '
    '"bullets": ["[첫째 연구 질문]", "[둘째 연구 질문]", '
    '"[왜 이 질문인가]"], "notes": "이 장에서는 연구 질문을 하나씩 읽고 '
    '왜 지금 이 질문인지 한 문장으로 말한다."}'
)


def _facts_line(request: str) -> str:
    """Prompt line of numbers in the request (see `report._facts_line`)."""
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
            "Usable figures: (the request has no figures — do not use metrics or chart, and do "
            "not make up numbers)"
        )
    line = (
        "Usable figures (every one in the request): "
        + ", ".join(found[:30])
        + " — facts the user wrote down, so each must appear on some slide at least once. "
        "Do not leave any out."
    )
    # implied arithmetic (`report.derived_values`)
    from app.services.report import derived_values

    if derived := derived_values(request):
        line += (
            "\nComputed values (copy each expression as is and do not recompute it; use it "
            "only on the one slide that needs it): "
            + "; ".join(derived)
        )
    return line


_CLAIM = re.compile(
    r"\d[\d,.]*\s*(?:억|만|천)?\s*(?:원|%|퍼센트|시간|분|초|일|주|개월|년|회|건|명|대|장|석)?"
)

#: Preference among layouts carrying the same facts; higher survives.
_SHAPE_RANK = {"table": 3, "bands": 2, "two-column": 1, "bullets": 1, "metrics": 0}


def _claims(row: dict[str, Any]) -> set[str]:
    """Figures a slide asserts; bare years excluded."""
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
    """Content words cut to two syllables (「채택」 ≈ 「채택합니다」)."""
    text = json.dumps(
        {k: v for k, v in row.items() if k not in ("notes", "layout", "title")}, ensure_ascii=False
    )
    return {
        w[:2] if len(w) > 2 and re.match(r"[가-힣]", w) else w
        for w in _WORD.findall(text)
        if w not in _STOP
    }


#: Retelling threshold (measured: retellings 0.56–0.67, distinct 0.36–0.46).
_RETOLD_SHARE = 0.55
_RETOLD_WORDS = 15


def _retold(slides: list[dict[str, Any]], drafted: dict[int, dict[str, Any]]) -> set[int]:
    """Indices of slides retelling earlier ones (earlier dropped if the newcomer ranks higher)."""
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
    """Korean-unit amounts as plain digits (「6.1만」→ 61000, 「4만 2천」→ 42000)."""
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
    """Every digit run in the request."""
    digits = {re.sub(r"[^\d.]", "", m) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", request)}
    return digits | _expanded_amounts(request)


#: Words marking a clause as instruction, not fact.
_ASK_WORDS = re.compile(
    r"(?:줘|주세요|해라|하라|만들|써\s|쓰고|바꿔|줄여|줄이|늘려|넣어|빼|합쳐|합치|병합|나눠|맞춰|맞춘)"
)
#: Counts about document shape (「6장」) or time, not subject facts.
_WHEN_UNIT = re.compile(
    r"(?:분|초|시간|개월|주|일|년|월|회|장|절|슬라이드|페이지|쪽|문장|개의?\s*장)\s*$"
)
#: Decimal point/thousands comma don't end a clause; table bars do.
_CLAUSE_BREAK = re.compile(
    r"(?<!\d)[,.]|[,.](?!\d)|[，、。!?;:()（）\[\]「」·|]|\s(?:그리고|또한|및)\s"
)


def _slide_words(slide: dict) -> str:
    """Every word a slide shows or says."""
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
    """Comma-to-comma clause around `text[start:end]`, bullet-length."""
    left = max((m.end() for m in _CLAUSE_BREAK.finditer(text, 0, start)), default=0)
    right_match = _CLAUSE_BREAK.search(text, end)
    right = right_match.start() if right_match else len(text)
    clause = text[left:right].strip(" -–—:")
    if len(clause) > 48:
        clause = text[start:right].strip(" -–—:")
    return clause.strip()


def required_totals(request: str) -> list[tuple[str, str]]:
    """`(line, result digits)` for each explicitly requested total (e.g. 36-month TCO)."""
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
    """`(part, sub-items)` for parts described as comma lists of short items; else `[]`."""
    if not enumerated_items(request):
        return []
    # compiled here: `_ENUM_MARK` defined later
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
    """Adds missing listed sub-items to their part's slide; returns changed indices."""
    changed: list[int] = []
    squeeze = lambda t: re.sub(r"\s+", "", t).lower()  # noqa: E731
    for name, pieces in listed_items(request):
        index = next(
            (i for i, s in enumerate(slides) if _covers(str(s.get("title") or ""), name)), None
        )
        if index is None:
            continue
        slide = slides[index]
        # shown text only: items in notes are unseen
        shown = squeeze(_slide_words({**slide, "notes": ""}))
        # missing only if none of the item's words appear (rephrasing allowed)
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


#: Farewell as closing title (「수고하셨습니다」): title names the slide, farewell goes to body.
_FAREWELL_TITLE = re.compile(
    r"^\s*(안녕히\s*(?:가십시오|가세요|계세요|계십시오)|수고\s*하셨습니다|수고\s*많으셨습니다|"
    r"끝(?:입니다)?|이상입니다|the\s+end|good\s*bye)\s*[.!]*\s*$",
    re.I,
)


def closing_title(title: str) -> str:
    """Closing title with a bare farewell replaced by 「마무리」."""
    return "마무리" if _FAREWELL_TITLE.match(title or "") or not title.strip() else title


#: Boundary between request and planner's own conditions.
_ADDED_CONDITIONS = "\n\n덧붙인 조건:\n"


#: User's wording for empty cells (「측정 안 함」).
_EMPTY_CELL_WORD = re.compile(
    r"(?:비어\s{0,8}있는|빈|없는|누락된?|결측)\s{0,8}(?:값|칸|셀|항목|데이터)?[은는이가]?\s{0,8}"
    r"[「\"\u201c']?([^」\"\u201d'\n]{1,12}?)[」\"\u201d']?\s{0,8}(?:으로|로)"
)


def _csv_tables(material: list[str]) -> list[list[list[str]]]:
    """Tab/comma tables in the material (CSV arrives tab-joined): header, 3+ rows, uniform width."""
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
    """Restores CSV categories a CSV-built table skipped; returns changed indices."""
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
            # table column → CSV column, learned from matched rows
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
            # CSV order
            order = {k: i for i, k in enumerate(categories)}
            rows[1:] = sorted(
                rows[1:], key=lambda r: order.get(str(r[0]).strip().lower(), len(order))
            )
            changed.append(index)
            break
    return changed


def restate_missing_facts(slides: list[dict], request: str) -> list[int]:
    """Adds stated-but-missing quantities to the most related slide; returns changed indices."""
    # planner conditions and pasted material are not facts: instruction figures only
    from app.services.context import instruction_part

    text = " ".join(instruction_part(request or "").split(_ADDED_CONDITIONS)[0].split())
    # dates/durations describe the occasion, not the subject
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
    # explicitly requested totals go onto the cost slide if missing
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
        # one clause covers all its numbers (no duplicate line)
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
    """Speaker notes as bullets when the number guard emptied a chart/metrics slide."""
    text = " ".join(str(n) for n in notes) if isinstance(notes, list) else str(notes or "")
    stops = r"(?<=[.!?。])" + units.NO_NUMBER_DOT_BREAK + r"\s+|(?<=다\.)\s*"
    parts = [p.strip() for p in re.split(stops, text) if p.strip()]
    return [p.rstrip(".。")[:60] for p in parts[:limit] if len(p) >= 4]


#: Ungiven counted amount (「95건」); unit ends the word (not 「개선」, 「9/11」).
_COUNTED = re.compile(
    r"(?<![\d./])\d[\d,]*(?:\.\d+)?\s*(?:만|억|천)?\s*(?:건|명|원|대|회|개|곳|석)(?![가-힣A-Za-z])"
)


def dedupe_rows(rows: list[list[str]]) -> list[list[str]]:
    """Drops later table rows repeating an earlier row (same numbers + 2 shared words)."""
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
    """Strips ungiven counted amounts from cells (header kept); with `given`, unit must match."""
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
            # per item: a bare word goes with its amount
            items = re.split(r"\s*(;|,(?!\d))\s*", text)
            kept_items, bare = [], False
            for item in items[::2]:
                left = " ".join(_COUNTED.sub(keep, item).split())
                if left != " ".join(item.split()) and len(left.split()) < 2:
                    bare = True
                    continue
                if left:
                    kept_items.append(left)
            if bare:
                cleaned = ", ".join(kept_items)
            cells.append(cleaned or ("—" if text.strip() else text))
        out.append(cells)
    return out


def _numbers_come_from(values: list[str], facts: set[str]) -> bool:
    """Whether every digit run in `values` is in `facts`; digitless passes."""
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
    """Whether `text` has a number+unit absent from the request (「11개소」 vs 「11월」)."""
    compact = re.sub(r"\s+", "", request)
    for m in _QUANTITY.finditer(text):
        token = re.sub(r"\s+", "", m.group(0))
        if re.fullmatch(r"\d{4}년", token) or token in compact:
            continue
        # 「2,400만원」 vs 「2,400만 원」
        if token.replace(",", "") in compact.replace(",", ""):
            continue
        return True
    return False


def _moment_in_request(moment: str, request: str) -> bool:
    """Whether a timeline moment is in the request (verbatim or digits)."""
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
    """`{index: data}` matched by position then title; missing indices filled per slide."""
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
        # copied bullets: left to per-slide pass
        bullets = [str(b).strip() for b in (row.get("bullets") or []) if str(b).strip()]
        if bullets and any(bullets == earlier for earlier in seen_bullets):
            continue
        if bullets:
            seen_bullets.append(bullets)
        # unrequested numbers in metrics/chart → bullets
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
        # drop unrequested-quantity bullets when ≥ 2 remain
        if request_text and isinstance(row.get("bullets"), list):
            sure_bullets = [
                b for b in row["bullets"] if not _unrequested_quantity(str(b), request_text)
            ]
            if 2 <= len(sure_bullets) < len(row["bullets"]):
                row = {**row, "bullets": sure_bullets}
        # < 2 requested moments → bullets
        if isinstance(row.get("timeline"), list):
            steps = [t for t in row["timeline"] if isinstance(t, list) and t]
            sure = [t for t in steps if _moment_in_request(str(t[0]), request_text)]
            if len(sure) < 2:
                row = {k: v for k, v in row.items() if k != "timeline"}
                row["bullets"] = [f"{t[0]} – {t[1]}" if len(t) > 1 else str(t[0]) for t in steps]
                row["layout"] = "bullets"
            elif len(sure) < len(steps):
                row = {**row, "timeline": sure}
        # dropped/missing chart → material's own table, if any
        if slide["layout"] == "chart" and not _clean_chart(row.get("chart")):
            drawn = chart_from_material(str(slide.get("title") or ""), [request_text])
            if drawn:
                row = {**row, "layout": "chart", "chart": drawn}
        # draft may change a non-structural layout
        wanted = str(row.get("layout") or "")
        if (
            wanted
            and slide["layout"] not in _STRUCTURAL
            and wanted in _LAYOUTS
            and wanted not in _STRUCTURAL
        ):
            slide["layout"] = wanted
        if slide["layout"] not in _STRUCTURAL and not any(row.get(k) for k in _DRAFT_CONTENT):
            # emptied by validation: notes as bullets (second pass may also fail)
            if slide["layout"] in ("chart", "metrics", "big-number"):
                slide["layout"] = "bullets"
            if bullets := _bullets_from_notes(row.get("notes")):
                row = {**row, "layout": "bullets", "bullets": bullets}
                out[index] = row
            continue
        if slide["layout"] in _STRUCTURAL or any(row.get(k) for k in _DRAFT_CONTENT):
            out[index] = row
    return out


#: Per-slide prompt by layout; default `_BULLETS_PROMPT`.
_PROMPTS: dict[str, str] = {}

_BULLETS_PROMPT = """You are writing only the "{heading}" slide of the deck below.

Full structure:
{outline}

Already said on earlier slides:
{written}

Rules:
- bullets: {count} items. Each item one line, within 40 characters. Do not end with punctuation.
- A slide is a screen to look at, not text to read. Do not put paragraphs on it.
- notes is what to say when explaining this slide aloud. 2~3 sentences.
- Do not repeat what earlier slides said.
- Do not write made-up figures. Without a basis, write without numbers.
- Item names and identifiers from the material (workload names, product names, column headers)
  stay as in the original. Do not translate or paraphrase them.
- Write in Korean unless the request itself is written in English.

Answer with a JSON object only.
Example: {{"bullets": ["학습 데이터 확보 비용", "라벨링 품질 편차"], "notes": "여기서는 ..."}}

Original request: {request}"""

_TABLE_PROMPT = """You are writing only the "{heading}" slide of the deck below.
This slide is one table that sets values side by side for comparison.

Full structure:
{outline}

Already said on earlier slides:
{written}

Rules:
- rows are the table's rows. **The first row is the header**; the rest are values.
- **2~4 columns, 3~6 rows including the header.** This table is shown on a screen. Any
  larger and it cannot be read from the back rows.
- One cell **within 12 characters**. Do not put sentences in — a table is a place to compare,
  not text to read. If explanation is needed, put it in notes.
- The first column is the **criterion** being compared; the other columns are the values for
  that criterion.
- Do not write made-up figures. Without a basis, write without numbers.
- Item names and identifiers from the material (workload names, product names, column headers)
  stay as in the original. Do not translate or paraphrase them.
- notes is what to say when explaining this table aloud. 2~3 sentences.
- Write in Korean unless the request itself is written in English.

Answer with a JSON object only.
Example: {{"rows": [["기준", "대안 A", "대안 B"],
              ["초기 비용", "0원", "약 3억"],
              ["도입 기간", "2주", "4개월"]],
      "notes": "여기서는 ..."}}

Original request: {request}"""

_CHART_PROMPT = """You are writing only the "{heading}" slide of the deck below.
This slide shows figures as a chart.

Full structure:
{outline}

Already said on earlier slides:
{written}

Rules:
- chart.kind is "bar" or "line". bar for comparing sizes by item, line for a trend over
  time.
- chart.categories are the horizontal-axis items. **3~8.** Names within 8 characters.
- chart.series is the list of series. **1~2.** Each series' values must have **the same count
  as categories.**
- chart.unit is the vertical axis's unit in one word — `건`, `%`, `억 원`. An empty string if
  none.
- **Do not write made-up figures. Without a basis, do not write this
  slide; answer with bullets.** A chart reads as even more factual than numbers.
- notes say what this chart shows. 2~3 sentences.
- Write in Korean unless the request itself is written in English.

Answer with a JSON object only.
Example: {{"chart": {{"kind": "bar", "unit": "건",
                "categories": ["1분기", "2분기", "3분기", "4분기"],
                "series": [{{"name": "처리 건수", "values": [120, 210, 380, 460]}}]}},
      "notes": "여기서는 ..."}}

Original request: {request}"""

_METRICS_PROMPT = """You are writing only the "{heading}" slide of the deck below.
This slide displays two or three numbers large.

Full structure:
{outline}

Already said on earlier slides:
{written}

Rules:
- metrics is a list of `[값, 이름]`. **2~4.**
- The value is **short** — `32%`, `1.4초`, `3억 원`. Do not put sentences in.
- The name says in one word what the number is. **Within 10 characters.**
- Do not write made-up figures. **Without a basis, do not write this
  slide; answer with bullets.** A number displayed large on screen reads as more factual
  than anything else.
- notes say where these numbers come from and what they mean. 2~3 sentences.
- Write in Korean unless the request itself is written in English.

Answer with a JSON object only.
Example: {{"metrics": [["32%", "오탐 감소"], ["1.4초", "평균 응답"]],
      "notes": "여기서는 ..."}}

Original request: {request}"""

_QUOTE_PROMPT = """You are writing only the "{heading}" slide of the deck below.
This slide displays only one sentence, large.

Full structure:
{outline}

Already said on earlier slides:
{written}

Rules:
- body is one sentence. Within 60 characters. The line from this talk most worth keeping.
- Do not quote real people. Write the claim this talk makes.
- notes is what to say when explaining this slide aloud. 2~3 sentences.
- Write in Korean unless the request itself is written in English.

Answer with a JSON object only.
Example: {{"body": "데이터가 아니라 전이가 병목이다", "notes": "여기서는 ..."}}

Original request: {request}"""


#: 429 backoff (s) without a reset hint; ~70 s total, past a TPM window.
_BACKOFF = (2.0, 6.0, 12.0, 20.0, 30.0)


async def _complete(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int,
) -> tuple[str, dict]:
    """One non-streaming call → `(text, usage)`; retries 429."""
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
                    # off where allowed; `thinking.switch` learns where not
                    **thinking.switch(model),
                },
            )
            if thinking.refused(model, response):
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": model, "messages": messages, "max_tokens": max_tokens,
                        **thinking.switch(model),
                    },
                )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            # TPM 429 names its reset time
            delay = ratelimit.retry_delay(response.text, dict(response.headers), _BACKOFF[attempt])
            log.info("deck call rate limited, retrying in %.0fs", delay)
            await asyncio.sleep(delay)
        response.raise_for_status()
        payload = response.json()

    # reasoning may eat the whole budget (`services/thinking.py`)
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
                # gateway rejects `reasoning`: retry with ceiling only
                again = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": messages, "max_tokens": bigger},
                )
        if again.status_code == 200:
            retried = again.json()
            spent = retried.get("usage") or {}
            first = payload.get("usage") or {}
            # both calls charged and counted
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
    """First JSON object or `{}`, strings read back to Hangul (on parsed values, not raw JSON)."""
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
    """Truncated object closed after its last complete value; `None` when hopeless."""
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
    """Markdown pipe table from a reply without `rows`."""
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
    """Table rows in any given form: `rows`, `table`, objects, or pipe text."""
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
    """Same structure with strings read back to Hangul."""
    if isinstance(value, str):
        return hangul.tidy_spacing(hangul.read_back(value)[0])
    if isinstance(value, list):
        return [_read_back_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _read_back_values(item) for key, item in value.items()}
    return value


def requested_slides(request: str) -> int | None:
    """Explicit total; may be under the default five."""
    return plan_rules.requested_count(
        request, ("슬라이드", "페이지", "장", "쪽"), maximum=_MAX_SLIDES
    )


def slide_range(request: str) -> tuple[int, int] | None:
    """Stated slide range (「8~10장」) when no exact total."""
    if requested_slides(request):
        return None
    return plan_rules.requested_range(
        request, ("슬라이드", "페이지", "장", "쪽"), maximum=_MAX_SLIDES
    )


def slides_for_minutes(request: str) -> int | None:
    """Fewest slides for the stated talk length (~1 per 2 min, capped); `None` if unstated."""
    match = re.search(r"(\d{1,3})\s*분(?!기|류|석|산|리|야|할|량|배|담|위|과)", request)
    if not match:
        return None
    minutes = int(match.group(1))
    if minutes < 4:
        return None
    return max(_MIN_SLIDES, min(minutes // 2, _DEFAULT_MAX))


def _theme_style(text: str) -> str:
    """Outline's `style` or `""` (regex: survives salvage)."""
    match = re.search(r'"style"\s*:\s*"([^"]+)"', text)
    return _STYLES.get((match.group(1).strip() if match else ""), "")


def _theme_accent(text: str, default: str = _ACCENT) -> str:
    """Outline's accent or `default` (regex: survives salvage)."""
    match = re.search(r'"theme"\s*:\s*"([^"]+)"', text)
    return _THEMES.get((match.group(1).strip() if match else ""), default)


_LEAKED_PLANNING = re.compile(
    r"^(?:slides?|first slide|layout|note|rule)s?\s*[:(]|\(integrating\b|\bis fine\)|"
    r"^\"?(?:chart|metrics|bullets|table)\"?\s+(?:and|only|if)\b|\d+\s*to\s*\d+\.|"
    r"^(?:slide|장|부분)\s*\d+\s*[:.]",
    re.I,
)


def sane_outline(plan: list[dict], request: str) -> bool:
    """Rejects leaked reasoning, repeated sections, or English plans for Korean requests."""
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
    """`(title, subtitle, plan)` of `{title, layout}`; salvages truncated JSON and plain lists."""
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
        # truncated JSON: objects before the cut intact
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
            # document title precedes the slide array
            title = plan[0]["title"]

    if not plan:
        # plain bullet/numbered lines
        for line in text.splitlines():
            if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line):
                heading = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line).strip(" #").strip()
                if heading:
                    plan.append({"title": heading, "layout": "bullets"})

    plan = plan[:_MAX_SLIDES]
    if plan:
        # export and preview expect slide one as cover
        plan[0]["layout"] = "title"
        if not title:
            title = plan[0]["title"]
        else:
            plan[0]["title"] = title
    return title, subtitle, plan


_PAIRED_PROMPT = """You are writing only the "{heading}" slide of the deck below.
{what}

Full structure:
{outline}

Already said on earlier slides:
{written}

Rules:
{rules}
- Do not write made-up content. If there is nothing to write, answer this slide as bullets.
- notes is what the presenter will say on this slide. 2~3 sentences.
- Write in Korean unless the request itself is written in English.

Answer with a JSON object only.
Example: {example}

Original request: {request}"""

#: Per paired layout: purpose, rules, example.
_PAIRED_RULES = {
    "bands": (
        "This slide lays out bands: a label on the left, its content on the right.",
        "- bands is a list of `[이름, 내용]`. **3~4.**\n"
        "- The name is one word saying what the row is. **Within 10 characters** — like 미션,"
        " 배경, 추진전략, 기대효과.\n"
        "- The content is one sentence. **Within 90 characters.** Do not put in two sentences.\n"
        "- The names must be distinct, not overlapping.",
        '{{"bands": [["미션", "선제적으로 AI 신기술에 대응하여 차세대 인재를 양성한다"],'
        ' ["배경", "AI 전환기에 기술수요와 인재 격차가 벌어지고 있다"]],'
        ' "notes": "여기서는 ..."}}',
    ),
    "tiles": (
        "This slide stands letters or numbers up large and puts a name beneath each.",
        "- tiles is a list of `[글자, 이름]`. **3~6.**\n"
        "- The letter is **within 4 characters** — a marker taken in at a glance, like P, H, A,"
        " 01, ①.\n"
        "- The marker and the name must not be the same word. `[AI] AI 기본역량` is not a"
        " marker but the same word written twice.\n"
        "- The name says what the marker is. **Within 24 characters.**\n"
        "- The markers must read as one set. This is the place to gather initials or number"
        " items, not to stand any letters up large.",
        '{{"tiles": [["P", "Physical AI"], ["H", "Human-centered AI"],'
        ' ["A", "Agentic AI"]], "notes": "여기서는 ..."}}',
    ),
    "timeline": (
        "This slide sets out points in time and what happened then, in order.",
        "- timeline is a list of `[시점, 일]`. **3~7.**\n"
        "- The point in time is **within 12 characters** — like 2024.05, 1학기, 3년차.\n"
        "- **If the material has no real dates, do not invent dates.** Use relative points"
        " like 1단계·2단계, 1~4주, 학기 초. Invented dates are plausible, so they read as"
        " fact, and someone at the talk always asks about them.\n"
        "- The event is one phrase on what happened then. **Within 60 characters.**\n"
        "- Put them in time order. Shuffled, it is not a history but a list.",
        '{{"timeline": [["2024.05", "1기 개설, 수료생 32명"],'
        ' ["2025.03", "2기 개설과 기업 연계 시작"]], "notes": "여기서는 ..."}}',
    ),
}

_PAIRED_RULES.update(
    {
        "steps": (
            "This slide lays the stages of a procedure across, in order. The position supplies"
            " the numbers.",
            "- steps is a list of `[단계, 내용]`. **3~5.**\n"
            "- The stage is that stage's name in one word. **Within 12 characters** — like 접수,"
            " 심사, 선정, 협약. Do not add numbers.\n"
            "- The content is one line on what is done at that stage. **Within 60 characters.**\n"
            "- Put them in order. Only procedures the request has — do not invent stages to fill"
            " the slots.",
            '{{"steps": [["접수", "온라인으로 신청서와 계획서를 낸다"],'
            ' ["심사", "서류와 발표로 평가한다"], ["협약", "선정 뒤 2주 안에 협약을 맺는다"]],'
            ' "notes": "여기서는 ..."}}',
        ),
        "cards": (
            "This slide lays parallel items out as named boxes.",
            "- cards is a list of `[이름, 내용]`. **3~4.**\n"
            "- The name is the box's title in one word. **Within 14 characters** — like 교육,"
            " 연구, 산학, 국제화.\n"
            "- The content is one or two sentences. **Within 80 characters.**\n"
            "- The items must be of equal rank. If the order carries meaning, it is steps; if a"
            " label must head each row, bands.",
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

_PATTERN_PROMPT = """You are writing only the "{heading}" slide of the deck below.
This slide is a 「{label}」 slide — {use}

Full structure:
{outline}

Already said on earlier slides:
{written}

Rules:
{rules}
- Do not write made-up content, figures, people or dates. If what there is to write does not
  fit this shape, answer this slide as bullets: {{"bullets": [...], "notes": "..."}}
- notes is what the presenter will say on this slide. 2~3 sentences.
- Write in Korean unless the request itself is written in English.

Answer with a JSON object only.
Example: {example_with_notes}

Original request: {request}"""

_PROMPTS.update(
    {
        pattern.name: _PATTERN_PROMPT.replace("{label}", pattern.label)
        .replace("{use}", pattern.use)
        .replace("{rules}", pattern.rules)
        .replace("{example_with_notes}", pattern.example[:-2] + ', "notes": "여기서는 ..."}}')
        for pattern in slide_patterns.PATTERNS
    }
)

_STATEMENT_PROMPT = """You are writing only the "{heading}" slide of the deck below.
This slide stands one key message of the talk up large — the presenter's conclusion, not
someone else's words.

Full structure:
{outline}

Already said on earlier slides:
{written}

Rules:
- title is the phrase to stand large. **Within 12 characters** — like 「전교생, AI 기초부터」.
- body is one sentence unpacking that phrase. **Within 60 characters.** Do not add facts that
  are not there.
- What the talk means to say, not words that frighten or rush.
- notes is what the presenter will say on this slide. 2~3 sentences.
- Write in Korean unless the request itself is written in English.

Answer with a JSON object only.
Example: {{"title": "전교생, AI 기초부터",
          "body": "학과와 상관없이 같은 출발선에서 AI 를 쓰게 한다",
      "notes": "여기서는 ..."}}

Original request: {request}"""

_BIG_NUMBER_PROMPT = """You are writing only the "{heading}" slide of the deck below.
This slide displays one number very large and adds its meaning in one line.

Full structure:
{outline}

Already said on earlier slides:
{written}

Rules:
- metrics is **one** `[값, 이름]`. Keep the value short — `32%`, `1.4초`, `3억 원`.
- body is one sentence on what that number means. **Within 60 characters.**
- Do not write made-up figures. **Without a basis, do not write this
  slide; answer with bullets.**
- notes say where this number comes from. 2~3 sentences.
- Write in Korean unless the request itself is written in English.

Answer with a JSON object only.
Example: {{"metrics": [["32%", "오탐 감소"]],
          "body": "새 규칙을 적용한 첫 달, 오탐 신고가 1/3 줄었다",
      "notes": "여기서는 ..."}}

Original request: {request}"""

_CLOSING_PROMPT = """You are writing only the "{heading}" slide of the deck below.
This slide is the last one — two or three things to remember and one closing line.

Full structure:
{outline}

Already said on earlier slides:
{written}

Rules:
- bullets are what to remember from this talk. **2~3**, each **within 30 characters.** Only
  what earlier slides actually said — do not bring up a new claim here.
- body is one closing line. **Within 30 characters** — like 「질문을 환영합니다」, 「감사합니다」.
- notes is what the presenter says while closing. 2~3 sentences.
- Write in Korean unless the request itself is written in English.

Answer with a JSON object only.
Example: {{"bullets": ["2027년부터 전교생 필수", "학과별 실습으로 이어진다"],
      "body": "질문을 환영합니다", "notes": "여기서는 ..."}}

Original request: {request}"""

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


#: a note sentence handing over to a slide that is not there
_NEXT_SLIDE = re.compile(
    r"[^.!?\n]*(?:다음\s{0,2}(?:으로|슬라이드|장)|이어서|이제\s{0,2}(?:다음|이어))[^.!?\n]*"
    r"(?:살펴보|보겠|넘어가|알아보|이어가)[^.!?\n]*[.!?]?\s*")


def end_without_next(slides: list[dict]) -> list[int]:
    """The last slide's notes lose a 「다음으로 … 살펴보겠습니다」 hand-over; returns indices."""
    if not slides or not isinstance(slides[-1].get("notes"), str):
        return []
    notes = slides[-1]["notes"]
    kept = _NEXT_SLIDE.sub("", notes).strip()
    if kept == notes.strip() or not kept:
        return []
    slides[-1]["notes"] = kept
    return [len(slides) - 1]


def mend_mixed_script(slides: list[dict], vocabulary: str) -> list[int]:
    """Mends Cyrillic slipped into Hangul (「프로мп트」); returns indices."""
    deck_words = " ".join(json.dumps(s, ensure_ascii=False) for s in slides)
    words = f"{vocabulary[:20000]} {deck_words}"

    def fix(value):
        if isinstance(value, str):
            value, kana = hangul.repair_kana(value, vocabulary[:20000])
            mended, slips = hangul.repair_mixed_script(value, words)
            slips = kana + slips
            # written 「A × B → D」 with a wrong D
            worked = units.fix_written_sums(units.fix_written_arithmetic(mended))
            return worked, bool(slips) or worked != mended
        if isinstance(value, list):
            pairs = [fix(v) for v in value]
            return [v for v, _ in pairs], any(c for _, c in pairs)
        return value, False

    changed = []
    for index, slide in enumerate(slides):
        hit = False
        for key in ("title", "body", "notes", "bullets", "items", "cards", "steps", "tiles",
                    "timeline", "bands", "columns", "rows", "metrics"):
            if key in slide:
                slide[key], did = fix(slide[key])
                hit = hit or did
        if hit:
            changed.append(index)
    return changed


_NOTE_WRAPPER = re.compile(
    r'^\s*\{\s*\\?"[a-z_]{3,30}\\?"\s*:\s*\\?"?(?P<body>.*?)\\?"?\s*\}?\s*$', re.S
)


def _mend_operands(value, settled):
    if isinstance(value, str):
        return key_figures.mend_operands(value, settled)
    if isinstance(value, list):
        return [_mend_operands(v, settled) for v in value]
    if isinstance(value, dict):
        return {k: _mend_operands(v, settled) for k, v in value.items()}
    return value


def mend_scaled_numbers(slides: list[dict], material: str) -> list[int]:
    """Restores amounts off by a power of ten from the material's; returns changed slides."""
    changed: list[int] = []
    for index, written, said in quality_gate.scaled_numbers(slides, material):

        def swap(value, _old=written, _new=said):
            if isinstance(value, str):
                return value.replace(_old, _new)
            if isinstance(value, list):
                return [swap(v) for v in value]
            if isinstance(value, dict):
                return {k: swap(v) for k, v in value.items()}
            return value

        slide = slides[index]
        slides[index] = {k: (v if k in ("image", "id") else swap(v)) for k, v in slide.items()}
        log.info("slide %d: %r put back to the material's %r", index + 1, written, said)
        changed.append(index)
    return sorted(set(changed))


_ASKS_QUIZ = re.compile(r"퀴즈|문항|확인 문제|형성평가")
_ASKS_DECISION = re.compile(r"요청 사항|요청사항|승인|의사\s?결정|결정 요청")
#: a lesson's length (not 「10분 발표」) and a lesson (not 「교육부」「교육비」)
_ASKS_MINUTES = re.compile(r"(\d{2,3})\s?분(?!\s?(?:발표|브리핑|보고|회의|스피치|피치))")
_ASKS_LESSON = re.compile(
    r"수업|강의|차시|교육\s?(?:과정|프로그램|세션)|(?<![가-힣])연수(?![원생])")
_MONEY_TITLE = re.compile(r"수익|손익|재무|매출|수익성")
_MONEY = re.compile(r"\d[\d,.]*\s?(?:만|억|조)?\s?원|\d[\d,.]*\s?(?:박스|명|건)")


def _shown(slide: dict) -> str:
    """Audience-visible content (no notes or picture)."""
    return _slide_words({k: v for k, v in slide.items() if k not in ("notes", "image")})


def requested_contents(slides: list[dict], request: str) -> dict[int, str]:
    """Slides holding a requested part only in name or notes, with what to add."""
    from app.services.context import instruction_part

    asked = instruction_part(request or "")
    titles = [str(s.get("title") or "") for s in slides]
    needs: dict[int, str] = {}

    def last_titled(pattern: re.Pattern) -> int | None:
        hits = [i for i, t in enumerate(titles) if pattern.search(t)]
        return hits[-1] if hits else None

    if _ASKS_QUIZ.search(asked):
        at = last_titled(re.compile(r"퀴즈|문항|확인 문제|형성평가|평가"))
        # question: 「?」, options, or Korean question ending
        asking = r"\?|①|②|O\s?/\s?X|(?:니까|나요|까요|인가요?|일까|는가|시오)(?=[\s.\"']|$)"
        if at is not None and not re.search(asking, _shown(slides[at])):
            needs[at] = ("This slide is a quiz. Put 2~3 real questions on the screen with options "
                         "(①~④) or O/X, and put the answers and explanations in the speaker "
                         "notes. Do not substitute a notice or a summary.")
    if _ASKS_DECISION.search(asked):
        at = last_titled(re.compile(r"요청|승인|결정|제안"))
        if at is not None and not (
            re.search(r"\d", _shown(slides[at])) and re.search(r"승인|요청|결정|배정|투입",
                                                              _shown(slides[at]))
        ):
            needs[at] = ("This slide asks for a decision. Write on the screen, with figures, what "
                         "(budget, staff, schedule) is to be approved, how much, and by when. Do "
                         "not leave it in the notes only.")
    minutes = _ASKS_MINUTES.search(asked)
    if minutes and _ASKS_LESSON.search(asked):
        total = int(minutes.group(1))
        planned = sum(int(m) for s in slides
                      for m in re.findall(r"(\d{1,2})\s?분", _shown(s)) if int(m) < total)
        if planned < 0.6 * total:
            at = next((i for i, t in enumerate(titles) if re.search(r"목차|흐름|순서|오늘", t)),
                      1 if len(slides) > 1 else 0)
            needs.setdefault(at, f"Put the time allocation by stage of the {total}-minute "
                                 f"lesson (e.g. 도입 5분·개념 15분·활동 15분·정리 10분, 합계 "
                                 f"{total}분) on this slide's screen.")
    for i, title in enumerate(titles):
        if _MONEY_TITLE.search(title) and not _MONEY.search(_shown(slides[i])):
            needs.setdefault(i, "This slide shows profitability. Write the material's "
                                "profit-and-loss results (selling price and cost, break-even "
                                "sales volume, monthly operating profit, etc.) on the screen as "
                                "figures.")
    return needs


#: Note turning into deck Markdown: heading, fence, 「발표 노트」 label.
_DECK_IN_NOTES = re.compile(r"(?:^|\s)#{1,3}\s|```|\*{1,2}발표\s?노트\*{1,2}|(?:^|\s)-{2,3}\s+#")
#: Next slides' JSON run on after a note.
_JSON_RUN_ON = re.compile(
    r'\\?"\s*[}\]]{1,4}\s*,?\s*[\[{]\s*\\?"(?:title|bullets|layout|speaker_notes|notes|body|rows|chart|slides)'
    r'\\?"\s*:|\\?"\s*,\s*\\?"(?:title|bullets|layout|speaker_notes|notes|body|rows|chart)\\?"\s*:')
#: Latin words a Korean deck may keep: names, acronyms, units, links.
_KEPT_LATIN = re.compile(r"https?://\S+|\b[A-Z0-9][A-Z0-9.&/+-]{0,9}\b|\b[A-Z][a-z]{1,20}(?:[A-Z][a-z]{1,20}){1,6}\b")


def foreign_slides(slides: list[dict], request: str) -> list[int]:
    """Slides of a Korean request written in another language (names/acronyms/links excepted)."""
    from app.services.context import instruction_part

    asked = instruction_part(request or "")
    if len(re.findall(r"[가-힣]", asked)) < 2 * len(re.findall(r"[A-Za-z]", asked)):
        return []
    out = []
    for index, slide in enumerate(slides):
        words = "\n".join(quality_gate._strings({k: v for k, v in slide.items()
                                                 if k not in ("image", "id", "layout", "accent")}))
        words = re.sub(r"```.*?```", "", words, flags=re.S)
        latin = len(re.findall(r"[A-Za-z]", _KEPT_LATIN.sub("", words)))
        if latin > 40 and latin > len(re.findall(r"[가-힣]", words)):
            out.append(index)
    return out


def strip_json_residue(slides: list[dict]) -> list[int]:
    """Strips leftover JSON wrappers from notes/body; returns changed slides."""
    changed = []
    for index, slide in enumerate(slides):
        hit = False
        for key in ("notes", "body"):
            text = slide.get(key)
            if not isinstance(text, str):
                continue
            clean = text
            if m := _NOTE_WRAPPER.match(clean):
                clean = m.group("body")
            clean = re.sub(r'\s*\\?"\s*\}\s*$', "", clean).replace('\\"', '"').strip()
            if run_on := _JSON_RUN_ON.search(clean):
                clean = clean[:run_on.start()].rstrip()
            if key == "notes" and (rest := _DECK_IN_NOTES.search(clean)):
                # whole-deck Markdown pasted after the note
                clean = clean[:rest.start()].rstrip(" -—")
            if clean != text:
                slide[key] = clean
                hit = True
        if hit:
            changed.append(index)
    return changed


#: the writer's own JSON in slide words: 「[ { "slide_id": "slide-cover」, 「{"notes": …」 (a
#: lecture's JSON example names other keys and stays)
_JSON_FRAGMENT = re.compile(
    r'(?:[\[{]\s{0,4})+\\?"(?:slide_?id|slides|title|subtitle|bullets|layout|notes|'
    r'speaker_notes|body|rows|chart|items|columns|metrics|id)\\?"\s{0,4}[:,\]}]'
    r'|\\?"slide_id\\?"')
#: a spoken sentence's end: punctuation, or a Korean final ending (「…입니다」 「…해요」)
_SPOKEN_END = re.compile(r"(?:[.!?。…]|(?<=[다요죠까]))[\"'」』”’)\]]{0,2}(?=\s|$)")


def finished_notes(notes: str) -> str:
    """Notes cut back to their last whole sentence (「… 412ms에서 38ms로」 broke off); notes
    with no sentence end at all are left as they are."""
    text = (notes or "").rstrip()
    ends = list(_SPOKEN_END.finditer(text))
    if not text or (ends and ends[-1].end() == len(text)):
        return text
    # no whole sentence at all: nothing to cut back to, kept as written
    return text[: ends[-1].end()].rstrip() if ends else text


def _leaf_clean(text: str) -> str:
    """Slide words without the writer's internal text: a JSON fragment and what follows it,
    the 「이 장을 쓰지 못했습니다」 stand-in."""
    if m := _JSON_FRAGMENT.search(text):
        text = text[: m.start()].rstrip(" \t-—:,")
    return "" if text.strip().strip("> ") == UNWRITTEN else text


def strip_internal_text(slides: list[dict]) -> list[int]:
    """Internal text the writer left on slides taken off: JSON fragments, the unwritten
    stand-in (kept only while nothing else is on the slide), broken-off notes."""
    changed: list[int] = []
    for index, slide in enumerate(slides):
        before = json.dumps(slide, ensure_ascii=False, sort_keys=True, default=str)
        body = str(slide.get("body") or "").strip()
        for key, value in list(slide.items()):
            if key in ("id", "layout", "image", "diagram", "chart", "accent", "body"):
                continue
            if isinstance(value, str):
                slide[key] = _leaf_clean(value)
            elif isinstance(value, list):
                kept = []
                for item in value:
                    if isinstance(item, str):
                        item = _leaf_clean(item)
                        if not item.strip():
                            continue
                    elif isinstance(item, list):
                        item = [_leaf_clean(c) if isinstance(c, str) else c for c in item]
                    kept.append(item)
                slide[key] = kept
        if body == UNWRITTEN:
            # the stand-in goes once anything else is on the slide
            slide.pop("body")
            if not has_content(slide):
                slide["body"] = UNWRITTEN
        elif body:
            slide["body"] = _leaf_clean(str(slide["body"])) or ""
            if not slide["body"]:
                slide.pop("body")
        if isinstance(slide.get("notes"), str):
            slide["notes"] = finished_notes(slide["notes"])
        if json.dumps(slide, ensure_ascii=False, sort_keys=True, default=str) != before:
            changed.append(index)
    return changed


def unshippable(slide: dict) -> str:
    """Why a slide must not reach the person as it is, or ''."""
    if str(slide.get("body") or "").strip() == UNWRITTEN:
        return "unwritten"
    if not has_content(slide):
        return "empty"
    words = "\n".join(quality_gate._strings({k: v for k, v in slide.items()
                                             if k not in ("image", "diagram")}))
    if _JSON_FRAGMENT.search(words) or UNWRITTEN in words:
        return "internal"
    return ""


_TITLE_LABELS = ("발표\\s{0,2}제목", "논문\\s{0,2}제목", "제목", "title")
_AUTHOR_LABELS = ("저자", "발표자", "발표\\s{0,2}자", "연구자", "authors?", "presenter")


def _given_line(request: str, labels: tuple[str, ...]) -> str:
    """Value of a 「제목: …」 line in the request, or `""`."""
    label = "|".join(labels)
    pattern = re.compile(
        rf"^[ \t]{{0,4}}[-*•]?[ \t]{{0,2}}(?:{label})[ \t]{{0,2}}[:：][ \t]{{0,2}}"
        r"(?P<v>\S[^\n]{0,200})$", re.I | re.M)
    m = pattern.search(request or "")
    return m.group("v").strip().strip("「」\"'") if m else ""


def given_cover(request: str, title: str, subtitle: str) -> tuple[str, str]:
    """`(title, subtitle)` from the request's 「제목:」 and 「저자:」, verbatim."""
    given_title = _given_line(request, _TITLE_LABELS)
    authors = _given_line(request, _AUTHOR_LABELS)
    if given_title:
        title = given_title
    if authors and authors not in (subtitle or ""):
        subtitle = authors
    return title, subtitle


def unnumbered(title: str) -> str:
    """「01. 시장 구조」 → 「시장 구조」 (dividers/agenda print their own number)."""
    bare = re.sub(r"^\s*(?:\d{1,2}|[IVX]{1,4})\s?[.)．:]\s*", "", title or "").strip()
    return bare or (title or "")


def _agenda_lines(slides: list[dict]) -> list[str]:
    """Agenda lines: dividers when ≥ 2, else body slides; at most eight."""
    names = [s["title"] for s in slides if s.get("layout") == "section"]
    if len(names) < 2:
        names = [s["title"] for s in slides if s.get("layout") not in (*_STRUCTURAL, "closing")]
    return [unnumbered(str(n)) for n in names if str(n).strip()][:8]


#: Keys describing a slide, not content.
_NOT_CONTENT = frozenset({"notes", "layout", "title", "heading", "id", "index", "n"})

#: Fields a drafted slide may carry content in.
_DRAFT_CONTENT = (
    "items",
    "columns",
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


_SENTENCE_OR_DROPPED_STOP = re.compile(
    r"(?<=[.!?。])" + units.NO_NUMBER_DOT_BREAK + r"\s+|\n+|(?<=습니다|입니다|합니다|됩니다)\s+"
)


def _salvaged_bullets(data: dict) -> list[str]:
    """Bullets from any non-metadata string/list field, whatever its name."""
    found: list[str] = []
    for key, value in data.items():
        if key.lower() in _NOT_CONTENT:
            continue
        if isinstance(value, str):
            # split on sentence ends, incl. dropped-period Korean endings
            found.extend(
                part
                for part in re.split(_SENTENCE_OR_DROPPED_STOP, value)
                if part.strip()
            )
        elif isinstance(value, list):
            found.extend(item for item in value if isinstance(item, str))
    return _clean_bullets(found)


def _clean_bullets(value: Any) -> list[str]:
    """Bullets as short single lines, however formatted."""
    items = value if isinstance(value, list) else []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item = " – ".join(str(v).strip() for v in item.values() if str(v).strip())
        elif isinstance(item, list):
            item = " – ".join(str(v).strip() for v in item if str(v).strip())
        text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", str(item)).strip()
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("`", "").strip()
        text = text.splitlines()[0].strip() if text else ""
        # table row (2+ bars) is no line; card 「제목 | 설명」 has one
        if text.count("|") >= 2:
            continue
        if text:
            out.append(_cut_at_word(text, _BULLET_HARD))
    return out[:6]


#: Bullet length past two slide rows (lint bound): rewritten.
LONG_BULLET = 45
#: Max bullet length before rewrite; cut between words.
_BULLET_HARD = 160


def _cut_at_word(text: str, limit: int) -> str:
    """`text` cut at the last space within `limit`; spaceless lines kept whole."""
    if len(text) <= limit:
        return text
    at = text.rfind(" ", 0, limit + 1)
    if at <= 0:
        return text
    return text[:at].rstrip(" ,，;:·—–-(")


#: Long-bullet cut points: comma, dash, colon.
_BULLET_CLAUSE = re.compile(r",\s|，|\s[—–-]\s|;\s|:\s")


def long_bullets(slides: list[dict[str, Any]]) -> dict[int, list[str]]:
    """`{slide index: bullets over LONG_BULLET}` for body slides."""
    out: dict[int, list[str]] = {}
    for index, slide in enumerate(slides):
        if str(slide.get("layout") or "") in (*_STRUCTURAL, "agenda"):
            continue
        if lines := [str(b) for b in slide.get("bullets") or [] if len(str(b)) > LONG_BULLET]:
            out[index] = lines
    return out


def fit_long_bullets(slides: list[dict[str, Any]]) -> list[int]:
    """Still-long bullets cut at a clause break; full line moved to notes."""
    touched = []
    for index, lines in long_bullets(slides).items():
        slide = slides[index]
        notes = str(slide.get("notes") or "")
        bullets = []
        for bullet in slide.get("bullets") or []:
            text = str(bullet)
            heads = [m.start() for m in _BULLET_CLAUSE.finditer(text)
                     if 12 <= m.start() <= LONG_BULLET]
            if text not in lines or not heads:
                bullets.append(bullet)
                continue
            bullets.append(text[: heads[-1]].rstrip(" ,，;:—–-"))
            if re.sub(r"\s", "", text) not in re.sub(r"\s", "", notes):
                notes = (notes.rstrip() + " " if notes.strip() else "") + text.rstrip(".") + "."
        if bullets != list(slide.get("bullets") or []):
            slide["bullets"] = bullets
            slide["notes"] = notes[:1200]
            touched.append(index)
    return touched


#: Slide table bounds (readable on 16:9).
_MAX_COLUMNS = 4
_MAX_ROWS = 6
_MAX_CELL = 24


_MAX_CATEGORIES = 8
_MAX_SERIES = 2


#: Figure-only layouts.
_NUMERIC_LAYOUTS = ("chart", "metrics", "big-number", *sorted(slide_patterns.NUMERIC))


def _offered_layouts(request: str, context: list[str]) -> list[str]:
    """Variety-check layouts; numeric only with figures, patterns never for variety."""
    classic = [layout for layout in _BODY_LAYOUTS if layout not in slide_patterns.BY_NAME]
    body = [layout for layout in classic if layout not in _NUMERIC_LAYOUTS]
    return classic if has_numbers(request, context) else body


#: Request about the user's own work (needs material).
_OWN_WORK = re.compile(
    r"학위논문|연구계획|과제 신청|사업 신청|신청 발표|녹취|캡스톤|산학|과제 제안|제안 발표"
)

#: Citable figure (decimal, 3+ digits non-year, number+unit); bounded quantifiers (user input).
_FIGURE = re.compile(
    r"(?<!\d)(?:"
    r"\d{1,12}[.,]\d"
    r"|\d{3,12}(?!\d)(?!\s{0,4}년)"
    r"|\d{1,12}\s{0,4}(?:%|퍼센트|원|명|건|배|억|만|천|시간|분|초|주|개월|점|개|회|위)"
    r")"
)


def has_numbers(request: str, context: list[str]) -> bool:
    """Whether request or context has a citable figure."""
    return bool(_FIGURE.search(request)) or any(_FIGURE.search(block) for block in context)


#: Title → pattern; first match wins (specific first).
_TITLE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?<![A-Za-z])swot(?![A-Za-z])|강점\s{0,3}[·,]?\s{0,3}약점", "swot"),
    (r"장단점|장점과\s{0,3}단점|장점\s{0,3}[·/]\s{0,3}단점|pros", "pros-cons"),
    (r"문제\s{0,3}(?:와|과|-|·|/|및)\s{0,3}해결|문제점과\s{0,3}(?:개선|대책)", "problem-solution"),
    (r"전후\s{0,3}비교|도입\s{0,3}전후|before|개선\s{0,3}전후|변화\s{0,3}비교", "before-after"),
    (r"오해와\s{0,3}(?:사실|진실)|오해\s{0,3}[·/]\s{0,3}사실|통념", "myth-fact"),
    (r"(?:해야|할)\s{0,3}것(?:과|와)\s{0,3}(?:하지|말)|do\s{0,3}&?\s{0,3}don", "do-dont"),
    (
        r"예상\s{0,3}질문|자주\s{0,3}묻는|"
        r"(?<![A-Za-z])faq(?![A-Za-z])|질의\s{0,3}응답|Q\s{0,2}&\s{0,2}A",
        "faq",
    ),
    (r"용어\s{0,3}(?:정리|풀이|설명)?$|핵심\s{0,3}(?:용어|개념)|개념\s{0,3}정리", "glossary"),
    (r"참고\s{0,3}문헌|참고\s{0,3}자료|출처|references", "references"),
    (r"역할\s{0,3}분담|팀\s{0,3}(?:구성|소개)|구성원|담당자", "team"),
    (r"체크\s{0,3}리스트|점검\s{0,3}(?:항목|목록)|준비물|확인\s{0,3}사항", "checklist"),
    (r"로드\s{0,3}맵|추진\s{0,3}일정|개발\s{0,3}일정|주차별|마일스톤|향후\s{0,3}계획", "roadmap"),
    (r"연혁|이정표|진행\s{0,3}경과", "milestones"),
    (
        r"프로세스|처리\s{0,3}(?:흐름|절차)|"
        r"업무\s{0,3}흐름|데이터\s{0,3}흐름|분석\s{0,3}절차",
        "process",
    ),
    (r"순환|선순환|사이클|pdca", "cycle"),
    (r"주요\s{0,3}기능|핵심\s{0,3}기능|기능\s{0,3}(?:구성|목록|소개)", "feature-grid"),
    (r"목표$|^목적$|(?:실험|연구|과제|조사)\s{0,3}목적$|기대\s{0,3}성과|성과\s{0,3}지표",
     "objectives"),
    (r"핵심\s{0,3}(?:요약|정리|메시지)|시사점|요약$|정리$|결론$|마무리$", "takeaways"),
    (r"연구\s{0,3}질문|토론\s{0,3}질문|문제\s{0,3}제기|던지는\s{0,3}질문", "question"),
    (r"가설", "hypothesis"),
    (r"(?:이란|란\s{0,3}무엇|의\s{0,3}정의|정의$)", "definition"),
    (
        r"(?:A안|B안|두\s{0,3}안|방안)\s{0,3}비교|"
        r"비교\s{0,3}분석|대안\s{0,3}비교|\bvs\b|대비",
        "compare-2",
    ),
)
_TITLE_PATTERN_RES = [(re.compile(p, re.I), name) for p, name in _TITLE_PATTERNS]
#: Generic layouts a titled pattern may replace.
_GENERIC_LAYOUTS = ("bullets", "two-column", "cards", "bands", "statement", "quote")


def suggest_patterns(
    plan: list[dict[str, str]], request: str, context: list[str]
) -> list[dict[str, str]]:
    """Title-named patterns replace generic layouts (once each; numeric need figures)."""
    from app.services.context import instruction_part

    # only the user's own words name a layout
    asked = requested_layout(instruction_part(request or ""))
    figures = has_numbers(request, context)
    used: set[str] = {str(item.get("layout") or "") for item in plan}
    out = []
    for item in plan:
        layout = str(item.get("layout") or "")
        title = str(item.get("title") or "")
        if layout in _GENERIC_LAYOUTS and not asked:
            for pattern, name in _TITLE_PATTERN_RES:
                if pattern.search(title) and name not in used:
                    if name in slide_patterns.NUMERIC and not figures:
                        break
                    item = {**item, "layout": name}
                    used.add(name)
                    break
        out.append(item)
    return out


#: 「정확도: 매칭 92%」: short head, separator, line.
_HEAD_LINE = re.compile(
    r"^(?P<head>[^:：|–—]{2,14}?)\s{0,2}(?:[:：|]|\s[–—-]\s)\s{0,2}(?P<body>.{4,200})$"
)


#: User asked for speaker notes or a script.
_NOTES_ASK = re.compile(
    r"발표자\s{0,2}노트|노트\s{0,2}(?:포함|도|까지|를)|대본|스크립트|speaker\s{0,2}notes", re.I
)


def _object_particle(word: str) -> str:
    """을 after a final consonant, 를 after a vowel (or non-Hangul)."""
    last = (word or " ")[-1]
    if "가" <= last <= "힣":
        return "을" if (ord(last) - 0xAC00) % 28 else "를"
    return "를"


def fill_missing_notes(
    slides: list[dict[str, Any]], request: str, *, title: str = "", subtitle: str = ""
) -> list[int]:
    """Notes for every slide when asked; no facts beyond the slide."""
    from app.services.context import instruction_part

    if not _NOTES_ASK.search(instruction_part(request or "")):
        return []
    changed: list[int] = []
    for index, slide in enumerate(slides):
        if str(slide.get("notes") or "").strip():
            continue
        layout = str(slide.get("layout") or "")
        name = str(slide.get("title") or title or "").strip()
        if layout == "title" or index == 0:
            lead = f" {subtitle.strip()}" if subtitle.strip() and subtitle.strip() != name else ""
            note = f"안녕하세요. 오늘은 「{name}」에 대해 발표하겠습니다.{lead}"
        elif layout == "agenda":
            items = [str(b).strip() for b in (slide.get("bullets") or []) if str(b).strip()]
            note = (
                f"발표는 {', '.join(items[:-1])}, 그리고 {items[-1]} 순서로 진행하겠습니다."
                if len(items) >= 2 else "발표 순서를 먼저 말씀드리겠습니다."
            )
        elif layout == "closing":
            note = "이상으로 발표를 마치겠습니다. 질문이 있으시면 말씀해 주십시오."
        elif layout == "section":
            note = f"다음은 {name}입니다."
        else:
            lines = [str(b).strip() for b in (slide.get("bullets") or []) if str(b).strip()]
            lines += [
                str(i[0]).strip() for i in (slide.get("items") or []) if i and str(i[0]).strip()
            ]
            rows = slide.get("rows") or []
            if not lines and rows and isinstance(rows[0], list):
                lines = [str(c).strip() for c in rows[0][1:] if str(c).strip()]
            point = f" 핵심은 {', '.join(lines[:3])}입니다." if lines else ""
            note = f"이 장에서는 {name}{_object_particle(name)} 살펴보겠습니다.{point}"
        slide["notes"] = note
        changed.append(index)
    return changed


def pad_to_floor(plan: list[dict[str, str]], floor: int) -> list[dict[str, str]]:
    """Adds 「핵심 정리」 then 「질의응답」 when short of the minimum (max two)."""
    if not plan or len(plan) >= floor or floor - len(plan) > 2:
        return plan
    out = list(plan)
    titles = " ".join(str(item.get("title") or "") for item in out)
    closing_at = next(
        (i for i, item in enumerate(out) if item.get("layout") == "closing"), len(out)
    )
    if len(out) < floor and not re.search(
        r"정리(?:\s|$)|요약|시사점|결론|마무리|takeaway", titles, re.I
    ):
        out.insert(closing_at, {"title": "핵심 정리", "layout": "takeaways"})
    if len(out) < floor and not re.search(r"질의|Q\s{0,2}&\s{0,2}A|질문", titles, re.I):
        out.append({"title": "질의응답", "layout": "question"})
    return out


def shape_patterns(slides: list[dict[str, Any]], request: str) -> list[int]:
    """「head: line」 bullet slides → cards (3) or feature grid (4–6); max two per deck."""
    from app.services.context import instruction_part

    if requested_layout(instruction_part(request or "")):
        return []
    used = {str(s.get("layout") or "") for s in slides}
    changed: list[int] = []
    for index, slide in enumerate(slides):
        if len(changed) >= 2:
            break
        bullets = slide.get("bullets")
        if slide.get("layout") != "bullets" or not isinstance(bullets, list):
            continue
        # figured slide: lines are words under the figure
        if slide.get("diagram") or (slide.get("image") or {}).get("src"):
            continue
        matches = [_HEAD_LINE.match(str(b).strip()) for b in bullets]
        if len(bullets) < 3 or not all(matches):
            continue
        name = "cards-3" if len(bullets) == 3 else "feature-grid"
        if name in used:
            continue
        pairs = [[m.group("head").strip(), m.group("body").strip()] for m in matches if m]
        items = slide_patterns.clean_pairs(name, pairs)
        # cut by limits: stays bullets
        if items != pairs:
            continue
        slide["layout"] = name
        slide["items"] = items
        slide.pop("bullets", None)
        used.add(name)
        changed.append(index)
    return changed


_GRAPH_TITLE = re.compile(r"그래프|차트|추이|도표|\bgraph\b|\bchart\b|plot", re.I)


def _grounded_layouts(
    plan: list[dict[str, str]], request: str, context: list[str]
) -> list[dict[str, str]]:
    """Numeric layouts → `bullets` without figures; graph-titled slides → chart with figures."""
    if has_numbers(request, context):
        return [
            {**item, "layout": "chart"}
            if _GRAPH_TITLE.search(str(item.get("title") or ""))
            and item.get("layout") not in (*_STRUCTURAL, "closing", "chart")
            and not str(item.get("layout") or "").startswith("chart-")
            else item
            for item in plan
        ]
    return [
        {**item, "layout": "bullets"} if item.get("layout") in _NUMERIC_LAYOUTS else item
        for item in plan
    ]


_MAX_QUOTES = 2


def _rationed_quotes(plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Quotes beyond `_MAX_QUOTES` → bullets."""
    out: list[dict[str, str]] = []
    spent = 0
    for item in plan:
        if item.get("layout") != "quote":
            out.append(item)
            continue
        spent += 1
        out.append(item if spent <= _MAX_QUOTES else {**item, "layout": "bullets"})
    return out


#: Number-only divider title (`01`, `Part 3`); multi-letter Roman only (I/V/X may be words).
_NUMBER_ONLY = re.compile(r"^\s*(?:part|섹션|section|장)?\s*(?:[0-9]+|[IVX]{2,})\s*[.)]?\s*$", re.I)


def _named_dividers(plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drops number-only dividers (renderer draws the number)."""
    return [
        item
        for item in plan
        if item.get("layout") != "section" or not _NUMBER_ONLY.match(item.get("title") or "")
    ]


#: chart category label length
_CATEGORY_CHARS = 10


def _short_category(label: str) -> str:
    """A category label within the axis's room, never cut inside its brackets: 「라운드 로빈
    (q=2)」 → 「라운드로빈(q=2)」, not 「라운드 로빈 (q=」."""
    label = " ".join(label.split())
    if len(label) <= _CATEGORY_CHARS:
        return label
    compact = re.sub(r"\s+", "", label)
    if len(compact) <= _CATEGORY_CHARS:
        return compact
    m = re.match(r"^(?P<head>[^(（]+)[(（](?P<inner>[^()（）]+)[)）]", compact)
    numbers = re.findall(r"\d+(?:\.\d+)?", m.group("inner")) if m else []
    if m and numbers and len(m.group("head")) + len(",".join(numbers)) + 2 <= _CATEGORY_CHARS:
        # the bracket's number tells apart rows of one name (퀀텀 2 / 퀀텀 4): it stays
        return m.group("head") + "(" + ",".join(numbers) + ")"
    cut = compact[:_CATEGORY_CHARS]
    if cut.count("(") > cut.count(")"):
        cut = cut[: cut.rfind("(")] or cut
    return cut


def _clean_chart(value: Any) -> dict[str, Any] | None:
    """Drawable chart or `None`; series cut to the shortest paired length."""
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "bar").strip().lower()
    if kind not in ("bar", "line"):
        kind = "bar"
    categories = [_short_category(str(c)) for c in (value.get("categories") or [])]
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
    # A row of zeros draws nothing: a value the writer did not have (SRTF 0, 0, 0, …).
    drawn = [s for s in series if any(v for v in s["values"][:width])]
    if len(drawn) < len(series):
        log.warning("chart series with only zeros dropped: %s",
                    [s["name"] for s in series if s not in drawn])
    series = drawn
    if not series:
        return None
    return {
        "kind": kind,
        "unit": str(value.get("unit") or "").strip()[:8],
        "categories": categories[:width],
        "series": [{"name": s["name"], "values": s["values"][:width]} for s in series],
    }


def _number(cell: str) -> float | None:
    """Table cell as a number (「-3.09」, 「1,590」, 「0.7 V」), or None."""
    match = re.fullmatch(
        r"\s{0,4}([-+−]?\d[\d,]{0,15}(?:\.\d{1,10})?)\s{0,2}[A-Za-z%°Ωμ]{0,6}\s{0,4}", cell
    )
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "").replace("−", "-"))
    except ValueError:
        return None


def chart_from_material(title: str, material: list[str]) -> dict[str, Any] | None:
    """Chart from the material's best-matching numeric table (cells only); `None` when none fits."""
    title_words = set(re.findall(r"[가-힣A-Za-z]{2,}", title))
    best: tuple[int, dict[str, Any]] | None = None
    for text in material:
        block: list[list[str]] = []
        lines = (text or "").splitlines() + [""]
        for line in lines:
            line = line.strip()
            if line.startswith("|") and line.endswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    block.append(cells)
                continue
            if len(block) >= 3:
                header, body = block[0], [r for r in block[1:] if len(r) == len(block[0])]
                numeric = [
                    j for j in range(1, len(header))
                    if len(body) >= 2 and all(_number(r[j]) is not None for r in body)
                ]
                if numeric and len(body) >= 2:
                    def overlap(j: int, head: list[str] = header) -> int:
                        return -len(title_words & set(re.findall(r"[가-힣A-Za-z]{2,}", head[j])))

                    named = sorted(numeric, key=overlap)[:_MAX_SERIES]
                    named.sort()
                    unit = re.search(r"[(（]([^()（）]{1,8})[)）]", header[named[0]])
                    first_numeric = all(_number(r[0]) is not None for r in body)
                    chart = _clean_chart({
                        "kind": "line" if first_numeric else "bar",
                        "unit": unit.group(1) if unit else "",
                        "categories": [r[0] for r in body],
                        "series": [
                            {"name": re.sub(r"\s{0,2}[(（][^()（）]*[)）]", "", header[j]),
                             "values": [_number(r[j]) for r in body]}
                            for j in named
                        ],
                    })
                    words = set(re.findall(r"[가-힣A-Za-z]{2,}", " ".join(header)))
                    score = len(title_words & words)
                    if chart and (best is None or score > best[0]):
                        best = (score, chart)
            block = []
    return best[1] if best else None


_MAX_METRICS = 4
_MAX_VALUE = 12
_MAX_LABEL = 16


#: Date-part metric labels; whole word (`일자` ⊂ 일자리).
_CALENDAR = re.compile(
    r"^(년도|연도|마감|기한|일자|신청월|개강|종강|년|월|일|요일"
    r"|마감일|마감월|마감년도|신청일|시작일|종료일|개강일|종강일|접수일|발표일)$"
)


def _clean_metrics(value: Any) -> list[list[str]]:
    """`[[값, 이름]]` pairs; half-empty and date-part labels dropped."""
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


#: Paired layout → (max pairs, left max, right max chars).
_PAIRED = {
    "bands": (4, 10, 90),
    "tiles": (6, 4, 24),
    "timeline": (7, 12, 60),
    "steps": (5, 12, 60),
    "cards": (4, 14, 80),
}


def _clean_pairs(value: Any, layout: str) -> list[list[str]]:
    """`[[왼쪽, 오른쪽]]` within `_PAIRED` bounds; half-empty dropped."""
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
    """Bounded table rows, ragged padded; empty under two rows."""
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
    """Slide picture as a `data:` URI (JSONB), or `None`; never raises."""
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
    settled: list | None = None,
    computed: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Writes slide bodies: one draft call, then per-slide gaps; numbers checked vs `computed`."""
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
    # announced up front: panel shows unwritten slides
    for slide in slides:
        yield {"type": "slide", "slide": slide, "done": False}

    # computed values count as given (not stripped)
    given_extra = [computed] if computed else []
    outline_text = "\n".join(f"{i + 1}. {s['title']}" for i, s in enumerate(slides))
    written: list[str] = []
    #: Divider count (divider numbers count dividers).
    divider = 0

    # one-call draft keeps the thread; per-slide pass fills gaps
    drafted: dict[int, dict[str, Any]] = {}
    #: Rest-of-deck view for a retold slide's rewrite.
    elsewhere: dict[int, str] = {}
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
                    request=prompt_request(request, 1500),
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
        # numbers a slide may use
        given = "\n".join([request, *(untrusted_context or []), *given_extra])
        drafted = _split_deck_draft(draft_text, slides, _facts_set(given), given)
        retold = _retold(slides, drafted)
        # approved slides all ship: retold ones rewritten, not dropped
        if retold:
            log.info("deck retold slides rewritten: %s", ",".join(str(i) for i in sorted(retold)))
            elsewhere = {
                i: "\n".join(
                    f"{slides[j]['title']}: {' / '.join(_drafted_text(row).splitlines())}"
                    for j, row in sorted(drafted.items()) if j != i and j not in retold
                )
                for i in retold
            }
            drafted = {i: row for i, row in drafted.items() if i not in retold}
        yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "done"}
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("deck draft failed, writing slide by slide: %s", exc)
        yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "error"}

    echo_reasks = 0
    for index, slide in enumerate(slides):
        # position in `progress`, not label
        label = str(slide["title"])
        progress = {"current": index + 1, "total": len(slides)}

        if slide["layout"] in _STRUCTURAL:
            # from the outline, no model call
            if slide["layout"] == "section":
                divider += 1
                slide["number"] = f"{divider:02d}."
                # divider prints its own 「01」
                slide["title"] = unnumbered(str(slide.get("title") or ""))
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
            "\n\nThis material is handed out and read without a presenter. Write so the "
            "tables, evidence and context can be understood within one slide, and do not hide "
            "the key explanation in notes only. Do not cram by shrinking the text; keep within "
            "the readable limits of the current layout."
            if density == "reading"
            else "\n\nThis material is explained by a presenter. Keep one key point per slide, "
            "prefer short phrases and wide margins, and put detailed explanation in notes."
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
                            written=(
                                elsewhere[index][-3000:] if index in elsewhere
                                else "\n".join(written)[-3000:]
                            ) or "(none yet)",
                            # prompts without `{count}` ignore it
                            count=(
                                ("6~8" if slide["layout"] == "two-column" else "4~6")
                                if density == "reading"
                                else ("4~6" if slide["layout"] == "two-column" else "2~4")
                            ),
                            request=prompt_request(request, 1500),
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
            # reset: no empty layout drawn
            if slide.get("layout") not in _STRUCTURAL:
                slide["layout"] = "bullets"
            slide["body"] = UNWRITTEN
            yield {"type": "slide", "slide": slide, "done": True}
            continue

        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]

        # drawn after text: failed drawing keeps the slide
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
        # same number check as draft path
        if index not in drafted:
            facts = _facts_set("\n".join([request, *(untrusted_context or []), *given_extra]))
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
            # object-shaped notes → their text
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
            chart = _clean_chart(data.get("chart")) or chart_from_material(
                str(slide.get("title") or ""), [request, *(untrusted_context or []), *given_extra]
            )
            if chart:
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
        elif slide["layout"] in slide_patterns.BY_NAME:
            _fill_pattern(slide, data)
        elif slide["layout"] == "table":
            if rows := _rows_any(data, str(data.get("body") or "")):
                given = "\n".join([request, *(untrusted_context or []), *given_extra])
                slide["rows"] = dedupe_rows(ground_cells(rows, _facts_set(given), given))
            else:
                slide["layout"] = "bullets"
                slide["bullets"] = _clean_bullets(data.get("bullets"))
        else:
            slide["bullets"] = _clean_bullets(data.get("bullets"))

        if not has_content(slide):
            slide["bullets"] = _salvaged_bullets(data)
        if not has_content(slide) and slide_messages is not None:
            # one more ask when the answer had no usable content
            try:
                again, more = await _complete(
                    model,
                    [
                        *slide_messages,
                        {"role": "assistant", "content": body[:400]},
                        {
                            "role": "user",
                            "content": "The previous answer had no usable content. Write again "
                            "only one JSON object filling this slide's bullets (3~4) and notes.",
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
            # UNWRITTEN (shown, not exported), reset to bullets
            if slide.get("layout") not in _STRUCTURAL:
                slide["layout"] = "bullets"
            slide["body"] = UNWRITTEN

        if notes:
            notes = notes_only_given(notes_without_echo(notes, slide), slide, request)
        if slide.get("layout") not in _STRUCTURAL:
            # < 2 note sentences: re-ask once (per-deck budget)
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
                                "\n\nThe request made 「적어 준 것만」 a condition. Say it only "
                                "in words found on the screen and in the request, and add "
                                "nothing new such as facts, advice or sanctions."
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
            given_facts = _facts_set("\n".join([request, *(untrusted_context or []), *given_extra]))
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

    # deck's own figures, planned on written slides; approved pictures kept
    async for event in _draw_figures(
        slides,
        accent=accent,
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
    # the writer's internal text off first, so emptied notes are filled below
    internal = set(strip_internal_text(slides))
    # dropped request numbers restored onto a related slide
    touched = internal | (
        set(restate_missing_facts(slides, request))
        | set(keep_listed_items(slides, request))
        | set(fill_csv_categories(slides, list(untrusted_context or []), request))
        | set(shape_patterns(slides, request))
        | set(fill_missing_notes(slides, request, title=title, subtitle=subtitle))
    )
    given_text = "\n".join([request, *(untrusted_context or []), *given_extra])
    touched |= set(mend_mixed_script(slides, given_text))
    touched |= set(strip_json_residue(slides))
    touched |= set(mend_scaled_numbers(slides, given_text))
    touched |= set(end_without_next(slides))
    # placeholders or name-only parts rewritten once
    needs = requested_contents(slides, request)
    # given-up or empty slides retried once
    for index, slide in enumerate(slides):
        if unshippable(slide):
            needs[index] = (
                f"This slide (「{slide.get('title') or ''}」) has not been written yet. Find what "
                "its title speaks of in the material and fill the screen text and the speaker "
                "notes. If the material lacks it, list the items to confirm."
            )
    for index, slide in enumerate(slides):
        words = "\n".join(quality_gate._strings({k: v for k, v in slide.items() if k != "image"}))
        if clash := key_figures.conflicts(words, settled or []):
            piece, figure, _ = clash[0]
            needs.setdefault(index, (
                f"「{figure.name}」 has the settled value {figure.value}, but this slide wrote "
                f"it differently as 「{piece[:80]}」. Correct it to the settled value and align "
                "the figures computed from it."
            ))
    # material runs (1~3차시, M1~M6) kept whole
    material = "\n".join([request, *(untrusted_context or []), *given_extra])
    written = "\n".join("\n".join(quality_gate._strings({k: v for k, v in sl.items()
                                                          if k != "image"})) for sl in slides)
    for kind, missing in quality_gate.missing_sequence(material, written):
        where = next((i for i in range(len(slides) - 1, -1, -1)
                      if re.search(r"로드맵|일정|단계|계획|차시|흐름|순서",
                                   str(slides[i].get("title") or ""))), None)
        if where is not None:
            needs.setdefault(where, (
                f"Of the {kind} sequence in the material, {', '.join(missing)} is missing from "
                "the deck. Put the material's sequence on this slide in full — if space runs "
                "short, shorten the items."))
    deck_words = ["\n".join(quality_gate._strings({k: v for k, v in sl.items() if k != "image"}))
                  for sl in slides]
    for sentence, name, written, usual in quality_gate.scaled_metric_slips(
            "\n".join([material, *deck_words])):
        where = next((i for i, w in enumerate(deck_words) if sentence in w), None)
        if where is not None:
            needs.setdefault(where, (
                f"「{name}」 is written as {usual:,.0f} in the material and on other slides, but "
                f"this slide wrote {written:,.0f}, a different order of magnitude. If the basis "
                f"is the same, correct it to {usual:,.0f}."))
    for index, slide in enumerate(slides):
        title = str(slide.get("title") or "")
        rest = "".join(quality_gate._strings({k: v for k, v in slide.items()
                                              if k not in ("image", "id", "title", "notes",
                                                           "layout", "accent")}))
        if (str(slide.get("layout") or "") not in ("section", "title", "agenda", "divider")
                and re.search(r"시사점|요약|결론|제언|마무리|핵심\s?(?:정리|메시지)", title)
                and len(re.sub(r"\s|감사합니다|질문|Q\s?&\s?A|[.!]", "", rest)) < 6):
            needs.setdefault(index, (
                f"This slide (「{title}」) has no content on the screen. Pick the key points its "
                "title names from the earlier slides and fill the screen with about 3 short "
                "items."))
    for index in foreign_slides(slides, request):
        needs.setdefault(index, (
            "This slide was written in another language. Rewrite the same content in Korean — "
            "the title, the screen text and the speaker notes alike. Keep only proper nouns, "
            "abbreviations and units as they were."))
    for index, lines in long_bullets(slides).items():
        needs.setdefault(index, (
            "Some items on this slide run past two rows on screen: "
            + " / ".join(f"「{line}」" for line in lines[:3])
            + ". Rewrite every item as a short phrase within 40 characters — one point per item, "
            "no sentence endings — and move the explanation into the speaker notes. Keep every "
            "fact, term and number."))
    for index, slide in enumerate(slides):
        if any(f["code"] == "placeholder" for f in quality_gate.deck_findings([slide])):
            needs[index] = ("Remove the placeholder text (「(여기에 …)」 and the like) and fill "
                            "its place with real content — for a quiz, the questions and options, "
                            "and the answers and explanations too. Do not invent names, dates "
                            "or amounts the material does not have: write the item so it holds "
                            "without them.")
    for index in sorted(needs):
        slide = slides[index]
        try:
            fixed, spent = await rewrite_slide(
                request=request, slides=slides, target_id=str(slide.get("id")),
                model=model, api_key=api_key, material=[*(untrusted_context or []), *given_extra],
                note=needs[index],
            )
        except Exception as exc:  # noqa: BLE001 — the slide stands, the gate reports it
            log.info("slide %s not rewritten for %r: %s", index + 1, needs[index][:30], exc)
            spent = getattr(exc, "usage", None) or {}
            usage["inputTokens"] += spent.get("inputTokens", 0)
            usage["outputTokens"] += spent.get("outputTokens", 0)
            continue
        usage["inputTokens"] += spent.get("inputTokens", 0)
        usage["outputTokens"] += spent.get("outputTokens", 0)
        slides[index] = {**slide, **fixed, "id": slide.get("id")}
        touched.add(index)
    # settled figure restored in a formula, product redone
    for index, slide in enumerate(slides):
        mended = {
            k: (v if k in ("image", "id") or not settled else _mend_operands(v, settled))
            for k, v in slide.items()
        }
        if mended != slide:
            slides[index] = mended
            touched.add(index)
    if needs or settled:
        # rewritten slides get the same mends
        vocabulary = "\n".join([request, *(untrusted_context or []), *given_extra])
        touched |= set(mend_mixed_script(slides, vocabulary))
        touched |= set(strip_json_residue(slides))
        touched |= set(mend_scaled_numbers(slides, vocabulary))
    # still-long lines cut, full text in notes
    touched |= set(fit_long_bullets(slides))
    # rewritten slides and late fills (a bullet added under the stand-in) cleaned again
    touched |= set(strip_internal_text(slides))
    # numbers naming computed values take those values
    recomputed: list[str] = []
    if computed:
        mended_calc, recomputed = calc_check.check_slides(slides, computed)
        touched |= set(mended_calc)
        if recomputed:
            log.warning("deck values set back to the computed ones: %s", recomputed[:12])
    for index in sorted(touched):
        yield {"type": "slide", "slide": auto_fit(slides[index]), "done": True}
    # never shipped: a slide still empty or holding internal text after its one rewrite
    gone = [(i, s) for i, s in enumerate(slides) if unshippable(s)]
    if gone and len(gone) < len(slides):
        slides[:] = [s for s in slides if not unshippable(s)]
        names = ", ".join(f"「{s.get('title') or i + 1}」" for i, s in gone)
        log.warning("deck slides dropped unwritten: %s", names)
        yield {"type": "step", "id": "dropped", "status": "error",
               "label": f"내용을 채우지 못한 장 {len(gone)}개를 뺐습니다",
               "detail": f"다시 써도 비어 있거나 내부 문구가 남아 뺀 장: {names}"}
    findings = quality_gate.deck_findings(slides)
    findings += [{"code": "long_line", "where": f"{i + 1}장", "detail": lines[0][:160]}
                 for i, lines in long_bullets(slides).items()]
    if findings:
        log.warning("deck quality findings: %s", [(f["code"], f["where"]) for f in findings][:12])
    # bracket blanks (「[담당자]」): gaps, not defects
    blanks, named = placeholders.summary(
        s for slide in slides for s in quality_gate._strings(
            {k: v for k, v in slide.items() if k not in ("image", "diagram")}))
    quality_detail = "\n".join(filter(None, [
        f"채울 빈칸: {named}" if blanks else "",
        ("코드 계산 값으로 바로잡음: " + "; ".join(recomputed[:12])) if recomputed else "",
    ]))
    yield {
        "type": "step", "id": "quality",
        "label": ("품질 점검: 문제 없음" if not findings else f"품질 점검 {len(findings)}건")
        + (f" · 계산 값 {len(recomputed)}곳 바로잡음" if recomputed else "")
        + (f" · 빈칸 {blanks}개" if blanks else ""),
        "status": "done" if not findings else "error",
        **({"detail": quality_detail} if quality_detail else {}),
    }
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
    accent: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Plans and draws the deck's figures; re-announces figured slides."""
    # 「구조와 흐름은 그림으로」 opens step slides to the planner
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
    # a numbered procedure in the request: its flow shows every step
    steps = diagrams.given_steps(request)
    planned = [dataclasses.replace(row, steps=steps) if row.figure == "flow" else row
               for row in planned]
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
            made, spent, png = await diagrams.make(
                row, model=model, api_key=api_key, slide=True, accent=accent
            )
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
        # drawn with the deck's own shapes: panel/PDF/report match the .pptx
        slide["image"] = {
            "src": diagram_render.data_uri(png),
            "caption": str(made.get("caption") or ""),
            "fit": "contain",
            "position": "right",
            "size": "full",
            "diagram": True,
            "renderer": "pptx",
            "key": made.get("key", ""),
        }
        _words_under_figure(slide)
        yield {
            "type": "step",
            "id": f"dia{row.index}",
            "label": name,
            "status": "done",
            "progress": progress,
        }
        yield {"type": "slide", "slide": auto_fit(slide), "done": True}


#: Bilingual-term request (「영어 원문 병기」).
_BILINGUAL = re.compile(
    r"(?:영어|영문|원문|원어)\s{0,3}(?:원문\s{0,3})?(?:을|를)?\s{0,3}(?:함께\s{0,3})?"
    r"(?:병기|같이\s{0,3}(?:적|써|표기)|나란히)"
    r"|(?:keep|show|give)\s+the\s+English\s+terms?",
    re.I,
)
_STOP_WORDS = frozenset(
    "the of and to in for on with by from is are was were be this that we our it its as at "
    "or an a not can which these those has have had than".split()
)


def bilingual_terms_rule(request: str, material: list[str]) -> str:
    """한국어(English) term rule when asked; lists the material's repeated English terms."""
    if not _BILINGUAL.search(request or ""):
        return ""
    counts: dict[str, int] = {}
    for text in material:
        tokens = re.findall(r"[A-Za-z]+|[^A-Za-z\s]", text or "")
        for size in (3, 2):
            for i in range(len(tokens) - size + 1):
                words = tokens[i : i + size]
                if all(
                    w.isalpha() and len(w) > 2 and w.lower() not in _STOP_WORDS for w in words
                ):
                    phrase = " ".join(words)
                    counts[phrase] = counts.get(phrase, 0) + 1
    repeated = [t for t, n in counts.items() if n >= 2]
    # longest form only
    terms = [t for t in repeated if not any(t != o and t in o for o in repeated)][:15]
    rule = (
        "The user asked for each technical term with its English original. On every slide, "
        "write a technical term the first time it appears as 한국어 용어(English term), e.g. "
        "원자적 주장(atomic claim), 자연어 추론(NLI). Use the Korean term the material or the "
        "field already uses (초록 for abstract, 주장 for claim); if there is none, keep the "
        "English term as is rather than coining a Korean word. Keep the same pair on every "
        "slide and in the notes."
    )
    if terms:
        rule += " English terms in the material to pair: " + ", ".join(terms) + "."
    return rule


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    tokens: dict[str, str] | None = None,
    #: Outline model; empty → writing model.
    outline_model: str = "",
    #: Absent: plan only (`proposal`/`needs`). Present: write exactly this.
    approved_plan: dict[str, Any] | None = None,
    #: False after "있는 자료로 진행" (no re-ask).
    may_ask: bool = True,
    #: `None` on planning pass, `[]` for 그림 없이.
    figures_plan: list[dict] | None = None,
    image_model: dict | None = None,
    #: Research before the outline.
    web_search: bool = True,
    #: a calculation skill is on: derived values by code even when the request names none
    code_calc: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `title`, `slide`, `deck`, `usage`; planning pass emits `proposal` only."""
    # outline usage apart (may be another model)
    usage = {
        "inputTokens": 0,
        "outputTokens": 0,
        "outlineInputTokens": 0,
        "outlineOutputTokens": 0,
    }
    if rule := bilingual_terms_rule(request, untrusted_context or []):
        trusted_context = [*(trusted_context or []), rule]
    wanted = requested_slides(request)
    span = slide_range(request)
    fixed_accent = (tokens or {}).get("accent") or ""
    # default look; outline may override
    suggested_theme, suggested_style = suggest_look(request)
    suggested_accent = _THEMES[suggested_theme]

    # both passes research (outline names slides, not facts)
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
        # counted as usage by the router
        yield {
            "type": "research",
            "research": {"searched": findings.searched, "queries": findings.queries},
        }
    # search off: no rule; unavailable vs empty told apart
    research_rule = ""
    if web_search and not findings.searched:
        research_rule = research.UNRESEARCHED_RULE
    elif web_search and not findings.sources:
        research_rule = research.EMPTY_RULE
    document_context = list(untrusted_context or [])
    # pasted material read whole as reference
    if pasted := pasted_material(request):
        document_context.append(pasted)
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
        # re-checked: approved plan may be edited
        plan = suggest_patterns(
        _named_dividers(_rationed_quotes(_grounded_layouts(plan, request, document_context))),
        request,
        document_context,
    )
        if (wanted := requested_slides(request)) and len(plan) != wanted:
            log.warning(
                "approved deck plan has %d slides against a requested %d: %s",
                len(plan), wanted, " · ".join(item["title"] for item in plan),
            )
        # derived numbers from code
        computed = ""
        # unasked values only from the person's own words and files, never search pages
        asked = calc.needed(request, document_context)
        unasked = (not asked and code_calc
                   and calc.needed(request, list(untrusted_context or []), always=True))
        if asked or unasked:
            yield {
                "type": "step", "id": "calc", "label": "코드로 수치 계산 중", "status": "running"
            }
            # planner's reading of the material
            computed, spent, status = await calc.computed_values(
                request, document_context if asked else list(untrusted_context or []),
                complete=_complete, model=outline_model or model, api_key=api_key,
                always=bool(unasked),
            )
            plan_rules.count(usage, spent, planned_apart=bool(outline_model))
            if computed:
                trusted_context = [*(trusted_context or []), computed]
                yield {"type": "computed", "values": computed}
            yield {
                "type": "step", "id": "calc",
                "label": "코드로 수치 계산" if status == "done" else "코드 계산 없음 — 모델이 계산",
                "status": "done" if status == "done" else "error",
            }
        # settled figures: told to every slide, checked at the end
        settled: list[key_figures.Figure] = []
        try:
            settled, spent = await key_figures.settle(
                "\n\n".join([pasted_material(request) or "", *list(untrusted_context or [])]),
                _complete, outline_model or model, api_key,
            )
            plan_rules.count(usage, spent, planned_apart=bool(outline_model))
        except Exception as exc:  # noqa: BLE001 — written without them
            log.info("key figures not settled for the deck: %s", exc)
        settled = [*settled, *key_figures.derived(settled)]
        if settled:
            document_context = [key_figures.block(settled), *document_context]
            log.info("deck settled figures: %s", [(f.name, f.value) for f in settled])
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
            settled=settled,
            computed=computed,
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
                    lo=wanted or (span[0] if span else None) or slides_for_minutes(request)
                    or _MIN_SLIDES,
                    hi=wanted or (span[1] if span else _DEFAULT_MAX),
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
                    request=prompt_request(request, 2000),
                )
                + nudge,
                request=request,
                trusted_context=trusted_context,
                untrusted_context=document_context,
                research_rule=research_rule,
            ),
            api_key,
            # scaled with slide count (no JSON truncation)
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
    # outline may answer with questions (`grounding.ASK_RULE`)
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
        # no subject: ask
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
    # grounded before the variety check
    plan = suggest_patterns(
        _named_dividers(_rationed_quotes(_grounded_layouts(plan, request, document_context))),
        request,
        document_context,
    )
    plan = keep_enumerated(plan, request, requested_slides(request))
    offered = _offered_layouts(request, document_context)

    # flat outline: one retry naming skipped layouts
    missing = plan_rules.flat_layouts(plan, offered) if plan else []
    if missing:
        log.info("deck outline flat, unused: %s", ",".join(missing))
        try:
            retry_text, retry_spent = await ask(
                "\n\nThe previous structure leaned on one layout. Redo it. "
                "Use each of the following layouts at least once, and do not use the same "
                "layout on three slides in a row: "
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
    # duration and range low end are minimums; exact count checked below
    floor = max(span[0] if span else 0, slides_for_minutes(request) or 0)
    needed = None if wanted else floor or None
    if plan and needed and len(plan) < needed:
        log.info("deck outline short: %d of %d slides, asking once more", len(plan), needed)
        try:
            retry_text, retry_spent = await ask(
                f"\n\nThe previous structure had {len(plan)} slides. This talk needs at least "
                f"{needed}. Redo it — do not split existing slides in two; give slides to the "
                "parts of the flow the request named that have none yet (cases, comparisons, "
                "remaining problems, a summary)."
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
        plan = pad_to_floor(plan, needed)
    # unreadable outline: one retry
    if not plan:
        # kept for later diagnosis
        head = " ".join((text or "").split())[:240]
        log.info("deck outline unreadable, asking once more; reply began: %r", head)
        try:
            retry_text, retry_spent = await ask(
                "\n\nThe previous answer could not be read. Output only one JSON object, with "
                "no explanation, preamble or code fence."
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
    # agenda for long decks; break up bullet runs
    plan = vary_layouts(ensure_agenda(plan))
    plan = suggest_patterns(
        _named_dividers(_rationed_quotes(_grounded_layouts(plan, request, document_context))),
        request,
        document_context,
    )
    # after all retries: listed parts stay separate
    plan = keep_enumerated(plan, request, wanted)
    plan = structure_as_drawable(plan)

    # after structural changes: agenda must not exceed an exact total
    if wanted and plan and len(plan) != wanted:
        try:
            retry_text, retry_spent = await ask(
                f"\n\nThe previous structure had {len(plan)} slides. The whole deck, cover and "
                f"agenda included, must be exactly {wanted} slides. Restructure it to that count "
                "without leaving out anything the request asked for."
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

    # request's title/authors verbatim on the cover
    title, subtitle = given_cover(request, title, subtitle)
    if plan and plan[0].get("layout") == "title" and _given_line(request, _TITLE_LABELS):
        plan[0]["title"] = title
    yield {
        "type": "step",
        "id": "outline",
        "label": f"구성 {len(plan)}장",
        "status": "done",
        "detail": " · ".join(item["title"] for item in plan),
    }
    # planning pass ends: caller stores and returns it approved
    proposal: dict[str, Any] = {
        "title": title[:200],
        "subtitle": subtitle[:200],
        "accent": accent,
        # request's style > outline's > venue's
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
    # pictures proposed with the outline, approved on a second card
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
        # writing pass writes a form (`_FRAME_RULE`)
        proposal["frame"] = True
    yield {"type": "proposal", "plan": proposal}
    yield {"type": "usage", **usage}


#: Layout words in 「…로」 requests.
_LAYOUT_WORDS: tuple[tuple[str, str], ...] = (
    (r"(?<![A-Za-z])swot(?![A-Za-z])", "swot"),
    (r"장단점|장점과\s{0,3}단점|pros\s{0,3}(?:and|&)?\s{0,3}cons", "pros-cons"),
    (r"체크\s{0,3}리스트|점검표|(?<![A-Za-z])checklist(?![A-Za-z])", "checklist"),
    (r"(?<![A-Za-z])faq(?![A-Za-z])|질의\s{0,3}응답|문답|Q\s{0,2}&\s{0,2}A", "faq"),
    (r"용어\s{0,3}(?:정리|풀이|사전)|(?<![A-Za-z])glossary(?![A-Za-z])", "glossary"),
    (r"피라미드|(?<![A-Za-z])pyramid(?![A-Za-z])", "pyramid"),
    (r"깔때기|퍼널|(?<![A-Za-z])funnel(?![A-Za-z])", "funnel"),
    (r"순환|사이클|(?<![A-Za-z])cycle(?![A-Za-z])|(?<![A-Za-z])pdca(?![A-Za-z])", "cycle"),
    (r"로드\s{0,3}맵|(?<![A-Za-z])roadmap(?![A-Za-z])", "roadmap"),
    (r"마일\s{0,3}스톤|이정표|(?<![A-Za-z])milestones?(?![A-Za-z])", "milestones"),
    (r"매트릭스|2\s{0,2}[x×]\s{0,2}2|사분면", "matrix"),
    (r"전후\s{0,3}비교|before\s{0,3}(?:and|&|/)?\s{0,3}after", "before-after"),
    (r"문제\s{0,3}(?:와|-|·|/)?\s{0,3}해결", "problem-solution"),
    (
        r"원형\s{0,3}(?:차트|그래프)|파이\s{0,3}(?:차트|그래프)|(?<![A-Za-z])pie(?![A-Za-z])",
        "chart-pie",
    ),
    (r"도넛", "chart-donut"),
    (r"가로\s{0,3}막대", "chart-hbar"),
    (r"누적\s{0,3}막대", "chart-stacked"),
    (r"프로세스|(?<![A-Za-z])process(?![A-Za-z])", "process"),
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
    """Layout the words ask for (「연표로 바꿔 줘」), or None; requires a 「…로」 construction."""
    text = text or ""
    for pattern, layout in _LAYOUT_WORDS:
        for match in re.finditer(pattern, text, re.I):
            tail = text[match.end() : match.end() + 12]
            asked = re.match(r"\s{0,8}(?:로|으로)(?:\b|[\s,.]|$)", tail) or _LAYOUT_ASK.match(tail)
            if asked:
                return layout
    return None



class SlideNotWritten(ValueError):
    """Empty rewrite; carries billed usage."""

    def __init__(self, message: str, usage: dict) -> None:
        super().__init__(message)
        self.usage = usage


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
    """Rewrites one slide in deck context → `(slide, usage)`; `typed` checked for layout changes."""
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
    # layout change: user's words first, planner's paraphrase second
    layout = requested_layout(typed) or requested_layout(note) or layout
    template = _PROMPTS.get(layout) or (_TABLE_PROMPT if layout == "table" else _BULLETS_PROMPT)
    prompt = template.format(
        heading=target.get("title") or "",
        outline=outline,
        written=written[-3000:] or "(none yet)",
        count="6~8" if layout == "two-column" else "3~5",
        request=request[:1500],
    )
    if note.strip():
        # labelled, else read as request
        prompt += f"\n\nWhy this slide is rewritten now (must be reflected):\n{note.strip()[:600]}"

    text, usage = await _complete(
        model,
        build_document_messages(
            SessionKind.slides, prompt, request=request, untrusted_context=material
        ),
        api_key,
        900 if layout in ("table", "chart", "timeline", "two-column") else 600,
    )
    parsed = _json_object(text)
    if layout in slide_patterns.BY_NAME and not notes_only:
        # pattern rewrite in own shape; id/accent/picture kept, old fields dropped
        rewritten = {
            k: v for k, v in target.items()
            if k
            not in ("bullets", "body", "rows", "metrics", "chart", "items", "columns", *_PAIRED)
        }
        rewritten["layout"] = layout
        _fill_pattern(rewritten, parsed)
        if has_content(rewritten):
            rewritten["notes"] = str(parsed.get("notes") or target.get("notes") or "").strip()
            fitted = auto_fit(rewritten)
            return keep_listed_dates(fitted, typed), usage
    rows = _rows_any(parsed, text)
    pairs = _clean_pairs(parsed.get(layout), layout) if layout in _PAIRED else []
    bullets = _clean_bullets(parsed.get("bullets"))
    body = str(parsed.get("body") or "").strip()
    notes = str(parsed.get("notes") or "").strip()
    chart = parsed.get("chart") if layout == "chart" else None
    if not (isinstance(chart, dict) and chart.get("categories") and chart.get("series")):
        chart = None
    metrics = _clean_metrics(parsed.get("metrics")) if layout in ("metrics", "big-number") else []
    # notes-only or title-only asks keep the slide
    asked_title = revise.requested_title(typed)
    if not (rows or pairs or bullets or body or notes or chart or metrics):
        if asked_title:
            return {**target, "title": asked_title}, usage
        log.warning(
            "slide rewrite empty for %s: %s", logs.safe(target.get("title")), logs.safe(text[:240])
        )
        raise SlideNotWritten("빈 슬라이드", usage)

    # merged: id, accent, picture survive
    result = {**target}
    if notes_only:
        # notes-only ask: slide words untouched, notes filtered as in drafts
        plain = _plain_notes(notes)
        strict = notes_only_given(notes_without_echo(plain, result), result, request)
        if not strict and _ONLY_GIVEN.search(request or ""):
            # 「적어 준 것만」: reading the slide beats no notes
            strict = notes_only_given(plain, result, request)
        notes = notes_grounded(strict, _facts_set("\n".join([request, *(material or [])])))
        if not notes:
            raise SlideNotWritten("노트 없음", usage)
        result["notes"] = notes
        return result, usage
    # title changed only when asked
    new_title = str(parsed.get("title") or "").strip()
    if re.search(r"제목|타이틀|\btitle\b|헤드라인", f"{typed} {note}", re.I):
        # user's quoted title wins; parser tells new from old
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
        # old body (maybe UNWRITTEN) must go
        result.pop("body", None)
    if body:
        result["body"] = body
        result.pop("bullets", None)
    if notes:
        result["notes"] = notes
    # verdicts belonged to the old text
    result.pop("factCheck", None)
    result.pop("textScale", None)
    fitted = auto_fit(result)
    # keeps deck body size unless it must shrink
    own = float(fitted.get("textScale") or 1.0)
    scale = min(own, deck_scale(slides))
    if scale >= 1.0:
        fitted.pop("textScale", None)
    else:
        fitted["textScale"] = scale
    fitted = keep_listed_dates(fitted, typed)
    return fitted, usage


#: All content fields (paired from `_PAIRED`); drawn figures count as content.
_CONTENT_FIELDS = (
    "bullets", "body", "rows", "metrics", "chart", "diagram", "items", "columns", *_PAIRED
)


def has_content(slide: dict) -> bool:
    """Whether the slide has content; structural slides by title alone."""
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
    """Slides with something on them."""
    return [s for s in slides if has_content(s)]


#: Shared type scale (`deck_type`); body only steps down (22 → 12 pt), never grows.
_SCALES = deck_type.SCALES
#: Deck-wide body floor; a crowded slide keeps its own smaller step.
_COMMON_FLOOR = _SCALES[1]

#: Layout whose runs get every other slide re-planned.
_MONOTONE = "bullets"


#: Layouts a drawn figure may replace (steps/timelines draw their own flow).
_DIAGRAM_LAYOUTS = ("bullets", "two-column", "cards", "bands", "tiles")


_NOTES_PROMPT = """The screen of the "{heading}" slide already reads:
{shown}

Write two sentences the presenter will actually say aloud to the audience on this slide. Do
not repeat the screen's sentences; choose among why it matters, the one thing the audience
should remember, and words that lead to the next slide. Write the words themselves, not
directions saying what to say such as 「~을 강조합니다」「~을 설명합니다」.
(Example: 「이 숫자가 중요한 이유는 …입니다.」) Do not invent figures or facts the request does
not have. Output only the sentences, in Korean unless the request itself is written in English.

Original request: {request}"""


def _slide_lines_text(slide: dict) -> str:
    return "\n".join(_slide_lines(slide))


def _slide_lines(slide: dict) -> list[str]:
    """Every line the slide shows (for read-aloud checks)."""
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
    """Timeline keeps every dated item the instruction listed, in order."""
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
    """Notes string in a JSON reply (`notes`/`speaker_notes`/`note`, any depth), or `None`."""
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
    # JSON reply → its notes, never braces
    if (inner := _notes_in_json(text)) is not None:
        text = inner
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*[-*•]\s*", "", text, flags=re.M)
    text = " ".join(text.replace("\\n", " ").split())
    # surrounding quotes stripped
    return text.strip().strip('"“”\'')


def notes_grounded(notes: str, facts: set[str]) -> str:
    """Notes minus sentences with numbers not in the request."""
    kept = []
    for sentence in units.SENTENCE_BREAK.split((notes or "").strip()):
        if not sentence.strip():
            continue
        if _numbers_come_from(re.findall(r"\d[\d,.]*(?:\s*(?:만|억|천))?", sentence), facts):
            kept.append(sentence.strip())
    return " ".join(kept)


#: Stage directions instead of speech.
_META_NOTE = re.compile(
    r"(강조합니다|강조한다|설명합니다|설명한다|언급합니다|소개합니다|안내합니다|짚어\s*줍니다|"
    r"넘어갑니다|전달합니다|이야기합니다|말씀드립니다|보여\s*줍니다|상기시킵니다)\s*[.。]?$"
)


_KO_ENDING = re.compile(
    r"(에는|으로|에서|입니다|합니다|습니다|했습니다|됩니다|있어요|해요|이에요|예요|은|는|이|가|을|를|의|에|로|도|과|와)$"
)


def _stems_ko(sentence: str) -> set[str]:
    """Lowercased content-word stems (particles/endings cut)."""
    out = set()
    for word in re.findall(r"[가-힣A-Za-z0-9]{1,}", sentence):
        stem = _KO_ENDING.sub("", word) or word
        if len(stem) >= 2 or stem.isascii():
            out.add(stem.lower())
    return out


#: 「적어 준 것만」: notes only from slide and request words.
_ONLY_GIVEN = re.compile(
    r"적어\s*준\s*것만|적은\s*것만|준\s*것만|없는\s*내용|지어내지|추가하지\s*마"
)
#: Speaking words (add no claim).
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
    """Under 「적어 준 것만」, drops note sentences adding claims; else unchanged."""
    if not notes or not _ONLY_GIVEN.search(request or ""):
        return notes
    allowed = _stems_ko(" ".join([request, *_slide_lines(slide), str(slide.get("title") or "")]))
    kept = []
    for sentence in units.SENTENCE_BREAK.split(notes.strip()):
        if not sentence.strip():
            continue
        if len(_claim_words(sentence, allowed)) <= 2:
            kept.append(sentence.strip())
    return " ".join(kept)


def _sentence_count(text: str) -> int:
    return len([x for x in units.SENTENCE_BREAK.split((text or "").strip()) if x.strip()])


def _join_notes(existing: str, added: str) -> str:
    """Existing notes plus new sentences not already said."""
    have = [x.strip() for x in units.SENTENCE_BREAK.split(existing or "") if x.strip()]
    for sentence in units.SENTENCE_BREAK.split(added or ""):
        sentence = sentence.strip()
        said = any(sentence == h or (len(sentence) > 12 and sentence in h) for h in have)
        if sentence and not said:
            have.append(sentence)
    return " ".join(have)


def notes_without_echo(notes: str, slide: dict) -> str:
    """Notes minus sentences that only read the slide aloud (char-bigram comparison)."""
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
    for sentence in units.SENTENCE_BREAK.split(notes.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        grams = _bigrams(sentence)
        if len(grams) < 6:
            kept.append(sentence)
            continue
        # read-aloud: Dice match, bullet containment, or multi-line coverage
        echo = any(
            2 * len(grams & g) / (len(grams) + len(g)) >= 0.6
            or (len(g) >= 5 and len(grams & g) / len(g) >= 0.5)
            for g in shown
            if g
        )
        covered = len(grams & shown_all) / len(grams)
        # terse line spelled out as a sentence
        spoken = any(
            tokens
            and all(t.lower() in sentence.lower() for t in tokens)
            and len(_stems_ko(sentence) - {t.lower() for t in tokens}) <= 3
            for tokens in short_lines
        )
        if echo or covered >= 0.85 or spoken:
            continue
        if _META_NOTE.search(sentence) and not re.search(r"\d", sentence):
            # 「…을 강조합니다」: not speech
            continue
        kept.append(sentence)
    return " ".join(kept)


def _drafted_text(data: dict | None) -> str:
    """Slide words for the figure planner."""
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


#: Figure band height above words (slide units).
_FIGURE_BAND = 70.0

#: Words kept under a figure: cards (count, length) or bullet lines.
_UNDER_CARDS = 4
_CARD_CHARS = 44
_UNDER_LINES = 3
_LINE_CHARS = 60
#: Short-line list kept whole under a figure.
_UNDER_LINES_SHORT = 6
_SHORT_LINE_CHARS = 24


def _clipped(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _words_under_figure(slide: dict) -> None:
    """Figured slide: few words under the figure (≤ 4 cards or ≤ 3 bullets), rest to notes."""
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
                if isinstance(pair, dict):  # {"title", "body"} counts as a pair
                    lines.append(_entry_line(pair))
                    continue
                if not isinstance(pair, list) or not pair:
                    continue
                name = str(pair[0]).strip()
                text = " ".join(str(cell).strip() for cell in pair[1:] if str(cell).strip())
                lines.append(f"{name}: {text}" if name and text else name or text)
        lines.extend(str(b).strip() for b in (slide.get("bullets") or []) if str(b).strip())
        if body := str(slide.get("body") or "").strip():
            lines.append(body)
        lines = [line for line in lines if line]
        if not lines:
            # other shapes count as words
            for key in ("items", "columns", "metrics"):
                for entry in slide.get(key) or []:
                    lines.append(_entry_line(entry))
            lines = [line for line in lines if line]
        if not lines:
            # figure alone: notes' opening or source sentence beneath
            drawn_from = str((slide.get("diagram") or {}).get("description") or "")
            source = str(slide.get("notes") or "") or drawn_from
            sentences = re.split(r"(?<=[.!?。다])" + units.NO_NUMBER_DOT_BREAK + r"\s+", source)
            lines = [x.strip() for x in sentences if len(x.strip()) >= 8][:2]
        slide["layout"] = "bullets"
        # short user-written list stays whole (text shrinks)
        keep = _UNDER_LINES_SHORT if len(lines) <= _UNDER_LINES_SHORT and all(
            len(line) <= _SHORT_LINE_CHARS for line in lines
        ) else _UNDER_LINES
        slide["bullets"] = [_clipped(line, _LINE_CHARS) for line in lines[:keep]]
        slide["body"] = ""
        spill = [line for line in lines[:keep] if len(line) > _LINE_CHARS] + lines[keep:]
    if spill:
        notes = str(slide.get("notes") or "").strip()
        slide["notes"] = "\n".join(filter(None, [notes, *spill]))[:800]


def _entry_line(entry: object) -> str:
    """Step/item/column/tile as one line: name and words."""
    if isinstance(entry, dict):
        name = str(entry.get("title") or entry.get("name") or entry.get("label") or "").strip()
        text = str(entry.get("body") or entry.get("text") or entry.get("value") or "").strip()
        return f"{name}: {text}" if name and text else name or text
    if isinstance(entry, list):
        parts = [str(cell).strip() for cell in entry if str(cell).strip()]
        return f"{parts[0]}: {' '.join(parts[1:])}" if len(parts) > 1 else "".join(parts)
    return str(entry or "").strip()


def _body_width(slide: dict) -> float:
    """Text column width (slide units), narrowed by a picture."""
    width = float(deck_type.TITLE_WIDTH)
    image = slide.get("image") or {}
    if image.get("src") and slide.get("layout") != "title":
        share = {"small": 0.32, "medium": 0.42, "large": 0.54}.get(
            str(image.get("size") or ""), 0.42
        )
        width = width * (1 - share) - 16
    return width


def _fill_pattern(slide: dict, data: dict) -> None:
    """Pattern field from the answer within limits; bullets when shape mismatches."""
    pattern = slide_patterns.BY_NAME[slide["layout"]]
    if pattern.shape == "pairs":
        raw = data.get("items")
        if raw is None:
            # field named after the layout or an older paired layout
            raw = next((data[k] for k in (slide["layout"], *_PAIRED) if data.get(k)), None)
        if items := slide_patterns.clean_pairs(pattern.name, raw):
            slide["items"] = items
            return
    elif pattern.shape == "columns":
        if columns := slide_patterns.clean_columns(pattern.name, data.get("columns")):
            slide["columns"] = columns
            return
    elif pattern.shape == "metrics":
        if metrics := _clean_metrics(data.get("metrics")):
            slide["metrics"] = metrics[: pattern.count[1]]
            return
    elif pattern.shape == "chart":
        chart = data.get("chart")
        if isinstance(chart, dict):
            chart = {**chart, "kind": "bar"}
        if cleaned := _clean_chart(chart):
            cleaned["kind"] = pattern.params.get("kind", "bar")
            slide["chart"] = cleaned
            return
    elif pattern.shape == "text":
        line = " ".join(str(data.get("body") or "").split()).strip('"“”')
        if line:
            slide["body"] = line[: pattern.right_max]
            return
    bullets = _clean_bullets(data.get("bullets"))
    if pattern.shape == "pairs" and (items := _pairs_from_bullets(pattern.name, bullets)):
        # bullet answer reshaped into the pattern
        slide["items"] = items
        return
    slide["layout"] = "bullets"
    slide["bullets"] = bullets


def _pairs_from_bullets(name: str, bullets: list[str]) -> list[list[str]]:
    """Bullets as pattern items (「head: line」 split; optional-right lists whole); `[]` if cut."""
    if not bullets:
        return []
    matches = [_HEAD_LINE.match(b) for b in bullets]
    if all(matches):
        pairs = [[m.group("head").strip(), m.group("body").strip()] for m in matches if m]
    else:
        pairs = [[b, ""] for b in bullets]
    items = slide_patterns.clean_pairs(name, pairs)
    return items if items == pairs else []


def _pattern_need(slide: dict, scale: float, width: float) -> float:
    """Pattern body height: text by lines, drawn arrangements by fixed boxes."""
    pattern = slide_patterns.BY_NAME.get(str(slide.get("layout") or ""))
    if pattern is None:
        return 0.0
    U, L = deck_type.units, deck_type.LEADING
    size = U("body") * scale
    if pattern.render == "list":
        left = width * 0.32
        return sum(
            max(
                deck_type.lines(str(i[0]), size, left),
                deck_type.lines(str(i[1]) if len(i) > 1 else "", size, width - left - 16),
            )
            * size
            * L["body"]
            + size * deck_type.BULLET_GAP
            for i in slide.get("items") or []
        )
    if pattern.render == "columns":
        cols = slide.get("columns") or []
        if not cols:
            return 0.0
        span = (width - 16 * (len(cols) - 1)) / len(cols) - 12
        return 24 * scale + max(
            sum(deck_type.lines(str(i), size, span) * size * L["body"] + 4 for i in c["items"])
            for c in cols
        )
    if pattern.render == "text":
        big = U("paragraph") * scale * 1.3
        return deck_type.lines(str(slide.get("body") or ""), big, width * 0.8) * big * 1.3 + 20
    # drawn arrangements shrink to the body box
    return 110.0 * scale


def _need(slide: dict, scale: float) -> float:
    """Body height (slide units) at `scale` plus extra title lines; mirrors `SlideView`."""
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
    # deck-drawn figure: band above words
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
            # bands/timelines share the body height
            need += fixed
    rows = slide.get("rows") or []
    if rows:
        # rows sized by count; long cells use the reserve
        need += deck_type.table_row_height(len(rows)) * len(rows)
    if layout in slide_patterns.BY_NAME:
        return need + _pattern_need(slide, scale, width)
    metrics = slide.get("metrics") or []
    if metrics and layout != "big-number":
        need += 6 + 14 + U("metric") * scale * 1.1 + 5 + U("metricLabel") * scale * 1.5 + 16
    return need


def auto_fit(slide: dict) -> dict:
    """Sets `textScale` to the first fitting ladder step (22 → 12 pt); manual scales kept."""
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
    """One deck body size: smallest needed step, not below 18 pt (crowded slides keep their own)."""
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
    """Deck body size: the step most body slides use."""
    body = _body_slides(slides)
    if not body:
        return 1.0
    seen = [float(s.get("textScale") or 1.0) for s in body]
    return max(set(seen), key=lambda value: (seen.count(value), value))


#: Table-of-contents titles.
_AGENDA_TITLE = re.compile(r"목차|발표\s*순서|차례|순서|agenda|contents|overview", re.I)


#: Enumerated request items (「(1) … (2) …」).
_ENUM_MARK = r"(?:\(\d{1,2}\)|[①-⑳]|(?<!\d)\d{1,2}\)|(?<![\d.])\d{1,2}\.\s)"
_ENUM_ITEM = re.compile(
    _ENUM_MARK + r"\s*([^—:\-–()\n]{2,30}?)\s*(?=[—:\-–]|" + _ENUM_MARK + r"|[.。]|$)"
)
_FILLER_LAYOUTS = {"statement", "quote", "big-number", "section", "agenda"}
#: Merge/shorten instruction, not a part's own words (「결과 병합」).
_MERGE_ASK = re.compile(
    r"한\s{0,8}장(?:으로|에)\s{0,8}(?:합|묶|넣|정리)|합쳐\s{0,8}(?:줘|서|라|주)|합치고|묶어\s{0,8}(?:줘|서|라)|"
    r"줄여\s{0,8}(?:줘|서|라|주)|줄이고|\d{1,15}\s{0,8}장으로\s{0,8}줄"
)


#: Label making list items deck parts (「내용:」), not one part's issues (「이슈:」).
_PART_LIST_LABEL = re.compile(
    r"(?:내용|구성|목차|순서|차례|장\s{0,8}구성|슬라이드|섹션|절|파트|chapters?|sections?|slides?)"
    r"\s{0,8}(?:은|는|이|가)?\s{0,8}[:：]\s{0,8}$"
)


#: Arrow-chained parts (「요약 → 진척 → 지표 순서」); not a flow inside one part.
_ARROW_CHAIN = re.compile(r"(?:[^→\n:：]{1,24}\s*→\s*){2,}[^→\n.。]{1,24}")
_ARROW_PARTS_TAIL = re.compile(r"^\s*(?:순서|순으로|차례|구성|흐름으로)")
_ARROW_PARTS_LEAD = re.compile(
    r"(?:장|발표|슬라이드|덱|deck|자료)[^\n]{0,20}(?:만들|구성|순서|짜)[^\n→]{0,12}[:：]\s*$"
)
_COUNT_TAIL = re.compile(r"\s*(?:셋|둘|넷|다섯|여섯|\d+\s*(?:건|개|가지))$")


#: Comma-listed parts with 「순서로」.
_COMMA_ORDER = re.compile(r"([^.。:：\n]{8,300}?)\s{0,3}(?:순서로|순으로|순서대로)")


def comma_parts(request: str) -> list[str]:
    """Comma-listed parts marked as deck order; `[]` otherwise."""
    from app.services.context import instruction_part

    text = " ".join(instruction_part(request or "").split())
    for match in _COMMA_ORDER.finditer(text):
        chunk = match.group(1)
        # list starts after the last stop/colon
        chunk = re.split(r"[.。:：]", chunk)[-1]
        pieces = [re.sub(r"[(（][^()（）]*[)）]", "", p).strip(" ,·") for p in chunk.split(",")]
        if len(pieces) >= 4:
            items = [p for p in pieces if 2 <= len(p) <= 16]
            if len(items) == len(pieces):
                return items
    return []


def arrow_parts(request: str) -> list[str]:
    """Arrow-chained parts marked as deck order (hints dropped); `[]` otherwise."""
    from app.services.context import instruction_part

    request = instruction_part(request or "")
    if "→" not in request:
        return comma_parts(request)
    text = " ".join(request.split())
    for match in _ARROW_CHAIN.finditer(text):
        chain = match.group(0)
        # order word inside or after the last item
        inside = re.search(r"\s*(?:순서|순으로|차례|구성|흐름으로)\s*[.。]?$", chain)
        if inside:
            chain = chain[: inside.start()]
        tail = text[match.end() : match.end() + 8]
        lead = text[max(0, match.start() - 60) : match.start()]
        if not (inside or _ARROW_PARTS_TAIL.match(tail) or _ARROW_PARTS_LEAD.search(lead)):
            continue
        items = []
        for raw in chain.split("→"):
            # chain starts after the last full stop
            raw = re.split(r"[.。:：;]", raw)[-1]
            item = re.sub(r"[(（][^()（）]*[)）]", "", raw).strip(" ,.。:")
            item = _COUNT_TAIL.sub("", item).strip()
            if 2 <= len(item) <= 14 and not re.fullmatch(r"[\d\s,.]+", item):
                items.append(item)
        if len(items) >= 3:
            return items
    return []


def enumerated_items(request: str) -> list[str]:
    """Parts listed under a parts label, in order; `[]` under two or for one part's list."""
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
    """Keeps every listed part on its own slide (split merges, add missing, trim filler)."""
    items = enumerated_items(request)
    if not plan or not items:
        return plan
    # merge asks or too-small counts: planner's call
    if _MERGE_ASK.search(request) or (wanted and len(items) + 2 > wanted):
        return plan
    # 「한 줄 요약」: statement slide, not split or renamed
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
    # requested agenda isn't filler
    asked_agenda = bool(re.search(r"목차|차례|agenda", request, re.I))
    fillers = _FILLER_LAYOUTS - ({"agenda"} if asked_agenda else set())
    # unrequested closing yields to listed parts
    if not re.search(r"마무리|맺음|끝인사|클로징|closing|감사\s*인사", request, re.I):
        fillers = fillers | {"closing"}
    for item in items:
        if any(_covers(str(o.get("title") or ""), item) for o in out):
            continue
        # renamed part: orphan body slide takes the user's name
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
            # no room: part stays merged
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
    """Trims dividers then agenda to the asked count; content never dropped."""
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
    """Decks over six slides get an `agenda` after the cover (or an agenda-titled early slide)."""
    if len(plan) <= 6 or any(item.get("layout") == "agenda" for item in plan):
        return plan
    for item in plan[1:4]:
        if _AGENDA_TITLE.search(str(item.get("title") or "")):
            item["layout"] = "agenda"
            return plan
    plan.insert(1, {"title": "발표 순서", "layout": "agenda"})
    return plan


#: Structure slide (components and relations, not sequence).
_STRUCTURE_TITLE = re.compile(
    r"구조|아키텍처|구성\s*(?:요소|도)|시스템\s*구성|전체\s*구성|컴포넌트|모듈\s*구성|"
    r"\b(?:architecture|structure|components?|topology)\b",
    re.I,
)


def structure_as_drawable(plan: list[dict]) -> list[dict]:
    """Structure slides on `_NOT_FOR_STRUCTURE` layouts → `bullets` (drawable)."""
    out = []
    for slide in plan:
        structural = _STRUCTURE_TITLE.search(str(slide.get("title") or ""))
        if structural and slide.get("layout") in _NOT_FOR_STRUCTURE:
            slide = {**slide, "layout": "bullets"}
        out.append(slide)
    return out


#: Layouts an architecture slide can't be.
_NOT_FOR_STRUCTURE = ("steps", "chart", "table", "metrics", "big-number", "timeline")


def vary_layouts(plan: list[dict]) -> list[dict]:
    """Breaks 3+ bullet runs: every other → bands or cards."""
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
    """Deck as Markdown."""
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
        # pattern slides (`slide_patterns`): items and columns are their whole screen
        for item in slide.get("items") or []:
            cells = ([str(c).strip() for c in item if str(c).strip()]
                     if isinstance(item, list) else [_entry_line(item)])
            if cells and cells[0]:
                parts.append("- " + " — ".join(cells))
        for column in slide.get("columns") or []:
            if isinstance(column, dict):
                entries = [str(i).strip() for i in column.get("items") or [] if str(i).strip()]
                parts.append(f"- **{column.get('title') or ''}** " + " · ".join(entries))
        if chart := slide.get("chart"):
            # chart data as a table, not a 「P1: 0 · 7 · 0」 line
            categories = [str(c) for c in chart.get("categories") or []]
            series = [item for item in chart.get("series") or [] if isinstance(item, dict)]
            if series:
                unit = f" ({chart['unit']})" if chart.get("unit") else ""
                head = ["항목" + unit, *(categories or [str(n + 1) for n in range(
                    max(len(item.get("values") or []) for item in series))])]
                parts.append("\n| " + " | ".join(head) + " |")
                parts.append("|" + "---|" * len(head))
                for item in series:
                    values = [str(v) for v in item.get("values") or []]
                    values += [""] * (len(head) - 1 - len(values))
                    parts.append(f"| {item.get('name') or '계열'} | " + " | ".join(values) + " |")
        if slide.get("notes"):
            parts.append(f"\n발표 노트: {slide['notes']}")
        parts.append("")
    return "\n".join(parts).strip() + "\n"


__all__ = ["filled", "has_content", "to_markdown", "write"]
