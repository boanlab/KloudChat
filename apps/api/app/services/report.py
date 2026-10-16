"""Report writing: outline, one-shot draft split by heading, per-section fallback and rewrite."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import re
from collections.abc import AsyncIterator
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core import logs
from app.core.config import settings
from app.models.chat import SessionKind
from app.services import (
    asked_form,
    block_fences,
    design_templates,
    diagram_render,
    diagrams,
    doc_formats,
    grounding,
    hangul,
    imagegen,
    internal_words,
    key_figures,
    pictures,
    placeholders,
    quality_gate,
    ratelimit,
    report_checks,
    research,
    revised_tables,
    settings_store,
    table_mend,
    thinking,
    units,
    weekdays,
)
from app.services import deck as deck_rules
from app.services import outline as plan_rules
from app.services.context import (
    build_document_messages,
    instruction_part,
    lead_instruction,
    pasted_material,
    requests_web_search,
)

log = logging.getLogger(__name__)

_MIN_SECTIONS = 3
_MAX_SECTIONS = 12


def requested_sections(request: str) -> int | None:
    """Explicit section count; page counts imply none."""
    return plan_rules.requested_count(request, ("섹션", "절"), maximum=_MAX_SECTIONS)


_OUTLINE_PROMPT = """Make the title and table of contents (목차) of a report for the request below.

Rules:
- The title is the one line printed on the document's cover. Do not copy the request
  sentence; write a noun phrase naming the subject. Leave out the full stop and filler
  such as "~에 대한 보고서".
- **Do not invent subject matter the request does not give.** If the request names only
  what the document is for and not what it is about, write a title naming that use and
  build the 목차 from the skeleton that use requires. A report filled with a field or a
  year the request never named is not the reader's and cannot be used as is.
- {lo}~{hi} sections.
- Sections do not overlap, and read in order they make one piece of writing. Each section
  answers **a different single question** — do not split one question in two, like
  「비용 비교」 and 「옵션 비교 분석」.
- A document that asks for a judgement (review, decision, proposal, feasibility) opens
  with a 「요약」 section and ends with 「권고안과 다음 단계」. Conclusions and
  recommendations live only in that last section. **Status and weekly reports, minutes,
  incident reports, lab reports and notices do not ask for a judgement** — give them no
  summary or recommendation section; that genre's items are the 목차.
{genre}
- **In a decision document with several alternatives, do not give each alternative its
  own section.** 「A의 경제적 분석」 「B의 경제적 분석」 split the columns of one table into
  sections. Instead compare them on the same criteria in one 「대안 비교」 section. **Never
  put a particular alternative's name (클라우드, 교체, 연장) in a section heading** — with
  an alternative's name in its heading, the section is a table column, not a section.
  Such a document usually runs: 요약 → 현황과 결정할 사안 → 비용 계산의 전제 → 대안 비교 →
  위험과 남는 문제 → 권고안과 다음 단계. Calculation comes before comparison — the
  table's numbers are copied from values derived by formula in the section before it,
  not computed anew in the table.
  **Use that skeleton only for a document that chooses between alternatives.** Do not put
  「비용 계산의 전제」 or 「대안 비교」 into minutes, lab reports, notices or trend analyses.
- **If the request names items, those items are the 목차.** For 「결정 사항, 반론, 다음
  발표자와 기한을 나눠」 the sections are those three (plus any needed opening); for
  「목적·이론·장치·절차·결과·오차 분석」 they are those six. Do not drop a requested item
  or rename it. With two axes — 「안건별로 결정·반론·조건·담당을 나눠」 — **make sections of
  one axis only** (one section per agenda item, with 결정·반론·조건·담당 as subheadings
  inside). Sections for both agenda items and item types say everything twice.
- If the request lists three or more things to analyse (「대상 환경, 배포 방법, 주요 기능,
  적용 기술」), include a section that compares them by product or vendor in one table
  (e.g. 「솔루션별 종합 비교」).
- A business plan or proposal puts a section near the front that fixes the 「핵심 가정」
  (price, unit cost, ARPU, CAC, churn, capacity, fixed cost) in one place, and does not
  leave out the funding plan or operational risks (quality, safety, logistics, etc.).
- Section headings only. Do not write their content.
- style is the impression that fits where this document is used. Pick exactly one of three:
  · 편집형 — documents read to make a judgement, such as reports, reviews and plans. Rules and wide margins.
  · 포스터형 — documents that must catch the eye first, such as notices and promotions. Strong colour fields.
  · 미니멀 — documents where restraint is the courtesy, such as papers and review material. Pale colours and small headings.
  If the request states an impression, follow it; otherwise choose from the subject.
{ask_rule}
- If the reference material includes a form or template document, use its items in their
  order as the 목차. Follow that form's count too; do not turn it into a generic report 목차.

Answer with a JSON object only. In "subject" write what this document is about **in the
request's own words** — an empty string if the request names no subject. If the request
chooses among several alternatives, put their short names in "alternatives" (otherwise an
empty array). Write the title and section headings in Korean unless the request is in English.
예: {{"title": "전이학습의 소량 데이터 효율성", "style": "미니멀", "subject": "전이학습",
     "sections": ["요약", "배경", "방법", "결과", "한계", "결론"], "alternatives": []}}
예: {{"title": "학과 서버 교체 여부 결정", "style": "편집형", "subject": "학과 서버 교체",
     "sections": ["요약", "현황과 결정할 사안", "비용 계산의 전제", "대안 비교",
                  "위험과 남는 문제", "권고안과 다음 단계"],
     "alternatives": ["교체", "1년 연장", "클라우드 이전"]}}
예: {{"title": "9월 학과 세미나 회의록", "style": "편집형", "subject": "학과 세미나",
     "sections": ["회의 개요", "결정 사항", "반론과 남은 쟁점", "다음 발표자와 기한"],
     "alternatives": []}}

Request: {request}"""

_SECTION_PROMPT = """You are writing only the "{heading}" section of the report below.

Full outline:
{outline}

Already written in earlier sections:
{written}

Sources:
{refs}

This section's role: {role}
{others}
{facts}
{genre}

Rules:
- Write only what belongs under "{heading}". Do not put the whole report here; other
  sections own their parts (comparison, schedule, recommendation).
- No heading line, not even in bold, and no top-level heading (#). Body only.
- Do not repeat what earlier sections said or the tables and blocks they drew.
- Prose is the default: usually two to four paragraphs of connected sentences. Do not
  fill the section with a numbered list like 「- **1번 항목**:」.
- **Use only the figures in the 「쓸 수 있는 수치」 list above and values computed from
  them**, with their units and magnitudes exactly as given — do not move a yearly
  figure into 만 units or change the number of digits. Show the formula with a computed
  value (「월 비용 × 12개월 = 연 비용」). Never present a figure that is not on the list as
  a fact — user counts, causes of failure, damage, other costs, years. An estimate the
  request asks for (시장 규모, TAM·SAM·SOM, 손익 전망) sets the rates and shares it needs
  as stated 「가정」 with their basis and computes from them. Where a fact is missing,
  write the sentence so it holds without it (「참석 인원은 신청 마감 후 확정합니다」) rather
  than leaving a bracket blank. Do not invent circumstances the request does not give, such
  as the cause or course of an incident.
- One register for the whole document. If earlier sections used 「~합니다」, so does this
  one; the first section uses 「~합니다」.
- Write at the level a practitioner can use as is: for each step what, how and why,
  criteria and examples, what to watch for. Unfold everything the material and request
  give; do not end on a one-line summary. Details come only from the material and request.
- **Never invent a specific value the material does not give** — amounts, dates,
  institutions, people, counterparties. Where a decision is needed, say what must be
  decided instead of filling it in: 「예산 규모는 확정 후 반영합니다」, not 「예산 2억 원」;
  「협약 기업은 선정 중입니다」, not 「A社·B社」. Write around a missing value with a
  sentence that stays true without it, or state an assumption as 「가정」 when a
  calculation needs one; in a table cell write 「미정」 and add one 「확인 필요: …」 line
  under the table. Brackets only for what the person fills in themselves — a signature,
  contact details, a name only they know (「[작성자]」 「[연락처]」). A 미정 the material
  itself states stays as written (「미정(다음 회의에서 재논의)」).
- A fact taken from the source list (material found by search) gets that source's number
  at the end of the sentence, like [1]. Never use a number that is not on the list. The
  [1], [2] marks in material the user pasted are not sources — do not number that
  content. Cite only facts that need support, not every sentence.
- Break into paragraphs where the point changes: one point per paragraph, two to four sentences.
- Put a caption line directly above every table: 「표: 제목」, a short noun phrase for what the table shows. Figures are captioned for you; write no figure caption.
- The reader has only this report, not the sources. Never point into a source's own structure (「매뉴얼 제2장」, 「보고서 3절」, 「p. 12」, 「해당 자료」, 「이 매뉴얼은」): say what the content is and cite [n]. If the source itself matters, name it once by its title and publisher (「과기정통부·KISA의 『AI 보안 위협 대응 매뉴얼』(2026)」), never as a bare 「매뉴얼」.
- Cite a source once for the sentences that rest on it, at the end of the last of them; do not put the same [n] after every sentence of a paragraph.
- A figure goes where the text describes something drawable: components and how they connect (method), steps in order (flow), two sides set against each other (compare), or a classification (concept). Put it on its own line right after that paragraph as `[[그림: kind | caption | description]]` — kind is method, flow, compare or concept; caption a short Korean noun phrase; description the boxes and arrows, named in the text's own words. Refer to it in the prose as 〔그림〕 (「〔그림〕과 같이 …」). At most one figure per section, and none for numbers (a table holds numbers). Refer to a table you write as 〔표〕 in the sentence before it.
- When the request names three or more things to analyse (「대상 환경, 배포 방법, 주요 기능, 적용 기술」), the report carries one comparison table whose rows are the actual products, vendors or options found in the sources (by name) and whose columns are exactly those things. Generic categories are not rows.
- A business plan or proposal states its assumptions once — price, unit cost, ARPU, CAC, churn, capacity, fixed cost — in one table, and every later figure is computed from that table; never a second value for the same assumption. Write each calculation as 「A × B = C」 with units so it can be checked.
- A figure that shows a classification puts the kinds side by side under their parent, not in a row of arrows; arrows are for steps and data flow only.
- When the request says a part must include something (「문항 포함」, 「예시 포함」, 「표로」), that thing itself appears in the part — the questions, the examples, the table — not a description of it. Material already written in the context (questions, a table, a calculation) is carried over as it is.
- Do not leave a blank for a figure the stated assumptions let you compute (initial funding = monthly fixed cost × months to break-even, and so on): compute it and show the formula. An input nobody gave is not invented: take a stated 「가정」 value or write the step as a formula of named inputs (「월 고정비 × 회수 개월」), and list such inputs once under 「확인 필요」.
- Do not copy these rules into the text; the reader never sees them.
- Every sentence ends in the 「~합니다」 / 「~입니다」 form, table cells and list items aside; never end a sentence in 「~다.」 (「확인했다.」, 「필요하다.」).
- Write the section in Korean.
{blocks}

Original request: {request}"""

_DRAFT_PROMPT = """Write the whole report in one pass, following the outline below.

Outline (write these headings in this order, each as a `## 제목` line):
{outline}

Sources:
{refs}

{facts}
{genre}

Rules:
- Do not add causes, reasons or judgements the material does not give — 「민감한 정보가
  포함되어 있어」, 「~때문으로 판단됩니다」 only if the material says so. What you do not know,
  leave out; do not write sentences explaining what is missing (「(자료에 없음)으로 처리해야
  할 부분은 …」). A value the document needs and nobody gave is never made up and never a
  bracket blank: write the sentence so it holds without it (「일정은 확정되는 대로
  안내합니다」), state an assumption as 「가정」 when a calculation needs one, and in a table
  cell write 「미정」 with one 「확인 필요: …」 line under the table. Brackets only for what
  the person fills in themselves — a signature, contact details, a name only they know
  (「[작성자]」 「[연락처]」). Do not write sentences that add no fact, such as 「기반을 마련했습니다」 or
  「긍정적인 성과」.
- Start each section with a `## 제목` line. Do not add sections that are not in the
  outline and do not drop any. No top-level heading (#); the title is set on the cover.
- If the first section is 「요약」: what has to be decided, the recommendation, and two
  reasons, in one or two paragraphs of prose, no table or list, about 200 characters.
- If the last section is 「권고안」 or 「다음 단계」: state one recommendation clearly, its
  grounds, and the actions in order. Conclusions and recommendations live only there;
  the middle sections give facts and comparisons and leave judgement to it.
- Write each section at the level a practitioner can use as is: for each step what, how
  and why, criteria and examples, what to watch for. Unfold everything in the material;
  details come only from the material and request.
- Each middle section answers a different question. **A section is at least two
  paragraphs of three or four sentences.** A one-paragraph section is a memo — say what
  is so, why, and what changes for the reader. No subheadings like 「결론」 「요약」
  「다음 단계」 inside a section; no numbered list like 「- **1번 항목**:」; do not repeat
  the section heading in bold.
- A document with two or more alternatives puts exactly one table at the start of the
  comparing section: alternatives as columns, criteria as rows. **The criteria are the
  ones the request names** — the field's own measures for a technique comparison;
  first-year cost, three-year total (with formula), risk of failure and what remains
  after the decision only in a cost decision. Draw the table straight away with no label
  above it, then say in a sentence or two what it shows — no list explaining each column.
  **Never draw the same table again in another section** — one comparison table per document.
- Prose is the default. Values by item go in tables (no blank lines between rows, 3–5
  rows), at most two tables in the document, with one sentence before (what is compared)
  and one after (so what). The two or three numbers that conclude the document go once in
  a ```kpi block (`값 | 이름` per line, at most 4). The one premise everything rests on
  goes once in a ```callout block (first line title, next line text). No other blocks.
- **If the request has a table of measured data, the results section shows that table
  again** — the request's columns plus the computed ones (gain, dB, theory, error %).
  This counts toward the two tables.
- **To state a difference, compute both numbers and set them side by side.** Do not say
  「고주파에서 위상이 이론값보다 다소 크다」 without computing it; derive the theoretical
  value, put it next to the measurement, and call a difference under 1% agreement. Do not
  invent procedures the request does not give, such as 「3회 반복 측정」.
- **Use only the figures in 「쓸 수 있는 수치」 above and values computed from them**, in
  Arabic numerals as the request writes them — do not shift magnitudes (만 to 백만) or use
  Chinese numerals. **Example numbers inside these rules are not this document's figures.**
  Write each calculation with formula and result, divisions to one decimal place
  (「월 비용 × 12개월 = 연 비용」, 「초기 비용 ÷ 연 비용 ≈ 회수 기간(년)」). Never present a
  figure that is not on the list as a fact — user counts, periods, other costs, residual
  values, years, working days; write around a missing one as above. An estimate the
  request asks for sets the rates and shares it needs as stated 「가정」 with their basis. Do not invent
  circumstances the request does not give. When comparing a large and a small number,
  write both to check the direction.
- Facts from the source list get the source number at the end of the sentence, like [1].
  No numbers that are not on the list, no source labels like [설계도면] as citations
  (a blank like 「[작성자]」 is not one), and not the [1], [2] marks of material the user
  pasted. No sources, no numbers.
- One register for the whole document: 「~합니다」. No first person (「저는」 「우리는」) —
  「클라우드 이전을 권고합니다」, not 「저는 … 권고합니다」.
- Numbers in a comparison table are the values computed earlier, copied as they are; do
  not recompute in the table, and never give the same item two values in table and text.
- Break into paragraphs where the point changes: one point per paragraph, two to four sentences.
- Put a caption line directly above every table: 「표: 제목」, a short noun phrase for what the table shows. Figures are captioned for you; write no figure caption.
- The reader has only this report, not the sources. Never point into a source's own structure (「매뉴얼 제2장」, 「보고서 3절」, 「p. 12」, 「해당 자료」, 「이 매뉴얼은」): say what the content is and cite [n]. If the source itself matters, name it once by its title and publisher (「과기정통부·KISA의 『AI 보안 위협 대응 매뉴얼』(2026)」), never as a bare 「매뉴얼」.
- Cite a source once for the sentences that rest on it, at the end of the last of them; do not put the same [n] after every sentence of a paragraph.
- A figure goes where the text describes something drawable: components and how they connect (method), steps in order (flow), two sides set against each other (compare), or a classification (concept). Put it on its own line right after that paragraph as `[[그림: kind | caption | description]]` — kind is method, flow, compare or concept; caption a short Korean noun phrase; description the boxes and arrows, named in the text's own words. Refer to it in the prose as 〔그림〕 (「〔그림〕과 같이 …」). At most one figure per section, and none for numbers (a table holds numbers). Refer to a table you write as 〔표〕 in the sentence before it.
- When the request names three or more things to analyse (「대상 환경, 배포 방법, 주요 기능, 적용 기술」), the report carries one comparison table whose rows are the actual products, vendors or options found in the sources (by name) and whose columns are exactly those things. Generic categories are not rows.
- A business plan or proposal states its assumptions once — price, unit cost, ARPU, CAC, churn, capacity, fixed cost — in one table, and every later figure is computed from that table; never a second value for the same assumption. Write each calculation as 「A × B = C」 with units so it can be checked.
- A figure that shows a classification puts the kinds side by side under their parent, not in a row of arrows; arrows are for steps and data flow only.
- When the request says a part must include something (「문항 포함」, 「예시 포함」, 「표로」), that thing itself appears in the part — the questions, the examples, the table — not a description of it. Material already written in the context (questions, a table, a calculation) is carried over as it is.
- Do not leave a blank for a figure the stated assumptions let you compute (initial funding = monthly fixed cost × months to break-even, and so on): compute it and show the formula. An input nobody gave is not invented: take a stated 「가정」 value or write the step as a formula of named inputs (「월 고정비 × 회수 개월」), and list such inputs once under 「확인 필요」.
- Do not copy these rules into the text.
- Every sentence ends in the 「~합니다」 / 「~입니다」 form, table cells and list items aside; never end a sentence in 「~다.」 (「확인했다.」, 「필요하다.」).
- Write the document in Korean.

Original request: {request}"""


_TABLE = re.compile(r"(?m)^\|.+\|\s*\n\|[\s:|-]+\|\s*\n(?:^\|.+\|\s*\n?)+")
_RESULTS_HEADING = re.compile(r"결과|측정|데이터|분석")


def _carry_table(request: str, headings: list[str], drafted: dict[str, str]) -> dict[str, str]:
    """Request's data table added to the results section if the draft dropped it."""
    found = _TABLE.search(request)
    if not found or not drafted:
        return drafted
    if any(_TABLE.search(text) for text in drafted.values()):
        return drafted
    target = next((h for h in headings if _RESULTS_HEADING.search(h) and h in drafted), None)
    if target is None:
        return drafted
    table = found.group(0).strip()
    drafted[target] = f"측정 데이터는 다음과 같습니다.\n\n{table}\n\n{drafted[target]}"
    return drafted


def _split_draft(draft: str, headings: list[str]) -> dict[str, str]:
    """Draft split into sections on loose `## ` matches."""

    def key(text: str) -> str:
        text = re.sub(r"^[\d.\s]+", "", text.strip())
        return re.sub(r"[\s:：.]+", "", text).lower()

    wanted = {key(h): h for h in headings}
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in draft.splitlines():
        m = re.match(r"^\s*#{1,3}\s+(.+?)\s*$", line)
        if m:
            k = key(m.group(1))
            if k in wanted:
                current = wanted[k]
                found[current] = []
                continue
            # stray sub-heading kept as bold text
            if current is not None:
                found[current].append(f"**{m.group(1).strip()}**")
                continue
        if current is not None:
            found[current].append(line)
    return {h: "\n".join(lines).strip() for h, lines in found.items() if "\n".join(lines).strip()}


#: block syntax per kind; each section sees only its allowed blocks
_BLOCK_SYNTAX = {
    "table": (
        "- Write comparisons and per-item values as a table, with no blank lines between rows.\n"
        "  3–5 rows suit; put one sentence before it (what is compared) and one after (so what).\n"
        "      | 기준 | 대안 A | 대안 B |\n"
        "      | --- | --- | --- |\n"
        "      | 초기 비용 | 0원 | 약 3억 원 |"
    ),
    "kpi": (
        "- If two or three numbers conclude the section, put them in a ```kpi block, one `값 | 이름` per line (at most 4).\n"
        "  Do not repeat values that are in a table. The text says what the numbers mean.\n"
        "      ```kpi\n      32% | 오탐 감소\n      1.4초 | 평균 응답 시간\n      ```"
    ),
    "steps": (
        "- Work done in order goes in a ```steps block, one `이름 | 설명` per line (at most 8 steps).\n"
        "      ```steps\n      자료 수집 | 공개 데이터와 내부 로그를 모은다\n"
        "      정제 | 중복과 결측을 걸러낸다\n      ```"
    ),
    "cards": (
        "- To set three or four strands of equal weight side by side (이해관계자·산출물·목표), use a ```cards block:\n"
        "  `- 줄` lines under `## 카드 제목` (at most 6 cards, five lines or fewer each).\n"
        "      ```cards\n      ## 산출물\n      - 네트워크 전면 교체\n      ## 목표\n"
        "      - 8개월 안에 완료\n      ```"
    ),
    "callout": (
        "- The one premise, warning or deadline that brings everything down if wrong goes in a ```callout block:\n"
        "  title on the first line, text on the next. At most one in the whole document.\n"
        "      ```callout\n      승인 없이는 시작하지 않는다\n"
        "      9월 교무회의 승인 전까지는 계약도 발주도 하지 않는다.\n      ```"
    ),
    "chart": (
        "- Values of one item that change over time (4 or more points) go in a ```chart block, not a table.\n"
        "  First line `종류 | 단위`, second line `가로축 이름 | 계열 이름들`, the rest are values.\n"
        "  Up to 8 x-axis points and 2 series. A row with an empty value is dropped whole. Never use invented figures.\n"
        "      ```chart\n      bar | 건\n      분기 | 처리 건수 | 반려 건수\n"
        "      1분기 | 120 | 8\n      2분기 | 210 | 11\n      ```"
    ),
}

_NUMBER = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:억|만|천|백)?\s*(?:원|%|퍼센트|시간|분|초|일|주|개월|년|회|건|명|대|장|쪽|GB|TB|MB|kg|km|m|건수)?",
)


#: bare-form writer rule when 있는 자료로 진행 answered a needed question
_FRAME_RULE = (
    "**This document is a bare form written without material.** Invent no facts, "
    "discussions, decisions, names or figures the request does not give. In each section, "
    "say in one or two sentences what belongs there, and leave each place to fill as its "
    "name in brackets, like 「[결정된 사항과 근거]」. Tables get a header row and empty "
    "cells only. Do not add sections or tables the request did not ask for, such as cost "
    "calculations or alternative comparisons. Formulas and general principles are fine."
)

#: opener for a document with no web source found
_UNVERIFIED_NOTE = (
    "_이 문서는 웹 검색에서 쓸 만한 자료를 얻지 못해 기억을 바탕으로 썼습니다. "
    "수치·연구명·서지는 확인이 필요합니다._"
)

#: option-weighing request: only fit for cost-table advice
_DECISION = re.compile(r"대안|결정|권고|비용|타당성|선택")

#: 개조식 genre marker; `_shaped` drops prose paragraph rules
_GAEJO_STYLE = "Style: 개조식."

#: per-format genre rules from each 서식's `genre.md`
_FORMAT_GENRES: dict[str, str] = design_templates.format_genres()
#: same request words as `doc_formats`, so head and body agree
_FORMAT_PATTERNS: dict[str, re.Pattern[str]] = {
    key: re.compile(doc_formats.BY_ID[key].match, re.I) for key in _FORMAT_GENRES
}

#: genre rules from `app/genres.toml`, in order
def _load_genres() -> tuple[tuple[re.Pattern[str], str, re.Pattern[str] | None], ...]:
    import tomllib
    from pathlib import Path

    raw = tomllib.loads((Path(__file__).resolve().parent.parent / "genres.toml")
                        .read_text(encoding="utf-8"))
    out: list[tuple[re.Pattern[str], str, re.Pattern[str] | None]] = []
    for entry in raw.get("genres") or []:
        if fmt := str(entry.get("format") or ""):
            if fmt in _FORMAT_GENRES:
                out.append((_FORMAT_PATTERNS[fmt], _FORMAT_GENRES[fmt], None))
            continue
        unless = str(entry.get("unless") or "")
        out.append((re.compile(str(entry["match"])), str(entry["rule"]),
                    re.compile(unless) if unless else None))
    return tuple(out)


_GENRES: tuple[tuple[re.Pattern[str], str, re.Pattern[str] | None], ...] = _load_genres()


_OWN_GENRES = re.compile(
    r"주간|월간|업무 ?보고|진행 ?상황|현황 ?보고|회의록|녹취|장애(?!인)|사고|실험|측정"
)


def _own_material(request: str) -> bool:
    """Request about the person's own material, carried with it (no web search)."""
    instruction = instruction_part(request)
    if _SCHOLARLY_DOC.search(instruction):
        return False
    return bool(_OWN_GENRES.search(instruction)) and _carries_material(request)


#: work documents sourced entirely from the person; web adds only unrelated refs
_MATERIAL_FORMATS = frozenset({
    "press", "official", "result_report", "minutes", "internal_draft", "gaejo", "event_plan",
    "status", "incident",
})
_GRANT_APPLICATION = re.compile(r"공모|지원\s{0,3}사업|보조\s{0,3}사업|보조금|사업\s{0,3}신청")


#: the ask names outside facts to look up (「국내 시장 규모와 성장 전망(공개 자료 기준)」)
_PUBLIC_FACTS = re.compile(
    r"공개(?:된)?\s{0,2}자료|공식\s{0,2}(?:자료|통계)|시장\s{0,2}(?:규모|전망|점유율|동향)|"
    r"성장\s{0,2}전망|경쟁(?:사|업체|\s{0,2}제품)|업계\s{0,2}동향|산업\s{0,2}(?:환경|동향)"
)


def asks_public_sources(request: str) -> bool:
    """Whether the instruction asks for outside facts, whatever the 서식 says about search.

    Memo lines are not asks: a minutes memo that mentions 「경쟁사」 still searches nothing."""
    instruction = instruction_part(request)
    asks = [instruction.strip().split("\n\n")[0]] + [
        line for line in instruction.splitlines() if _ASK_LABEL.match(line)]
    return requests_web_search(instruction) or bool(_PUBLIC_FACTS.search("\n".join(asks)))


def material_complete(request: str, format_id: str | None, *, has_files: bool = False) -> bool:
    """Work document written from the person's material alone (no web unless asked)."""
    instruction = instruction_part(request)
    if asks_public_sources(request) or _SCHOLARLY_DOC.search(instruction):
        return False
    named = (format_id or "") in _MATERIAL_FORMATS or bool(
        _GRANT_APPLICATION.search(lead_instruction(request)))
    if (format_id or "") == "comparison" and not named:
        # priced options decided as given; unpriced ones still looked up
        priced = len(_MONEY.findall(request)) >= 2
        return has_files or (priced and _carries_material(request))
    if named and (has_files or _carries_material(request)):
        return True
    # any other work document built on attached files or a given memo, nothing to look up
    if (format_id or "") in _RESEARCH_FORMATS and not _FORM_GENRES.search(instruction):
        return False
    return (has_files or _given_memo(instruction)) and not _LOOKS_UP.search(_ask_lines(instruction))


#: an ask that points at the material given with it: 「아래 초록과 실험 결과로」, 「아래 메모로」,
#: 「이 변경 목록을 정리해 줘」
_FROM_GIVEN = re.compile(
    r"(?:아래|위|이|다음|첨부(?:한|된)?|붙인|붙여\s{0,2}넣은|준|주신)\s{0,2}"
    r"(?:[가-힣]{1,10}\s{0,2}(?:와|과|및|,)\s{0,2}){0,3}[가-힣 ]{0,10}"
    r"(?:자료|내용|메모|초안|초록|결과|목록|기록|정보|표|데이터|보도\s{0,2}자료|발표문|공지|문서|원고)"
    r"\s{0,2}(?:만으로|만|으로|로|대로|를|을|에서|기반으로|바탕으로|참고해)")
#: material the person marks as made up or complete: 「(가상)」, 「아래 자료가 전부임」 — the web
#: holds nothing about it (r8 A12-T2, A13-T2, A16-T2: searches for a made-up product)
_SELF_CONTAINED = re.compile(
    r"\(\s{0,2}가상|가상\s{0,2}[)—-]|가상\s{0,2}(?:회사|제품|자료|기업|서비스|사례)|"
    r"(?:아래|위)(?:\s{0,2}(?:자료|내용))?(?:가|이)\s{0,2}전부")


def given_material_suffices(request: str, *, has_files: bool = False) -> bool:
    """The ask works on material the person gives (pasted under it, attached, or marked made
    up) and names nothing to look up: an automatic web search adds only unrelated pages (r8
    A09-T1: 19 searches for a talk built from its own abstract and results; A02-T2: a made-up
    press release checked on the web). An explicit 「조사해서」, 「찾아봐」, 「최신」 still
    searches."""
    if edits_given_text(request, has_files=has_files):
        # the ask alone is read: 「만족도 조사」 in the text to polish asks for nothing
        return True
    instruction = instruction_part(request)
    asks = f"{lead_instruction(request)}\n{_ask_lines(instruction)}"
    if requests_web_search(instruction) or _EXPLICIT_LOOKUP.search(asks):
        return False
    if _SELF_CONTAINED.search(request):
        # 「아래 자료가 전부임」: even a 「고객 사례 한 줄」 comes from it
        return True
    if asks_public_sources(request) or _LOOKS_UP.search(asks):
        return False
    return (has_files or _carries_material(request)) and bool(
        _FROM_GIVEN.search(lead_instruction(request)))


#: an ask to edit the given text itself, not to add to it
_EDITS_TEXT = re.compile(
    r"맞춤법|띄어쓰기|교정|윤문|퇴고|다듬|어투|말투|문체|군더더기|오탈자|비문|매끄럽게")


def edit_ask(request: str, *, has_files: bool = False) -> str:
    """The ask, when it proofreads or polishes the text that comes with it (the first
    paragraph over pasted text, or the message over a file) and names nothing to look up;
    else "". That text is the whole job and nothing in it is a question (r10 SK-copyedit-2:
    「아직」 in a pasted report started four unrelated searches)."""
    instruction = instruction_part(request or "")
    head, gap, rest = instruction.strip().partition("\n\n")
    ask = head if gap and rest.strip() else instruction
    # a pasted paragraph is the text to edit, short as it may be
    given = has_files or len(rest.strip()) >= 80 or _carries_material(request or "")
    if not given or not _EDITS_TEXT.search(ask):
        return ""
    if requests_web_search(ask) or _EXPLICIT_LOOKUP.search(ask):
        return ""
    return ask


def edits_given_text(request: str, *, has_files: bool = False) -> bool:
    return bool(edit_ask(request, has_files=has_files))


#: an ask to look something up, whatever material comes with it
_EXPLICIT_LOOKUP = re.compile(r"조사|검색|찾아|최신|요즘|현재|공개\s{0,2}(?:자료|정보)")


#: formats that cite outside work even with a memo
_RESEARCH_FORMATS = frozenset({"plan", "paper", "term", "review", "lab", "proposal"})
#: forms and notices: the person's facts only
_FORM_GENRES = re.compile(r"IRB|심의|신청서|공고|공지|안내|FAQ|자주\s{0,2}묻는|가이드|매뉴얼", re.I)
#: asks for outside facts
_LOOKS_UP = re.compile(
    r"조사|동향|사례|검색|찾아|최신|트렌드|벤치마킹|국내외|해외|다른\s{0,2}(?:대학|기관|학교|회사)"
    r"|타\s{0,2}(?:대학|기관|학교|사)|통계|법령|근거\s{0,2}자료"
    # a market-entry review asks what the person's memo cannot hold
    r"|공개\s{0,2}(?:자료|통계|출처)|시장\s{0,2}규모|성장\s{0,2}전망|경쟁사|경쟁\s{0,2}구도|산업\s{0,2}환경")
#: a memo line that is the ask itself: 「알고 싶은 것: 시장 규모와 …」
_ASK_LABEL = re.compile(r"^[ \t]{0,8}(?:[-*•·]\s{0,2})?(?:알고\s{0,2}싶은|궁금한|요청\s{0,2}사항|원하는\s{0,2}것"
                        r"|질문|검토\s{0,2}(?:사항|항목|내용))[^:：\n]{0,10}[:：]")
#: 「항목: 값」 line, bulleted or not
_KEY_VALUE = re.compile(
    r"^[ \t]{0,8}(?:[-*•·□○◦▪]\s{0,2}|\d{1,2}[.)]\s{0,2})?[가-힣A-Za-z][^:：\n]{0,24}[:：]\s{0,2}\S",
    re.M)


def _given_memo(instruction: str) -> bool:
    """The facts come with the request: 3+ 「항목: 값」 lines or 3+ numbered steps."""
    return (len(_KEY_VALUE.findall(instruction)) >= 3
            or len(re.findall(r"[①-⑳]", instruction)) >= 3)


def _ask_lines(instruction: str) -> str:
    """The instruction's own lines, memo lines left out."""
    return "\n".join(line for line in instruction.splitlines()
                     if line.strip() and (_ASK_LABEL.match(line) or (
                         not _KEY_VALUE.match(line)
                         and not re.match(r"^\s{0,8}[-*•·□○◦▪]\s", line))))


_SCHOLARLY_DOC = re.compile(
    r"논문\s{0,3}초안|논문을\s{0,3}써|투고|학회\s{0,3}논문|워크숍\s{0,3}논문|관련\s{0,3}연구|"
    r"연구\s{0,3}동향|문헌|선행\s{0,3}연구|서베이|survey|literature|related work",
    re.I,
)


def scholarly_document(request: str) -> bool:
    """Paper-sourced document (literature mode)."""
    return bool(_SCHOLARLY_DOC.search(instruction_part(request)))


def _genre_rule(request: str, fmt: doc_formats.DocFormat | None = None,
                template_id: str | None = None) -> str:
    """Genre shape rule from the instruction, or `""`; 서식 format rule first."""
    template = design_templates.get(template_id) if template_id else None
    if template is not None and template.user and template.genre:
        return template.genre
    if fmt is not None and fmt.id in _FORMAT_GENRES:
        return _FORMAT_GENRES[fmt.id]
    # ask's first paragraph first: memo lines don't change the genre
    for instruction in dict.fromkeys((lead_instruction(request), instruction_part(request))):
        for pattern, rule, unless in _GENRES:
            if pattern.search(instruction) and not (unless and unless.search(instruction)):
                # the request's own parts come first (r8 A07: a genre's closing 「시행일·문의처」
                # over a report that named its sections)
                return f"{rule} {GENRE_YIELD}" if asked_form.asked(request) else rule
    return ""


#: told after a genre rule when the request names its own form (`asked_form`)
GENRE_YIELD = ("The request names its own parts, order or counts: those come first, and this "
               "genre's shape applies only where the request leaves the form open.")


def _plain_register(prompt: str) -> str:
    """「~합니다」 register rules replaced by one 「~다」 rule."""
    out: list[str] = []
    dropping, placed = False, False
    for line in prompt.split("\n"):
        if line.startswith("- "):
            dropping = any(phrase in line for phrase in _POLITE_RULES)
            if dropping and not placed:
                out.append(_PLAIN_RULE)
                placed = True
        elif not line.startswith("  "):
            dropping = False
        if not dropping:
            out.append(line)
    return "\n".join(out)


_PROSE_ONLY = (
    "Prose is the default",
    "A section is at least two",
    "Break into paragraphs",
    "One register for the whole document",
    "Every sentence ends in the",
)


#: plain 「~다」 register genre marker
_PLAIN_REGISTER = "Register: 「~다」"
_POLITE_RULES = ("One register for the whole document", "Every sentence ends in the")
_PLAIN_RULE = ("- One register for the whole document: 「~다」 (「제안한다」, 「필요하다」, "
               "「~이다」). Never 「~합니다」 / 「~입니다」, table cells and list items aside.")


def _shaped(prompt: str, genre: str) -> str:
    """Prompt adjusted for 개조식 and 「~다」 genres."""
    if _PLAIN_REGISTER in genre and _GAEJO_STYLE not in genre:
        return _plain_register(prompt)
    if _GAEJO_STYLE not in genre:
        return prompt
    out: list[str] = []
    dropping = False
    for line in prompt.split("\n"):
        if line.startswith("- "):
            dropping = any(phrase in line for phrase in _PROSE_ONLY)
        elif not line.startswith("  "):
            dropping = False
        if not dropping:
            out.append(line)
    return "\n".join(out)


def gaejo_body(sections: list[dict], fmt: doc_formats.DocFormat | None) -> list[dict]:
    """개조식 bodies as canonical list Markdown with 공문서 dates; 공문 as 1. 가. items."""
    if fmt is not None and fmt.id == "official":
        return [
            {**sec, "content": doc_formats.official_body(
                doc_formats.official_notation(str(sec.get("content") or "")))}
            if not _REFERENCE_HEADING.search(str(sec.get("heading") or "")) else sec
            for sec in sections
        ]
    if fmt is None or fmt.lists != "gaejo":
        return sections
    return [
        {
            **sec,
            "content": doc_formats.gaejo_markdown(
                doc_formats.official_notation(str(sec.get("content") or ""))
            ),
        }
        if not _REFERENCE_HEADING.search(str(sec.get("heading") or ""))
        else sec
        for sec in sections
    ]


_MONEY = re.compile(
    r"(?<![\d,.])\d[\d,]{0,15}(?:\.\d{1,3})?\s*(?:억|만|천|백)?\s*원(?!인|리|칙|자)"
)


def _without_invented_money(text: str, evidence: list[str] | None = None) -> str:
    """Sums of money no source states nor derives marked 「(확인 필요)」, never blanked."""
    from app.services import value_guard

    found = list(_MONEY.finditer(text or ""))
    if not found:
        return text
    ev = value_guard.Evidence.of(list(evidence or []), answer=text)
    out, at = [], 0
    for m in found:
        line, base = _line_of(text, m.start())
        out.append(text[at:m.end()])
        at = m.end()
        if text[at:at + len(value_guard.MARK) + 1].lstrip().startswith(value_guard.MARK):
            continue
        if not value_guard.supported(m.group(0), line, m.start() - base, ev, 0):
            out.append(value_guard.MARK)
    out.append(text[at:])
    return "".join(out)


def unmark_worked(sections: list[dict], evidence: list[str]) -> list[dict]:
    """Money marked unsupported in one section, unmarked where another section works it out
    from the material (「(1,480,000 × 30 × 0.95 + 5,000 × 30) × 1.1 = 46,563,000원」)."""
    from app.services import value_guard

    mark = value_guard.MARK
    whole = "\n".join(str(sec.get("content") or "") for sec in sections)
    if mark not in whole:
        return sections
    out = []
    for sec in sections:
        text = str(sec.get("content") or "")
        if mark not in text:
            out.append(sec)
            continue
        ev = value_guard.Evidence.of(list(evidence), answer=text, worked=whole)
        pieces, at = [], 0
        for m in _MONEY.finditer(text):
            if not text.startswith(mark, m.end()):
                continue
            line, base = _line_of(text, m.start())
            if value_guard.supported(m.group(0), line, m.start() - base, ev, 0):
                pieces.append(text[at:m.end()])
                at = m.end() + len(mark)
        pieces.append(text[at:])
        fixed = "".join(pieces)
        out.append({**sec, "content": fixed} if fixed != text else sec)
    return out


def _line_of(text: str, pos: int) -> tuple[str, int]:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end >= 0 else len(text)], start


_OWNER_HEADER = re.compile(r"담당|책임|owner", re.I)
_DUE_HEADER = re.compile(r"기한|마감|완료일|납기|due|deadline", re.I)
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}")
_MONTH_DAY = re.compile(r"(\d{1,2})\s*[/.월]\s*(\d{1,2})\s*일?")
_ISO_DAY = re.compile(r"\d{4}-(\d{2})-(\d{2})")
_BLANK_CELLS = {"", "미정", "(미정)", "-", "—", "tbd", "n/a", "없음"}


def _cell_sourced(
    cell: str, compact: str, row: str = "", dates: list[dict] | None = None
) -> bool:
    """Owner or due-date cell names something in the request or material (dates bound to their source line)."""
    flat = re.sub(r"\s+", "", cell)
    if placeholders.is_blank(cell) or flat.lower().strip("*_`") in _BLANK_CELLS or (
        flat in compact and dates is None
    ):
        return True
    days = [(int(m), int(d)) for m, d in _MONTH_DAY.findall(flat)] + [
        (int(m), int(d)) for m, d in _ISO_DAY.findall(flat)
    ]
    if dates is not None and days:
        entries = report_checks.bound_rows(row, dates)
        if entries:
            written = report_checks._cell_dates(cell)
            return any(report_checks.date_matches(w, e) for w in written for e in entries)
    elif dates is not None and flat in compact:
        return True
    for month, day in days:
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        spellings = (
            f"{month}/{day}",
            f"{month:02d}/{day:02d}",
            f"{month}월{day}일",
            f"{month:02d}월{day:02d}일",
            f"{month}.{day}",
            f"-{month:02d}-{day:02d}",
        )
        if any(sp in compact for sp in spellings):
            return True
    if days:
        return False
    words = [w.strip("*_`()") for w in re.split(r"[\s,·/()]+", cell)]
    return any(len(w) >= 2 and w in compact for w in words)


#: a cell that names a time: digits or relative-time words (「다음 회의 때」, 「이번 주」)
_TIME_WORDS = re.compile(r"\d|다음|이번|지난|내일|모레|글피|주|월|요일|회의|때|까지|분기|반기|초|말|중순|순")


def _unsourced_owner_dates(text: str, source: str, dates: list[dict] | None = None) -> str:
    """Unmentioned 담당/기한 cells become 「[담당자]」/「[기한]」 blanks."""
    compact = re.sub(r"\s+", "", source)
    if not compact or "|" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("|") and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            # one combined 「담당·기한」 column holds sentences: not a blank-able value
            guarded = [
                k for k, h in enumerate(header)
                if bool(_OWNER_HEADER.search(h)) != bool(_DUE_HEADER.search(h))
            ]
            out.extend((line, lines[i + 1]))
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if guarded:
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    row = " ".join(c for k, c in enumerate(cells) if k not in guarded or
                                   _OWNER_HEADER.search(header[k] if k < len(header) else ""))
                    for k in guarded:
                        if k < len(cells) and _DUE_HEADER.search(header[k] if k < len(header) else "") \
                                and not _TIME_WORDS.search(cells[k]):
                            # 「기준」, 「동일」: words about a due, not a day the writer made up
                            continue
                        if k < len(cells) and _OWNER_HEADER.search(
                                header[k] if k < len(header) else "") \
                                and report_checks._ANY_OWNER.search(cells[k].strip()):
                            # 「전원」 「각 담당자」: everyone, no one invented (r7 A01-T1)
                            continue
                        if k < len(cells) and not _cell_sourced(cells[k], compact, row, dates):
                            cells[k] = placeholders.for_header(header[k] if k < len(header) else "")
                    out.append("| " + " | ".join(cells) + " |")
                else:
                    out.append(lines[i])
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


#: 「10월 11일(일)」, 「10/11(일)」, 「2026-10-11」 with the particle after it
_DAY_WRITTEN = re.compile(
    r"(?<![\d./-])(?:(?P<y>20\d{2})\s{0,2}(?:[-./]|년)\s{0,2})?(?P<m>\d{1,2})\s{0,2}(?:월\s{0,2}|/|[-.]\s{0,2})"
    r"(?P<d>\d{1,2})(?![\d/])\s{0,2}일?\.?(?:\s{0,2}[(（][월화수목금토일][)）])?"
    r"(?P<josa>과|와|을|를|이|가|은|는)?")
_AFTER_VOWEL = {"과": "와", "을": "를", "이": "가", "은": "는"}


def blank_today(text: str, source: str, today: dt.date) -> str:
    """Today's date the source never gives (the writer's clock, not the meeting's) → 「[일자]」."""
    given = {(int(m.group("m")), int(m.group("d"))) for m in _DAY_WRITTEN.finditer(source or "")}
    if (today.month, today.day) in given:
        return text

    def one(m: re.Match) -> str:
        if (int(m.group("m")), int(m.group("d"))) != (today.month, today.day) or (
                m.group("y") and int(m.group("y")) != today.year):
            return m.group(0)
        josa = m.group("josa") or ""
        return "[일자]" + _AFTER_VOWEL.get(josa, josa)

    return _DAY_WRITTEN.sub(one, text or "")


def local_today() -> dt.date:
    """Today in `settings.timezone` (the writer's 「Today is …」)."""
    try:
        zone = ZoneInfo(settings.timezone)
    except Exception:  # noqa: BLE001 — misconfigured tz must not break writing
        zone = dt.UTC
    return dt.datetime.now(zone).date()


def _facts_line(request: str, sources: list[dict[str, Any]]) -> str:
    """Closed list of numbers the document may use."""
    found: list[str] = []
    for text in [request, *[str(s.get("quote") or "") for s in sources]]:
        for match in _NUMBER.finditer(text):
            token = re.sub(r"\s+", "", match.group(0))
            if len(token) < 2 or token.isdigit() and len(token) > 4:
                continue
            if token not in found:
                found.append(token)
    if not found:
        line = (
            "쓸 수 있는 수치: none. The request and material contain no figures at all — **write "
            "no amounts, periods, head counts, percentages, measurements, component values or "
            "performance gains of any kind.** Leave each place a value would go as its name in "
            "brackets, like 「[금액]」「[측정값]」, and instead of computing or inventing a value, say "
            "what must be measured or confirmed and how. Formulas and general principles "
            "(such as f_c = 1/(2πRC)) are fine. Only when the request itself asks for an "
            "estimate (시장 규모, TAM·SAM·SOM, 손익 전망) set the inputs it needs as stated "
            "「가정」 values with their basis and compute from them."
        )
        if _DECISION.search(request):
            line += (
                " The 「비용 계산의 전제」 section lists what must be confirmed, such as quotes, "
                "the budget ceiling and the number of people covered, and the comparison table's "
                "rows are 「필요한 것」 「위험」 「되돌릴 수 있는가」 instead of costs."
            )
        return line
    line = "쓸 수 있는 수치 (every figure in the request and material): " + ", ".join(found[:40])
    if derived := derived_values(request):
        # arithmetic done here; writer copies finished figures
        line += (
            "\n계산된 값 (do not recompute; use each once, in the section that needs it such as"
            " cost or comparison, and do not repeat it in other sections; table cells hold only the"
            " value, the formula goes once in the header or a sentence): "
            + "; ".join(derived)
        )
    return line


_MONTHLY_WON = re.compile(r"월\s*(?:약\s*)?([\d,]+(?:\.\d+)?)\s*만\s*원")
_HORIZON_MONTHS = re.compile(r"(\d{1,3})\s*개월")
_HORIZON_YEARS = re.compile(r"(\d)\s*년\s*(?:TCO|총|치|간|동안|기준)")
_ANNUAL_BUDGET = re.compile(r"연\s*(?:간\s*)?예산\s*([\d,]+(?:\.\d+)?)\s*만\s*원")
_ADDED_MONTHLY = re.compile(r"추가\s{0,8}\(?\s{0,8}월\s{0,8}(?:약\s{0,8})?([\d,]{1,15}(?:\.\d{1,15})?)\s{0,8}만\s{0,8}원")


def _won(value: float) -> str:
    """만 원 figure in Korean notation (「1억 6,920만 원」)."""
    whole = round(value)
    if abs(value - whole) > 1e-6:
        return f"{value:,.1f}만 원"
    if whole >= 10000:
        eok, man = divmod(whole, 10000)
        return f"{eok}억 {man:,}만 원" if man else f"{eok}억 원"
    return f"{whole:,}만 원"


def _label_before(text: str, end: int) -> str:
    """Clause naming the amount."""
    head = text[max(0, end - 40):end]
    head = re.split(r"[,:;\n•·()]|\s(?:와|과|및)\s", head)[-1]
    return re.sub(r"\s{0,8}(?:은|는|이|가|의)?\s{0,8}월\s{0,8}(?:약\s{0,8})?$", "", head).strip(" -–—")[:24]


def derived_values(text: str) -> list[str]:
    """Finished arithmetic as 「식 = 값」 lines, or `[]`."""
    monthly: list[tuple[str, float]] = []
    for match in _MONTHLY_WON.finditer(text):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        label = _label_before(text, match.start())
        if all(abs(value - v) > 1e-9 or label != lab for lab, v in monthly):
            monthly.append((label, value))
    if not monthly:
        return []
    horizons: list[int] = []
    budget = None
    if m := _ANNUAL_BUDGET.search(text):
        budget = float(m.group(1).replace(",", ""))
    if budget is not None or re.search(r"연\s*(?:간|비용|예산)|1년|연간", text):
        horizons.append(12)
    for m in _HORIZON_MONTHS.finditer(text):
        n = int(m.group(1))
        if 2 <= n <= 120 and n not in horizons:
            horizons.append(n)
    for m in _HORIZON_YEARS.finditer(text):
        n = int(m.group(1)) * 12
        if n not in horizons:
            horizons.append(n)
    added = [float(m.group(1).replace(",", "")) for m in _ADDED_MONTHLY.finditer(text)]
    lines: list[str] = []
    for label, value in monthly[:6]:
        if any(abs(value - a) < 1e-9 for a in added):
            continue
        name = f"{label} " if label else ""
        for h in horizons:
            lines.append(f"{name}{_won(value)} × {h}개월 = {_won(value * h)}")
    if added and monthly:
        base_label, base = monthly[0]
        for a in added:
            for h in horizons:
                lines.append(
                    f"({_won(base).replace(' 원', '')} + {_won(a).replace(' 원', '')}) × {h}개월"
                    f" = {_won((base + a) * h)}"
                )
    if budget is not None:
        for label, value in monthly[:6]:
            if any(abs(value - a) < 1e-9 for a in added):
                continue
            annual = value * 12
            gap = annual - budget
            verdict = f"예산보다 {_won(abs(gap))} {'초과' if gap > 0 else '여유'}" if gap else "예산과 같음"
            lines.append(f"{label + ' ' if label else ''}연 {_won(annual)} vs 연 예산 {_won(budget)}: {verdict}")
    return lines[:14]


def _others_line(headings: list[str], index: int) -> str:
    """What other sections own."""
    others = [h for i, h in enumerate(headings) if i != index and h.strip()]
    if not others:
        return ""
    return (
        "Owned by other sections (do not write them here): "
        + " / ".join(others)
        + ". Conclusions, recommendations and next steps go only in the sections named for them."
    )


_SUMMARY_WORDS = ("요약", "개요", "핵심", "결론", "제언", "executive", "summary")
_COMPARE_WORDS = ("비교", "분석", "비용", "대안", "옵션", "검토", "평가", "현황", "결과", "이력")
_PLAN_WORDS = ("일정", "계획", "추진", "실행", "절차", "단계", "로드맵", "방법")
_PEOPLE_WORDS = ("이해관계자", "역할", "산출물", "목표", "담당", "체계", "조직")
_TREND_WORDS = ("추이", "추세", "변화", "월별", "연도별", "분기별", "시계열")


def _section_role(heading: str, index: int, total: int, written: str) -> tuple[str, str]:
    """`(role, block_rules)` for a section; callout and kpi are offered once per document."""
    name = heading.lower()
    has = lambda words: any(w in name for w in words)  # noqa: E731
    allowed: list[str] = []
    if has(_SUMMARY_WORDS) and index == 0:
        role = (
            "A summary for someone who will not read the report: what must be decided, what is "
            "recommended, and two reasons, in one or two paragraphs. Prose only — no tables, "
            "blocks or lists. About 200 characters."
        )
        return role, ""
    if has(_SUMMARY_WORDS) and index == total - 1:
        role = (
            "Conclusion: the one thing that remains from what came before, and what to do next, "
            "in one paragraph. No tables or blocks."
        )
        return role, ""
    if has(_TREND_WORDS):
        allowed.append("chart")
    if has(_COMPARE_WORDS):
        allowed.append("table")
        if "```kpi" not in written:
            allowed.append("kpi")
    if has(_PLAN_WORDS):
        allowed.append("steps")
        allowed.append("table")
    if has(_PEOPLE_WORDS):
        allowed.append("cards")
    if not allowed:
        allowed.append("table")
    if "```callout" not in written and index >= total - 2:
        allowed.append("callout")
    seen: list[str] = []
    for one in allowed:
        if one not in seen:
            seen.append(one)
    role = ("Body section: write what this section's heading promises. Blocks only of the "
            "kinds allowed below, at most one per section.")
    rules = "\n".join(_BLOCK_SYNTAX[one] for one in seen)
    return role, "- Blocks this section may use:\n" + rules


#: plain-text formats: no KPI tiles or callouts
PLAIN_FORMATS = frozenset({"official", "press", "minutes", "result_report", "internal_draft"})
#: forms and notices read as text: no pictures unless asked, no tiles or callouts
_FORM_DOCUMENT = re.compile(
    r"IRB|심의\s{0,2}신청|신청서|공고문|모집\s{0,2}공고|FAQ|자주\s{0,2}묻는\s{0,2}질문|안내문", re.I)


def form_document(request: str) -> bool:
    """Whether the ask is a form or notice (IRB 신청서, 공고문, FAQ, 안내문)."""
    return bool(_FORM_DOCUMENT.search(lead_instruction(request or "")))
_BOX = re.compile(r"^```(?P<kind>kpi|callout)[ \t]*\n(?P<body>.*?)^```[ \t]*$\n?", re.S | re.M)


def plain_blocks(text: str, document: str = "") -> str:
    """kpi rows and callouts as plain lines; a kpi value `document` already states is dropped
    (no trailing dump of figures the tables and sentences give)."""
    told = re.sub(r"\s", "", _BOX.sub("", document or ""))

    def one(m: re.Match) -> str:
        lines = [line.strip() for line in m.group("body").splitlines() if line.strip()]
        if not lines:
            return ""
        if m.group("kind") == "kpi":
            rows = []
            for line in lines:
                value, _, name = line.partition("|")
                if told and re.sub(r"\s", "", value) in told:
                    continue
                rows.append(f"- {name.strip()}: {value.strip()}" if name.strip() else f"- {value.strip()}")
            return "\n".join(rows) + "\n" if rows else ""
        head, rest = lines[0], " ".join(lines[1:])
        return f"※ {head}" + (f": {rest}" if rest else "") + "\n"
    return _BOX.sub(one, text or "")


#: lone 「본문」 section: no chapters, printed without heading
_BODY_ONLY = re.compile(r"^\s*(?:본문|내용|body)\s*$", re.I)


def untitled_body(sections: list[dict], outline: tuple[str, ...] | list[str]) -> list[dict]:
    """Heading dropped for a lone 「본문」 outline."""
    if len(outline) != 1 or not _BODY_ONLY.match(outline[0]) or len(sections) != 1:
        return sections
    return [{**sections[0], "heading": ""}]


#: empty block makes the model invent citations
_NO_REFS = "(none. Do not use numbered citations.)"


#: 429 backoff without reset time; ~70 s covers a TPM window
_BACKOFF = (2.0, 6.0, 12.0, 20.0, 30.0)


async def title_block_for(
    fmt: doc_formats.DocFormat, request: str, title: str, model: str, api_key: str,
    material: str = "",
) -> tuple[dict[str, Any], dict[str, int]]:
    """Format head fields: code-read labels first, model for the rest, else blank."""
    given = f"{request}\n{material}" if material.strip() else request
    read = doc_formats.head_values(fmt, given)
    spent = {"inputTokens": 0, "outputTokens": 0}
    answer: dict[str, Any] = {}
    if any(not read.get(name) for name in fmt.fields) or fmt.subtitle or fmt.keywords:
        try:
            text, spent = await _complete(
                model,
                [{"role": "user", "content": doc_formats.fill_prompt(fmt, request, title, material)}],
                api_key,
                400,
            )
            answer = _json_object_of(text)
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.info("title block fill failed: %s", exc)
    return doc_formats.title_block(fmt, answer, given, read), spent


def _json_object_of(text: str) -> dict[str, Any]:
    block = text[text.find("{") : text.rfind("}") + 1] if "{" in (text or "") else ""
    try:
        data = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_MOST_TOKENS = 16000


def _tiebreak_model(model: str) -> str:
    """Larger self-hosted model for tie-breaking; strict-local twin on strict-local turns."""
    big = settings_store.cached_model_roles().code_fallback
    if model.startswith("strict-local/"):
        if big.startswith("local/"):
            return "strict-local/" + big.split("/", 1)[1]
        return big if big.startswith("strict-local/") else model
    return big or model


async def _complete(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int,
) -> tuple[str, dict]:
    """Non-streaming call returning `(text, usage)`; retries 429."""
    base, _ = await settings_store.litellm_config()
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
    ) as client:
        transient_retried = False
        for attempt in range(len(_BACKOFF) + 1):
            try:
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": thinking.room(model, max_tokens),
                        # thinking off where allowed (`thinking.switch`)
                        **thinking.switch(model),
                    },
                )
            except httpx.TimeoutException:
                # transient timeout: one retry
                if transient_retried or attempt == len(_BACKOFF):
                    raise
                transient_retried = True
                log.info("report call timed out, retrying once")
                await asyncio.sleep(3)
                continue
            if (
                response.status_code in (502, 503, 504)
                and not transient_retried
                and attempt < len(_BACKOFF)
            ):
                transient_retried = True
                log.info("report call got %s, retrying once", response.status_code)
                await asyncio.sleep(5)
                continue
            if thinking.refused(model, response):
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": model, "messages": messages,
                        "max_tokens": thinking.room(model, max_tokens),
                        **thinking.switch(model),
                    },
                )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            # TPM 429 names its reset time
            delay = ratelimit.retry_delay(response.text, dict(response.headers), _BACKOFF[attempt])
            log.info("report call rate limited, retrying in %.0fs", delay)
            await asyncio.sleep(delay)
        response.raise_for_status()
        payload = response.json()

    # reasoning model may exhaust the ceiling thinking; asked twice, the second time with
    # what the first re-ask thought on top (r7 A04-T2: GLM-5.3 thought through 3,100 tokens
    # too and every section failed 「empty section」)
    asked = thinking.room(model, max_tokens)
    for _ in range(2):
        bigger = min(thinking.starved(payload, asked), _MOST_TOKENS)
        if bigger <= asked:
            break
        asked = bigger
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
                # gateway rejecting `reasoning` refuses the whole call
                again = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": messages, "max_tokens": bigger},
                )
        if again.status_code == 200:
            retried = again.json()
            spent = retried.get("usage") or {}
            first = payload.get("usage") or {}
            # both calls billed and counted
            payload = retried
            payload["usage"] = {
                "prompt_tokens": int(first.get("prompt_tokens") or 0)
                + int(spent.get("prompt_tokens") or 0),
                "completion_tokens": int(first.get("completion_tokens") or 0)
                + int(spent.get("completion_tokens") or 0),
            }

    # cut-off answer re-asked once with more room
    choice = (payload.get("choices") or [{}])[0]
    roomier = min(max_tokens * 2, _MOST_TOKENS)
    if (choice.get("finish_reason") == "length" and (choice.get("message") or {}).get("content")
            and roomier > max_tokens):
        log.info("%s: answer cut at %s tokens, re-asking with %s", model, max_tokens, roomier)
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
        ) as client:
            again = await client.post(
                "/v1/chat/completions",
                json={"model": model, "messages": messages, "max_tokens": roomier,
                      **thinking.switch(model)},
            )
        if again.status_code == 200:
            first = payload.get("usage") or {}
            payload = again.json()
            spent = payload.get("usage") or {}
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


_THINK_BLOCK = re.compile(r"<think>[\s\S]*?</think>", re.I)


async def _complete_thinking(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int,
) -> tuple[str, dict]:
    """Thinking-on call for self-hosted models; no retries."""
    switch = thinking.template_switch_on(model)
    if not switch:
        raise ValueError(f"{model} has no thinking switch")
    base, _ = await settings_store.litellm_config()
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"model": model, "messages": messages, "max_tokens": max_tokens, **switch},
        )
        response.raise_for_status()
        payload = response.json()
    message = (payload.get("choices") or [{}])[0].get("message") or {}
    text = _THINK_BLOCK.sub("", message.get("content") or "").strip()
    raw = payload.get("usage") or {}
    return text, {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }


#: Korean label → renderer style name
_STYLES = {"편집형": "editorial", "포스터형": "poster", "미니멀": "minimal"}


def _outline_style(text: str) -> str:
    """Outline's style, or `""`; regex so partial answers keep it."""
    match = re.search(r'"style"\s*:\s*"([^"]+)"', text)
    return _STYLES.get((match.group(1).strip() if match else ""), "")


_subject_missing = grounding.subject_missing


#: own-work documents empty without material
_RESULTS = re.compile(r"결과|시험|실험|측정|캡스톤|제안서|설계 변경|기획서|연구 ?계획")
#: web-sourced documents needing a search
_FROM_THE_WEB = re.compile(
    r"동향|조사해|문헌|선행 ?연구|최근 (?:\d+ ?년|연구)|연구를 정리|현황을 조사|비교표|인용|"
    r"참고문헌|시카고|APA|출처를"
)


def _from_the_web(request: str) -> bool:
    return bool(_FROM_THE_WEB.search(request))


#: words pointing at unattached material
_MATERIAL = re.compile(
    r"녹취|녹음|피드백|기록을|표가 있|파일|첨부|메모를|학위논문|논문 ?\d+ ?장"
    r"|(?:표|자료)를 (?:붙|드립|줍|보냅|첨부)"
)


_PAGES = re.compile(r"(?<!\d)(\d{1,4})\s{0,3}(?:장|쪽|페이지|p)\s{0,3}(?:이상|분량|짜리|내외|정도)")


def _long_form(request: str) -> bool:
    """Per-section calls: 8+ pages, or substantial material with 5+ parts."""
    m = _PAGES.search(request)
    if m and int(m.group(1)) >= 8:
        return True
    listed = _LISTED.search(instruction_part(request))
    parts = len([p for p in re.split(r"[,，]", listed.group(1)) if p.strip()]) if listed else 0
    return parts >= 5 and len(pasted_material(request)) >= 3000


_FIGURE_LINE = re.compile(r"^!\[(?P<alt>[^\]]{0,200})\]\((?P<src>[^)\s]{1,8000000})\)\s*$")
_CAPTION_LINE = re.compile(
    r"^\s{0,3}[*_]{0,2}\s{0,2}(?P<kind>그림|표|Figure|Table)\s{0,2}\d{0,3}\s{0,2}[.:．：]?\s{0,2}"
    r"(?P<text>.{1,160}?)\s{0,2}[*_]{0,2}\s*$",
    re.I,
)


_TRAILING_TABLE_CAPTION = re.compile(
    r"(?<=[.!?。\]])\s{1,4}[*_]{0,2}표\s{0,2}\d{0,3}\s{0,2}[.:：]\s{0,2}(?P<text>[^.!?\n]{2,80}?)[*_]{0,2}\s*$"
)


def repair_pipe_tables(body: str) -> str:
    """Bare 「a | b | c」 lines (2+, equal cells) as a Markdown table; a half-formed table (a
    bare header and rule, a row ending in a bare 「|」) rebuilt whole first, so its first row
    never becomes the header (r9 LFLF R3-A01-T1)."""
    lines = table_mend.rebuild(body or "").split("\n")
    out: list[str] = []
    i = 0

    def cells(line: str) -> list[str] | None:
        text = line.strip()
        if not text or text.startswith(("|", "```", "#", ">", "!")) or " | " not in text:
            return None
        # 「- 비목 | 예산」: the list marker is not cell text
        text = re.sub(r"^(?:[-*•·]|\d{1,2}[.)])\s+", "", text)
        parts = [c.strip() for c in text.split("|")]
        return parts if len(parts) >= 2 and all(parts) else None

    fenced = False
    while i < len(lines):
        if lines[i].strip().startswith("```"):
            fenced = not fenced
        # nothing in a fence is a table
        first = None if fenced else cells(lines[i])
        if first is None:
            out.append(lines[i])
            i += 1
            continue
        run = [first]
        j = i + 1
        while j < len(lines) and (row := cells(lines[j])) is not None and len(row) == len(first):
            run.append(row)
            j += 1
        if len(run) < 2:
            out.append(lines[i])
            i += 1
            continue
        if out and out[-1].strip():
            out.append("")
        out.append("| " + " | ".join(run[0]) + " |")
        out.append("|" + "---|" * len(run[0]))
        out.extend("| " + " | ".join(row) + " |" for row in run[1:])
        if j < len(lines) and lines[j].strip():
            out.append("")
        i = j
    return "\n".join(out)


def split_glued_tables(body: str) -> str:
    """Repeated head row and rule start a second table."""
    lines = (body or "").split("\n")
    rule = re.compile(r"^\s*\|?\s*:?-{2,}")
    out: list[str] = []
    for i, line in enumerate(lines):
        starts_new = (
            line.strip().startswith("|") and i + 1 < len(lines) and rule.match(lines[i + 1])
            and out and out[-1].strip().startswith("|") and not rule.match(out[-1])
        )
        if starts_new:
            out.append("")
        out.append(line)
    return "\n".join(out)


def balance_fences(body: str) -> str:
    """Unpaired fences mended so none swallows the section."""
    lines = (body or "").split("\n")
    fences = [i for i, line in enumerate(lines) if line.strip().startswith("```")]
    if len(fences) % 2 == 0:
        return body
    for i in fences:
        if lines[i].strip() != "```":
            continue
        after = next((line for line in lines[i + 1:] if line.strip()), "")
        if re.search(r"[가-힣]{2,}.*(?:다|요)[.!?]", after) and not after.strip().startswith("```"):
            del lines[i]
            return "\n".join(lines)
    return "\n".join(lines).rstrip() + "\n```"


def unglue_blocks(body: str) -> str:
    """Fence or table row glued to a sentence moved to its own line."""
    body = re.sub(r"(?m)^([^\n`|]*\S)[ \t]+(```[A-Za-z]*)[ \t]*$", r"\1\n\n\2", body or "")
    body = re.sub(r"(?m)^(?!```)([^\n`]*[.!?。])[ \t]+```[ \t]*$", r"\1\n```", body)
    return re.sub(r"(?m)^([^\n|]*[.!?。\]])[ \t]+(\|[^\n]*\|)[ \t]*$", r"\1\n\n\2", body)


def _caption_text(line: str, kind: str) -> str | None:
    m = _CAPTION_LINE.match(line or "")
    if not m or m.group("kind").lower() not in (kind, {"그림": "figure", "표": "table"}[kind]):
        return None
    return m.group("text").strip(" *_.:")


#: sentence pointing into a source's internals (「매뉴얼 제2장」, 「p. 12」)
_SOURCE_INNARDS = re.compile(
    r"(?:매뉴얼|보고서|백서|가이드라인|가이드|안내서|지침|자료|문서)\s{0,2}(?:의\s{0,2})?"
    r"(?:제\s?\d{1,3}\s?(?:장|절|부|편)|\d{1,3}\s?(?:장|절)(?=[\s에은는의]))"
    r"|(?<![가-힣])제\s?\d{1,3}\s?장\s{0,2}['‘「“\"]"
    r"|(?<![가-힣A-Za-z])pp?\.\s?\d{1,4}"
    r"|(?:해당|이|그|동|위)\s(?:매뉴얼|백서|가이드라인|가이드|안내서|지침|자료|문서)(?:은|는|이|가|에서|에|의)"
)
# single `.` alternative avoids ambiguity; dotted dates/numbers end a sentence only at line end
_SENTENCE = re.compile(r"(?:[^.!?\n]|(?:(?<=\bp)|(?<=\bpp)|(?<=\d)(?=\.\d)|(?<=\d)(?=\.(?:\s{1,2}\d|\s{0,2}\())|(?:(?<=\d\.\s\d)|(?<=\d\.\s\d\d))(?!\.\s{0,2}[\d(])(?!\.\s*$))\.){0,2000}?(?<!\bp)(?<!\bpp)(?:(?!(?<=\d)\.\s{0,2}[\d(])(?<!\d\.\s\d)(?<!\d\.\s\d\d)[.!?]|(?:(?<=\d\.\s\d)|(?<=\d\.\s\d\d))\.(?=\s*$))(?:\s{0,2}\[\d{1,3}\](?:\[\d{1,3}\]){0,20})?(?=\s|$)")


def source_innard_sentences(sections: list[dict]) -> dict[int, list[str]]:
    """Per section, sentences pointing into source internals; references skipped."""
    found: dict[int, list[str]] = {}
    for index, sec in enumerate(sections):
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            continue
        for line in str(sec.get("content") or "").split("\n"):
            if line.lstrip().startswith(("|", "```", "!", "#")):
                continue
            for m in _SENTENCE.finditer(line):
                sentence = m.group(0).strip()
                if _SOURCE_INNARDS.search(sentence):
                    found.setdefault(index, []).append(sentence)
    return found





_TRAILING_CITE = re.compile(r"\s{0,2}((?:\[\d{1,3}(?:[,，]\s?\d{1,3})*\]){1,6})(?=\s{0,2}[.!?。]?\s*$)")


def _cited(sentence: str) -> tuple[str, ...] | None:
    m = _TRAILING_CITE.search(sentence)
    if not m:
        return None
    return tuple(sorted(set(re.findall(r"\d{1,3}", m.group(1)))))


def collapse_repeated_citations(body: str) -> str:
    """Same-source citations in consecutive sentences kept once, on the last."""
    out: list[str] = []
    for block in re.split(r"(\n\s*\n)", body or ""):
        stripped = block.strip()
        if (
            not stripped
            or "\n" in stripped
            or re.match(r"^(?:\||#|>|[-*+]\s|[□■○◦ㅇㆍ·∙※]\s|\d{1,3}[.)]\s|```|!\[|\*\*표|\*그림)", stripped)
        ):
            out.append(block)
            continue
        parts = re.split(r"(?<=[.!?。])" + units.NO_NUMBER_DOT_BREAK + r"(\s{1,64})(?=\S)", block)
        sentences = parts[0::2]
        cites = [_cited(sentence) for sentence in sentences]
        for index in range(len(sentences) - 1):
            if not cites[index] or cites[index] != cites[index + 1]:
                continue
            bare = _TRAILING_CITE.sub("", sentences[index], count=1)
            # figures keep their own citation for the evidence check
            if re.search(r"\d", bare):
                continue
            sentences[index] = bare
        parts[0::2] = sentences
        out.append("".join(parts))
    return "".join(out)


#: 「[[그림: kind | caption | body]]」; the body may hold single brackets (「[김도윤: 검수]」, r5
#: A01-T1 left the whole mark in the minutes)
_FIGURE_MARK = re.compile(r"\[\[\s*그림\s*[:：]\s*(?P<body>(?:[^\]\n]|\](?!\])){3,900}?)\s*\]\]")
#: a mark left open to the line's end: taken out, never shown
_FIGURE_OPEN = re.compile(r"[ \t]*\[\[\s*그림\s*[:：][^\n]*")
_FIGURE_REF, _TABLE_REF = "〔그림〕", "〔표〕"
#: phrase around a reference to an undrawable figure
_DANGLING_REF = re.compile(
    r"(?:위|아래|다음)?\s?〔(?:그림|표)〕(?:과|와|에서|에|은|는|을|를|의|처럼)?\s*"
    r"(?:같이|처럼|보듯이|보듯|나타낸 것처럼|정리한 것처럼|나타냈듯이)?\s*,?\s*"
)


def _figure_markdown(made: dict, png: bytes) -> str:
    """Figure drawn with deck shapes, plus caption."""
    caption = str(made.get("caption") or "").strip() or "그림"
    return f"![{caption}]({diagram_render.data_uri(png)})\n\n*그림: {caption}*"


def figure_marks(body: str) -> list[dict[str, str]]:
    """Placed figures: kind, caption, description, mark."""
    out = []
    for m in _FIGURE_MARK.finditer(body or ""):
        parts = [x.strip() for x in m.group("body").split("|")]
        kind = parts[0].lower() if parts else ""
        if kind not in diagrams.FIGURES or len(parts) < 3:
            out.append({"mark": m.group(0), "kind": "", "caption": "", "description": ""})
            continue
        out.append({"mark": m.group(0), "kind": kind, "caption": parts[1][:80],
                    "description": " | ".join(parts[2:])[:700]})
    return out


#: a sentence's end: a full stop or a closing mark after it, a citation, a closed quote
_SENTENCE_DONE = re.compile(r"(?:[.!?。…]|[.!?。][)\]」』\"'*]+|\[\d{1,3}(?:\s?,\s?\d{1,3})*\][.]?)\s*$")
_LAST_STOP = re.compile(r"[.!?。](?:\s?\[\d{1,3}(?:\s?,\s?\d{1,3})*\])?(?=\s)")


def cut_unfinished_tail(body: str) -> str:
    """A section whose last paragraph stops mid-sentence (「… 이러한 부처별 추」, the call ran
    out) ends at its last whole sentence; tables, lists, headings and fences are left alone."""
    text = (body or "").rstrip()
    if not text:
        return body
    start = text.rfind("\n") + 1
    last = text[start:]
    stripped = last.strip()
    if (not stripped or stripped.startswith(("|", "#", "-", "*", "+", ">", "```", "!", "["))
            or re.match(r"\d{1,3}[.)]\s", stripped) or "```" in text[start - 4:start]
            or text.count("```") % 2 or _SENTENCE_DONE.search(stripped)
            or re.search(r"[다요음함임됨:：)\]」』”\"'*_]$", stripped)):
        return body
    stops = list(_LAST_STOP.finditer(last))
    if not stops:
        return body
    kept = last[:stops[-1].end()].rstrip()
    # only a short dangling piece goes: a long one is a sentence the writer meant
    if len(last) - len(kept) > 200:
        return body
    return text[:start] + kept


def shown_while_writing(body: str) -> str:
    """Section as shown before figures are drawn."""
    return _FIGURE_MARK.sub(
        lambda m: f"*〔그림 준비 중: {(m.group('body').split('|') + ['', ''])[1].strip() or '그림'}〕*",
        body or "",
    )


def drop_figure(body: str, mark: str) -> str:
    """Undrawable figure's mark and nearest 〔그림〕 reference removed."""
    at = body.find(mark)
    if at < 0:
        return body
    head, tail = body[:at], body[at + len(mark):]
    ref = head.rfind(_FIGURE_REF)
    if ref >= 0:
        # the reference with its particle (「〔그림〕에서」), with or without 「위」 before it
        match = next((m for start in range(max(0, ref - 3), ref + 1)
                      if (m := _DANGLING_REF.match(head, start)) and m.end() > ref), None)
        if match:
            before, after = head[: match.start()].rstrip(" "), head[match.end():].lstrip(" ")
            joint = " " if before and not before.endswith("\n") and after else ""
            head = before + joint + after
        else:
            head = head[:ref] + head[ref + len(_FIGURE_REF):]
    return re.sub(r"\n{3,}", "\n\n", head.rstrip() + "\n\n" + tail.lstrip())


#: repeated metrics; one kind may have several names
_METRIC_KINDS = {
    "growth": r"성장률|CAGR|연평균\s?성장|연평균",
    "size": r"시장\s?규모|시장\s?가치|시장은|시장이",
    "share": r"점유율|비중",
    "margin": r"이익률|마진",
    "conversion": r"전환율|클릭률|CTR",
    "price": r"ARPU|객단가|판매가|단가",
}
_METRIC_VALUE = re.compile(
    r"(\d{1,4}(?:[.,]\d{1,3})?)\s?(%|억\s?달러|조\s?원|억\s?원|만\s?달러|달러|원)"
)


def metric_conflicts(sections: list[dict]) -> list[list[tuple[int, str]]]:
    """Groups of sentences giving one metric different values in the same unit."""
    found: dict[tuple[str, str], list[tuple[str, frozenset[str], int, str]]] = {}
    for index, sec in enumerate(sections):
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            continue
        for line in str(sec.get("content") or "").split("\n"):
            if line.lstrip().startswith(("|", "```", "!", "#", "**표")):
                continue
            for m in _SENTENCE.finditer(line):
                sentence = m.group(0).strip()
                values = [(n, re.sub(r"\s", "", u)) for n, u in _METRIC_VALUE.findall(sentence)]
                for kind, pattern in _METRIC_KINDS.items():
                    if not re.search(pattern, sentence):
                        continue
                    mine = [v for v in values if (v[1] == "%") != (kind in ("size", "price"))]
                    if len(mine) == 1:
                        number, unit = mine[0]
                        found.setdefault((kind, unit), []).append(
                            (number.replace(",", ""), _scope(sentence), index, sentence)
                        )
                    break
    groups = []
    for entries in found.values():
        # conflict only when one scope contains the other
        clash: dict[int, tuple[int, str]] = {}
        for i, (value_a, scope_a, index_a, sentence_a) in enumerate(entries):
            for value_b, scope_b, index_b, sentence_b in entries[i + 1:]:
                if value_a != value_b and (scope_a <= scope_b or scope_b <= scope_a):
                    clash[id(sentence_a)] = (index_a, sentence_a)
                    clash[id(sentence_b)] = (index_b, sentence_b)
        if clash:
            groups.append(list(clash.values())[:6])
    return groups


_SCOPE_NOISE = frozenset({"연평균", "성장률", "CAGR", "시장", "규모", "점유율", "비중", "이익률",
                          "전환율", "약", "기준", "현재", "전망", "예상"})


def _scope(sentence: str) -> frozenset[str]:
    """Subject words before the first topic/subject particle."""
    head = re.split(r"(?<=[가-힣A-Za-z)])(?:은|는|이|가)\s", sentence, maxsplit=1)[0][:60]
    words = re.findall(r"[가-힣A-Za-z]+", head)
    return frozenset(w for w in words if w not in _SCOPE_NOISE)





#: model-only findings, for the reader to check
_TO_CHECK = ("fact", "question", "judged_figure", *report_checks.MODEL_LABELS)

_QUALITY_LABELS = {
    "unmentioned": "본문이 언급하지 않는 그림·표", "particle": "번호 뒤 조사",
    "figure_numbering": "그림 번호", "table_numbering": "표 번호",
    "table_without_caption": "캡션 없는 표", "fence_glued": "깨진 블록",
    "stray_caption": "떨어진 표 캡션", "pipe_rows_not_a_table": "표로 읽히지 않는 줄",
    "leftover_mark": "남은 표시", "mixed_script": "다른 문자 섞임", "literal_br": "<br> 노출",
    "placeholder": "자리표시", "source_innards": "출처 내부 지칭",
    "prompt_talk": "작성 지침 언급", "fence_language_apart": "블록 형식 줄 분리",
    "cut_table_row": "잘린 표 행", "table_rows": "빠진 표 행·합계와 다른 행",
    "carried_number_changed": "자료와 자릿수가 다른 수치",
    "formula_unit_slip": "단위가 어긋난 계산식",
    "picture_in_code": "코드 블록 속 그림",
    "settled_figure_changed": "정해진 수치와 다른 값",
    "fact": "웹 자료와 다른 주장(확인 필요)",
    "question": "평가 문항 검토 필요",
    "judged_figure": "정해진 수치와 다를 수 있는 문장(확인 필요)",
    "long_paragraph": "긴 문단", "repeated_citation": "반복 인용",
    "metric_conflict": "같은 지표 다른 값", "empty_section": "빈 절",
    "table_value_conflict": "표 사이 값 불일치", "broken_glyph_or_cite": "깨진 글자·빈 인용",
    "numeric_inconsistency": "앞뒤가 맞지 않는 수치", "unbalanced_fence": "닫히지 않은 블록",
    **report_checks.LABELS,
    **report_checks.MODEL_LABELS,
}


#: repair-prompt imperative endings
_INSTRUCTION = re.compile(r"(?:고쳐|맞춰|답하|써|바로잡|채우|빼|확인해|하)라(?![가-힣])|말라")


def reader_note(code: str, why: str) -> str:
    """Reader-facing finding text, never the repair prompt nor a judge model's reasoning: a
    model-labelled finding keeps what it found (「「60」은 자료에서 「…」의 값일 수 있다」), not
    the judge's account of why (r8 L27L A01-T1·A04-T2, L27F·QMX A04-T1: 「(판정: …)」 and
    the judge's thinking in the step)."""
    if code in report_checks.MODEL_LABELS:
        why = _JUDGE_WHY.sub(r"\1. 확인 필요.", why or "")
    if code == "judged_figure" or _INSTRUCTION.search(why or ""):
        return _QUALITY_LABELS.get(code, "확인 필요")
    return (why or "")[:200] or _QUALITY_LABELS.get(code, "확인 필요")


#: the judge's own reasoning after a model finding's claim: 「… 수 있다(판정: …). 확인 필요.」
_JUDGE_WHY = re.compile(r"(수 있다)\(.*$", re.S)


_AMOUNT = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(조|억|만)?\s?(원|명|박스|회|개|건|%)?")
_SCALE = {"조": 1e12, "억": 1e8, "만": 1e4, None: 1.0, "": 1.0}


def _amount(text: str) -> float | None:
    """Korean amount as a number, or None."""
    found = _AMOUNT.findall(text or "")
    if len(found) != 1:
        return None
    number, scale, _unit = found[0]
    return float(number.replace(",", "")) * _SCALE[scale or None]


def _won_like(value: float, like: str) -> str:
    """Amount formatted like `like`."""
    tail = re.sub(r"^.*?(?:조|억|만)?\s?원", "", like, count=1) if "원" in like else ""
    if value >= 1e8:
        body = f"{value / 1e8:,.4f}".rstrip("0").rstrip(".") + "억 원"
    elif value >= 1e4:
        body = f"{value / 1e4:,.2f}".rstrip("0").rstrip(".") + "만 원"
    else:
        body = f"{value:,.0f}원"
    return body + tail


_KO_AMOUNT = re.compile(
    r"(?<![\d.,])(?:\d[\d,]*(?:\.\d+)?\s?조(?:\s?\d[\d,]*\s?억)?(?:\s?\d[\d,]*\s?만)?"
    r"|\d[\d,]*(?:\.\d+)?\s?억(?:\s?\d[\d,]*\s?만)?"
    r"|\d[\d,]*(?:\.\d+)?\s?만"
    r"|\d{1,3}(?:,\d{3})+)(?![\d.])"
)


def _ko_value(raw: str) -> float:
    total, rest = 0.0, raw.replace(" ", "")
    for unit, scale in (("조", 1e12), ("억", 1e8), ("만", 1e4)):
        if unit in rest:
            head, rest = rest.split(unit, 1)
            total += float(head.replace(",", "") or 0) * scale
    if rest:
        total += float(rest.replace(",", ""))
    return total


def _ko_amount(value: float) -> str:
    if value >= 1e8:
        whole, rest = divmod(round(value), int(1e8))
        return f"{whole:,}억" + (f" {rest / 1e4:,.0f}만" if rest >= 1e4 else "")
    if value >= 1e4 and value % 1e4 == 0:
        return f"{value / 1e4:,.0f}만"
    return f"{value:,.0f}"


def fix_restated_amounts(text: str, given: str) -> str:
    """Power-of-ten slip against a material calculation corrected (2+ exact amounts required)."""
    lines = [
        {round(_ko_value(m.group(0)), 2) for m in _KO_AMOUNT.finditer(line)}
        for line in (given or "").split("\n")
    ]
    lines = [values for values in lines if len(values) >= 3]
    if not lines or not text:
        return text

    def mend(sentence: str) -> str:
        spans = list(_KO_AMOUNT.finditer(sentence))
        found = [(m.group(0), round(_ko_value(m.group(0)), 2)) for m in spans]
        swaps: list[tuple[int, int, str]] = []
        for m, (raw, value) in zip(spans, found, strict=True):
            # bare digits are in the writer's unit (「4,200 ÷ 12,000」 in 만) unless 원 follows
            if not re.search(r"[조억만]", raw) and not re.match(r"\s?원", sentence[m.end():]):
                continue
            for values in lines:
                if value in values or not value:
                    continue
                others = {v for r, v in found if r != raw and v in values}
                if len(others) < 2:
                    continue
                match = next(
                    (g for g in values for k in (-4, -3, -2, -1, 1, 2, 3, 4)
                     if g and abs(value * 10**k - g) < 0.5),
                    None,
                )
                if match is not None:
                    swaps.append((m.start(), m.end(), _ko_amount(match)))
                    break
        # in place: the same digits elsewhere (「4,200만 원」) untouched
        for start, end, amount in reversed(swaps):
            sentence = sentence[:start] + amount + sentence[end:]
        return sentence

    return _SENTENCE.sub(lambda m: mend(m.group(0)), text)


def fix_table_formulas(body: str) -> str:
    """Table amount off its shown product by a power of ten corrected."""
    out = []
    corrected: dict[str, str] = {}
    for line in (body or "").split("\n"):
        if not line.strip().startswith("|") or "×" not in line:
            out.append(line)
            continue
        cells = line.strip().strip("|").split("|")
        formula = next((c for c in cells if "×" in c and "=" not in c), None)
        if formula is None:
            out.append(line)
            continue
        # 「475만 명 × 2% × 118,800원」: a rate is a hundredth (r3 A10 T2 SOM row)
        factors = [None if (a := _amount(part)) is None else a / 100 if "%" in part else a
                   for part in formula.split("×")]
        if not factors or any(f is None for f in factors):
            out.append(line)
            continue
        product = 1.0
        for factor in factors:
            product *= factor
        fixed = list(cells)
        for k, cell in enumerate(cells):
            if cell is formula or "원" not in cell or "×" in cell:
                continue
            stated = _amount(cell)
            if not stated or not product:
                continue
            ratio = stated / product
            power = round(__import__("math").log10(ratio)) if ratio > 0 else 0
            if power != 0 and abs(ratio / 10 ** power - 1) < 0.01:
                fixed[k] = f" {_won_like(product, cell.strip())} "
                old = re.search(r"\d[\d,.]{0,20}\s?(?:조|억|만)", cell)
                new = re.search(r"\d[\d,.]{0,20}\s?(?:조|억|만)", fixed[k])
                if old and new:
                    corrected[old.group(0)] = new.group(0)
        out.append("|" + "|".join(fixed) + "|" if fixed != cells else line)
    text = "\n".join(out)
    # wrong figure repeated elsewhere fixed too
    for old, new in corrected.items():
        text = re.sub(rf"(?<![\d.,]){re.escape(old)}", new, text)
    return text


#: a won amount as written: 「11,286,000,000원」, 「113억 원」, 「29조 2,000억 원」
_WON_AMOUNT = re.compile(
    r"(?<![\d.,])(\d[\d,]*(?:\.\d+)?\s?(?:조|억|만)?(?:\s?\d[\d,]*(?:\.\d+)?\s?(?:억|만))?)\s?원")
#: a worked result in prose: 「= 11,286,000,000원」
_WORKED = re.compile(r"[=≈]\s{0,2}(\d[\d,]*(?:\.\d+)?\s?(?:조|억|만)?(?:\s?\d[\d,]*\s?(?:억|만))?)"
                     r"\s?원")


def _won_value(text: str) -> float | None:
    try:
        return units._ko_number(text.replace(" ", ""))
    except ValueError:
        return None


_WORDS = re.compile(r"[가-힣A-Za-z][가-힣A-Za-z]+|\d[\d,]{2,}(?:원|명)?")


def _power_slip(value: float, against: dict[float, str], near: str) -> int:
    """`k` when `value` is 10^k (100 to 10,000 times either way) a worked amount whose
    sentence shares a word with `near` (the row, its header, the caption), else 0."""
    words = set(_WORDS.findall(near))
    for worked, sentence in against.items():
        if not worked or not value or not words & set(_WORDS.findall(sentence)):
            continue
        power = round(math.log10(value / worked))
        if 2 <= abs(power) <= 4 and abs(value / worked / 10 ** power - 1) < 0.002:
            return power
    return 0


def fix_table_magnitudes(sections: list[dict]) -> list[dict]:
    """A table or KPI amount a power of ten off an amount the prose works out (「= N원」)
    scaled back, with the column it totals (r3 A10 T2 27B: prose SOM 「= 11,286,000,000원
    ≈ 113억 원」, table 「11,286억 원」 and rows summing to it). An amount the prose also
    states as written is left alone."""
    prose = [line for sec in sections for line in str(sec.get("content") or "").split("\n")
             if not line.lstrip().startswith("|")]
    worked = {v: line for line in prose for m in _WORKED.finditer(line) if (v := _won_value(m.group(1)))}
    stated = {v for line in prose for m in _WON_AMOUNT.finditer(line) if (v := _won_value(m.group(1)))}
    if not worked:
        return sections
    out = []
    for sec in sections:
        lines = str(sec.get("content") or "").split("\n")
        changed = False
        start = 0
        while start < len(lines):
            if not lines[start].lstrip().startswith("|"):
                start += 1
                continue
            end = start
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            rows = [lines[i].strip().strip("|").split("|") for i in range(start, end)]
            caption = lines[start - 1] if start and lines[start - 1].strip() else (
                lines[start - 2] if start > 1 else "")
            for col in range(max(len(r) for r in rows)):
                # body cells holding one won amount (no header, rule or working)
                cells = [(i, r[col]) for i, r in enumerate(rows)
                         if i and col < len(r) and "원" in r[col] and "×" not in r[col]
                         and not re.match(r"\s*:?-{2,}", r[col])
                         and len(_WON_AMOUNT.findall(r[col])) == 1]
                values = {i: _won_value(_WON_AMOUNT.search(c).group(1)) for i, c in cells}
                for i, cell in cells:
                    value = values[i]
                    if not value or any(abs(value - s) <= 1e-6 * s for s in stated):
                        continue
                    header = rows[0][col] if col < len(rows[0]) else ""
                    near = " ".join([caption, header, *rows[i][:col], *rows[i][col + 1:]])
                    power = _power_slip(value, worked, near)
                    if not power:
                        continue
                    others = [v for j, v in values.items() if j != i and v]
                    # rows that add up to the slipped total slipped with it
                    column = [i, *([j for j in values if j != i and values[j]]
                                   if others and abs(sum(others) - value) <= 1e-3 * value else [])]
                    for j in column:
                        cell_j = rows[j][col]
                        written = _WON_AMOUNT.search(cell_j)
                        fixed = _won_like(values[j] / 10 ** power, written.group(0))
                        rows[j][col] = cell_j[: written.start()] + fixed + cell_j[written.end():]
                    log.info("table amounts scaled by 10^%d to the prose's working: %s",
                             -power, logs.safe(cell.strip()))
                    changed = True
                    break
            for k, i in enumerate(range(start, end)):
                lead = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                rebuilt = lead + "|" + "|".join(rows[k]) + "|"
                if rows[k] != lines[i].strip().strip("|").split("|"):
                    lines[i] = rebuilt
            start = end
        out.append({**sec, "content": "\n".join(lines)} if changed else sec)
    return out


_UNDECIDED = re.compile(r"^\s*(?:\(미정\)|미정|TBD|-|—|\[[가-힣][가-힣 ·]{0,9}\])\s*$")


def _table_rows(body: str):
    """(label, value cell, line index, cells) per table body row."""
    lines = (body or "").split("\n")
    for i, line in enumerate(lines):
        if not line.strip().startswith("|") or re.match(r"^\s{0,16}\|?\s{0,16}:?-{2}", line):
            continue
        if i + 1 < len(lines) and re.match(r"^\s{0,16}\|?\s{0,16}:?-{2}", lines[i + 1]):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0]:
            yield re.sub(r"\s+", "", cells[0]), cells[1], i, cells


def fill_undecided_cells(sections: list[dict]) -> list[dict]:
    """Placeholder assumption takes the figure from another table, matched by first cell."""
    decided: dict[str, str] = {}
    unit = re.compile(r"\d.{0,300}(?:원|만|억|조|%|명|박스|회|개월|건)")
    for sec in sections:
        for label, value, _, _ in _table_rows(str(sec.get("content") or "")):
            if _UNDECIDED.match(value) or not re.search(r"\d", value):
                continue
            # value with unit beats a bare one
            if label not in decided or (unit.search(value) and not unit.search(decided[label])):
                decided[label] = value
    out = []
    for sec in sections:
        lines = str(sec.get("content") or "").split("\n")
        changed = False
        for label, value, i, cells in list(_table_rows("\n".join(lines))):
            if _UNDECIDED.match(value) and label in decided:
                cells[1] = decided[label]
                lines[i] = "| " + " | ".join(cells) + " |"
                changed = True
        out.append({**sec, "content": "\n".join(lines)} if changed else sec)
    return out


def drop_repeated_tables(sections: list[dict]) -> list[dict]:
    """Duplicate table removed with its caption."""
    seen: dict[str, str] = {}
    out = []
    for sec in sections:
        lines = str(sec.get("content") or "").split("\n")
        tables: list[tuple[int, int]] = []
        i = 0
        while i < len(lines):
            if lines[i].strip().startswith("|") and i + 1 < len(lines) and re.match(
                r"^\s*\|?\s*:?-{2,}", lines[i + 1]
            ):
                end = i
                while end < len(lines) and lines[end].strip().startswith("|"):
                    end += 1
                tables.append((i, end))
                i = end
            else:
                i += 1
        drop: list[tuple[int, int]] = []
        for start, end in tables:
            rows = list(_table_rows("\n".join(lines[start:end])))
            same = [r for r in rows if seen.get(r[0]) == re.sub(r"[^\d.]", "", r[1]) and re.search(r"\d", r[1])]
            if len(rows) >= 3 and len(same) >= 0.7 * len(rows):
                cap = start - 1
                while cap >= 0 and not lines[cap].strip():
                    cap -= 1
                if cap >= 0 and re.match(r"^\s*(?:\*\*)?표\s?\d{0,3}\s?[.:：]", lines[cap]):
                    start = cap
                drop.append((start, end))
            else:
                for label, value, _, _ in rows:
                    if re.search(r"\d", value):
                        seen.setdefault(label, re.sub(r"[^\d.]", "", value))
        for start, end in reversed(drop):
            del lines[start:end]
        out.append({**sec, "content": re.sub(r"\n{3,}", "\n\n", "\n".join(lines))} if drop else sec)
    return out


_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s?년")


_GAP = re.compile(
    r"명시\s?(?:없|되지)|확인\s?필요|자료\s?(?:없|에\s?없)|정보\s?없|공개\s?(?:안|되지)|미정|미확인"
    r"|^\s{0,16}\[[가-힣][가-힣 ·]{0,9}\]\s{0,16}$|^\s{0,16}[-—]\s{0,16}$|^\s{0,16}\(?\s{0,8}N/?A\s{0,8}\)?\s{0,16}$",
    re.I,
)
_GAPS_PER_REPORT = 4

_GAP_PROMPT = """Find 「{header}」 of 「{label}」 in the web material below and write it as one line
(60 characters or fewer) for a single table cell.

Check first:
- Same target: is the material about that very company, product or industry, 「{label}」 (a similar other industry or company is not)?
- Same measure: does the material's value match 「{header}」 in definition, unit and period (a website bounce rate is not a subscription churn rate)?
Write a value only when both hold, and copy the passage of the material's text it came from verbatim, unchanged.
Write the value in Korean.

Answer with one JSON object only:
{{"same_target": true, "same_measure": true, "value": "칸에 들어갈 한 줄 [n]", "quote": "자료 구절"}}
둘 중 하나라도 아니면 {{"same_target": false, "same_measure": false, "value": "", "quote": ""}}

{context}"""
#: business's own assumptions: never filled from the web
_OWN_ASSUMPTION = re.compile(
    r"원가|비용|가격|판매가|단가|이탈률|해지율|유지율|전환율|CAC|LTV|매출|이익|예산|고정비|변동비|"
    r"마진|공헌|성장률|점유율|규모|목표|구독료|객단가"
)


def table_gaps(sections: list[dict]) -> list[tuple[int, int, int, str, str]]:
    """Open comparison cells, at most one per row."""
    found: list[tuple[int, int, int, str, str]] = []
    rule = re.compile(r"^\s{0,16}\|?\s{0,16}:?-{2}")
    for si, sec in enumerate(sections):
        lines = str(sec.get("content") or "").split("\n")
        for i in range(len(lines) - 1):
            if not (lines[i].lstrip().startswith("|") and rule.match(lines[i + 1])):
                continue
            header = [c.strip(" *") for c in _cells(lines[i])]
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                cells = [c.strip() for c in _cells(lines[j])]
                for col in range(1, min(len(cells), len(header))):
                    if _GAP.search(cells[col]) and header[col]:
                        label = re.sub(r"\[\d{1,3}\]|\*", "", cells[0]).strip()
                        found.append((si, j, col, label, header[col]))
                        break
                j += 1
    return found


async def fill_gaps_from_search(
    sections: list[dict], sources: list[dict], *, model: str, api_key: str
) -> tuple[list[dict], dict]:
    """Open comparison cells searched individually and filled; unsettled ones kept."""
    spent = {"inputTokens": 0, "outputTokens": 0}
    gaps = [g for g in table_gaps(sections)
            if not _OWN_ASSUMPTION.search(f"{g[3]} {g[4]}")][:_GAPS_PER_REPORT]
    if not gaps:
        return sections, spent

    async def look(label: str, header: str):
        findings = await research.run(f"{label} {header}", model=model, api_key=api_key,
                                      max_sources=2)
        if not findings.sources or "본문 발췌" not in findings.context:
            return None, findings, {}
        text, used = await _complete(
            model,
            [{"role": "user", "content": _GAP_PROMPT.format(
                label=label, header=header, context=findings.context[:12000])}],
            api_key,
            max_tokens=400,
        )
        start, end = text.find("{"), text.rfind("}")
        try:
            judged = json.loads(text[start:end + 1]) if start >= 0 else {}
        except json.JSONDecodeError:
            judged = {}
        value = str(judged.get("value") or "").strip()
        quote = re.sub(r"\s+", "", str(judged.get("quote") or ""))
        page = re.sub(r"\s+", "", findings.context)
        # same measure, words and numbers on the page
        numbers = re.findall(r"\d[\d,.]*", re.sub(r"\[\d{1,3}\]", "", value))
        if (judged.get("same_target") is not True or judged.get("same_measure") is not True
                or len(quote) < 6 or quote not in page
                or any(n.replace(",", "") not in quote.replace(",", "") for n in numbers)):
            return None, findings, used
        return value, findings, used

    looked = await asyncio.gather(*(look(g[3], g[4]) for g in gaps), return_exceptions=True)
    out = [dict(sec) for sec in sections]
    for (si, li, col, label, header), result in zip(gaps, looked, strict=True):
        if isinstance(result, BaseException):
            log.info("gap search for %r failed: %s", logs.safe(label), logs.safe(result))
            continue
        answer, findings, used = result
        spent["inputTokens"] += int(findings.usage.get("inputTokens", 0)) + int(
            used.get("inputTokens", 0) if used else 0)
        spent["outputTokens"] += int(findings.usage.get("outputTokens", 0)) + int(
            used.get("outputTokens", 0) if used else 0)
        if (not answer or answer.startswith("없음") or _GAP.search(answer) or len(answer) > 160
                or not re.search(r"\[\d{1,2}\]", answer)):
            continue
        renumber: dict[str, str] = {}
        for src in findings.sources:
            if f"[{src['ordinal']}]" in answer:
                ordinal = len(sources) + 1
                sources.append({**src, "ordinal": ordinal})
                renumber[str(src["ordinal"])] = str(ordinal)
        if not renumber:
            continue
        answer = re.sub(
            r"\[(\d{1,2})\]", lambda m, mapped=renumber: f"[{mapped.get(m.group(1), '')}]", answer
        )
        answer = re.sub(r"\[\]", "", answer).replace("|", "/").strip()
        lines = str(out[si].get("content") or "").split("\n")
        cells = [c.strip() for c in _cells(lines[li])]
        cells[col] = answer
        lines[li] = "| " + " | ".join(cells) + " |"
        out[si]["content"] = "\n".join(lines)
        log.info("filled %r / %r from a second search", logs.safe(label), logs.safe(header))
    return out, spent


_FLAGGED_PER_REPORT = 10


def _sentence_with(line: str, needle: str) -> str:
    for m in _SENTENCE.finditer(line + " "):
        if needle in m.group(0):
            return m.group(0).strip()
    return ""


def flagged_sentences(
    sections: list[dict], material: str, settled: list | None = None
) -> list[tuple[int, str, str]]:
    """Sentence-level gate findings no mend settles."""
    found: list[tuple[int, str, str]] = []
    for index, written, said in quality_gate.scaled_numbers(sections, material):
        for line in str(sections[index].get("content") or "").split("\n"):
            if line.lstrip().startswith(("|", "!", "```", "#")):
                continue
            if sentence := _sentence_with(line, written):
                found.append((index, sentence, f"「{written}」는 자료의 「{said}」와 자릿수가 "
                              "다르다. 문장 안의 계산과 자료로 맞는 값을 확인해 고쳐라."))
                break
    for index, sec in enumerate(sections):
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            continue
        for line in str(sec.get("content") or "").split("\n"):
            if line.lstrip().startswith(("|", "!", "```", "#")):
                continue
            for formula in units.slipped_formulas(line):
                if sentence := _sentence_with(line, formula):
                    found.append((index, sentence, f"식 「{formula}」의 결과가 곱과 천 배 이상 "
                                  "다르다. 항 하나의 단위가 잘못 쓰였다(예: 4만 원을 4,000만원). "
                                  "자료의 값으로 항을 바로잡고 결과를 다시 계산해라."))
            for m in _SENTENCE.finditer(line + " "):
                if quality_gate._PLACEHOLDER.search(m.group(0)):
                    found.append((index, m.group(0).strip(), PLACEHOLDER_LEFT))
    for index, sec in enumerate(sections):
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            continue
        for piece, figure, wrote in key_figures.conflicts(str(sec.get("content") or ""), settled or []):
            if not piece.startswith("|"):
                found.append((index, piece, f"「{figure.name}」은 이 문서에서 {figure.value}로 정해진 "
                              f"값인데 이 문장은 {units._ko_written(wrote, figure.value)}"
                              f"{figure.unit}로 썼다. 정해진 값으로 고치고 그 값으로 계산한 수치도 "
                              "맞춰라. 이 문장이 다른 대상·경우(다른 고객층, 인상 시나리오 등)를 "
                              "말하는 것이면 원문을 그대로 답하라."))
    # order: wrong formula, changed figure, placeholder
    found.sort(key=lambda f: 0 if "식 「" in f[2] else 1 if "정해진" in f[2]
               else 2 if "자릿수" in f[2] else 3)
    return found[:_FLAGGED_PER_REPORT]


_JUDGE_FIGURES = """{figures}
문장: {sentence}

Question: does this sentence wrongly write a value that contradicts the settled figures (확정 수치)?
A scenario assuming a different case, a total, a step of a calculation, or the value of a
different item is not a contradiction. Answer with one word only: 「예」 or 「아니오」."""
_JUDGED_PER_REPORT = 40


def _all_in_material(sentence: str, known: list[float]) -> bool:
    """All numbers stated in the material."""
    written = report_checks._material_numbers(sentence)
    return bool(written) and all(report_checks._grounded(n, known) for n in written)


async def judge_figure_sentences(
    sections: list[dict], settled: list, *, judge: str, api_key: str, material: str = ""
) -> tuple[list[tuple[int, str, str]], dict]:
    """Code-passed settled-figure sentences checked by the judge model; returns flagged and usage."""
    spent = {"inputTokens": 0, "outputTokens": 0}
    if not settled:
        return [], spent
    known = report_checks._material_numbers(material)
    names = [n for f in settled for n in f.names]
    figures = "확정 수치: " + ", ".join(f"{f.name} {f.value}" for f in settled) + "."
    asked: list[tuple[int, str]] = []
    for index, sec in enumerate(sections):
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            continue
        for line in str(sec.get("content") or "").split("\n"):
            if line.lstrip().startswith(("|", "!", "```", "#")):
                continue
            for m in _SENTENCE.finditer(line + " "):
                sentence = m.group(0).strip()
                if (any(n in sentence for n in names) and re.search(r"\d", sentence)
                        and not key_figures.conflicts(sentence, settled)
                        and not (known and _all_in_material(sentence, known))):
                    asked.append((index, sentence))
    asked = asked[:_JUDGED_PER_REPORT]
    gate = asyncio.Semaphore(8)

    async def ask(sentence: str):
        async with gate:
            return await _complete(
                judge,
                [{"role": "user", "content": _JUDGE_FIGURES.format(figures=figures, sentence=sentence)}],
                api_key,
                max_tokens=6,
            )

    replies = await asyncio.gather(*(ask(s) for _, s in asked), return_exceptions=True)
    found = []
    for (index, sentence), reply in zip(asked, replies, strict=True):
        if isinstance(reply, BaseException):
            continue
        text, used = reply
        spent["inputTokens"] += used.get("inputTokens", 0)
        spent["outputTokens"] += used.get("outputTokens", 0)
        if text.strip().startswith("예"):
            found.append((index, sentence, f"이 문장은 확정 수치({figures[7:]})와 어긋난다. 정해진 "
                          "값으로 고치고 그 값으로 계산한 수치도 맞춰라. 다른 경우를 말하는 문장이면 "
                          "원문을 그대로 답하라."))
    if found:
        log.info("judge flagged %d of %d figure sentences", len(found), len(asked))
    return found, spent



def relabel_unit_columns(sections: list[dict]) -> list[dict]:
    """「CAC(천 원)」-style header relabelled in won when prose uses won."""
    from app.services import quality_gate

    whole = "\n".join(str(sec.get("content") or "") for sec in sections)
    wrong = {header for header, _ in quality_gate.mislabelled_columns(whole)}
    if not wrong:
        return sections
    out = []
    for sec in sections:
        body = str(sec.get("content") or "")
        lines = body.split("\n")
        for i, line in enumerate(lines):
            if line.lstrip().startswith("|"):
                cells = line.split("|")
                fixed = [re.sub(r"\((?:천|만|백만|억)\s?원\)", "(원)", c) if c.strip() in wrong
                         else c for c in cells]
                if fixed != cells:
                    log.info("column unit set to won: %s", logs.safe([c.strip() for c in cells if c.strip() in wrong]))
                    lines[i] = "|".join(fixed)
        out.append({**sec, "content": "\n".join(lines)})
    return out


def restore_choices(sections: list[dict], material: str) -> list[dict]:
    """Carried question's lost choices restored from the material."""
    from app.services import quality_gate

    questions = quality_gate.question_choices(material)
    if not questions:
        return sections
    out = []
    for sec in sections:
        body = str(sec.get("content") or "")
        flat = re.sub(r"[^가-힣A-Za-z0-9]", "", body)
        if not any(q[1] in flat for q in questions):
            out.append(sec)
            continue
        lines = body.split("\n")
        for stem, key, block, _ in questions:
            if not quality_gate.lost_question_parts(f"1. {stem}\n" + "\n".join(block), body):
                continue
            at = next((i for i, line in enumerate(lines)
                       if key in re.sub(r"[^가-힣A-Za-z0-9]", "", line)), None)
            if at is None or "①" in lines[at]:
                continue
            end = at + 1
            while end < len(lines) and re.match(r"\s{0,16}(?:[-*]\s{0,8})?[①-⑩]", lines[end]):
                end += 1
            log.info("choices put back under %r", stem[:40])
            lines[at + 1:end] = [line.strip() for line in block]
        out.append({**sec, "content": "\n".join(lines)})
    return out


def normalize_document(
    sections: list[dict], *, settled: list | None = None, sources: list[dict] | None = None,
    request: str = "", context: str = "",
) -> list[dict]:
    """Deterministic report mends in fixed order; idempotent."""
    out = []
    for sec in sections:
        mended, slips = hangul.repair_mixed_script(str(sec.get("content") or ""), context[:20000])
        # tables as list items restored before caption numbering
        mended = doc_formats.tables_out_of_items(mended)
        if slips:
            log.info("mended mixed-script words in %r: %s", logs.safe(sec.get("heading")), logs.safe(slips[:5]))
        out.append({**sec, "content": mended})
    out = relabel_unit_columns(out)
    try:
        out = restore_choices(out, context)
    except Exception:  # noqa: BLE001 — the questions stay as written
        log.exception("restoring choices failed")
    try:
        out = fix_pnl_tables(drop_repeated_tables(fill_undecided_cells(
            fix_table_magnitudes(final_tidy(out, settled)))))
    except Exception:  # noqa: BLE001 — a mend that fails leaves the text as it was
        log.exception("final tidy failed; the sections are kept as written")
    out = caption_figures_and_tables(out)
    # calendar weekdays override the writer's
    out = [{**sec, "content": weekdays.fix_weekdays(
        str(sec.get("content") or ""), context=f"{request}\n{context}")[0]} for sec in out]
    out = [
        sec if _REFERENCE_HEADING.search(str(sec.get("heading") or ""))
        else {**sec, "content": collapse_repeated_citations(str(sec.get("content") or ""))}
        for sec in out
    ]
    return fill_reference_list(
        drop_unbacked_citations(
            reference_list_only(
                tidy_references(enforce_sentence_counts(trim_leading_conclusion(out), request))
            ),
            sources,
        ),
        context,
    )



def _amount_or_none(written: str) -> float | None:
    """「3,200만」 as a number; None if not one amount, so checks skip it."""
    try:
        if "천만" in written:
            return units._ko_number(written.replace("천만", "")) * 1e7
        return units._ko_number(written)
    except ValueError:
        return None


def numeric_issues(sections: list[dict]) -> list[tuple[int, str, str]]:
    """Sentences whose own figures disagree, as (section, sentence, problem, rewrite hint)."""
    found: list[tuple[int, str, str]] = []
    for index, sec in enumerate(sections):
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            continue
        for line in str(sec.get("content") or "").split("\n"):
            if line.lstrip().startswith(("!", "```", "#")):
                continue
            # table row read as one sentence
            pieces = [line.strip()] if line.lstrip().startswith("|") else [
                m.group(0).strip() for m in _SENTENCE.finditer(line)
            ]
            for sentence in pieces:
                weekly = re.search(r"주\s?(\d{1,2})\s?회", sentence)
                monthly = re.search(r"월\s?(\d{1,2})\s?회", sentence)
                yearly = re.search(r"(?:연간?|1년에?)\s?(\d{1,3})\s?회", sentence)
                if yearly:
                    per_year = int(yearly.group(1))
                    if weekly and abs(int(weekly.group(1)) * 52 - per_year) > 0.15 * per_year:
                        found.append((index, sentence, f"주 {weekly.group(1)}회는 연 "
                                      f"{int(weekly.group(1)) * 52}회다(연 {per_year}회와 맞지 않음)"))
                    elif monthly and abs(int(monthly.group(1)) * 12 - per_year) > 0.15 * per_year:
                        found.append((index, sentence, f"월 {monthly.group(1)}회는 연 "
                                      f"{int(monthly.group(1)) * 12}회다(연 {per_year}회와 맞지 않음)"))
                rate = re.search(r"(?:CAGR|연평균\s?성장률|연평균)[^\d%]{0,12}(\d{1,3}(?:\.\d+)?)\s?%", sentence)
                years = [int(y) for y in _YEAR.findall(sentence)]
                amounts = [
                    _amount_or_none(a)
                    for a in re.findall(
                        r"\d[\d,]{0,20}(?:\.\d{1,15})?\s?(?:천만|조|억|만)\s?(?:\d[\d,]{0,20}\s?(?:억|만))?", sentence
                    )
                ]
                if rate and len(set(years)) == 2 and len(amounts) == 2 and all(amounts):
                    span = abs(years[1] - years[0])
                    start, end = (amounts if years[0] < years[1] else amounts[::-1])
                    if span and start > 0:
                        implied = ((end / start) ** (1 / span) - 1) * 100
                        stated = float(rate.group(1))
                        # >300%/yr: not one market's start and end
                        if abs(implied - stated) > 1.5 and -50 < implied < 300:
                            found.append((index, sentence, f"{start:,.0f}에서 {end:,.0f}로 "
                                          f"{span}년이면 연평균 {implied:.1f}%다(적힌 {stated}%와 맞지 "
                                          "않음) — 출처를 다시 확인해 맞는 값만 남겨라"))
    return found





def _named_amount(text: str, names: str) -> float | None:
    """The one settled amount under `names`, or None."""
    values = set()
    for m in re.finditer(
        rf"(?:{names})[^\d\n|]{{0,12}}\|?\s?(\d[\d,]*(?:\.\d+)?\s?(?:조|억|만)?"
        rf"(?:\s?\d[\d,]*\s?만)?)\s?(?:\|\s?)?(만\s?)?원",
        text,
    ):
        value = _amount_or_none(m.group(1))
        if value is None:
            continue
        values.add(round(value * 1e4 if m.group(2) and value < 1e4 else value, 2))
    return values.pop() if len(values) == 1 else None


def fix_pnl_tables(sections: list[dict]) -> list[dict]:
    """Monthly P&L table recomputed when price, unit cost and fixed cost are all stated."""
    whole = "\n".join(str(sec.get("content") or "") for sec in sections)
    price = _named_amount(whole, r"판매가|객단가|박스\s?가격|단가")
    unit_cost = _named_amount(whole, r"변동비|단위\s?원가|박스당\s?원가")
    fixed = _named_amount(whole, r"월\s?고정비|고정비")
    if not (price and unit_cost and fixed) or unit_cost >= price:
        return sections
    out = []
    for sec in sections:
        lines = str(sec.get("content") or "").split("\n")
        changed = False
        i = 0
        while i < len(lines) - 1:
            head = lines[i]
            if not (head.strip().startswith("|") and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1])):
                i += 1
                continue
            cols = [c.strip() for c in head.strip().strip("|").split("|")]
            def col(pattern: str, cols: list[str] = cols) -> int | None:
                return next(
                    (k for k, c in enumerate(cols) if re.search(pattern, c) and "누적" not in c), None
                )
            volume, revenue = col(r"판매량|박스|수량"), col(r"매출")
            contribution, profit = col(r"공헌이익|기여이익"), col(r"영업이익|영업손익|손익")
            j = i + 2
            end = j
            while end < len(lines) and lines[end].strip().startswith("|"):
                end += 1
            # inputs must match some row exactly
            def agrees(row: str, volume=volume, revenue=revenue, cols=cols) -> bool:
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                try:
                    boxes = units._ko_number(re.sub(r"[^\d.,]", "", cells[volume]))
                    stated = units._ko_number(re.sub(r"^[-−△▲]", "", cells[revenue].strip()))
                except (ValueError, IndexError, TypeError):
                    return False
                if "만" not in cells[revenue] and "억" not in cells[revenue] and (
                    "만 원" in cols[revenue] or "(만" in cols[revenue]
                ):
                    stated *= 1e4
                return bool(boxes) and abs(stated - boxes * price) <= max(1.0, boxes * price * 0.005)
            if revenue is None or not any(agrees(row) for row in lines[i + 2:end]):
                i = end
                continue
            if volume is not None and (revenue is not None or profit is not None):
                while j < len(lines) and lines[j].strip().startswith("|"):
                    cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                    try:
                        boxes = units._ko_number(re.sub(r"[^\d.,]", "", cells[volume]))
                    except (ValueError, IndexError):
                        j += 1
                        continue
                    if not boxes:
                        j += 1
                        continue
                    want = {revenue: boxes * price, contribution: boxes * (price - unit_cost),
                            profit: boxes * (price - unit_cost) - fixed}
                    for k, right in want.items():
                        if k is None or k >= len(cells) or not re.search(r"\d", cells[k]):
                            continue
                        negative = cells[k].lstrip().startswith(("-", "−", "△", "▲"))
                        try:
                            stated = units._ko_number(re.sub(r"^[-−△▲]", "", cells[k].strip()))
                        except ValueError:
                            continue
                        stated = -stated if negative else stated
                        if "만" in cells[k] or "억" in cells[k]:
                            pass
                        elif "만 원" in cols[k] or "(만" in cols[k]:
                            stated *= 1e4
                        if abs(stated - right) > max(1.0, abs(right) * 0.005):
                            shown = units._ko_written(abs(right), "만") + " 원"
                            if "만 원" in cols[k] or "(만" in cols[k]:
                                shown = f"{abs(right) / 1e4:,.0f}"
                            cells[k] = ("-" if right < 0 else "") + shown
                            changed = True
                    lines[j] = "| " + " | ".join(cells) + " |"
                    j += 1
            i = j
        out.append({**sec, "content": "\n".join(lines)} if changed else sec)
    return out


#: talk about writing instructions the reader never saw (incl. checks of settled blocks)
_PROMPT_TALK = re.compile(
    r"쓸 수 있는 수치|수치 목록|요청 자료(?:에|의|로)|작성 지침|아직 (?:작성|쓰이)지 않았|개요(?:에|의) 따르면"
    r"|(?:확정|계산)\s{0,2}블록|확정 날짜 블록"
    r"|[×x*]\s{0,2}[\d.,]{1,12}\s{0,2}=\s{0,2}[\d,]{1,20}\s{0,2}원[^.\n]{0,80}?(?:과|와)\s{0,2}"
    r"(?:다릅니다|다르다|달라|일치하지\s{0,2}않)"
    r"|본\s{0,2}섹션|[\"「]?\d{2,4}\s{0,2}자\s{0,2}내외[\"」]?\s{0,2}로|(?:한두|두세|\d)\s{0,2}문장으로\s{0,2}압축"
)
#: writer's note to itself, 「[[확정 수치 검토: … 압축.]]」; not a figure mark or a citation
_WRITER_NOTE = re.compile(r"\[\[(?!\s*그림\s*[:：])(?!\s*\d{1,3}\s*\]\])(?:(?!\n\s*\n)[^\[\]]){2,3000}\]\]")
#: note left open to the paragraph's end, or closed with its opening already gone
_OPEN_NOTE = re.compile(r"\[\[(?!\s*그림\s*[:：])(?!\s*\d{1,3}\s*\]\])[^\[\]\n]{20,}$", re.M)
_CLOSED_NOTE_TAIL = re.compile(r"^(?:(?!\n\s*\n)[^\[\]]){0,3000}?\]\](?!\()", re.M)


def drop_writer_notes(body: str) -> str:
    """`[[…]]` notes the writer left itself removed whole, never sentence by sentence."""
    if "]]" not in body and "[[" not in body:
        return body
    out = _WRITER_NOTE.sub("", body)
    out = _OPEN_NOTE.sub("", out)
    out = _CLOSED_NOTE_TAIL.sub(lambda m: "" if "[[" not in m.group(0) else m.group(0), out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip("\n") + ("\n" if body.endswith("\n") else "")
_LONE_LANGUAGE = re.compile(r"(?m)^```[ \t]*\n(callout|kpi|cards|diagram|mermaid|chart)[ \t]*$")
_TABLE_CAPTION_LINE = re.compile(r"^\s*(?:\*\*)?표\s?(?:\d{1,3})?[.:]")


def _cells(line: str) -> list[str]:
    return line.strip().strip("|").split("|")


def drop_cut_rows(body: str) -> str:
    """Truncated table rows dropped; empty tables lose their caption."""
    lines = body.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            out.append(lines[i])
            i += 1
            continue
        block = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            block.append(lines[i])
            i += 1
        width = len(_cells(block[0]))
        kept = [row for row in block
                if row.rstrip().endswith("|") or len(_cells(row)) >= width]
        rows = [row for row in kept[1:] if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", row)]
        if len(block) > 2 and not rows:
            while out and not out[-1].strip():
                out.pop()
            if out and _TABLE_CAPTION_LINE.match(out[-1]):
                out.pop()
            continue
        out.extend(kept)
    return "\n".join(out)


def drop_prompt_talk(body: str) -> str:
    """Instruction-talk sentences removed; empty paragraphs dropped."""
    out = []
    for line in body.split("\n"):
        if line.lstrip().startswith(("|", "```", "!", "#")) or not _PROMPT_TALK.search(line):
            out.append(line)
            continue
        kept = "".join(m.group(0) for m in _SENTENCE.finditer(line + " ")
                       if not _PROMPT_TALK.search(m.group(0)))
        rest = _SENTENCE.sub("", line + " ").strip()
        line = (kept + (" " + rest if rest and not _PROMPT_TALK.search(rest) else "")).strip()
        if line:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


#: fences drawn as blocks; others printed as text
_DRAWN_FENCES = ("mermaid", "callout", "kpi", "cards", "diagram", "chart")


def unfence_pictures(body: str) -> str:
    """Code fence holding a picture, or prose fenced as markdown, unwrapped (else PDF prints
    base64 or the body in a code font)."""
    lines = body.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        opening = re.match(r"^\s{0,16}```\s{0,16}([\w-]{0,100})\s{0,200}$", lines[i])
        if not opening:
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip().startswith("```"):
            j += 1
        if opening.group(1).lower() in _DRAWN_FENCES:
            # drawn block copied with its closing fence
            out.extend(lines[i:j + 1])
            i = j + 1
            continue
        inner = lines[i + 1:j]
        if (opening.group(1).lower() in _PROSE_FENCES
                or any(_FIGURE_LINE.match(x.strip()) or "](data:image/" in x for x in inner)):
            # a picture, or prose wrapped as 「```markdown」: the body itself
            out.extend(inner)
        else:
            out.extend(lines[i:j + 1])
        i = j + 1
    return "\n".join(out)


#: words of a tool or API error message: 「required_context_missing」, 「Provide the … argument」
_ERROR_WORDS = re.compile(
    r"\b(?:error|errors|missing|required|argument|arguments|parameter|context|tool|invalid|"
    r"unable|cannot|failed|failure|exception|provide|undefined|null|denied|timeout)\b"
    r"|\b[a-z]{2,20}(?:_[a-z]{2,20}){1,5}\b",
    re.I,
)


def error_text(body: str, request: str = "") -> bool:
    """Whether a section of a Korean document is a tool or error message instead of prose
    (「required_context_missing / Required context is missing. Provide the … argument」)."""
    text = re.sub(r"```.*?```", " ", body or "", flags=re.S)
    text = "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith(("|", "!")))
    hangul = len(re.findall(r"[가-힣]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if latin < 20 or hangul > latin * 0.25:
        return False
    if request and len(re.findall(r"[가-힣]", request)) < len(re.findall(r"[A-Za-z]", request)):
        return False  # an English document
    return len(_ERROR_WORDS.findall(text)) >= 2


#: fence language that only wraps document prose: 「```markdown」
_PROSE_FENCES = ("markdown", "md", "text", "txt", "plain", "plaintext")


_JSON_PROSE_FENCE = re.compile(r"```json[ \t]{0,8}\n(\{[\s\S]{0,200000}?\})\s{0,8}\n```")


_KPI_ROW = re.compile(r"^\s{0,3}(?![|`#>*-])[^|\n`]{1,24}?\s\|\s[^|\n`]{1,48}$")
_KPI_EDGE = re.compile(r"^\s{0,3}`{1,2}\s{0,2}(?:kpi)?\s{0,2}$", re.I)


def mend_kpi_fences(body: str) -> str:
    """Loose KPI rows wrapped in a ```kpi fence."""
    if " | " not in (body or ""):
        return body
    lines = body.split("\n")
    out: list[str] = []
    fenced = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line)
            i += 1
            continue
        if fenced:
            out.append(line)
            i += 1
            continue
        opened = bool(_KPI_EDGE.match(line))
        j = i + 1 if opened else i
        rows: list[str] = []
        while j < len(lines) and (_KPI_ROW.match(lines[j]) or not lines[j].strip()):
            if lines[j].strip():
                rows.append(lines[j].strip())
            j += 1
        closed = opened and j < len(lines) and bool(_KPI_EDGE.match(lines[j]))
        bare = not opened and 2 <= len(rows) <= 4 and all(re.match(r"[\d+\-−]", r) for r in rows)
        if rows and len(rows) <= 4 and (closed or bare):
            out += ["```kpi", *rows, "```"]
            i = j + 1 if closed else j
            if not closed and i < len(lines) and not lines[i - 1].strip():
                out.append("")
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


#: droppable all-empty columns; sign columns stay
_DROPPABLE_COLUMN = re.compile(r"기한|마감|완료\s{0,2}(?:예정)?일|일정|비고")


def drop_empty_columns(body: str) -> str:
    """All-empty 기한·비고 column dropped, caption too."""
    if "|" not in (body or ""):
        return body
    lines = body.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            j += 1
        block = lines[i:j]
        rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in block]
        rule = next((k for k, row in enumerate(rows)
                     if row and all(re.fullmatch(r":?-{2,}:?", c) for c in row if c)), None)
        body_rows = rows[rule + 1:] if rule == 1 else []
        width = len(rows[0])
        empty = [c for c in range(width)
                 if len(body_rows) >= 2 and c > 0 and _DROPPABLE_COLUMN.search(rows[0][c])
                 and all(c >= len(r) or r[c] in ("", "-", "—") for r in body_rows)]
        if empty and len(empty) < width - 1 and all(len(r) == width for r in rows):
            gone = {rows[0][c] for c in empty}
            block = ["| " + " | ".join(c for k, c in enumerate(r) if k not in empty) + " |"
                     for r in rows]
            for at in (len(out) - 1, len(out) - 2):
                if 0 <= at < len(out) and _CAPTION_LINE.match(out[at] or ""):
                    for name in gone:
                        out[at] = re.sub(rf"\s{{0,2}}[·,/]\s{{0,2}}{re.escape(name)}(?=\s|$|[·,/])", "", out[at])
            if j < len(lines) and _CAPTION_LINE.match(lines[j] or ""):
                for name in gone:
                    lines[j] = re.sub(rf"\s{{0,2}}[·,/]\s{{0,2}}{re.escape(name)}(?=\s|$|[·,/])", "", lines[j])
        out += block
        i = j
    return "\n".join(out)


def final_tidy(sections: list[dict], settled: list | None = None) -> list[dict]:
    """Deterministic mends rerun after rewriting passes."""
    tidied = []
    for sec in sections:
        body = internal_words.strip_tool_calls(str(sec.get("content") or ""))
        body = _JSON_PROSE_FENCE.sub(
            lambda m: unwrap_json_prose(m.group(1)) if unwrap_json_prose(m.group(1)) != m.group(1)
            else m.group(0), body)
        if body.lstrip().startswith("{"):
            # body wrapped as JSON, closed or truncated
            body = re.sub(r'\n?\s{0,200}"\s{0,200}\}\s{0,200}\}?\s{0,200}$', "", unwrap_json_prose(body).replace(
                '\\n', "\n"))
        if not _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            body = unfence_pictures(_LONE_LANGUAGE.sub(r"```\1", mend_kpi_fences(body)))
            body = drop_empty_columns(body)
            # 「<br>」 as 「 · 」
            body = "\n".join(
                re.sub(r"\s{0,32}<\s{0,8}br\s{0,8}/?\s{0,8}>\s{0,32}", " · " if line.lstrip().startswith("|") else " ",
                       line, flags=re.I)
                for line in body.split("\n")
            )
            # 「[[5]]」/「[]」 from a citation pass
            body = re.sub(r"\[\[(\d{1,3})\]\](?!\()", r"[\1]", body)
            body = re.sub(r"\s?\[\](?!\()", "", body)
            body = internal_words.scrub(drop_prompt_talk(drop_cut_rows(drop_writer_notes(body))))
            # work notes a rewrite brought back; a section never emptied here
            noted = internal_words.strip_work_notes(body, document=True)
            body = noted if noted.strip() else body
            # settled figures restored in formulas before recomputing
            body = key_figures.mend_operands(body, settled or [])
            body = units.settle_corrections(
                fix_table_formulas(units.fix_written_sums(units.fix_written_arithmetic(body))))
        tidied.append({**sec, "content": body})
    return tidied


def _renumber_references(sections: list[dict]) -> tuple[list[dict], set[str]]:
    """Captions renumbered in document order; returns sections and names already cited."""
    rule = re.compile(r"^\s{0,3}\|?\s{0,3}:?-{2,}")
    moved = {"표": {}, "그림": {}}
    counts = {"표": 0, "그림": 0}
    for sec in sections:
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            continue
        lines = str(sec.get("content") or "").split("\n")
        for i, line in enumerate(lines):
            if (figure := _FIGURE_LINE.match(line.strip())) or line.strip().startswith("```mermaid"):
                counts["그림"] += 1
                old = re.match(r"\s{0,16}그림\s?(\d{1,3})", figure.group("alt")) if figure else None
                if old:
                    moved["그림"].setdefault(old.group(1), counts["그림"])
            elif (line.strip().startswith("|") and i + 1 < len(lines) and rule.match(lines[i + 1])
                  and not (i and lines[i - 1].strip().startswith("|"))):
                counts["표"] += 1
                above = next((x for x in reversed(lines[:i]) if x.strip()), "")
                old = re.match(r"^\s*(?:\*\*)?표\s?(\d{1,3})[.:]", above)
                if old:
                    moved["표"].setdefault(old.group(1), counts["표"])
    renames = {f"{kind} {old}": f"{kind} {new}" for kind, table in moved.items()
               for old, new in table.items() if str(new) != old}
    caption = re.compile(r"^\s*(?:\*\*표\s?\d|!\[그림\s?\d|\*그림\s?\d)")
    pointer = re.compile(r"(표|그림)\s?(\d{1,3})(?![\d.])")
    out, mentioned = [], set()
    for sec in sections:
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            out.append(sec)
            continue
        lines = str(sec.get("content") or "").split("\n")
        for i, line in enumerate(lines):
            if caption.match(line) or line.lstrip().startswith("|"):
                continue
            line = pointer.sub(lambda m: renames.get(f"{m.group(1)} {m.group(2)}", m.group(0)), line)
            mentioned |= {f"{m.group(1)} {m.group(2)}" for m in pointer.finditer(line)}
            lines[i] = line
        out.append({**sec, "content": "\n".join(lines)})
    return out, mentioned


#: a section named only for where it sits: its name is no table's caption
_GENERIC_SECTION = re.compile(r"(?:붙임|별첨|첨부|참고|부록|기타|비고)(?:\s?(?:자료|\d{1,2}))?")
#: a line titling the table under it: 「- 표: 교체 방식 비교」, 「[표] …」, 「<표 2> …」
_TABLE_TITLE = re.compile(
    r"^\s{0,8}(?:[-*•·]\s{1,3})?[*_]{0,2}\s{0,2}(?:\[\s?표\s?\d{0,3}\s?\]|[<〈《【]\s?표\s?\d{0,3}\s?[>〉》】]"
    r"|표\s?\d{0,3}\s?[.:：])\s{0,2}(?P<text>[^\n|]{1,80}?)\s{0,2}[*_]{0,2}\s*$")
#: a label or heading that leads into a table: 「## 교체 일정」, 「**예산 내역**」, 「차수별 일정:」
_LEAD_LABEL = re.compile(
    r"^\s{0,8}(?:#{1,6}\s+(?P<h>[^\n]{1,60}?)|(?:[-*•·]\s{1,3})?\*\*(?P<b>[^*\n]{1,40})\*\*\s{0,2}[:：]?"
    r"|(?:[-*•·]\s{1,3})?(?P<c>[^\n:：.。]{2,40})[:：])\s*$")


def _table_title(line: str) -> str | None:
    m = _TABLE_TITLE.match(line or "")
    return m.group("text").strip(" *_.:") if m else None


def _lead_caption(result: list[str]) -> str:
    """Caption from the line leading into a table, or the nearest heading above it."""
    k = len(result) - 1
    while k >= 0 and not result[k].strip():
        k -= 1
    if k >= 0 and (m := _LEAD_LABEL.match(result[k])) \
            and not re.match(r"\s*(?:\||\*\*표\s?\d|\*그림\s?\d|!\[)", result[k]):
        return (m.group("h") or m.group("b") or m.group("c") or "").strip(" *_.:")
    for line in reversed(result[:max(k, 0)]):
        if (m := re.match(r"^\s{0,3}#{1,6}\s+(.{1,60}?)\s*$", line)):
            return m.group(1).strip(" *_")
    return ""


def caption_figures_and_tables(sections: list[dict]) -> list[dict]:
    """Figures captioned below, tables above, numbered in order."""
    sections, mentioned = _renumber_references(sections)
    figure_no = table_no = 0
    previous_table: tuple[list[str], str] | None = None
    out = []
    for sec in sections:
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")) or str(
            sec.get("format") or "markdown"
        ) != "markdown":
            out.append(sec)
            continue
        content = str(sec.get("content") or "")
        section_name = re.sub(r"^\s*(?:[\dⅠ-Ⅻ]{1,3}[.)]|[가-하][.)]|제\s?\d{1,2}\s?[장절])\s*", "",
                              str(sec.get("heading") or "")).strip(" *#")
        if _GENERIC_SECTION.fullmatch(section_name):
            section_name = ""
        used_titles: set[str] = set()
        # 「−」 lacks a PDF glyph; empty 「[]」; title-less 「표 4」 line
        content = content.replace("\u2212", "-")
        content = re.sub(r"\s?\[\s*\]", "", content)
        content = re.sub(r"(?m)^\s*표\s?\d{1,3}\s*\n", "", content)
        lines = content.split("\n")
        result: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            figure = _FIGURE_LINE.match(line.strip())
            if figure:
                figure_no += 1
                title = _caption_text(figure.group("alt"), "그림") or figure.group("alt").strip()
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and (text := _caption_text(lines[j], "그림")):
                    title = title or text
                    lines[j] = ""
                if not _resolve_reference(
                    result, lines, i, _FIGURE_REF, f"그림 {figure_no}"
                ) and f"그림 {figure_no}" not in mentioned:
                    _point_from_paragraph_before(result, f"그림 {figure_no}")
                result.append(f"![그림 {figure_no}. {title or '그림'}]({figure.group('src')})")
                i += 1
                continue
            if line.strip().startswith("```mermaid"):
                end = i + 1
                while end < len(lines) and not lines[end].strip().startswith("```"):
                    end += 1
                result.extend(lines[i : end + 1])
                figure_no += 1
                j = end + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                title = _caption_text(lines[j], "그림") if j < len(lines) else None
                if title is not None:
                    lines[j] = ""
                result.append("")
                result.append(f"*그림 {figure_no}. {title or '도식'}*")
                _resolve_reference(result, lines, i, _FIGURE_REF, f"그림 {figure_no}")
                i = end + 1
                continue
            if line.strip().startswith("|") and i + 1 < len(lines) and re.match(
                r"^\s{0,3}\|?\s{0,3}:?-{2,}", lines[i + 1]
            ):
                table_no += 1
                k = len(result) - 1
                while k >= 0 and not result[k].strip():
                    k -= 1
                if k >= 0 and re.fullmatch(r"\s{0,3}\*\*표\s?\d{1,3}\.?\*\*\s*", result[k]):
                    # a bare number caption from an earlier pass: numbered again below
                    del result[k:]
                    k = len(result) - 1
                    while k >= 0 and not result[k].strip():
                        k -= 1
                given = (_caption_text(result[k], "표") or _table_title(result[k])) if k >= 0 \
                    else None
                if given is not None:
                    del result[k:]
                elif k >= 0 and (tail := _TRAILING_TABLE_CAPTION.search(result[k])):
                    # caption glued to the paragraph's last line lifted off
                    given = tail.group("text").strip(" *_.:")
                    result[k] = result[k][: tail.start()].rstrip()
                header = [c.strip(" *") for c in line.strip().strip("|").split("|") if c.strip()]
                end = i
                while end < len(lines) and lines[end].strip().startswith("|"):
                    end += 1
                if given is None:
                    k = end
                    while k < len(lines) and not lines[k].strip():
                        k += 1
                    if k < len(lines) and (below := _caption_text(lines[k], "표")) is not None:
                        given = below
                        lines[k] = ""
                # no caption given: the nearest label or heading, else a descriptive section
                # name, else the number alone; never its column names (「표 1. 항목·내용·비고」,
                # r5 A03-T2·A07-T1·A01-T1)
                title = given or next((t for t in (_lead_caption(result), section_name)
                                       if t and t not in used_titles), "")
                if title:
                    used_titles.add(title)
                    k = len(result) - 1
                    while k >= 0 and not result[k].strip():
                        k -= 1
                    if k >= 0 and re.fullmatch(r"\s{0,8}\*\*" + re.escape(title)
                                               + r"\*\*\s{0,2}[:：]?\s*", result[k]):
                        del result[k:]  # the bold label became the caption
                # table shaped like the previous takes its caption, number advanced
                if not given and previous_table and previous_table[0] == header:
                    step = re.search(r"(\d{1,2})\s?(차시|회차|주차|단계|일차|개월차)", previous_table[1])
                    if step:
                        title = (previous_table[1][: step.start(1)] + str(int(step.group(1)) + 1)
                                 + previous_table[1][step.end(1):])
                previous_table = (header, title)
                if not _resolve_reference(
                    result, lines, i, _TABLE_REF, f"표 {table_no}"
                ) and f"표 {table_no}" not in mentioned:
                    _point_from_paragraph_before(result, f"표 {table_no}")
                result.extend(["", f"**표 {table_no}. {title}**" if title
                               else f"**표 {table_no}**", ""])
                result.extend(lines[i:end])
                i = end
                continue
            result.append(line)
            i += 1
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(result)).strip("\n")
        # dangling reference removed with its phrase
        if _FIGURE_REF in text or _TABLE_REF in text:
            text = re.sub(r"[ \t]{2,}", " ", _DANGLING_REF.sub(" ", text))
            text = re.sub(r" ([.,)])", r"\1", text)
        text = fix_number_particles(_FIGURE_OPEN.sub("", _FIGURE_MARK.sub("", text)))
        out.append({**sec, "content": text})
    return out


def _resolve_reference(result: list[str], lines: list[str], at: int, ref: str, name: str) -> bool:
    """Nearest 〔그림〕/〔표〕 becomes the number; returns whether the text referenced it."""
    for k in range(len(result) - 1, -1, -1):
        if ref in result[k]:
            j = result[k].rfind(ref)
            result[k] = result[k][:j] + name + result[k][j + len(ref):]
            return True
    for k in range(at + 1, len(lines)):
        if ref in lines[k]:
            lines[k] = lines[k].replace(ref, name, 1)
            return True
    return False


def _point_from_paragraph_before(result: list[str], name: str) -> None:
    """Unreferenced figure/table cited at the end of the nearest prior prose paragraph."""
    for k in range(len(result) - 1, -1, -1):
        line = result[k].rstrip()
        if not line.strip():
            continue
        if re.match(r"^\s*#", line):
            return
        if re.match(r"^\s*(?:\||>|!\[|\*\*표|\*그림|```|[-*+]\s|[□■○◦ㅇㆍ·∙※]\s|\d{1,3}[.)]\s)", line):
            continue
        m = re.search(r"(\s?(?:\[\d{1,3}\])+)?([.!?。])$", line)
        if m:
            head = line[: m.start()]
            # merge into an existing bracket
            if (prior := re.search(r"\(((?:그림|표) \d{1,3}(?:, (?:그림|표) \d{1,3})*)\)$", head)):
                if name in prior.group(1).split(", "):
                    return
                head = head[: prior.start()] + f"({prior.group(1)}, {name})"
            else:
                head = head + f"({name})"
            result[k] = head + (m.group(1) or "") + m.group(2)
        return


#: final-digit sound for particle agreement
_DIGIT_CODA = {"0": "ㅇ", "1": "ㄹ", "2": "", "3": "ㅁ", "4": "", "5": "", "6": "ㄱ",
               "7": "ㄹ", "8": "ㄹ", "9": ""}
_PARTICLES = {"은": ("은", "는"), "는": ("은", "는"), "이": ("이", "가"), "가": ("이", "가"),
              "을": ("을", "를"), "를": ("을", "를"), "과": ("과", "와"), "와": ("과", "와"),
              "으로": ("으로", "로"), "로": ("으로", "로")}


def fix_number_particles(text: str) -> str:
    """Particle after a figure/table number agrees with its reading (「표 1은」)."""
    def agree(m: re.Match) -> str:
        number, particle = m.group(2), m.group(3)
        coda = "ㅂ" if number.endswith("0") and len(number) > 1 else _DIGIT_CODA[number[-1]]
        closed, opened = _PARTICLES[particle]
        if particle in ("으로", "로"):
            pick = opened if coda in ("", "ㄹ") else closed
        else:
            pick = closed if coda else opened
        return f"{m.group(1)}{pick}"
    return re.sub(r"((?:그림|표) (\d{1,3}))(으로|로|은|는|이|가|을|를|과|와)(?=[\s,.)」]|$)", agree, text)


_ABBREVIATION_END = re.compile(r"(?:\bvs|\be\.g|\bi\.e|\bet al|\bcf|\bFig|\bEq|\bNo|\bpp?)\.$", re.I)


def split_long_paragraphs(body: str, limit: int = 400) -> str:
    """Long prose paragraphs split at sentence ends; blocks and sentences intact."""
    out: list[str] = []
    fenced = False
    body = re.sub(r"(?m)^([^|\n][^\n]*)\n(?=\|)", r"\1\n\n", body or "")
    for block in re.split(r"\n\s*\n", body):
        stripped = block.strip()
        if stripped.count("```") % 2:
            fenced = not fenced
        prose = (
            not fenced
            and "```" not in stripped
            and len(stripped) > limit
            and not re.match(r"^(?:\||#|>|[-*+]\s|[□■○◦ㅇㆍ·∙※]\s|\d{1,3}[.)]\s|\[\[)", stripped)
            and "\n|" not in stripped
        )
        if not prose:
            out.append(block)
            continue
        sentences = re.split(r"(?<=[.!?。])" + units.NO_NUMBER_DOT_BREAK + r"(?:\s+)(?=[^\s])", stripped)
        # abbreviation dots don't end sentences
        merged: list[str] = []
        for sentence in sentences:
            if merged and _ABBREVIATION_END.search(merged[-1]):
                merged[-1] += " " + sentence
            else:
                merged.append(sentence)
        sentences = merged
        chunk: list[str] = []
        size = 0
        for sentence in sentences:
            if chunk and size >= 150 and size + len(sentence) > limit:
                out.append(" ".join(chunk))
                chunk, size = [], 0
            chunk.append(sentence)
            size += len(sentence)
            if size >= 180 and (len(chunk) >= 3 or size >= 330):
                out.append(" ".join(chunk))
                chunk, size = [], 0
        if chunk:
            if out and size < 120 and out[-1] and not out[-1].startswith(("|", "#", "```")):
                out[-1] = out[-1] + " " + " ".join(chunk)
            else:
                out.append(" ".join(chunk))
    return "\n\n".join(out)


def demote_subheadings(body: str) -> str:
    """In-section `##`/`#` demoted to `###`; fenced code untouched."""
    out, fenced = [], False
    for line in (body or "").split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and re.match(r"^#{1,2}\s+\S", line):
            line = "###" + line.lstrip("#")
            line = re.sub(r"^###(?=\S)", "### ", line)
        out.append(line)
    return "\n".join(out)


def _without_own_heading(body: str, heading: str) -> str:
    """Section text minus a repeated own heading."""
    lines = body.lstrip().split("\n", 1)
    first = re.sub(r"^#{1,6}\s*|\*+", "", lines[0]).strip()
    # lone bold first line is a heading
    if (
        re.fullmatch(r"\*\*[^*\n]{1,40}\*\*\s*", lines[0])
        and not re.search(r"[.!?。]", lines[0])
        and len(lines) > 1
    ):
        return lines[1].lstrip()
    if first != heading.strip() and (
        re.match(r"^#{1,6}\s+", lines[0]) or re.fullmatch(r"\*\*.+\*\*\s*", lines[0])
    ):
        # numbered headings may repeat the outline; numbered lists stay
        first = re.sub(r"^\d+[.)]\s+", "", first)
    if first and first == heading.strip():
        return lines[1].lstrip() if len(lines) > 1 else ""
    # short title-like first line matching the heading is a renamed heading
    if (
        len(lines) > 1
        and len(first) <= 40
        and not re.search(r"[.!?。:|]", first)
        and lines[1].startswith("\n")
    ):
        a, b = _heading_grams(first), _heading_grams(heading)
        if a and b and (len(a & b) / len(b) >= 0.8 or len(a & b) / len(a) >= 0.8):
            return lines[1].lstrip()
    return body


def _heading_grams(text: str) -> set[str]:
    plain = re.sub(r"[^가-힣A-Za-z0-9]", "", text)
    return {plain[i : i + 2] for i in range(len(plain) - 1)} if len(plain) > 1 else {plain}


def _carries_material(request: str) -> bool:
    """Request carries its own material (long, or has a table)."""
    return len(request) >= 300 or "|" in request


def _results_without_data(request: str, attached: list[str], *, web: bool = False) -> bool:
    """Request needs material nothing attached carries; `web`: search is on this turn, so a
    research request (「…동향을 조사해서」) has its material coming (r3 A07 T1)."""
    if any(block.strip() for block in attached) or _carries_material(request):
        return False
    if _MATERIAL.search(request):
        return True
    if web and _from_the_web(request):
        return False
    if not _RESULTS.search(request):
        return False
    return not deck_rules.has_numbers(request, [])


def _fold_alternatives(headings: list[str], alternatives: list[str]) -> list[str]:
    """Per-alternative sections merged into one 대안 비교."""
    if not alternatives or not headings:
        return headings
    names = [a for a in alternatives if len(a) >= 2]
    keep: list[str] = []
    dropped = 0
    for h in headings:
        compact = h.replace(" ", "")
        if any(n.replace(" ", "") in compact for n in names) and "비교" not in h:
            dropped += 1
            continue
        keep.append(h)
    if dropped and not any("비교" in h for h in keep):
        at = next((i + 1 for i, h in enumerate(keep) if "계산" in h or "전제" in h), None)
        keep.insert(min(len(keep), 2) if at is None else at, "대안 비교")
    return keep


_FOLLOW_OUTLINE = re.compile(
    r"개요\s{0,3}(?:로|대로|에\s{0,3}따라|를\s{0,3}따라)|이\s{0,3}(?:구성|목차)\s{0,3}(?:으로|로|대로)|"
    r"아래\s{0,3}(?:구성|목차)\s{0,3}(?:으로|로|대로)"
)
#: pasted-outline section line (서론·본론·결론, 「1.」, 「Ⅱ.」, 「제2장」)
_OUTLINE_LINE = re.compile(
    r"^\s{0,3}(?:#{1,4}\s{1,3}|\*\*)?\s{0,2}((?:서론|본론|결론|맺음말|들어가며|나가며)[^\n*]{0,70}|"
    r"(?:[1-9][0-9]?|[IVX]{1,4}|[ⅠⅡⅢⅣⅤⅥⅦⅧ])[.)]\s{0,2}[^\n*]{2,70}|제\s{0,2}\d{1,2}\s{0,2}[장절][^\n*]{0,60})"
    r"\s{0,2}(?:\*\*)?\s{0,3}$"
)


def given_outline(request: str) -> list[str]:
    """Sections of a pasted outline the instruction says to follow (3+), else `[]`."""
    if not _FOLLOW_OUTLINE.search(instruction_part(request)):
        return []
    material = request[len(instruction_part(request)) :]
    # runs split at restarted numbering or a new pasted block
    runs: list[list[str]] = [[]]
    last_number = 0
    for line in material.splitlines():
        if re.match(r"^\s{0,3}\[\d{1,2}\]\s{0,3}$", line) or re.match(r"^\s{0,3}-{3,8}\s{0,3}$", line):
            runs.append([])
            last_number = 0
            continue
        m = _OUTLINE_LINE.match(line)
        if not m:
            continue
        heading = " ".join(m.group(1).replace("**", "").split()).rstrip(":：")
        number = re.match(r"(\d{1,2})[.)]", heading)
        if number and int(number.group(1)) <= last_number and runs[-1]:
            runs.append([])
        last_number = int(number.group(1)) if number else last_number
        if heading and heading not in runs[-1]:
            runs[-1].append(heading[:80])
    runs = [run for run in runs if len(run) >= 3]
    if not runs:
        return []
    # run naming the instruction's parts, else the last (latest agreed)
    parts = re.findall(r"서론|본론|결론|맺음말|들어가며|나가며", instruction_part(request))
    for run in reversed(runs):
        if parts and any(part in heading for part in parts for heading in run):
            return run
    return runs[-1]


_LISTED = re.compile(r"구성(?:은|:|：)\s{0,3}([^.。\n]{6,300})")


def without_instruction_aside(heading: str, request: str) -> str:
    """Planner-copied parenthetical stripped from a heading."""
    m = re.fullmatch(r"\s{0,3}(.{2,40}?)\s{0,3}[(（]([^()（）]{6,80})[)）]\s{0,3}", heading or "")
    if m and m.group(2).strip() in instruction_part(request):
        return m.group(1).strip()
    return heading


def with_listed_sections(headings: list[str], request: str) -> list[str]:
    """Planned headings plus instruction-named missing ones; 참고문헌 last."""
    instruction = instruction_part(request)
    named: list[str] = []
    if m := _LISTED.search(instruction):
        named = [p.strip(" ·") for p in re.split(r"[,，]", m.group(1)) if p.strip()]
    if re.search(r"참고\s{0,3}문헌", instruction) and not any("참고" in n for n in named):
        named.append("참고문헌")

    def where(name: str, among: list[str]) -> int | None:
        core = re.sub(r"\s{1,8}", "", name)[:4]
        return next(
            (i for i, h in enumerate(among) if core and core in re.sub(r"\s{1,8}", "", h)), None
        )

    out = list(headings)
    for index, name in enumerate(named):
        if not (2 <= len(name) <= 30) or where(name, out) is not None:
            continue
        if "참고" in name:
            out.append(name)
            continue
        anchor = next(
            (pos for prev in reversed(named[:index]) if (pos := where(prev, out)) is not None),
            None,
        )
        out.insert(0 if anchor is None else anchor + 1, name)
    return out


def _heading_of(item: Any) -> str:
    """Outline item as a heading; slipped-in questions excluded."""
    if isinstance(item, dict):
        item = item.get("heading") or item.get("title") or ""
    elif not isinstance(item, (str, int, float)):
        return ""
    text = str(item).strip()
    if text.startswith("{"):
        return ""
    # English gloss dropped
    if re.search(r"[가-힣]", text):
        text = re.sub(r"\s{0,3}[(（][A-Za-z][A-Za-z0-9 ,:;&/'.-]{1,80}[)）]\s{0,3}$", "", text).strip()
    return text


def _parse_outline(text: str) -> tuple[str, list[str]]:
    """`(title, headings)` from wrapped JSON; title may be empty."""
    title = ""
    obj = re.search(r"\{.*\}", text, re.S)
    if obj:
        try:
            data = json.loads(obj.group(0))
            if isinstance(data, dict):
                title = str(data.get("title") or "").strip()
                items = data.get("sections") or []
                headings = [h for h in map(_heading_of, items) if h]
                alternatives = [
                    str(x).strip() for x in (data.get("alternatives") or []) if str(x).strip()
                ]
                headings = _fold_alternatives(headings, alternatives)
                if headings:
                    return title, headings[:_MAX_SECTIONS]
        except json.JSONDecodeError:
            pass
    match = re.search(r"\[.*\]", text, re.S)
    if match:
        try:
            items = json.loads(match.group(0))
            headings = [h for h in map(_heading_of, items) if h]
            if headings:
                return title, headings[:_MAX_SECTIONS]
        except json.JSONDecodeError:
            pass
    # truncated reply: partial `sections` array counts
    cut = re.search(r'"sections"\s*:\s*\[(?P<body>.*)', text, re.S)
    if cut:
        body = cut.group("body").split("]", 1)[0]
        found = re.findall(r'(?:"heading"\s*:\s*)?"((?:[^"\\]|\\.){1,120})"(?=\s*[,}\]]|\s*$)', body)
        headings = [
            h for h in map(_heading_of, found)
            if h and h not in ("heading", "title", "question", "options")
        ]
        if headings:
            if not title and (named := re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.){1,120})"', text)):
                title = named.group(1).strip()
            return title, headings[:_MAX_SECTIONS]
    # format ignored: list under the last header counts
    raw = text.splitlines()
    start = 0
    for i, line in enumerate(raw):
        if _OUTLINE_LIST_HEADER.match(line):
            start = i + 1
    lines = [
        re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line).strip(" #").strip()
        for line in raw[start:]
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)
    ]
    headings = []
    for line in lines:
        if not line or _OUTLINE_NOTE.search(line):
            continue
        # bracketed gloss dropped
        line = re.sub(r"\s{0,3}[(（][^()（）]{1,40}[)）]\s*$", "", line).strip()
        if line and len(line) <= 40:
            headings.append(line)
    return title, headings[:_MAX_SECTIONS]


_OUTLINE_LIST_HEADER = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*\**\s*(?:sections?|구조|목차|섹션|outline)\s*\**\s*[:：]\s*\**\s*$", re.I
)
_OUTLINE_NOTE = re.compile(
    r"\*\*[^*]{1,30}\*\*\s*[:：]|[:：]\s*$|^(?:title|subject|style|alternatives|제목|주제|스타일|"
    r"요청된 항목|요청 항목|대안)\s*[:：]",
    re.I,
)


def outline_is_json(text: str) -> bool:
    """Outline came as the requested JSON object."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return bool(re.search(r'"sections"\s*:\s*\[', text or ""))
    try:
        return isinstance(json.loads(match.group(0)), dict)
    except json.JSONDecodeError:
        return bool(re.search(r'"sections"\s*:\s*\[', text or ""))


def _refs_block(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return _NO_REFS
    # hand-typed sources: title and url only
    return "\n".join(
        f"[{s.get('ordinal', i + 1)}] {s.get('title') or s.get('url') or ''}"
        f" ({s.get('publisher') or ''})\n{s.get('quote') or ''}"
        for i, s in enumerate(sources)
    )


async def _draw(figure: dict, image_model: dict | None, api_key: str) -> dict | None:
    """Stored figure as a `data:` URI, or `None`; never raises."""
    if not image_model:
        return None
    base, _ = await settings_store.litellm_config()
    try:
        made = await imagegen.generate(
            base_url=base,
            api_key=api_key,
            model=str(image_model.get("id") or ""),
            prompt=imagegen.compose_prompt(str(figure.get("prompt") or ""), aspect="4:3", style=""),
            aspect="4:3",
        )
    except Exception as exc:  # noqa: BLE001 — a missing figure is not a failed report
        log.warning("figure could not be drawn: %s", exc)
        return None
    return {
        "src": pictures.encode(made.mime, made.data),
        "caption": str(figure.get("caption") or ""),
        "width": made.width,
        "height": made.height,
        # popped into the turn's usage
        "_in": made.input_tokens,
        "_out": made.output_tokens,
    }


_FIGURE_FENCE = re.compile(r"^```(?:kpi|chart)\b.*?^```\s*$", re.S | re.M)


def _grounded_figures(text: str, grounded: bool) -> str:
    """Figure blocks dropped when there is no material; prose kept."""
    if not grounded:
        return _FIGURE_FENCE.sub("", text).strip()
    return re.sub(r"\n{3,}", "\n\n", _KPI_FENCE.sub(_kpi_or_nothing, text)).strip()


_KPI_FENCE = re.compile(r"^```kpi\b.*?^```\s*$", re.S | re.M)
_UNDETERMINED = re.compile(r"미정|확인 필요|해당 없음|N/A|TBD|\?|\[[가-힣]", re.I)


def _kpi_or_nothing(match: re.Match[str]) -> str:
    """kpi block dropped if any value is a placeholder."""
    body = match.group(0).strip("`").split("\n", 1)[1] if "\n" in match.group(0) else ""
    lines = [
        line for line in body.split("\n") if line.strip() and not line.strip().startswith("```")
    ]
    if any(
        _UNDETERMINED.search(line.split("|")[0]) or not re.search(r"\d", line.split("|")[0])
        for line in lines
    ):
        return ""
    # thousands separators for tiles
    return re.sub(r"(?m)^([^|\n]{0,80}?)(?<![\d.,])(\d{5,15})(?![\d.,])",
                  lambda m: f"{m.group(1)}{int(m.group(2)):,}", match.group(0))


def _pasted_without_marker(request: str) -> str:
    """Unmarked pasted material (120+ chars) after the first paragraph, or `""`."""
    head, _, rest = (request or "").strip().partition("\n\n")
    rest = rest.strip()
    return rest if head and len(rest) >= 120 else ""


def _request_material(request: str) -> str:
    """Request's own material: pasted block, memo, or price lines."""
    if pasted := pasted_material(request):
        return pasted
    if memo := _pasted_without_marker(request):
        return f"# 요청에 붙여 넣은 자료\n{memo}"
    lines = [line.strip() for line in (request or "").splitlines()
             if report_checks.price_line(line)]
    if not lines:
        # inline amount sentences as material, else nothing to read
        sentences = re.split(r"(?<=[.!?。])\s+|\n+", instruction_part(request or ""))
        lines = [t.strip() for t in sentences if _WON.search(t)]
        if sum(len(_WON.findall(t)) for t in lines) < 2:
            lines = []
    return "# 요청에 적은 자료\n" + "\n".join(lines) if lines else ""


_WON = re.compile(r"\d[\d,]{0,15}(?:\.\d{1,4})?\s{0,2}(?:[조억천만]\s{0,2}){0,4}"
                  r"(?:\d[\d,]{0,15}\s{0,2}(?:[천만]\s{0,2}){1,2})?원")


def _source_text(row: dict[str, Any]) -> str:
    """Search source words, for value tracing."""
    return " ".join(str(row.get(k) or "") for k in ("title", "snippet", "quote", "content", "text", "excerpt"))


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    #: admin-named planner; empty uses `model`
    outline_model: str = "",
    #: approved 목차: absent plans and stops, present writes exactly it
    approved_plan: dict[str, Any] | None = None,
    #: False after 있는 자료로 진행, to avoid ask loops
    may_ask: bool = True,
    #: `None` on planning; `[]` means 그림 없이
    figures_plan: list[dict] | None = None,
    #: empty disables figure proposal
    image_model: dict | None = None,
    web_search: bool = True,
    project_sources: list[dict[str, Any]] | None = None,
    #: session 서식; may fix format and genre
    template_id: str | None = None,
    #: agent's `doc_formats` id when the 서식 fixes none
    format_id: str | None = None,
    design_tokens: dict[str, str] | None = None,
    #: agent's 진행 과정 설정, over its 서식's
    process: dict[str, Any] | None = None,
    #: attached check skills; None reads the 서식's own
    check_keys: tuple[str, ...] | None = None,
    check_options: dict[str, Any] | None = None,
    #: server runs for a person's check skills (`skill_specs`)
    skill_specs: dict[str, Any] | None = None,
    #: one record per stage, run or not
    trace: list[dict[str, Any]] | None = None,
    #: router settlement: given, `report-save` stores the document
    turn: dict[str, Any] | None = None,
    session_id: str = "",
    #: keeps the bundle per stage for reruns
    recorder: Any = None,
    #: receives the built work bundle
    bundle_out: list | None = None,
    #: composed run: earlier text (never a figure source), code output (a source)
    derived_context: list[str] | None = None,
    tool_outputs: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `section` and a final `usage` event; caller owns persistence."""
    from app.pipeline import registry, report_stages
    from app.pipeline.runner import Runner
    from app.pipeline.stage import Context

    bundle = report_stages.bundle_for(
        request=request, model=model, api_key=api_key, trusted_context=trusted_context,
        untrusted_context=untrusted_context, outline_model=outline_model,
        approved_plan=approved_plan, may_ask=may_ask, figures_plan=figures_plan,
        image_model=image_model, web_search=web_search, project_sources=project_sources,
        template_id=template_id, format_id=format_id, design_tokens=design_tokens,
        check_keys=check_keys, turn=dict(turn or {}), session_id=session_id,
        derived=list(derived_context or []), tool_outputs=list(tool_outputs or []),
        check_options=dict(check_options or {}), skill_specs=dict(skill_specs or {}),
        process=dict(process) if process else None,
    )
    pipe = registry.for_report(bundle.spec.format_id)
    flow = "plan" if approved_plan is None else "write"
    if bundle_out is not None:
        bundle_out.append((bundle, pipe.name, flow))
    runner = Runner(pipe.stages(flow), name=f"{pipe.name}.{flow}", recorder=recorder)
    if recorder is not None:
        recorder.pipeline, recorder.flow = pipe.name, flow
        recorder.begin(bundle)
    try:
        async for event in runner.run(bundle, Context(options=dict(pipe.options))):
            yield event
    finally:
        if trace is not None:
            trace.extend(bundle.records)


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
    material: list[str] | None = None,
    #: genre rules also apply to the rewrite
    doc_format: str = "",
) -> tuple[str, dict]:
    """Rewrites one section in document context; `material` is the request's own data."""
    outline = "\n".join(f"{i + 1}. {s.get('heading') or ''}" for i, s in enumerate(sections))
    written = "\n\n".join(
        f"## {s.get('heading')}\n{s.get('content') or ''}"
        for s in sections
        if s.get("id") != target_id and (s.get("content") or "").strip()
    )
    position = next((i for i, s in enumerate(sections) if s.get("id") == target_id), 0)
    role, blocks = _section_role(heading, position, len(sections), written)
    fmt = doc_formats.BY_ID.get(doc_format)
    genre = _genre_rule(request, fmt)
    prompt = _SECTION_PROMPT.format(
        heading=heading,
        outline=outline,
        written=written[-4000:] or "(nothing yet)",
        # keeps citation numbers valid
        refs=_refs_block(sources or []),
        request=request[:1500],
        role=role,
        blocks=blocks,
        others=_others_line([str(x.get("heading") or "") for x in sections], position),
        # existing document and material numbers allowed
        facts=_facts_line(
            "\n".join(
                [request, *(material or []), *[str(s.get("content") or "") for s in sections]]
            ),
            sources or [],
        ),
        genre=genre,
    )
    prompt = _shaped(prompt, genre)
    if note.strip():
        # last and labelled, else read as the original request
        prompt += (
            f"\n\nWhy this section is being rewritten (must be applied):\n{note.strip()[:600]}\n"
            "If the reason names a form (three numbered items, one table, three sentences), write "
            "exactly that form. Add no tables or blocks the reason does not ask for, and do not "
            "redraw a table another section already has."
        )
    body, spent = await _complete(
        model,
        build_document_messages(
            SessionKind.report, prompt, request=request, untrusted_context=material,
            model=model,
        ),
        api_key,
        1200,
    )
    # same normalisation as `write`
    body = hangul.tidy_spacing(hangul.read_back(_without_own_heading(
        internal_words.strip_tool_calls(body), heading))[0])
    target = next((s for s in sections if s.get("id") == target_id), {})
    others = [str(s.get("content") or "") for s in sections if s.get("id") != target_id]
    body = _without_borrowed_tables(body, target.get("content") or "", others, note)
    body = _keeps_its_tables(body, target.get("content") or "", note)
    if fmt is not None and fmt.lists == "gaejo":
        body = doc_formats.gaejo_markdown(doc_formats.official_notation(body))
    return body, spent


def _without_borrowed_tables(body: str, before: str, others: list[str], note: str) -> str:
    """Rewrite tables minus ones drawn elsewhere or never asked for; a dropped table's caption
    goes with it."""
    rx = revised_tables.TABLE
    if not rx.search(body):
        return body
    elsewhere = [m.group(0) for text in others for m in rx.finditer(text)]
    own = [m.group(0) for m in rx.finditer(before)]
    had_table = bool(own) or bool(re.search(r"표", note))

    def borrowed(table: str) -> bool:
        if _table_key(table) in {_table_key(t) for t in elsewhere}:
            return True
        # another part's table redrawn here, not this part's own one
        return (any(revised_tables.same_table(table, t) for t in elsewhere)
                and not any(revised_tables.same_table(table, t) for t in own))

    def keep_or_drop(m: re.Match[str]) -> str:
        if borrowed(m.group(0)) or not had_table:
            return ""
        return m.group(0)

    out = rx.sub(keep_or_drop, body)
    return re.sub(r"\n{3,}", "\n\n", revised_tables.drop_orphan_captions(out)).strip()


_DROPS_TABLE = re.compile(r"표[^.\n]{0,12}(?:빼|없애|삭제|지워|제외|말고|대신|합쳐|합치|통합|병합)")
_CAPTION = re.compile(r"^\s{0,3}\**\s{0,2}(?:<표|표)\s{0,2}(\d{1,3})\b[^\n]{0,120}$")


def _keeps_its_tables(body: str, before: str, note: str) -> str:
    """Tables lost in a rewrite restored after the paragraph naming them, else at the end; a
    table the rewrite redrew (indented under an item, a column added) is not lost."""
    rx = revised_tables.TABLE
    if not rx.search(before) or _DROPS_TABLE.search(note or ""):
        return body
    kept = [m.group(0) for m in rx.finditer(body)]
    blocks: list[tuple[str, str]] = []
    for m in rx.finditer(before):
        if any(_table_key(m.group(0)) == _table_key(t) or revised_tables.same_table(m.group(0), t)
               for t in kept):
            continue
        if kept and not re.search(r"표", note or ""):
            # the rewrite drew its own tables: an unlike old one was replaced, not lost
            continue
        head = before[:m.start()].rstrip("\n").split("\n")[-1] if m.start() else ""
        caption = head if _CAPTION.match(head) else ""
        blocks.append((caption.strip(), m.group(0).strip()))
    out = body.rstrip()
    for caption, table in blocks:
        number = _CAPTION.match(caption).group(1) if caption else ""
        paragraphs = out.split("\n\n")
        if caption and caption in (p.strip() for p in paragraphs):
            # caption kept, table lost
            at = [p.strip() for p in paragraphs].index(caption)
            paragraphs.insert(at + 1, table)
            out = "\n\n".join(paragraphs)
            continue
        block = f"{caption}\n\n{table}" if caption else table
        at = next((i for i, p in enumerate(paragraphs)
                   if number and re.search(rf"표\s{{0,2}}{number}(?!\d)", p)), None)
        if at is None:
            paragraphs.append(block)
        else:
            paragraphs.insert(at + 1, block)
        out = "\n\n".join(paragraphs)
    return out


def _table_key(table: str) -> str:
    """Table header row, whitespace and alignment stripped."""
    head = table.strip().split("\n", 1)[0]
    return re.sub(r"[\s:|-]+", "", head)


_PRODUCT = re.compile(
    r"(?P<a>\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?P<ua>만|억|천)?\s{0,8}(?P<unit>원|명|건|개|대|시간|분)?\s{0,8}"
    r"[×x\*]\s{0,8}(?P<b>\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?P<ub>개월|년|개|명|대|회|일|주|배|시간)?\s{0,8}=\s{0,8}"
    r"(?P<c>\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?P<uc>만|억|천)?\s{0,8}(?P<unitc>원|명|건|개|대|시간|분)?"
)


def _as_number(text: str) -> float:
    return float(text.replace(",", ""))


#: total written before its formula
_VALUE_THEN_PRODUCT = re.compile(
    r"(?P<c>\d[\d,]*(?:\.\d+)?\s*억(?:\s*\d[\d,]*\s*만)?|\d[\d,]*(?:\.\d+)?\s*만)\s*원?\s*"
    r"\(\s*(?P<a>\d[\d,]*(?:\.\d+)?)\s*만\s*원?\s*[×x\*]\s*(?P<b>\d[\d,]*)\s*(?:개월|년|개|명|대|회)\s*\)"
)


_AMOUNT_WON = re.compile(
    r"(?<![\d,.])((?:\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}억\s{0,8})?(?:\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}만)|\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}억)\s{0,8}원"
)


def fix_ledger_magnitudes(text: str, request: str) -> str:
    """Power-of-ten slip against a ledger result restored."""
    results: list[float] = []
    for line in derived_values(request or ""):
        if " = " not in line:
            continue
        right = line.rsplit(" = ", 1)[1]
        match = _AMOUNT_WON.search(right)
        value = _to_man(match.group(1)) if match else None
        if value:
            results.append(value)
    if not results:
        return text

    def fix(m: re.Match) -> str:
        value = _to_man(m.group(1))
        if value is None or any(abs(value - r) < 0.5 for r in results):
            return m.group(0)
        for r in results:
            for factor in (10, 100, 0.1, 0.01):
                if abs(value - r * factor) < 0.5:
                    return _won(r)
        return m.group(0)

    return _AMOUNT_WON.sub(fix, text)


def _to_man(text: str) -> float | None:
    """Korean amount in 만, or `None`."""
    plain = text.replace(",", "").replace(" ", "")
    match = re.fullmatch(r"(?:(\d{1,15}(?:\.\d{1,15})?)억)?(?:(\d{1,15}(?:\.\d{1,15})?)만)?", plain)
    if not match or not (match.group(1) or match.group(2)):
        return None
    return float(match.group(1) or 0) * 10000 + float(match.group(2) or 0)


#: restarted phrase: same noun under two particles, first adverbial (「방향으로 방향을」)
_DOUBLED = re.compile(
    r"(?<![가-힣])([가-힣]{2,6})(으로|로|에서|에게|에|의|와|과|도)\s+"
    r"\1(으로|로|에서|에게|에|을|를|이|가|은|는|의|와|과|도)(?![가-힣])"
)


def undouble_words(text: str) -> str:
    """Restarted phrase keeps its second half; tables and code untouched."""
    out = []
    fenced = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
        if fenced or line.lstrip().startswith("|"):
            out.append(line)
            continue
        out.append(_DOUBLED.sub(lambda m: f"{m.group(1)}{m.group(3)}", line))
    return "\n".join(out)


#: prose subtraction is scratch work; cost workings stay
_SHOWN_ARITHMETIC = re.compile(
    r"(?<![\d.])\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}[-−–]\s{0,8}\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}=\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)"
)
_NUMBER_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: typed 개조식 markers count as list lines
_GAEJO_MARKS = ("□", "■", "○", "◦", "ㅇ", "ㆍ", "·", "∙", "※")
_SENTENCE_END = re.compile(r"(?<=[.!?。])" + units.NO_NUMBER_DOT_BREAK + r"\s{1,8}|(?<=다\.)\s{0,8}(?=[가-힣A-Za-z(「\d])")


def _number_keys(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUMBER_TOKEN.finditer(text)}


def _collapse_arithmetic(body: str) -> str:
    """Prose scratch arithmetic collapsed to its result."""
    out = []
    fenced = False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
        if fenced or line.lstrip().startswith("|"):
            out.append(line)
        else:
            out.append(_SHOWN_ARITHMETIC.sub(r"\1", line))
    return "\n".join(out)


def trim_table_echo(body: str) -> str:
    """Sentences only echoing same-section table cells dropped; last prose sentence kept."""
    lines = body.split("\n")
    table_cells: set[str] = set()
    for line in lines:
        if line.lstrip().startswith("|"):
            for cell in line.strip().strip("|").split("|"):
                table_cells |= _number_keys(cell)
    if len(table_cells) < 2:
        return _collapse_arithmetic(body)
    out: list[str] = []
    fenced = False
    prose_kept = 0
    for line in lines:
        if line.strip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        if fenced or line.lstrip().startswith(("|", "#", "-", "*", ">", *_GAEJO_MARKS)) or not line.strip():
            out.append(line)
            continue
        line = _SHOWN_ARITHMETIC.sub(r"\1", line)
        sentences = [x.strip() for x in _SENTENCE_END.split(line) if x.strip()]
        keep = []
        for sentence in sentences:
            numbers = _number_keys(sentence)
            # an echo: the row's figures and nothing more; who decided what stays (회의록)
            echo = len(numbers) >= 2 and numbers <= table_cells and not _DECIDED.search(sentence)
            keep.append(not echo or bool(_ENUMERATED.match(sentence)))
        # a cut sentence the next kept one leans on comes back
        for i in range(len(sentences) - 2, -1, -1):
            if not keep[i] and keep[i + 1] and _LEANS_BACK.match(sentences[i + 1]):
                keep[i] = True
        kept = [x for x, k in zip(sentences, keep, strict=True) if k]
        prose_kept += len(kept)
        if kept:
            out.append(" ".join(kept))
    if prose_kept == 0:
        return _SHOWN_ARITHMETIC.sub(r"\1", body)
    return "\n".join(out)


_POINT_UNIT = re.compile(r"(?<![\d.])(\d{1,15}(?:\.\d{1,15})?)\s{0,8}(?:%p|퍼센트\s{0,8}포인트|포인트)(?![\w가-힣])")
_BARE_RATIO = re.compile(r"(?<![\d.%])0\.\d{1,15}(?![\d.%])")


def fix_point_units(text: str) -> str:
    """「%p」 after a ratio difference dropped."""
    # separators kept so breaks and fences survive
    parts = re.split(r"((?<=[.!?。])" + units.NO_NUMBER_DOT_BREAK + r"\s{1,8})", text)
    for i in range(0, len(parts), 2):
        sentence = parts[i]
        if _POINT_UNIT.search(sentence) and len(_BARE_RATIO.findall(sentence)) >= 2 and "%" not in (
            _POINT_UNIT.sub(r"\1", sentence)
        ):
            parts[i] = _POINT_UNIT.sub(r"\1", sentence)
    return "".join(parts)


_PERCENT_FORMULA = re.compile(
    r"\(?\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?:-|−|–)\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}\)?\s{0,8}(?:÷|/)\s{0,8}"
    r"(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?:×|x|\*)\s{0,8}100\s{0,8}=\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}%"
)
_RATIO_FORMULA = re.compile(
    r"(?<![\d.)])(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?:÷|/)\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?:×|x|\*)\s{0,8}100"
    r"\s{0,8}=\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}%"
)


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _pct(value: float) -> str:
    rounded = round(value, 1)
    return f"{rounded:.1f}".rstrip("0").rstrip(".") + "%"


def fix_percent_formulas(text: str) -> str:
    """Percentage formula results recomputed; wrong figures fixed everywhere."""
    wrong: dict[str, str] = {}

    def diff(m: re.Match) -> str:
        a, b, c, shown = _num(m.group(1)), _num(m.group(2)), _num(m.group(3)), m.group(4)
        if c == 0:
            return m.group(0)
        right = _pct((a - b) / c * 100)
        if right != _pct(_num(shown)):
            wrong[shown + "%"] = right
            return m.group(0)[: m.start(4) - m.start()] + right
        return m.group(0)

    def ratio(m: re.Match) -> str:
        a, b, shown = _num(m.group(1)), _num(m.group(2)), m.group(3)
        if b == 0:
            return m.group(0)
        right = _pct(a / b * 100)
        if right != _pct(_num(shown)):
            wrong[shown + "%"] = right
            return m.group(0)[: m.start(3) - m.start()] + right
        return m.group(0)

    out = _PERCENT_FORMULA.sub(diff, text)
    out = _RATIO_FORMULA.sub(ratio, out)
    for bad, good in wrong.items():
        out = out.replace(bad, good)
    return out


_LEADING_CONCLUSION = re.compile(r"결론|요약|제언|권고|핵심\s*요약|executive|summary", re.I)


def _section_text(section: dict) -> str:
    return re.sub(r"<[^>]{1,2000}>", " ", str(section.get("content") or ""))


def trim_leading_conclusion(sections: list[dict]) -> list[dict]:
    """Front-loaded conclusion drops restatements of later numbers; never emptied."""
    if len(sections) < 2 or not _LEADING_CONCLUSION.search(str(sections[0].get("heading") or "")):
        return sections
    body = str(sections[0].get("content") or "")
    if str(sections[0].get("format") or "markdown") != "markdown":
        return sections
    trimmed, cut = trim_restatements(body, [_section_text(s) for s in sections[1:]])
    if cut and trimmed.strip():
        sections[0] = {**sections[0], "content": trimmed}
    return sections


_KO_COUNT = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6}
_SENTENCE_ASK = re.compile(
    r"(요약|결론|서론|개요|배경|제언|권고|도입)[은는이가도]?\s{0,8}(?:절|부분|문단)?[은는이가도]?\s{0,8}"
    r"(한|두|세|네|다섯|여섯|\d{1,15})\s{0,8}문장(?:\s{0,8}(?:으로|이내|이하|안에|까지|만))?"
)


def requested_sentence_counts(request: str) -> dict[str, int]:
    """Requested sentence counts per section."""
    out: dict[str, int] = {}
    for m in _SENTENCE_ASK.finditer(request or ""):
        count = _KO_COUNT.get(m.group(2)) or int(m.group(2)) if m.group(2) else None
        if count:
            out[m.group(1)] = count
    return out


def enforce_sentence_counts(sections: list[dict], request: str) -> list[dict]:
    """Sentence-count limits applied to prose only."""
    wanted = requested_sentence_counts(request)
    if not wanted:
        return sections
    for index, section in enumerate(sections):
        heading = str(section.get("heading") or "")
        count = next((n for word, n in wanted.items() if word in heading), None)
        if not count or str(section.get("format") or "markdown") != "markdown":
            continue
        body = str(section.get("content") or "")
        out, seen, fenced = [], 0, False
        for line in body.split("\n"):
            if line.strip().startswith("```"):
                fenced = not fenced
            if fenced or not line.strip() or line.lstrip().startswith(("|", "#", "-", "*", ">", "!", *_GAEJO_MARKS)):
                out.append(line)
                continue
            kept = []
            for sentence in _SENTENCE_END.split(line):
                if not sentence.strip():
                    continue
                if seen < count:
                    kept.append(sentence.strip())
                    seen += 1
            if kept:
                out.append(" ".join(kept))
        if seen > count or out != body.split("\n"):
            sections[index] = {**section, "content": "\n".join(out).strip() + "\n"}
    return sections


def fix_value_then_product(text: str) -> str:
    """Total before its formula recomputed and fixed everywhere."""
    out = text or ""
    corrections: list[tuple[str, str]] = []
    for match in reversed(list(_VALUE_THEN_PRODUCT.finditer(out))):
        written = _to_man(match.group("c"))
        try:
            product = _as_number(match.group("a")) * _as_number(match.group("b"))
        except ValueError:
            continue
        if written is None or product <= 0 or abs(product - written) <= max(0.5, product * 0.005):
            continue
        right = _won(product).replace(" 원", "")
        out = out[: match.start("c")] + right + out[match.end("c") :]
        corrections.append((match.group("c").strip(), right))
    for wrong, right in corrections:
        out = re.sub(rf"(?<![\d,.]){re.escape(wrong)}(?![\d,.])", right, out)
    return out


def _format_like(value: float, sample: str) -> str:
    """`value` formatted like `sample`."""
    rounded = round(value, 2)
    if abs(rounded - round(rounded)) < 1e-9:
        rounded = int(round(rounded))
    text = f"{rounded:,}" if "," in sample or (isinstance(rounded, int) and rounded >= 1000) else f"{rounded}"
    return text


def fix_products(text: str) -> str:
    """「A × B = C」 recomputed; same-scale products only."""
    out = text or ""
    corrections: list[tuple[str, str, str, str]] = []
    # right to left keeps offsets valid
    for match in reversed(list(_PRODUCT.finditer(out))):
        if (match.group("ua") or "") != (match.group("uc") or ""):
            continue
        if units.inside_longer_expression(out, match.start()):
            # 「1,320,000원 × 30 × 1.1 = 43,560,000원」: 「30 × 1.1」 is no product of its own
            continue
        try:
            a = _as_number(match.group("a"))
            b = _as_number(match.group("b"))
            c = _as_number(match.group("c"))
        except ValueError:
            continue
        product = a * b
        if product <= 0 or abs(product - c) <= max(0.5, abs(product) * 0.005):
            continue
        right = _format_like(product, match.group("c"))
        out = out[: match.start("c")] + right + out[match.end("c") :]
        corrections.append(
            (match.group("c"), right, match.group("uc") or "", match.group("unitc") or "")
        )
    # repeats fixed as whole numbers with scale and unit only
    for wrong, right, scale, unit in corrections:
        tail = (rf"\s*{re.escape(scale)}" if scale else "") + (rf"\s*{re.escape(unit)}" if unit else "")
        pattern = re.compile(rf"(?<![\d,.]){re.escape(wrong)}(?![\d,.]){tail}" if (scale or unit)
                             else rf"(?<![\d,.]){re.escape(wrong)}(?![\d,.])")
        def _swap(m: re.Match[str], wrong: str = wrong, right: str = right) -> str:
            return m.group(0).replace(wrong, right, 1)

        out = pattern.sub(_swap, out)
    return out


_PLACEHOLDER = re.compile(
    # value holes only; 「[담당자]」 is the person's
    r"\((?:미정|측정값|값|TBD|TODO|n/a|추후\s*기입|확인\s*필요)\)|\[(?:측정값|값|수치|금액)\]",
    re.I,
)
_FILL_PROMPT = """Below is the draft of the report's "{heading}" section. Its tables and sentences still
have blanks such as [측정값]·[금액]. The attached material (reference data) contains those values.
Rewrite the section, filling the blanks with the material's actual values. Do not make up a
value the material does not have; leave that blank ([측정값] etc.) as it is. Answer with the
body only, no section heading, in Markdown, in Korean. Do not wrap it in JSON.

{body}"""


_TABLE_FIX_PROMPT = """Below is the draft of the report's "{heading}" section. Its tables have these defects:
{problems}

Rewrite the section with whole tables: one row per item, phase or program the request and
material list (none merged, none left out), every row under its header, and the rows adding up
exactly to the 합계 row. Copy every number from the request and material; a value they do not
give is never made up: its cell says 「미정」 and one 「확인 필요: …」 line under the table names it.
Keep the sentences as they are. Answer with the body only, no section heading, in Markdown, in
Korean. Do not wrap it in JSON.

{body}"""


_FILLER = re.compile(
    r"(명시되어 있지 않습니다|확인되지 않았습니다|자료에 (?:다른|별도의) [^.]*없습니다|"
    r"고려할 필요가 있습니다|중요한 역할을 합니다|할 수 있을 것으로 보입니다|"
    r"필요할 것으로 판단됩니다|주목할 만합니다|의미가 있다고 할 수 있습니다)"
)
_SPACED_SENTENCE_END = units.SENTENCE_BREAK
_LINE_LEAD = re.compile(r"^\s{0,24}")
_LIST_LEAD = re.compile(r"^\s{0,24}(?:(?:[-*+]|\d{1,3}[.)]|[□■○◦ㅇㆍ·∙※])\s{1,4})?")
_NUMBER_FACT = re.compile(r"(?<![A-Za-z\d])\d[\d,.]*(?:\s*(?:만|억|천))?[가-힣%]?")


_PARTICLE = re.compile(
    r"(되었습니다|였습니다|이었습니다|했습니다|합니다|됩니다|입니다|습니다|되었고|이었고|였고|"
    r"되었|되어|하였|하여|했다|한다|하는|하고|하며|이며|으며|에서는|에서|으로|은|는|이|가|을|를|"
    r"의|에|로|와|과|도|된|한|며)$"
)
#: filler words; restatement still counts
_CONNECTIVES = frozenset(
    "이번 총 동안 기간 대상 기준 통해 위해 경우 또한 그리고 이후 당시 전체 각각 모두 해당 "
    "진행 운영 실시 수행 소속 포함 전제 결과 과정 중 및 등".split()
)


def _stems(text: str) -> set[str]:
    """Content-word stems; short stems and connectives excluded."""
    out = set()
    for word in re.findall(r"[가-힣A-Za-z]{2,}", text):
        stem = _PARTICLE.sub("", word) or word
        if len(stem) >= 2 and stem not in _CONNECTIVES:
            out.add(stem)
    return out


#: test lines: never trimmed (wrong choices repeat numbers)
#: a formula or a numbered step: never a restatement to trim (r5 A10-T2 lost 「연간 시장 규모(원) =
#: N(명) × 9,900원 × 12개월 × c」 and its 「2단계」 paragraph)
_WORKED_LINE = re.compile(r"[=×÷]|\d\s?[x*/]\s?\d|(?:^|\s|\()\d{1,2}\s?단계")
_ASSESSMENT_LINE = re.compile(
    r"[①-⑩]|^\s*(?:\*\*)?(?:서술형\s?)?\d{1,2}\s?[.)번](?:\*\*)?\s|^\s*\*\*서술형|"
    r"\d{1,2}\s?점\)|[?？]\s{0,8}$|(?:정답|모범\s?답|채점|배점|보기)\s?[:：]|시오\.?\s{0,8}$"
)


#: sentence leaning on the one before: 「이 요건은」, 「따라서」, 「둘째,」
_LEANS_BACK = re.compile(
    r"^(?:[-*+]\s+)?(?:이|그|저|이런|이러한|이와|그런|그러한|그와|두|세|네|이때|그때|이에|이로써|"
    r"따라서|그러므로|그래서|그러나|하지만|다만|또한|즉|한편|반면|둘째|셋째|넷째|다섯째|"
    r"마지막으로|나머지)(?=[\s,])"
)
#: a decision and who made or asked it: 「…의 제안과 …의 승인으로 결정되었습니다」
_DECIDED = re.compile(r"결정|승인|합의|제안|요청|지시|확정|하기로|않기로|보류")
#: one item of a counted run (「첫째, …」 … 「셋째, …」): cutting one breaks the run
_ENUMERATED = re.compile(r"^(?:[-*+]\s+)?(?:첫째|둘째|셋째|넷째|다섯째|여섯째|마지막으로)(?=[\s,])")
#: section heading asking a question: 「Q7. 신청 기간과 방법은?」
QUESTION_HEADING = re.compile(r"\?\s*$|^\s*(?:Q|문)\s?\d{1,2}\b")
#: line asking a question: 「**Q2. 신청 기간은?**」
_QUESTION_LINE = re.compile(r"\?\s*(?:\*\*|__)?\s*$")


def trim_restatements(
    body: str, earlier: list[str], *, keep_items: bool = False, question: bool = False
) -> tuple[str, list[str]]:
    """`(body, cut sentences)`: pure restatements and filler dropped; blocks kept, never emptied.
    An answer's lead sentence (after a question heading or line) and any sentence the next one
    leans on (「이 …」, 「둘째,」) stay."""
    if not body.strip():
        return body, []
    # pool grows sentence by sentence
    earlier_text = " ".join(earlier)
    earlier_numbers = set(_NUMBER_FACT.findall(earlier_text))
    earlier_words = _stems(earlier_text)
    has_table = any(line.strip().startswith("|") for line in body.split("\n"))
    # per line: (line, sentences or None, kept flags)
    rows: list[tuple[str, list[str] | None, list[bool]]] = []
    in_block = False
    answer_lead = question
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            in_block = not in_block
        if line.strip().startswith("|"):
            # reading a table back is a restatement
            cells = " ".join(
                c for c in line.split("|") if c.strip() and not set(c.strip()) <= set("-: ")
            )
            earlier_numbers |= set(_NUMBER_FACT.findall(cells))
            earlier_words |= _stems(cells)
        if (in_block or line.strip().startswith("|") or line.strip().startswith("#")
                or not line.strip() or _ASSESSMENT_LINE.search(line)
                or _WORKED_LINE.search(line)
                or keep_items and _LIST_LEAD.match(line).group(0).strip()):
            rows.append((line, None, []))
            if line.strip().startswith("#") and _QUESTION_LINE.search(line):
                answer_lead = True
            continue
        sentences = [x for x in _SPACED_SENTENCE_END.split(line) if x.strip()]
        flags: list[bool] = []
        for sentence in sentences:
            text = sentence.strip()
            numbers = set(_NUMBER_FACT.findall(text))
            words = _stems(text)
            # ratios of given numbers aren't new facts
            given = {n for n in numbers if n in earlier_numbers or re.fullmatch(r"\d{1,15}배", n)}
            restated = (
                bool(numbers)
                and numbers <= given
                and (
                    len(numbers) >= 2
                    and len(words - earlier_words) <= max(1, len(words) // 5)
                    # three given numbers together: same statement
                    or len(numbers) >= 3
                    and len(words - earlier_words) <= len(words) // 2
                    or len(numbers) == 1
                    and len(text) >= 8
                    and not (words - earlier_words)
                )
            )
            dangling = (
                not has_table
                and re.search(r"(?:위|아래|다음|이)\s*표(?:는|에서|를|가|의)", text)
                and re.search(r"(?:보여|나타내|정리|제시|요약)", text)
            )
            keep = (answer_lead or _QUESTION_LINE.search(text) or _ENUMERATED.match(text)
                    or not (restated or dangling or _FILLER.search(text)))
            # the question's answer is the sentence after it
            answer_lead = bool(_QUESTION_LINE.search(text))
            flags.append(bool(keep))
            if keep:
                earlier_numbers |= numbers
                earlier_words |= words
        rows.append((line, sentences, flags))
    # a cut sentence the next one leans on comes back; chains walk backwards
    chain: list[tuple[int, int] | None] = []
    for r, (line, sentences, _) in enumerate(rows):
        if sentences is None:
            if line.strip():
                chain.append(None)
            continue
        chain.extend((r, k) for k in range(len(sentences)))
    for i in range(len(chain) - 2, -1, -1):
        here, after = chain[i], chain[i + 1]
        if here is None or after is None:
            continue
        flags_here, flags_after = rows[here[0]][2], rows[after[0]][2]
        if (not flags_here[here[1]] and flags_after[after[1]]
                and _LEANS_BACK.match(rows[after[0]][1][after[1]].strip())):
            flags_here[here[1]] = True
    cut: list[str] = []
    out_lines: list[str] = []
    for line, sentences, flags in rows:
        if sentences is None:
            out_lines.append(line)
            continue
        if sentences and not any(flags) and not any(x.strip() for x in out_lines):
            # never emptied
            flags[0] = True
        cut.extend(x.strip() for x, f in zip(sentences, flags, strict=True) if not f)
        kept = [x for x, f in zip(sentences, flags, strict=True) if f]
        if kept and kept == sentences:
            out_lines.append(line)
        elif kept:
            # indent kept; marker kept if the first sentence went
            lead = (_LINE_LEAD if flags[0] else _LIST_LEAD).match(line).group(0)
            out_lines.append(lead + " ".join(k.strip() for k in kept))
        elif not sentences:
            out_lines.append(line)
    if not cut:
        return body, []
    return "\n".join(out_lines).strip() + "\n", cut


_FIGURE_BLOCK = re.compile(r"```(?:mermaid|chart)\b.*?```\n?(?:\*[^\n]*\*\n?)?", re.S)
_FIGURE_KEYWORDS = {"graph", "flowchart", "LR", "TD", "TB", "RL", "style", "fill", "classDef",
                    "class", "subgraph", "end", "direction", "br", "hot", "mermaid", "chart"}


def _figure_words(block: str) -> set[str]:
    """Diagram label words without syntax."""
    body = re.sub(r"^```\w*|```$", "", block.strip(), flags=re.M)
    body = re.sub(r"\*[^\n]*\*\s*$", "", body)
    return {w for w in re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9]{1,}", body) if w not in _FIGURE_KEYWORDS}


def drop_repeated_figures(text: str) -> str:
    """Duplicate diagram dropped (4/5 word overlap); captioned one kept."""
    blocks = list(_FIGURE_BLOCK.finditer(text))
    if len(blocks) < 2:
        return text
    words = [_figure_words(m.group(0)) for m in blocks]
    captioned = [bool(re.search(r"\*[^\n]*\*\s*$", m.group(0).strip())) for m in blocks]
    drop: set[int] = set()
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            a, b = words[i], words[j]
            if not a or not b:
                continue
            small, large = (a, b) if len(a) <= len(b) else (b, a)
            if len(small & large) / len(small) < 0.8:
                continue
            loser = i if (captioned[j] and not captioned[i]) else j
            drop.add(loser)
    if not drop:
        return text
    out = text
    for index in sorted(drop, reverse=True):
        m = blocks[index]
        out = out[: m.start()] + out[m.end():]
    return re.sub(r"\n{3,}", "\n\n", out).strip() + ("\n" if text.endswith("\n") else "")


def drop_redundant_kpi(text: str) -> str:
    """kpi block duplicating a same-section table dropped."""
    match = re.search(r"```kpi\n(.*?)```\n?", text, flags=re.S)
    if not match:
        return text
    values = [line.split("|")[0].strip() for line in match.group(1).splitlines() if "|" in line]
    table_cells = " ".join(line for line in text.splitlines() if line.strip().startswith("|"))
    if values and all(v and v in table_cells for v in values):
        return (text[: match.start()] + text[match.end():]).strip() + "\n"
    return text


def placeholder_heavy(text: str) -> bool:
    """Three or more value holes: unfilled frame."""
    return len(_PLACEHOLDER.findall(text or "")) >= 3


_REFERENCE_HEADING = re.compile(r"참고\s{0,3}문헌|references|bibliography|인용\s{0,3}문헌", re.I)
_REF_START = re.compile(r"(?<!\S)\[(\d{1,3})\]\s")


def _title_key(entry: str) -> str:
    """Longest lowercased Latin run (the title, in practice)."""
    body = re.sub(r"https?://\S{1,400}|doi:\S{1,200}|arXiv:\s{0,3}\S{1,40}", " ", entry)
    runs = re.findall(r"[A-Za-z][A-Za-z0-9:,'\- ]{12,300}", body)
    title = max(runs, key=len) if runs else body
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))[:80]


def _references_from_table(body: str) -> str:
    """Table-form reference list as numbered lines."""
    if not re.search(r"(?m)^\s{0,3}\|", body):
        return body
    # rejoin rows split by blank lines
    body = re.sub(r"\|\s{0,8}\n\s{0,8}\n\s{0,3}(\[\d{1,3}\]\s{0,3}\|)", r"| \1", body)
    out = []
    for line in body.split("\n"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        first = next((c for c in cells if c), "")
        number = re.fullmatch(r"\[(\d{1,3})\]", first)
        if line.lstrip().startswith(("|", "[")) and number:
            rest = [
                c.rstrip(". ") for c in cells[cells.index(first) + 1 :]
                if c and c not in ("(미정)", "미정", "-", "–") and not placeholders.is_blank(c)
            ]
            out.append(f"{first} " + ". ".join(rest) + ".")
        elif re.fullmatch(r"\s{0,3}\|?[\s|:\-]{3,200}\|?\s{0,3}", line) or (
            line.lstrip().startswith("|") and not number
        ):
            continue
        else:
            out.append(line)
    return "\n".join(out)


def _split_references(body: str) -> tuple[str, list[tuple[int, str]]] | None:
    body = _references_from_table(body)
    starts = list(_REF_START.finditer(body))
    if len(starts) < 2:
        return None
    lead = body[: starts[0].start()].strip()
    bounds = [m.start() for m in starts] + [len(body)]
    entries = [
        # blank line ends an entry
        (int(m.group(1)), re.split(r"\n\s{0,8}\n", body[m.end() : bounds[i + 1]].strip())[0])
        for i, m in enumerate(starts)
    ]
    return lead, entries


#: notes end like sentences; entries don't
_PROSE_END = re.compile(r"(?:니다|[이한된있없했였같않]다|이다|이에요|해요)\s{0,2}$")
#: boundary between run-together entries
_NEXT_ENTRY = re.compile(
    r"(?<=[.)])\s{1,4}(?="
    r"[가-힣]{2,4}\s{0,1}(?:\([^()]{0,40}\))?\.\s{0,2}(?:\(\d{4}\)\.\s{0,2})?[『「]"
    r"|[A-Z][a-z]{1,30},\s(?:[A-Z][a-z]{1,30}|[A-Z]\.)"
    r"|\(저자\s{0,2}확인\s{0,2}필요\)|\[저자\]|\[\d{1,3}\]\s)"
)


_ENTRY_START = re.compile(
    r"^(?:\[\d{1,3}\]|\d{1,3}[.)]|[-*]\s|[가-힣]{2,5}(?:\([^()]{0,40}\))?[.,]"
    r"|[A-Z][A-Za-z'’-]{1,30},|\(저자|\[저자\]|[『「*])"
)


def reference_list_only(sections: list[dict]) -> list[dict]:
    """Reference section cleaned: notes dropped, one entry per line, bold labels kept."""
    out = []
    for sec in sections:
        if not (
            _REFERENCE_HEADING.search(str(sec.get("heading") or ""))
            and str(sec.get("format") or "markdown") == "markdown"
        ):
            out.append(sec)
            continue
        text = str(sec.get("content") or "")
        # trailing number belongs to the next entry
        text = re.sub(r"[ \t]{1,4}(\[\d{1,3}\])[ \t]{0,4}\n", r"\n\1 ", text)
        text = re.sub(r"\s{0,4}(\*\*[^*\n]{2,40}\*\*)\s{0,4}", r"\n\n\1\n\n", text)
        lines: list[str] = []
        for block in re.split(r"\n\s{0,8}\n|\n", text):
            block = block.strip()
            if not block:
                continue
            if re.fullmatch(r"\*\*[^*\n]{2,40}\*\*", block):
                lines.append(block)
                continue
            pieces = re.split(r"(?<=[다요])\.\s{1,4}|(?<=\d{4}\.)\s{1,4}(?=[가-힣])", block)
            for piece in pieces:
                piece = piece.strip()
                if piece and _PROSE_END.search(piece.rstrip(".")):
                    # trailing Korean note after the last entry
                    starts = [m.end() for m in re.finditer(r"[.)]\s{1,4}(?=[가-힣])", piece)]
                    piece = piece[: starts[-1]].strip() if starts else ""
                if not piece:
                    continue
                for entry in _NEXT_ENTRY.split(piece):
                    entry = entry.strip()
                    if entry and (_ENTRY_START.match(entry) or re.search(r"[『「]", entry[:40])):
                        lines.append(entry)
                    elif entry and lines and re.match(r"[A-Za-z0-9]", entry):
                        lines[-1] = f"{lines[-1]} {entry}"
        kept = [
            line for i, line in enumerate(lines)
            if not line.startswith("**")
            or (i + 1 < len(lines) and not lines[i + 1].startswith("**"))
        ]
        content = "\n\n".join(kept)
        out.append({**sec, "content": content + "\n"} if content.strip() else sec)
    return out


#: a placeholder left in a sentence: filled from the material, else written around
PLACEHOLDER_LEFT = ("자리표시(「(미정)」, 「(여기에 …)」 등)가 남아 있다. 자료에 있는 값으로 채우고, "
                    "없으면 그 값 없이도 맞는 문장으로 고쳐 써라(표 칸은 「미정」, 표 아래 「확인 필요」 한 줄).")

_AMEND_PROMPT = """Only add to the section below. {instruction}

Rules: do not change or delete a single character of the existing sentences and table rows.
Leave everything but what you add as it is. Do not invent values the material does not give;
write what you add so it holds without them (a table cell: 「미정」). Write additions in Korean.
Answer with the amended section body only (no heading or explanation).

절:
{content}"""

_CITATION_ASK = re.compile(r"저자\s{0,2}연도|괄호\s{0,2}인용|본문\s{0,2}인용|(?<![A-Za-z])APA(?![A-Za-z])")
_ENDPOINT = re.compile(r"(GET|POST|PUT|PATCH|DELETE)\s{0,3}\|?\s{0,3}`?(/[\w/{}:.-]{1,120})")


def _works(sections: list[dict], material: str) -> list[str]:
    """Citable works: reference entries, else author-year-title material lines."""
    refs = next(
        (str(s.get("content") or "") for s in sections
         if _REFERENCE_HEADING.search(str(s.get("heading") or ""))),
        "",
    )
    pool = refs.splitlines() if refs.strip() else (material or "").splitlines()
    out = []
    for line in pool:
        line = line.strip(" -*·\t").replace("**", "")
        if re.search(r"(?:1[5-9]|20)\d{2}", line) and re.search(r"[『「]|\*[^*]{2,}\*", line) \
                and re.match(r"[가-힣A-Z]", line) and len(line) <= 200 and line not in out:
            out.append(line)
    return out[:20]


_UNCONFIRMED = " (검색으로 확인되지 않음)"


async def _mark_unverified_references(sections: list[dict]) -> list[dict]:
    index = next(
        (i for i, s in enumerate(sections)
         if _REFERENCE_HEADING.search(str(s.get("heading") or ""))
         and str(s.get("format") or "markdown") == "markdown"),
        None,
    )
    if index is None:
        return sections
    entries = [line for line in str(sections[index].get("content") or "").split("\n")]
    titles: dict[int, str] = {}
    for n, line in enumerate(entries):
        m = re.search(r"[『「]([^』」]{3,120})[』」]|\*([^*]{3,120})\*", line)
        # web sources carry links: only memory-named works
        if m and "http" not in line and _UNCONFIRMED.strip() not in line:
            titles[n] = (m.group(1) or m.group(2)).strip()
    if not titles:
        return sections
    found = await research.verify_titles(list(dict.fromkeys(titles.values())))
    for n, title in titles.items():
        if found.get(title) is False:
            entries[n] = entries[n].rstrip() + _UNCONFIRMED
    out = list(sections)
    out[index] = {**sections[index], "content": "\n".join(entries)}
    return out


def additions_needed(
    sections: list[dict], request: str, material: str
) -> list[tuple[int, str]]:
    """`(section index, addition)` for asked-for content the draft omitted."""
    instruction = instruction_part(request)
    body_idx = [
        i for i, s in enumerate(sections)
        if not _REFERENCE_HEADING.search(str(s.get("heading") or ""))
        and str(s.get("format") or "markdown") == "markdown"
    ]
    out: list[tuple[int, str]] = []
    body = "\n".join(str(sections[i].get("content") or "") for i in body_idx)
    if _CITATION_ASK.search(instruction) and not _AUTHOR_YEAR.search(body):
        works = _works(sections, material)
        if works:
            listing = "\n".join(f"- {w}" for w in works)
            ask = (
                "Append a (저자 연도) citation to the end of each claim in this section that a "
                "work below supports. Do not use works not on the list. Add nothing to a "
                "sentence no work fits.\n" + listing
            )
            for i in body_idx:
                if len(str(sections[i].get("content") or "")) >= 300:
                    out.append((i, ask))
    return out


_QUESTION_NUMBER = re.compile(
    r"^\s{0,16}(?:#{1,4}\s{0,8})?(?:\*\*)?\s{0,8}(?:\[?\d{1,2}\s?[.)\]번]|[①-⑩]|문항\s?\d{1,2}|Q\d{1,2})"
)
#: 「(?<!십)시오」: notices' 「주십시오」 isn't a question
_ASKS = re.compile(r"\?|(?<!십)시오\.?|것은\??\s{0,16}(?:\*\*)?\s{0,16}$|무엇인가|설명하라|쓰라")
_ASKED_FOR = re.compile(r"문항|평가|퀴즈|시험|문제")
_QUESTION_TAIL = re.compile(
    r"^(?:[①-⑩]|\(?[1-5가-마]\)|[-*+]\s|\||>|---|#{1,4}\s{0,8}(?:\*\*)?\s{0,8}(?:정답|해설|채점|모범)|"
    r"(?:\*\*)?\s{0,8}(?:정답|해설|풀이|채점|"
    r"모범|예시\s?답|배점|평가\s?기준|부분\s?점수|오답|\d{1,2}\s?점)|.{0,40}\d{1,2}\s?점\)?$)"
)
_ASSESSMENT_HEADING = re.compile(r"평가|문항|퀴즈")


def _question_lines(text: str) -> list[int]:
    """Lines opening a question, incl. bare numbers whose next line asks."""
    lines = text.split("\n")
    found = []
    for i, line in enumerate(lines):
        if not _QUESTION_NUMBER.match(line):
            continue
        if _ASKS.search(line) or (
            len(line) < 60 and i + 1 < len(lines) and _ASKS.search(lines[i + 1])
            and not _QUESTION_NUMBER.match(lines[i + 1])
        ):
            found.append(i)
    return found


def _stem_of(lines: list[str], i: int) -> str:
    """Asking words of the question at line `i`."""
    return lines[i] if _ASKS.search(lines[i]) or i + 1 >= len(lines) else lines[i + 1]


def _stem_key(line: str) -> str:
    stem = _QUESTION_NUMBER.sub("", line)
    stem = re.sub(r"\([^)]{0,20}\)", "", stem)
    return re.sub(r"[^가-힣A-Za-z0-9]", "", stem)[:12]


def question_set(material: str) -> str:
    """The material's question set (5+), as written."""
    lines = (material or "").split("\n")
    stems = _question_lines(material or "")
    if len(stems) < 5:
        return ""
    # last question ends at the first non-question line
    end = stems[-1] + 1
    after_blank = False
    answers = 0  # inside a 「## 정답 및 해설」 section: its heading level
    while end < len(lines):
        line = lines[end].strip()
        heading = re.match(r"^(#{1,4})\s", line)
        if re.match(r"^(?:검색어:|본문 발췌:|출처: https?://|\[\d{1,3}\] )", line):
            break
        # bold title after a gap starts the next piece
        if (after_blank and re.fullmatch(r"\*\*[^*]{2,60}\*\*", line)
                and not re.search(r"정답|해설|채점|모범|문항|\d", line)):
            break
        if heading and re.search(r"정답|해설|채점|모범", line):
            answers = answers or len(heading.group(1))
        elif answers:
            if heading and len(heading.group(1)) <= answers:
                break
        # plain line ends a question only after a blank
        elif line and not _QUESTION_TAIL.match(line) and after_blank:
            break
        after_blank = not line
        end += 1
    while end > stems[-1] + 1 and not lines[end - 1].strip():
        end -= 1
    block = [line for line in lines[stems[0]:end] if line.strip() != "---"]
    # inner headings as bold lines
    block = [re.sub(r"^\s{0,16}#{1,4}\s{0,16}(.{1,2000}?)\s{0,64}$", r"**\1**", line) for line in block]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(block)).strip()


def carry_question_set(sections: list[dict], material: str, request: str) -> list[dict]:
    """Material questions carried verbatim."""
    if not _ASKED_FOR.search(instruction_part(request)):
        return sections
    block = question_set(material)
    if not block:
        return sections
    body = re.sub(r"[^가-힣A-Za-z0-9]", "", "\n".join(str(s.get("content") or "") for s in sections))
    given = material.split("\n")
    keys = [k for k in (_stem_key(_stem_of(given, i)) for i in _question_lines(material)) if k]
    if not keys or sum(1 for k in keys if k in body) >= 0.6 * len(keys):
        return sections
    body_idx = [i for i, s in enumerate(sections)
                if not _REFERENCE_HEADING.search(str(s.get("heading") or ""))]
    if not body_idx:
        return sections
    target = next((i for i in body_idx
                   if _ASSESSMENT_HEADING.search(str(sections[i].get("heading") or ""))),
                  body_idx[-1])
    out = [dict(s) for s in sections]
    out[target]["content"] = (
        str(out[target].get("content") or "").rstrip() + "\n\n**평가 문항**\n\n" + block
    )
    log.info("carried %d questions into %r", len(keys), out[target].get("heading"))
    return out


def add_missing_endpoints(sections: list[dict], material: str) -> list[dict]:
    """Omitted material API endpoints added to the API section."""
    given: dict[tuple[str, str], str] = {}
    for line in (material or "").splitlines():
        for m, path in _ENDPOINT.findall(line):
            if re.match(r"/(?:GET|POST|PUT|PATCH|DELETE)\b", path):
                continue  # 「GET/POST/PUT」: a list of verbs, not a path
            key = (m, path.rstrip("/"))
            if key not in given:
                rest = line.split(path, 1)[-1]
                note = re.sub(r"^[\s`|:：—–-]{1,200}|[\s`|]{1,200}$", "", rest)
                note = re.sub(r"\s{0,16}\|\s{0,16}", " · ", note)
                given[key] = note[:80]
    body_idx = [
        i for i, s in enumerate(sections)
        if not _REFERENCE_HEADING.search(str(s.get("heading") or ""))
        and str(s.get("format") or "markdown") == "markdown"
    ]
    if len(given) < 5 or not body_idx:
        return sections
    body = "\n".join(str(sections[i].get("content") or "") for i in body_idx)
    written = {(m, p.rstrip("/")) for m, p in _ENDPOINT.findall(body)}
    missing = [k for k in given if k not in written]
    if len(given) - len(missing) >= 0.9 * len(given):
        return sections
    target = max(
        body_idx,
        key=lambda i: (
            len(_ENDPOINT.findall(str(sections[i].get("content") or ""))),
            bool(re.search(r"API|설계|인터페이스", str(sections[i].get("heading") or ""))),
        ),
    )
    rows = "\n".join(f"| {m} | `{p}` | {given[(m, p)] or '-'} |" for m, p in missing[:40])
    addition = (
        "\n\n대화에서 정리한 API 가운데 위에 없는 것은 다음과 같다.\n\n"
        "| 메서드 | 경로 | 설명 |\n|---|---|---|\n" + rows
    )
    out = list(sections)
    out[target] = {**sections[target], "content": str(sections[target]["content"]).rstrip() + addition}
    return out


def only_added(before: str, after: str) -> bool:
    """`after` keeps every line of `before` and is longer."""
    norm = lambda t: re.sub(r"\s+", " ", t).strip()  # noqa: E731
    kept_lines = [norm(line) for line in before.splitlines() if norm(line)]
    after_norm = norm(re.sub(r"\s{0,2}\([^()]{2,60}?(?:1[5-9]|20)\d{2}[a-z]?\)", "", after))
    after_full = norm(after)
    if not after.strip() or len(after) <= len(before):
        return False
    present = sum(
        1 for line in kept_lines
        if line in after_full
        or norm(re.sub(r"\s{0,2}\([^()]{2,60}?(?:1[5-9]|20)\d{2}[a-z]?\)", "", line)) in after_norm
        or all(part.strip() in after_norm for part in re.split(r"(?<=[.!?다])\s", line) if part.strip())
    )
    return present >= 0.95 * len(kept_lines)


_AUTHOR_YEAR = re.compile(
    r"(?:\(\s{0,2}([가-힣]{2,4}|[A-Z][a-z]{1,20})(?:\s{0,2}(?:외|et al\.))?[\s,]{1,3}((?:1[5-9]|20)\d{2})[a-z]?\s{0,2}\)"
    r"|([가-힣]{2,4}|[A-Z][a-z]{1,20})\s{0,1}\(((?:1[5-9]|20)\d{2})[a-z]?\))"
)


def fill_reference_list(sections: list[dict], material: str) -> list[dict]:
    """Empty reference section rebuilt from cited 「저자 (연도)」 works."""
    index = next(
        (
            i for i, sec in enumerate(sections)
            if _REFERENCE_HEADING.search(str(sec.get("heading") or ""))
            and str(sec.get("format") or "markdown") == "markdown"
        ),
        None,
    )
    if index is None:
        return sections
    current = str(sections[index].get("content") or "")
    if any(_ENTRY_START.match(line.strip()) or re.search(r"[『「]", line[:40])
           for line in current.splitlines() if line.strip()):
        return sections
    body = "\n".join(str(sec.get("content") or "") for i, sec in enumerate(sections) if i != index)
    cited: list[tuple[str, str]] = []
    for m in _AUTHOR_YEAR.finditer(body):
        name, year = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if (name, year) not in cited:
            cited.append((name, year))
    if not cited:
        return sections
    lines = [line.strip(" -*·\t") for line in (material or "").splitlines()]
    entries = []
    for name, year in cited:
        found = next(
            (line for line in lines
             if line.startswith(name) and year in line and re.search(r"[『「]|\*[^*]{2,}\*", line)
             and len(line) <= 300),
            "",
        )
        entries.append(found.replace("**", "") if found else f"{name} ({year}). [서지 정보]")
    out = list(sections)
    out[index] = {**sections[index], "content": "\n\n".join(entries) + "\n"}
    return out


def drop_unbacked_citations(sections: list[dict], sources: list[dict]) -> list[dict]:
    """Unbacked 「[n]」 markers dropped; pasted numbered notes aren't sources."""
    backed = set(range(1, len(sources or []) + 1))
    for sec in sections:
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            backed |= {
                int(n) for n in re.findall(r"(?m)^\s{0,4}\[(\d{1,3})\]", str(sec.get("content") or ""))
            }

    def strip(text: str) -> str:
        text = re.sub(
            r"[ \t]{0,2}\[(\d{1,3})\](?!\()",
            lambda m: m.group(0) if int(m.group(1)) in backed else "",
            text,
        )
        return re.sub(r"[ \t]{1,2}([.,])", r"\1", text) if text else text

    out = []
    for sec in sections:
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")) or str(
            sec.get("format") or "markdown"
        ) != "markdown":
            out.append(sec)
            continue
        out.append({**sec, "content": strip(str(sec.get("content") or ""))})
    return out


def tidy_references(sections: list[dict]) -> list[dict]:
    """Reference list deduplicated and renumbered, citations following."""
    index = next(
        (
            i for i, sec in enumerate(sections)
            if _REFERENCE_HEADING.search(str(sec.get("heading") or ""))
            and str(sec.get("format") or "markdown") == "markdown"
        ),
        None,
    )
    if index is None:
        return sections
    split = _split_references(str(sections[index].get("content") or ""))
    if split is None:
        return sections
    lead, entries = split
    mapping: dict[int, int] = {}
    kept: list[str] = []
    by_key: dict[str, int] = {}
    for old, text in entries:
        key = _title_key(text)
        if key and key in by_key:
            mapping[old] = by_key[key]
            continue
        kept.append(text)
        by_key[key] = len(kept)
        mapping[old] = len(kept)
    listing = "\n\n".join(
        ([lead] if lead else []) + [f"[{n}] {text}" for n, text in enumerate(kept, 1)]
    )

    def renumber(text: str) -> str:
        text = re.sub(
            r"\[(\d{1,3})\](?!\()",
            lambda m: f"[{mapping.get(int(m.group(1)), int(m.group(1)))}]",
            text,
        )
        return re.sub(r"(\[\d{1,3}\])(?:[ ,]{0,2}\1){1,10}", r"\1", text)

    out = []
    for i, sec in enumerate(sections):
        if i == index:
            out.append({**sec, "content": listing + "\n"})
        elif str(sec.get("format") or "markdown") == "markdown":
            out.append({**sec, "content": renumber(str(sec.get("content") or ""))})
        else:
            out.append(sec)
    return out


_PROSE_KEYS = ("content", "body", "text", "본문", "내용")


def _prose_in(data, depth: int = 0) -> list[str]:
    """Prose fields of a nested JSON answer."""
    if depth > 3:
        return []
    if isinstance(data, dict):
        for key in _PROSE_KEYS:
            if isinstance(data.get(key), str) and data[key].strip():
                return [data[key]]
        return [p for v in data.values() for p in _prose_in(v, depth + 1)]
    if isinstance(data, list):
        return [p for v in data for p in _prose_in(v, depth + 1)]
    return []


def unwrap_json_prose(text: str) -> str:
    """Body wrapped as one-key JSON unwrapped; else unchanged."""
    stripped = (text or "").strip()
    fence = re.fullmatch(r"```(?:json)?\s{0,64}(\{.{0,200000}\})\s{0,64}```", stripped, re.S)
    if fence:
        stripped = fence.group(1)
    if stripped.startswith("{") and not stripped.endswith("}"):
        # truncated: the one opened string is the body
        opened = re.match(
            r'\{\s*(?:"[^"\n]{1,40}"\s*:\s*\{\s*)?'
            r'(?:"[^"\n]{1,40}"\s*:\s*(?:-?\d{1,6}|"[^"\n]{0,120}")\s*,\s*){0,4}'
            r'"[^"\n]{1,40}"\s*:\s*"', stripped)
        if not opened:
            return text
        body = stripped[opened.end():]
        try:
            return json.loads(f'"{body}"', strict=False)
        except json.JSONDecodeError:
            end = body.rfind('"')
            cut = body[:end] if end > 0 and re.fullmatch(r'"\s*,?\s*', body[end:]) else body
            return cut.replace('\\n', "\n").replace('\\"', '"').replace("\\t", " ")
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return text
    try:
        # strict=False: raw newlines in strings
        data = json.loads(stripped, strict=False)
    except (json.JSONDecodeError, ValueError):
        return text
    if isinstance(data, dict):
        # content/body fields at any depth, sections in order
        prose = _prose_in(data)
        if prose:
            return "\n\n".join(prose)
        values = [v for v in data.values() if isinstance(v, str) and v.strip()]
        if len(values) == 1 and len(data) <= 2:
            return values[0]
        if values and all(isinstance(v, str) for v in data.values()):
            return "\n\n".join(values)
    return text


def word_count(sections: list[dict]) -> int:
    return sum(len((s.get("content") or "").split()) for s in sections)


def to_markdown(title: str, sections: list[dict]) -> str:
    """The document as a Markdown file; the app's block fences (callout, kpi, chart …), which a
    Markdown viewer shows as they stand, written as plain Markdown (r6 A04-T1)."""
    parts = [f"# {title}"]
    for section in sections:
        # untitled lone 「본문」 (공문·안내문): no empty 「## 」
        head = f"## {section['heading']}\n\n" if str(section.get("heading") or "").strip() else ""
        parts.append(f"\n{head}{block_fences.plain(section.get('content') or '')}")
    return "\n".join(parts).strip() + "\n"
