"""Report writing: outline, one-shot draft split by heading, per-section fallback and rewrite."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core import logs
from app.core.config import settings
from app.models.chat import SessionKind
from app.services import deck as deck_rules
from app.services import (
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
    settings_store,
    thinking,
    units,
    weekdays,
)
from app.services import outline as plan_rules
from app.services.context import (
    build_document_messages,
    instruction_part,
    pasted_material,
)

log = logging.getLogger(__name__)

#: Section-count bounds for the outline.
_MIN_SECTIONS = 3
_MAX_SECTIONS = 12


def requested_sections(request: str) -> int | None:
    """An explicit section count; page counts do not imply a number of sections."""
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
  value (「월 비용 × 12개월 = 연 비용」). Never invent a figure that is not on the list —
  user counts, ratios, causes of failure, damage, other costs, years; where one is needed,
  leave a blank naming it in brackets (「[금액]」 「[인원]」). Do not invent circumstances
  the request does not give, such as the cause or course of an incident.
- One register for the whole document. If earlier sections used 「~합니다」, so does this
  one; the first section uses 「~합니다」.
- Write at the level a practitioner can use as is: for each step what, how and why,
  criteria and examples, what to watch for. Unfold everything the material and request
  give; do not end on a one-line summary. Details come only from the material and request.
- **Never invent a specific value the material does not give** — amounts, dates,
  institutions, people, counterparties. Where a decision is needed, say what must be
  decided instead of filling it in: 「[예산 규모]」, not 「예산 2억 원」;
  「[협약 기업]」, not 「A社·B社」. Anything the request and material do not give is such a
  bracket blank (「[담당자]」 「[일자]」), never made up; a 미정 the material itself states
  stays as written (「미정(다음 회의에서 재논의)」).
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
- Do not leave a blank for a figure the stated assumptions let you compute (initial funding = monthly fixed cost × months to break-even, and so on): compute it and show the formula. Only an input nobody gave stays a bracket blank (「[월 고정비]」), and such inputs are listed once under 「확인 필요」.
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
  할 부분은 …」). A value the document needs and nobody gave is a bracket blank naming it
  (「[담당자]」 「[기한]」 「[금액]」), never made up. Do not write sentences that add no fact, such as 「기반을 마련했습니다」 or
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
  (「월 비용 × 12개월 = 연 비용」, 「초기 비용 ÷ 연 비용 ≈ 회수 기간(년)」). Never invent a
  figure that is not on the list — user counts, ratios, periods, other costs, residual
  values, years, working days; leave a bracket blank naming it (「[금액]」 「[인원]」). Do not invent
  circumstances the request does not give. When comparing a large and a small number,
  write both to check the direction.
- Facts from the source list get the source number at the end of the sentence, like [1].
  No numbers that are not on the list, no source labels like [설계도면] as citations
  (a blank like 「[담당자]」 is not one), and not the [1], [2] marks of material the user
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
- Do not leave a blank for a figure the stated assumptions let you compute (initial funding = monthly fixed cost × months to break-even, and so on): compute it and show the formula. Only an input nobody gave stays a bracket blank (「[월 고정비]」), and such inputs are listed once under 「확인 필요」.
- Do not copy these rules into the text.
- Every sentence ends in the 「~합니다」 / 「~입니다」 form, table cells and list items aside; never end a sentence in 「~다.」 (「확인했다.」, 「필요하다.」).
- Write the document in Korean.

Original request: {request}"""


_TABLE = re.compile(r"(?m)^\|.+\|\s*\n\|[\s:|-]+\|\s*\n(?:^\|.+\|\s*\n?)+")
_RESULTS_HEADING = re.compile(r"결과|측정|데이터|분석")


def _carry_table(request: str, headings: list[str], drafted: dict[str, str]) -> dict[str, str]:
    """Puts the request's own data table into the results section when the draft left it out."""
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
    """The draft cut into sections by its `## ` lines, matched loosely; unwritten headings are absent."""

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
            # A stray sub-heading is kept as bold text.
            if current is not None:
                found[current].append(f"**{m.group(1).strip()}**")
                continue
        if current is not None:
            found[current].append(line)
    return {h: "\n".join(lines).strip() for h, lines in found.items() if "\n".join(lines).strip()}


#: Block syntax per kind; a section is shown only the blocks it may use.
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


#: Writer rule for a bare form, when 있는 자료로 진행 answered a question the document needs.
_FRAME_RULE = (
    "**This document is a bare form written without material.** Invent no facts, "
    "discussions, decisions, names or figures the request does not give. In each section, "
    "say in one or two sentences what belongs there, and leave each place to fill as its "
    "name in brackets, like 「[결정된 사항과 근거]」. Tables get a header row and empty "
    "cells only. Do not add sections or tables the request did not ask for, such as cost "
    "calculations or alternative comparisons. Formulas and general principles are fine."
)

#: Opens a document written without a single web source, when one was looked for.
_UNVERIFIED_NOTE = (
    "_이 문서는 웹 검색에서 쓸 만한 자료를 얻지 못해 기억을 바탕으로 썼습니다. "
    "수치·연구명·서지는 확인이 필요합니다._"
)

#: A request that weighs options — the only kind the cost-table advice fits.
_DECISION = re.compile(r"대안|결정|권고|비용|타당성|선택")

#: Marks a 개조식 genre: `_shaped` drops the prose writer's paragraph rules for it.
_GAEJO_STYLE = "Style: 개조식."

#: Genre rules of the formats a 서식 fixes, by format id: each bound 서식 carries its own
#: (`genre.md` beside its `instructions.md`, `doc_format` in its `template.toml`). The four
#: 공문서 genres (개조식 보고서, 간이기안문, 추진 계획, 결과 보고서) open their 개조식 part with
#: `_GAEJO_STYLE`.
_FORMAT_GENRES: dict[str, str] = design_templates.format_genres()
#: The same request words `doc_formats` reads, so the head and the body rules agree.
_FORMAT_PATTERNS: dict[str, re.Pattern[str]] = {
    key: re.compile(doc_formats.BY_ID[key].match, re.I) for key in _FORMAT_GENRES
}

#: Genre shape rules, matched on the request and given to the outline and the draft, in the
#: order `app/genres.toml` lists them; an entry naming a format reads its 서식's rule.
def _load_genres() -> tuple[tuple[re.Pattern[str], str], ...]:
    import tomllib
    from pathlib import Path

    raw = tomllib.loads((Path(__file__).resolve().parent.parent / "genres.toml")
                        .read_text(encoding="utf-8"))
    out: list[tuple[re.Pattern[str], str]] = []
    for entry in raw.get("genres") or []:
        if fmt := str(entry.get("format") or ""):
            if fmt in _FORMAT_GENRES:
                out.append((_FORMAT_PATTERNS[fmt], _FORMAT_GENRES[fmt]))
            continue
        out.append((re.compile(str(entry["match"])), str(entry["rule"])))
    return tuple(out)


_GENRES: tuple[tuple[re.Pattern[str], str], ...] = _load_genres()


_OWN_GENRES = re.compile(
    r"주간|월간|업무 ?보고|진행 ?상황|현황 ?보고|회의록|녹취|장애(?!인)|사고|실험|측정"
)


def _own_material(request: str) -> bool:
    """Whether the request is about the person's own material and carries it (no web search).

    Judged on the instruction only; never true for a paper or a literature document."""
    instruction = instruction_part(request)
    if _SCHOLARLY_DOC.search(instruction):
        return False
    return bool(_OWN_GENRES.search(instruction)) and _carries_material(request)


#: A document whose related work is the literature: a paper draft, a survey.
_SCHOLARLY_DOC = re.compile(
    r"논문\s{0,3}초안|논문을\s{0,3}써|투고|학회\s{0,3}논문|워크숍\s{0,3}논문|관련\s{0,3}연구|"
    r"연구\s{0,3}동향|문헌|선행\s{0,3}연구|서베이|survey|literature|related work",
    re.I,
)


def scholarly_document(request: str) -> bool:
    """Whether the document's sources are papers: researched in the literature mode."""
    return bool(_SCHOLARLY_DOC.search(instruction_part(request)))


def _genre_rule(request: str, fmt: doc_formats.DocFormat | None = None,
                template_id: str | None = None) -> str:
    """The shape rule for the request's genre, or `""`; read from the instruction only.

    A format fixed by the 서식 (`doc_formats.detect` with a template id) names its own rule,
    so the head and the body agree; a person's template gives its own."""
    template = design_templates.get(template_id) if template_id else None
    if template is not None and template.user and template.genre:
        return template.genre
    if fmt is not None and fmt.id in _FORMAT_GENRES:
        return _FORMAT_GENRES[fmt.id]
    instruction = instruction_part(request)
    for pattern, rule in _GENRES:
        if pattern.search(instruction):
            return rule
    return ""


#: Prose-writer rules a 개조식 genre contradicts; `_shaped` drops the whole rule.
_PROSE_ONLY = (
    "Prose is the default",
    "A section is at least two",
    "Break into paragraphs",
    "One register for the whole document",
    "Every sentence ends in the",
)


def _shaped(prompt: str, genre: str) -> str:
    """`prompt` without the prose rules a 개조식 genre overrides; unchanged for any other."""
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
    """Bodies of a 개조식 format in canonical list Markdown with 공문서 dates and times."""
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


def _without_invented_money(text: str) -> str:
    """Every sum of money replaced by 「[금액]」, for a document with no figures to draw on."""
    return _MONEY.sub("[금액]", text)


_OWNER_HEADER = re.compile(r"담당|책임|owner", re.I)
_DUE_HEADER = re.compile(r"기한|마감|완료일|납기|due|deadline", re.I)
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}")
_MONTH_DAY = re.compile(r"(\d{1,2})\s*[/.월]\s*(\d{1,2})\s*일?")
_ISO_DAY = re.compile(r"\d{4}-(\d{2})-(\d{2})")
_BLANK_CELLS = {"", "미정", "(미정)", "-", "—", "tbd", "n/a", "없음"}


def _cell_sourced(
    cell: str, compact: str, row: str = "", dates: list[dict] | None = None
) -> bool:
    """Whether an owner or due-date cell names something the request or material names.

    A date matches by month and day in any of the usual spellings (9/10 · 9월 10일 ·
    2026-09-10); a person or team by any word of two characters or more. With `dates`
    (date-calc's readings, each bound to its source line) a date is kept when the line this
    row came from resolves to it — not because the same day appears somewhere else.
    """
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
            written = report_checks._cell_dates(cell, 0)
            return any(report_checks.date_matches(w, e) for w in written for e in entries)
        # A row that shares no word with any line: read as before.
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


def _unsourced_owner_dates(text: str, source: str, dates: list[dict] | None = None) -> str:
    """Owner (담당) and due-date (기한) cells the request and material never mention become a
    blank named by the column: 「[담당자]」, 「[기한]」.

    `dates`: date-calc's readings of the material, which bind a resolved deadline to its line."""
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
            guarded = [
                k for k, h in enumerate(header) if _OWNER_HEADER.search(h) or _DUE_HEADER.search(h)
            ]
            out.extend((line, lines[i + 1]))
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if guarded:
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    row = " ".join(c for k, c in enumerate(cells) if k not in guarded or
                                   _OWNER_HEADER.search(header[k] if k < len(header) else ""))
                    for k in guarded:
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


def _facts_line(request: str, sources: list[dict[str, Any]]) -> str:
    """The closed list of numbers the document may use, read off the request and the sources."""
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
            "(such as f_c = 1/(2πRC)) are fine."
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
        # Implied arithmetic is done here, so the writer copies finished figures.
        line += (
            "\n계산된 값 (do not recompute; use each once, in the section that needs it such as"
            " cost or comparison, and do not repeat it in other sections; table cells hold only the"
            " value, the formula goes once in the header or a sentence): "
            + "; ".join(derived)
        )
    return line


#: 「월 470만 원」 「월 약 690만 원」 — a monthly amount in 만 원.
_MONTHLY_WON = re.compile(r"월\s*(?:약\s*)?([\d,]+(?:\.\d+)?)\s*만\s*원")
_HORIZON_MONTHS = re.compile(r"(\d{1,3})\s*개월")
_HORIZON_YEARS = re.compile(r"(\d)\s*년\s*(?:TCO|총|치|간|동안|기준)")
_ANNUAL_BUDGET = re.compile(r"연\s*(?:간\s*)?예산\s*([\d,]+(?:\.\d+)?)\s*만\s*원")
_ADDED_MONTHLY = re.compile(r"추가\s{0,8}\(?\s{0,8}월\s{0,8}(?:약\s{0,8})?([\d,]{1,15}(?:\.\d{1,15})?)\s{0,8}만\s{0,8}원")


def _won(value: float) -> str:
    """A 만 원 figure the way a Korean reader writes it: 「5,640만 원」, 「1억 6,920만 원」."""
    whole = round(value)
    if abs(value - whole) > 1e-6:
        return f"{value:,.1f}만 원"
    if whole >= 10000:
        eok, man = divmod(whole, 10000)
        return f"{eok}억 {man:,}만 원" if man else f"{eok}억 원"
    return f"{whole:,}만 원"


def _label_before(text: str, end: int) -> str:
    """The words naming the amount: the clause in front of it, up to a delimiter."""
    head = text[max(0, end - 40):end]
    head = re.split(r"[,:;\n•·()]|\s(?:와|과|및)\s", head)[-1]
    return re.sub(r"\s{0,8}(?:은|는|이|가|의)?\s{0,8}월\s{0,8}(?:약\s{0,8})?$", "", head).strip(" -–—")[:24]


def derived_values(text: str) -> list[str]:
    """Finished arithmetic the request calls for, as 「식 = 값」 lines; `[]` when none.

    Monthly amounts are carried to the horizons the text names (「연」, 「36개월」, 「3년 TCO」),
    an added monthly cost is folded in, and yearly totals are set against the yearly budget."""
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
            continue  # the added cost is shown folded into the base below
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
    """What the other sections own, so this one does not write them."""
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


#: Placeholder for an empty source list; an empty block makes the model invent citations.
_NO_REFS = "(none. Do not use numbered citations.)"


#: Seconds between retries of a rate-limited call when the gateway names no reset time.
#: Five rounds, about seventy seconds in all: past a token-per-minute window.
_BACKOFF = (2.0, 6.0, 12.0, 20.0, 30.0)


async def title_block_for(
    fmt: doc_formats.DocFormat, request: str, title: str, model: str, api_key: str,
    material: str = "",
) -> tuple[dict[str, Any], dict[str, int]]:
    """The format's head fields: what code reads under a matching label in the request and
    material first, the model asked only for the rest; a value neither gives stays blank."""
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


#: The most a re-asked, cut-off answer is given.
_MOST_TOKENS = 16000


def _tiebreak_model(model: str) -> str:
    """The larger self-hosted model that settles two pre-pass readings that differ, on the
    turn's own route: a strict-local turn gets its `strict-local/` twin, or its own model."""
    big = (settings.code_fallback_model or "").strip()
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
    """One non-streaming call. Returns `(text, usage)`. Retries a 429."""
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
                        "max_tokens": max_tokens,
                        # Off where the provider allows; `thinking.switch` learns where not.
                        **thinking.switch(model),
                    },
                )
            except httpx.TimeoutException:
                # A busy model server times one call out; the next usually goes through.
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
                        "model": model, "messages": messages, "max_tokens": max_tokens,
                        **thinking.switch(model),
                    },
                )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            # A token-per-minute 429 names when its window resets; wait for that.
            delay = ratelimit.retry_delay(response.text, dict(response.headers), _BACKOFF[attempt])
            log.info("report call rate limited, retrying in %.0fs", delay)
            await asyncio.sleep(delay)
        response.raise_for_status()
        payload = response.json()

    # A reasoning model can spend the whole ceiling thinking; re-ask with a bigger one.
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
                # A gateway that rejects `reasoning` refuses the whole call.
                again = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": messages, "max_tokens": bigger},
                )
        if again.status_code == 200:
            retried = again.json()
            spent = retried.get("usage") or {}
            first = payload.get("usage") or {}
            # Both calls are billed, so both are counted.
            payload = retried
            payload["usage"] = {
                "prompt_tokens": int(first.get("prompt_tokens") or 0)
                + int(spent.get("prompt_tokens") or 0),
                "completion_tokens": int(first.get("completion_tokens") or 0)
                + int(spent.get("completion_tokens") or 0),
            }

    # An answer cut at the ceiling is asked once more with room.
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


#: Document look: asked for in Korean, stored as the English name the renderer knows.
_STYLES = {"편집형": "editorial", "포스터형": "poster", "미니멀": "minimal"}


def _outline_style(text: str) -> str:
    """The look the outline chose, or `""`. Read by regex so a partial answer keeps its style."""
    match = re.search(r'"style"\s*:\s*"([^"]+)"', text)
    return _STYLES.get((match.group(1).strip() if match else ""), "")


#: The planner's stated subject, checked against the request — see grounding.
_subject_missing = grounding.subject_missing


#: Documents about the person's own work that carry nothing without it.
_RESULTS = re.compile(r"결과|시험|실험|측정|캡스톤|제안서|설계 변경|기획서|연구 ?계획")
#: Documents whose facts all come from outside — nothing to write without a search.
_FROM_THE_WEB = re.compile(
    r"동향|조사해|문헌|선행 ?연구|최근 (?:\d+ ?년|연구)|연구를 정리|현황을 조사|비교표|인용|"
    r"참고문헌|시카고|APA|출처를"
)


def _from_the_web(request: str) -> bool:
    return bool(_FROM_THE_WEB.search(request))


#: Words that point at a thing the person has and did not attach.
_MATERIAL = re.compile(
    r"녹취|녹음|피드백|기록을|표가 있|파일|첨부|메모를|학위논문|논문 ?\d+ ?장"
    r"|(?:표|자료)를 (?:붙|드립|줍|보냅|첨부)"
)


_PAGES = re.compile(r"(?<!\d)(\d{1,4})\s{0,3}(?:장|쪽|페이지|p)\s{0,3}(?:이상|분량|짜리|내외|정도)")


def _long_form(request: str) -> bool:
    """Whether each section gets its own call, for detail a single draft would not give.

    True for eight or more pages, or substantial pasted material with five or more named parts."""
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
    """Two or more bare 「a | b | c」 lines with equal cell counts become a Markdown table.

    The first line is the head; lines already in a proper table are left alone."""
    lines = (body or "").split("\n")
    out: list[str] = []
    i = 0

    def cells(line: str) -> list[str] | None:
        text = line.strip()
        if not text or text.startswith(("|", "```", "#", ">", "!")) or " | " not in text:
            return None
        parts = [c.strip() for c in text.split("|")]
        return parts if len(parts) >= 2 and all(parts) else None

    fenced = False
    while i < len(lines):
        if lines[i].strip().startswith("```"):
            fenced = not fenced
        # Inside a fence (```kpi rows are 「value | label」) nothing is a table.
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
    """A head row and rule repeated inside a table start a second table."""
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
    """Unpaired 「```」 fences mended so one cannot swallow the rest of the section.

    A bare fence followed by ordinary sentences goes; failing that, the last fence is closed."""
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
    """A fence or a table row written onto the end of a sentence goes back to its own
    line: 「… 발전합니다 [4]. ```kpi」 and 「… 가능합니다 [5]. | 기준 | … |」."""
    body = re.sub(r"(?m)^([^\n`|]*\S)[ \t]+(```[A-Za-z]*)[ \t]*$", r"\1\n\n\2", body or "")
    body = re.sub(r"(?m)^(?!```)([^\n`]*[.!?。])[ \t]+```[ \t]*$", r"\1\n```", body)
    return re.sub(r"(?m)^([^\n|]*[.!?。\]])[ \t]+(\|[^\n]*\|)[ \t]*$", r"\1\n\n\2", body)


def _caption_text(line: str, kind: str) -> str | None:
    m = _CAPTION_LINE.match(line or "")
    if not m or m.group("kind").lower() not in (kind, {"그림": "figure", "표": "table"}[kind]):
        return None
    return m.group("text").strip(" *_.:")


#: A sentence that points into a source the reader does not have: 「매뉴얼 제2장 'X'은」,
#: 「보고서 3절에서」, 「p. 12」, 「해당 매뉴얼은」.
_SOURCE_INNARDS = re.compile(
    r"(?:매뉴얼|보고서|백서|가이드라인|가이드|안내서|지침|자료|문서)\s{0,2}(?:의\s{0,2})?"
    r"(?:제\s?\d{1,3}\s?(?:장|절|부|편)|\d{1,3}\s?(?:장|절)(?=[\s에은는의]))"
    r"|(?<![가-힣])제\s?\d{1,3}\s?장\s{0,2}['‘「“\"]"
    r"|(?<![가-힣A-Za-z])pp?\.\s?\d{1,4}"
    r"|(?:해당|이|그|동|위)\s(?:매뉴얼|백서|가이드라인|가이드|안내서|지침|자료|문서)(?:은|는|이|가|에서|에|의)"
)
# One `.` alternative (the cases are lookarounds) so no dot can be read two ways; a dot
# inside a dotted date or number run (「2026. 11. 16.(월)」, 「1. 2.」) ends a sentence only
# at the line's end.
_SENTENCE = re.compile(r"(?:[^.!?\n]|(?:(?<=\bp)|(?<=\bpp)|(?<=\d)(?=\.\d)|(?<=\d)(?=\.(?:\s{1,2}\d|\s{0,2}\())|(?:(?<=\d\.\s\d)|(?<=\d\.\s\d\d))(?!\.\s{0,2}[\d(])(?!\.\s*$))\.){0,2000}?(?<!\bp)(?<!\bpp)(?:(?!(?<=\d)\.\s{0,2}[\d(])(?<!\d\.\s\d)(?<!\d\.\s\d\d)[.!?]|(?:(?<=\d\.\s\d)|(?<=\d\.\s\d\d))\.(?=\s*$))(?:\s{0,2}\[\d{1,3}\](?:\[\d{1,3}\]){0,20})?(?=\s|$)")


def source_innard_sentences(sections: list[dict]) -> dict[int, list[str]]:
    """Per section index, the sentences that point into a source's own chapters or call
    it 「해당 자료」 without naming it. A reference list is not prose and is skipped."""
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





#: The citation a sentence ends with: 「… 있습니다 [2].」, 「… 있습니다[2][4].」, 「… 있습니다. [2]」.
_TRAILING_CITE = re.compile(r"\s{0,2}((?:\[\d{1,3}(?:[,，]\s?\d{1,3})*\]){1,6})(?=\s{0,2}[.!?。]?\s*$)")


def _cited(sentence: str) -> tuple[str, ...] | None:
    m = _TRAILING_CITE.search(sentence)
    if not m:
        return None
    return tuple(sorted(set(re.findall(r"\d{1,3}", m.group(1)))))


def collapse_repeated_citations(body: str) -> str:
    """Consecutive sentences of a paragraph citing the same sources carry the number once, on the last.

    Tables, lists, quotes, code and headings are left alone."""
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
            # A figure keeps its own source: the evidence check asks every number for one.
            if re.search(r"\d", bare):
                continue
            sentences[index] = bare
        parts[0::2] = sentences
        out.append("".join(parts))
    return "".join(out)


#: A figure the writer placed in its text: 「[[그림: flow | 처리 흐름 | 요청이 게이트웨이를 …]]」.
_FIGURE_MARK = re.compile(r"\[\[\s*그림\s*[:：]\s*(?P<body>[^\]\n]{3,900}?)\s*\]\]")
#: How the prose points at a figure or a table before it is numbered.
_FIGURE_REF, _TABLE_REF = "〔그림〕", "〔표〕"
#: The phrase around a reference whose figure could not be drawn, taken out with it.
_DANGLING_REF = re.compile(
    r"(?:위|아래|다음)?\s?〔(?:그림|표)〕(?:과|와|에서|에|은|는|을|를|의|처럼)?\s*"
    r"(?:같이|처럼|보듯이|보듯|나타낸 것처럼|정리한 것처럼|나타냈듯이)?\s*,?\s*"
)


def _figure_markdown(made: dict, png: bytes) -> str:
    """The figure as the body carries it: the picture drawn with the deck renderer's shapes,
    and its caption."""
    caption = str(made.get("caption") or "").strip() or "그림"
    return f"![{caption}]({diagram_render.data_uri(png)})\n\n*그림: {caption}*"


def figure_marks(body: str) -> list[dict[str, str]]:
    """The figures the writer placed: kind, caption, description, and the mark itself."""
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


def shown_while_writing(body: str) -> str:
    """The section as the panel shows it before its figures are drawn."""
    return _FIGURE_MARK.sub(
        lambda m: f"*〔그림 준비 중: {(m.group('body').split('|') + ['', ''])[1].strip() or '그림'}〕*",
        body or "",
    )


def drop_figure(body: str, mark: str) -> str:
    """A figure that could not be drawn leaves no mark and no 〔그림〕 pointing at nothing:
    the reference nearest before the mark goes with it."""
    at = body.find(mark)
    if at < 0:
        return body
    head, tail = body[:at], body[at + len(mark):]
    ref = head.rfind(_FIGURE_REF)
    if ref >= 0:
        match = _DANGLING_REF.match(head, max(0, ref - 2))
        if match and match.start() <= ref:
            head = head[: match.start()] + head[match.end():]
        else:
            head = head[:ref] + head[ref + len(_FIGURE_REF):]
    return re.sub(r"\n{3,}", "\n\n", head.rstrip() + "\n\n" + tail.lstrip())


#: Metrics a document states more than once; one kind may go by several names.
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
    """Groups of prose sentences giving one kind of metric different values in the same unit.

    Each group is [(section index, sentence), …]. Tables, the reference list and sentences
    giving two values (a range, a before and after) are skipped."""
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
                    # A rate is a percentage; a size is an amount.
                    mine = [v for v in values if (v[1] == "%") != (kind in ("size", "price"))]
                    if len(mine) == 1:
                        number, unit = mine[0]
                        found.setdefault((kind, unit), []).append(
                            (number.replace(",", ""), _scope(sentence), index, sentence)
                        )
                    break
    groups = []
    for entries in found.values():
        # Two values conflict only when one sentence's scope holds the other's.
        clash: dict[int, tuple[int, str]] = {}
        for i, (value_a, scope_a, index_a, sentence_a) in enumerate(entries):
            for value_b, scope_b, index_b, sentence_b in entries[i + 1:]:
                if value_a != value_b and (scope_a <= scope_b or scope_b <= scope_a):
                    clash[id(sentence_a)] = (index_a, sentence_a)
                    clash[id(sentence_b)] = (index_b, sentence_b)
        if clash:
            groups.append(list(clash.values())[:6])
    return groups


#: Words that name a measure, not what is measured.
_SCOPE_NOISE = frozenset({"연평균", "성장률", "CAGR", "시장", "규모", "점유율", "비중", "이익률",
                          "전환율", "약", "기준", "현재", "전망", "예상"})


def _scope(sentence: str) -> frozenset[str]:
    """What a sentence's figure is about: the words before its first topic or subject
    particle (「글로벌 AI 사이버 보안 시장은」 → {글로벌, AI, 사이버, 보안})."""
    head = re.split(r"(?<=[가-힣A-Za-z)])(?:은|는|이|가)\s", sentence, maxsplit=1)[0][:60]
    words = re.findall(r"[가-힣A-Za-z]+", head)
    return frozenset(w for w in words if w not in _SCOPE_NOISE)





#: What each quality finding is called in the step list.
#: Findings a model made and no rule can confirm: listed for the reader to check.
_TO_CHECK = ("fact", "question", "judged_figure", *report_checks.MODEL_LABELS)

_QUALITY_LABELS = {
    "unmentioned": "본문이 언급하지 않는 그림·표", "particle": "번호 뒤 조사",
    "figure_numbering": "그림 번호", "table_numbering": "표 번호",
    "table_without_caption": "캡션 없는 표", "fence_glued": "깨진 블록",
    "stray_caption": "떨어진 표 캡션", "pipe_rows_not_a_table": "표로 읽히지 않는 줄",
    "leftover_mark": "남은 표시", "mixed_script": "다른 문자 섞임", "literal_br": "<br> 노출",
    "placeholder": "자리표시", "source_innards": "출처 내부 지칭",
    "prompt_talk": "작성 지침 언급", "fence_language_apart": "블록 형식 줄 분리",
    "cut_table_row": "잘린 표 행",
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


#: A repair instruction's own voice: 「고쳐라」, 「맞춰라」, 「답하라」.
_INSTRUCTION = re.compile(r"(?:고쳐|맞춰|답하|써|바로잡|채우|빼|확인해|하)라(?![가-힣])|말라")


def reader_note(code: str, why: str) -> str:
    """What a finding says to the reader on the quality step: never the repair prompt."""
    if code == "judged_figure" or _INSTRUCTION.search(why or ""):
        return _QUALITY_LABELS.get(code, "확인 필요")
    return (why or "")[:200]


_AMOUNT = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(조|억|만)?\s?(원|명|박스|회|개|건|%)?")
_SCALE = {"조": 1e12, "억": 1e8, "만": 1e4, None: 1.0, "": 1.0}


def _amount(text: str) -> float | None:
    """「38.4억 원」 → 3.84e9, 「8만 원」 → 80000, 「400명」 → 400; None when not one number."""
    found = _AMOUNT.findall(text or "")
    if len(found) != 1:
        return None
    number, scale, _unit = found[0]
    return float(number.replace(",", "")) * _SCALE[scale or None]


def _won_like(value: float, like: str) -> str:
    """An amount written the way `like` was: 「3.84억 원/연」 for 「38.4억 원/연」."""
    tail = re.sub(r"^.*?(?:조|억|만)?\s?원", "", like, count=1) if "원" in like else ""
    if value >= 1e8:
        body = f"{value / 1e8:,.4f}".rstrip("0").rstrip(".") + "억 원"
    elif value >= 1e4:
        body = f"{value / 1e4:,.2f}".rstrip("0").rstrip(".") + "만 원"
    else:
        body = f"{value:,.0f}원"
    return body + tail


#: A Korean amount: 「4억 8,000만」, 「3,000만」, 「38.4억」, 「12,000」.
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
    """An amount a power of ten off a calculation the material already did gets the material's amount.

    The sentence must carry two or more of that line's amounts exactly."""
    lines = [
        {round(_ko_value(m.group(0)), 2) for m in _KO_AMOUNT.finditer(line)}
        for line in (given or "").split("\n")
    ]
    lines = [values for values in lines if len(values) >= 3]
    if not lines or not text:
        return text

    def mend(sentence: str) -> str:
        found = [(m.group(0), round(_ko_value(m.group(0)), 2)) for m in _KO_AMOUNT.finditer(sentence)]
        for raw, value in found:
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
                    sentence = sentence.replace(raw, _ko_amount(match), 1)
                    break
        return sentence

    return _SENTENCE.sub(lambda m: mend(m.group(0)), text)


def fix_table_formulas(body: str) -> str:
    """A table row's amount that is its shown product off by a power of ten is corrected.

    Any other mismatch is left for a person."""
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
        factors = [_amount(part) for part in formula.split("×")]
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
    # The wrong figure repeated elsewhere in the section (「연 약 38.4억 원」) goes with it.
    for old, new in corrected.items():
        text = re.sub(rf"(?<![\d.,]){re.escape(old)}", new, text)
    return text


_UNDECIDED = re.compile(r"^\s*(?:\(미정\)|미정|TBD|-|—|\[[가-힣][가-힣 ·]{0,9}\])\s*$")


def _table_rows(body: str):
    """(label, value cell, line index, cells) for each body row of each table in `body`."""
    lines = (body or "").split("\n")
    for i, line in enumerate(lines):
        if not line.strip().startswith("|") or re.match(r"^\s{0,16}\|?\s{0,16}:?-{2}", line):
            continue
        if i + 1 < len(lines) and re.match(r"^\s{0,16}\|?\s{0,16}:?-{2}", lines[i + 1]):
            continue  # the head row
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0]:
            yield re.sub(r"\s+", "", cells[0]), cells[1], i, cells


def fill_undecided_cells(sections: list[dict]) -> list[dict]:
    """An assumption that is 「[금액]」 or 「(미정)」 in one table and a figure in another takes
    the figure.

    Rows are matched by their first cell."""
    decided: dict[str, str] = {}
    unit = re.compile(r"\d.{0,300}(?:원|만|억|조|%|명|박스|회|개월|건)")
    for sec in sections:
        for label, value, _, _ in _table_rows(str(sec.get("content") or "")):
            if _UNDECIDED.match(value) or not re.search(r"\d", value):
                continue
            # A figure with its unit beats a bare one from a table that split the unit off.
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
    """A table restating an earlier one (most rows, same labels and figures) goes with its caption."""
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


#: A comparison cell the writer could not fill from the first search.
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
#: A business's own assumption (cost, price, churn, budget): never filled from the web.
_OWN_ASSUMPTION = re.compile(
    r"원가|비용|가격|판매가|단가|이탈률|해지율|유지율|전환율|CAC|LTV|매출|이익|예산|고정비|변동비|"
    r"마진|공헌|성장률|점유율|규모|목표|구독료|객단가"
)


def table_gaps(sections: list[dict]) -> list[tuple[int, int, int, str, str]]:
    """`(section, line, column, row label, column header)` for each comparison cell left
    open (「(자료에 배포 방식 명시 없음)」, 「—」), at most one per row."""
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
    """Each open comparison cell is searched for on its own and filled from what the pages say.

    New pages join the sources; a cell the pages do not settle stays as written."""
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
        # The same thing measured the same way, its words on the page, its numbers in them.
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
        # The pages' own numbers become the document's next ones.
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
    """`(section, sentence, what is wrong)` for sentence-sized gate findings no mend settles."""
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
                    found.append((index, m.group(0).strip(), "자리표시(「(미정)」, 「(여기에 …)」 "
                                  "등)가 남아 있다. 자료에 있는 값으로 채우고, 없으면 그 자리 "
                                  "이름을 「[담당자]」 「[금액]」처럼 대괄호로 남겨라."))
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
    # A wrong formula first: it misleads; a settled figure changed next; a placeholder only shows.
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
    """Every number the sentence writes is one the material states (「약 1,200쪽」)."""
    written = report_checks._material_numbers(sentence)
    return bool(written) and all(report_checks._grounded(n, known) for n in written)


async def judge_figure_sentences(
    sections: list[dict], settled: list, *, judge: str, api_key: str, material: str = ""
) -> tuple[list[tuple[int, str, str]], dict]:
    """Settled-figure sentences the code passed, put to the judge model as yes/no questions.

    A sentence whose every number the material states is not asked: it copies the
    material, which the settled figures come from. Returns the ones the judge calls
    wrong, in `flagged_sentences`' shape, and the usage."""
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
    """A 「CAC(천 원)」-style header over amounts the prose gives in won is relabelled in won."""
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
    """A carried question that lost any of its choices gets them back as the material wrote them."""
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
    """Every deterministic mend of a written report, in one fixed order.

    Idempotent, so the verify stage can run it again after a repair."""
    out = []
    for sec in sections:
        mended, slips = hangul.repair_mixed_script(str(sec.get("content") or ""), context[:20000])
        if slips:
            log.info("mended mixed-script words in %r: %s", logs.safe(sec.get("heading")), logs.safe(slips[:5]))
        out.append({**sec, "content": mended})
    out = relabel_unit_columns(out)
    try:
        out = restore_choices(out, context)
    except Exception:  # noqa: BLE001 — the questions stay as written
        log.exception("restoring choices failed")
    try:
        out = fix_pnl_tables(drop_repeated_tables(fill_undecided_cells(final_tidy(out, settled))))
    except Exception:  # noqa: BLE001 — a mend that fails leaves the text as it was
        log.exception("final tidy failed; the sections are kept as written")
    out = caption_figures_and_tables(out)
    # Weekdays beside dates are the calendar's, whatever the writer counted.
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
    """「3,200만」 as a number; None for what is not one amount (a range read as
    「3만 4만」), so a check skips it instead of failing the report."""
    try:
        if "천만" in written:
            return units._ko_number(written.replace("천만", "")) * 1e7
        return units._ko_number(written)
    except ValueError:
        return None


def numeric_issues(sections: list[dict]) -> list[tuple[int, str, str]]:
    """Sentences whose own figures disagree (a weekly rate and its yearly total, a CAGR).

    Each is (section index, sentence, what is wrong, phrased for a rewrite)."""
    found: list[tuple[int, str, str]] = []
    for index, sec in enumerate(sections):
        if _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            continue
        for line in str(sec.get("content") or "").split("\n"):
            if line.lstrip().startswith(("!", "```", "#")):
                continue
            # A table row has no full stop: the row is read as one sentence.
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
                        # Beyond 300% a year the two amounts are not one market's start and
                        # end (a sentence comparing two different quantities).
                        if abs(implied - stated) > 1.5 and -50 < implied < 300:
                            found.append((index, sentence, f"{start:,.0f}에서 {end:,.0f}로 "
                                          f"{span}년이면 연평균 {implied:.1f}%다(적힌 {stated}%와 맞지 "
                                          "않음) — 출처를 다시 확인해 맞는 값만 남겨라"))
    return found





def _named_amount(text: str, names: str) -> float | None:
    """The one amount the document gives under `names`; None when absent or not settled."""
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
    """A monthly P&L table recomputed from the document's price, unit cost and fixed cost.

    Cumulative (「누적」) columns are left; nothing changes unless all three inputs are stated."""
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
            # The inputs must be this table's: some row already agrees with them exactly.
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


#: A sentence about the writing instructions, not the subject: 「쓸 수 있는 수치 목록에 따르면」,
#: 「… 절이 아직 작성되지 않았습니다」. The reader never saw the instructions.
#: The writer's own check of a settled block (「85,000,000원 × 0.10 = 8,500,000원이 견적 확정 블록의
#: 7,727,273원과 다릅니다」, client-proposal-3) is talk about the instructions too.
_PROMPT_TALK = re.compile(
    r"쓸 수 있는 수치|수치 목록|요청 자료(?:에|의|로)|작성 지침|아직 (?:작성|쓰이)지 않았|개요(?:에|의) 따르면"
    r"|(?:확정|계산)\s{0,2}블록|확정 날짜 블록"
    r"|[×x*]\s{0,2}[\d.,]{1,12}\s{0,2}=\s{0,2}[\d,]{1,20}\s{0,2}원[^.\n]{0,80}?(?:과|와)\s{0,2}"
    r"(?:다릅니다|다르다|달라|일치하지\s{0,2}않)"
)
#: A fence whose language sits on the next line: 「```」 then 「callout」.
_LONE_LANGUAGE = re.compile(r"(?m)^```[ \t]*\n(callout|kpi|cards|diagram|mermaid|chart)[ \t]*$")
_TABLE_CAPTION_LINE = re.compile(r"^\s*(?:\*\*)?표\s?(?:\d{1,3})?[.:]")


def _cells(line: str) -> list[str]:
    return line.strip().strip("|").split("|")


def drop_cut_rows(body: str) -> str:
    """A table row cut off mid-way (fewer cells than the header, no closing 「|」) is the
    end of a reply that ran out, and goes; a table left with no rows goes with its caption."""
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
    """Sentences about the writing instructions are taken out; a paragraph left empty goes."""
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


#: Fences whose body the renderers draw as blocks; any other fence is printed as text.
_DRAWN_FENCES = ("mermaid", "callout", "kpi", "cards", "diagram", "chart")


def unfence_pictures(body: str) -> str:
    """A plain code fence holding a picture is unwrapped, or the PDF prints its base64."""
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
            # A drawn block is copied whole, its closing fence with it: that fence is not
            # the opening of a plain one.
            out.extend(lines[i:j + 1])
            i = j + 1
            continue
        inner = lines[i + 1:j]
        if any(_FIGURE_LINE.match(x.strip()) or "](data:image/" in x for x in inner):
            out.extend(inner)
        else:
            out.extend(lines[i:j + 1])
        i = j + 1
    return "\n".join(out)


#: A fenced JSON object in a section body.
_JSON_PROSE_FENCE = re.compile(r"```json[ \t]{0,8}\n(\{[\s\S]{0,200000}?\})\s{0,8}\n```")


#: One KPI row: a short value, a bar, a short name (「120만 원 | 안2 연간 비용」).
_KPI_ROW = re.compile(r"^\s{0,3}(?![|`#>*-])[^|\n`]{1,24}?\s\|\s[^|\n`]{1,48}$")
#: A lone backtick or two where a ```kpi fence was meant (「`」, 「``kpi」).
_KPI_EDGE = re.compile(r"^\s{0,3}`{1,2}\s{0,2}(?:kpi)?\s{0,2}$", re.I)


def mend_kpi_fences(body: str) -> str:
    """A KPI block written with a lone backtick (or none) around its 「값 | 이름」 rows becomes a
    ```kpi fence, so it renders as KPI tiles and never as stray 「`」 and 「a | b」 lines."""
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
        # Fenced by stray backticks, or two to four bare rows that start with a figure.
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


#: Columns that say nothing when every cell is empty; a 확인·서명 column left for a pen stays.
_DROPPABLE_COLUMN = re.compile(r"기한|마감|완료\s{0,2}(?:예정)?일|일정|비고")


def drop_empty_columns(body: str) -> str:
    """A 기한·비고 column empty in every row (회의록의 「기한」 left blank throughout) goes, and its
    name leaves a 「표 n. 항목·내용·기한」 caption next to the table."""
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
    """The deterministic mends run again after the rewriting passes, which can undo them."""
    tidied = []
    for sec in sections:
        body = str(sec.get("content") or "")
        # Each ```json block holding a section's prose is that prose.
        body = _JSON_PROSE_FENCE.sub(
            lambda m: unwrap_json_prose(m.group(1)) if unwrap_json_prose(m.group(1)) != m.group(1)
            else m.group(0), body)
        if body.lstrip().startswith("{"):
            # A body the draft handed back wrapped as JSON, closed or cut off.
            body = re.sub(r'\n?\s{0,200}"\s{0,200}\}\s{0,200}\}?\s{0,200}$', "", unwrap_json_prose(body).replace(
                '\\n', "\n"))
        if not _REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            body = unfence_pictures(_LONE_LANGUAGE.sub(r"```\1", mend_kpi_fences(body)))
            body = drop_empty_columns(body)
            # 「<br>」 written for a line break: a cell keeps its parts with 「 · 」.
            body = "\n".join(
                re.sub(r"\s{0,32}<\s{0,8}br\s{0,8}/?\s{0,8}>\s{0,32}", " · " if line.lstrip().startswith("|") else " ",
                       line, flags=re.I)
                for line in body.split("\n")
            )
            # 「[[5]]」 and 「[]」 left by a citation pass: one number, or nothing.
            body = re.sub(r"\[\[(\d{1,3})\]\](?!\()", r"[\1]", body)
            body = re.sub(r"\s?\[\](?!\()", "", body)
            body = internal_words.scrub(drop_prompt_talk(drop_cut_rows(body)))
            # A settled figure slipped inside a formula is set back first, then every
            # product and sum is worked out again.
            body = key_figures.mend_operands(body, settled or [])
            body = fix_table_formulas(units.fix_written_sums(units.fix_written_arithmetic(body)))
        tidied.append({**sec, "content": body})
    return tidied


def _renumber_references(sections: list[dict]) -> tuple[list[dict], set[str]]:
    """Numbered captions renumbered 1, 2, 3 … in document order, the text's references following.

    Returns the sections and the names the text already points at (「표 5」)."""
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


def caption_figures_and_tables(sections: list[dict]) -> list[dict]:
    """Every figure captioned below as 「그림 N. 제목」 (its alt text), every table above as 「표 N. 제목」.

    Numbered in document order; a writer's caption is renumbered, a missing table caption
    is named from the header row."""
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
        # 「−」 has no glyph in the exported PDF's face (it prints as □); 「[]」 is a citation
        # with nothing in it; a line that is only 「표 4」 is a caption that lost its title.
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
                # A caption line under the picture (blank lines between) is folded into it.
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
                # A drawn-in-browser figure: its caption is the line after the fence.
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
                # The caption above: the writer's own line (blank lines between), renumbered.
                k = len(result) - 1
                while k >= 0 and not result[k].strip():
                    k -= 1
                given = _caption_text(result[k], "표") if k >= 0 else None
                if given is not None:
                    del result[k:]
                elif k >= 0 and (tail := _TRAILING_TABLE_CAPTION.search(result[k])):
                    # 「… 필요합니다 [5]. 표: 배포 모델 비교」: the caption written onto the
                    # paragraph's last line is lifted off it.
                    given = tail.group("text").strip(" *_.:")
                    result[k] = result[k][: tail.start()].rstrip()
                header = [c.strip(" *") for c in line.strip().strip("|").split("|") if c.strip()]
                end = i
                while end < len(lines) and lines[end].strip().startswith("|"):
                    end += 1
                if given is None:
                    # 「표: …」 written under the table instead: it moves above, said once.
                    k = end
                    while k < len(lines) and not lines[k].strip():
                        k += 1
                    if k < len(lines) and (below := _caption_text(lines[k], "표")) is not None:
                        given = below
                        lines[k] = ""
                title = given or "·".join(header[:3]) or "표"
                # A table shaped like the one before it (a lesson per table) takes that
                # table's caption with its number moved on: 「1차시 …」 → 「2차시 …」.
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
                result.extend(["", f"**표 {table_no}. {title}**", ""])
                result.extend(lines[i:end])
                i = end
                continue
            result.append(line)
            i += 1
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(result)).strip("\n")
        # A reference with no figure or table to point at is taken out with its phrase,
        # leaving the space between the words it stood between.
        if _FIGURE_REF in text or _TABLE_REF in text:
            text = re.sub(r"[ \t]{2,}", " ", _DANGLING_REF.sub(" ", text))
            text = re.sub(r" ([.,)])", r"\1", text)
        text = fix_number_particles(_FIGURE_MARK.sub("", text))
        out.append({**sec, "content": text})
    return out


def _resolve_reference(result: list[str], lines: list[str], at: int, ref: str, name: str) -> bool:
    """The 〔그림〕/〔표〕 nearest before the figure or table becomes its number; failing
    that, the first one after it in the section. Whether the text pointed at it."""
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
    """A figure or table the text never mentions is pointed at the way a report does it:
    「…로 구성됩니다(그림 2).」 at the end of the nearest prose paragraph before it. A table,
    caption or picture in between is stepped over; a heading ends the search."""
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
            # 「…(표 2).」 already there: one bracket, 「(표 2, 그림 3)」.
            if (prior := re.search(r"\(((?:그림|표) \d{1,3}(?:, (?:그림|표) \d{1,3})*)\)$", head)):
                if name in prior.group(1).split(", "):
                    return  # pointed at already: a second pass adds nothing
                head = head[: prior.start()] + f"({prior.group(1)}, {name})"
            else:
                head = head + f"({name})"
            result[k] = head + (m.group(1) or "") + m.group(2)
        return


#: Korean reads the last digit of a number; the particle after it follows that sound.
_DIGIT_CODA = {"0": "ㅇ", "1": "ㄹ", "2": "", "3": "ㅁ", "4": "", "5": "", "6": "ㄱ",
               "7": "ㄹ", "8": "ㄹ", "9": ""}
_PARTICLES = {"은": ("은", "는"), "는": ("은", "는"), "이": ("이", "가"), "가": ("이", "가"),
              "을": ("을", "를"), "를": ("을", "를"), "과": ("과", "와"), "와": ("과", "와"),
              "으로": ("으로", "로"), "로": ("으로", "로")}


def fix_number_particles(text: str) -> str:
    """「표 1는」 → 「표 1은」, 「그림 2과」 → 「그림 2와」: the particle after a figure or
    table number agrees with how the number is read."""
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


def split_long_paragraphs(body: str, limit: int = 400) -> str:
    """A prose paragraph over `limit` characters is cut at sentence ends into paragraphs
    of two or three sentences (about 180–330 characters). Tables, lists, quotes, code
    and headings are left as they are; a sentence is never split."""
    out: list[str] = []
    fenced = False
    # A table written right under its paragraph, with no blank line, is its own block.
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
        chunk: list[str] = []
        size = 0
        for sentence in sentences:
            # Close the paragraph before a sentence that would carry it past the limit.
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
    """「## 분류 기준 확립」 inside a section becomes 「### …」 (and 「#」 too), so a
    section's parts do not read as sections of the document. Fenced code is left alone."""
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
    """The section's text minus a repeat of its own heading on the first line."""
    lines = body.lstrip().split("\n", 1)
    first = re.sub(r"^#{1,6}\s*|\*+", "", lines[0]).strip()
    # A lone bold line at the top with no sentence in it is a heading whatever it says
    # (「**위험과 대응**」 over a section called 「위험」); the body starts below it.
    if (
        re.fullmatch(r"\*\*[^*\n]{1,40}\*\*\s*", lines[0])
        and not re.search(r"[.!?。]", lines[0])
        and len(lines) > 1
    ):
        return lines[1].lstrip()
    if first != heading.strip() and (
        re.match(r"^#{1,6}\s+", lines[0]) or re.fullmatch(r"\*\*.+\*\*\s*", lines[0])
    ):
        # Numbered headings may repeat the outline; plain numbered lists remain body text.
        first = re.sub(r"^\d+[.)]\s+", "", first)
    if first and first == heading.strip():
        return lines[1].lstrip() if len(lines) > 1 else ""
    # A plain title-like first line (short, no sentence mark, a blank line after it) that
    # contains the heading, or is contained in it, is the heading under a new name.
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
    """Whether the request carries its own material: long enough, or with a table in it."""
    return len(request) >= 300 or "|" in request


def _results_without_data(request: str, attached: list[str]) -> bool:
    """Whether the request names or needs material (녹취, 표, 파일, results) that nothing attached carries."""
    if any(block.strip() for block in attached) or _carries_material(request):
        return False
    if _MATERIAL.search(request):
        return True
    if not _RESULTS.search(request):
        return False
    return not deck_rules.has_numbers(request, [])


def _fold_alternatives(headings: list[str], alternatives: list[str]) -> list[str]:
    """Drops sections named after one alternative and keeps a single 대안 비교 section."""
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
        # After the calculation section if there is one, else after the situation.
        at = next((i + 1 for i, h in enumerate(keep) if "계산" in h or "전제" in h), None)
        keep.insert(min(len(keep), 2) if at is None else at, "대안 비교")
    return keep


_FOLLOW_OUTLINE = re.compile(
    r"개요\s{0,3}(?:로|대로|에\s{0,3}따라|를\s{0,3}따라)|이\s{0,3}(?:구성|목차)\s{0,3}(?:으로|로|대로)|"
    r"아래\s{0,3}(?:구성|목차)\s{0,3}(?:으로|로|대로)"
)
#: A line of a pasted outline that names a section: 서론·본론·결론, 「1.」 「Ⅱ.」 「제2장」.
_OUTLINE_LINE = re.compile(
    r"^\s{0,3}(?:#{1,4}\s{1,3}|\*\*)?\s{0,2}((?:서론|본론|결론|맺음말|들어가며|나가며)[^\n*]{0,70}|"
    r"(?:[1-9][0-9]?|[IVX]{1,4}|[ⅠⅡⅢⅣⅤⅥⅦⅧ])[.)]\s{0,2}[^\n*]{2,70}|제\s{0,2}\d{1,2}\s{0,2}[장절][^\n*]{0,60})"
    r"\s{0,2}(?:\*\*)?\s{0,3}$"
)


def given_outline(request: str) -> list[str]:
    """The sections of an outline the person pasted and asked the document to follow;
    `[]` unless the instruction says to follow it and the material has three or more
    section lines."""
    if not _FOLLOW_OUTLINE.search(instruction_part(request)):
        return []
    material = request[len(instruction_part(request)) :]
    # Several pasted answers each bring their own headings: a run ends where a numbered
    # list starts over (「1.」 after 「3.」) or a new pasted block begins (「[2]」).
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
    # The outline is the run that has the parts the instruction names (서론·결론, 본론 N절);
    # failing that, the last one — the latest answer is the outline that was agreed.
    parts = re.findall(r"서론|본론|결론|맺음말|들어가며|나가며", instruction_part(request))
    for run in reversed(runs):
        if parts and any(part in heading for part in parts for heading in run):
            return run
    return runs[-1]


#: 「구성은 A, B, C」 「끝에 참고문헌」 — sections the instruction names.
_LISTED = re.compile(r"구성(?:은|:|：)\s{0,3}([^.。\n]{6,300})")


def without_instruction_aside(heading: str, request: str) -> str:
    """「결과(측정 표와 계산한 이득·이론값·오차)」 → 「결과」: a parenthetical the planner
    copied from the instruction is what the section should hold, not its name."""
    m = re.fullmatch(r"\s{0,3}(.{2,40}?)\s{0,3}[(（]([^()（）]{6,80})[)）]\s{0,3}", heading or "")
    if m and m.group(2).strip() in instruction_part(request):
        return m.group(1).strip()
    return heading


def with_listed_sections(headings: list[str], request: str) -> list[str]:
    """The planned headings plus any section the instruction names and the plan lacks.

    참고문헌 goes last, others before it."""
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
        # After the nearest earlier named section the plan has; first when there is none.
        anchor = next(
            (pos for prev in reversed(named[:index]) if (pos := where(prev, out)) is not None),
            None,
        )
        out.insert(0 if anchor is None else anchor + 1, name)
    return out


def _heading_of(item: Any) -> str:
    """An outline item as a heading: a string, or an object's heading/title. A question
    the model slipped into the list (「{'question': …, 'options': …}」) is not a section."""
    if isinstance(item, dict):
        item = item.get("heading") or item.get("title") or ""
    elif not isinstance(item, (str, int, float)):
        return ""
    text = str(item).strip()
    if text.startswith("{"):
        return ""
    # 「검토 목적 (review purpose)」: an English gloss on a Korean heading is not part of it.
    if re.search(r"[가-힣]", text):
        text = re.sub(r"\s{0,3}[(（][A-Za-z][A-Za-z0-9 ,:;&/'.-]{1,80}[)）]\s{0,3}$", "", text).strip()
    return text


def _parse_outline(text: str) -> tuple[str, list[str]]:
    """`(title, headings)` from whatever the model wrapped its JSON in; the title may be empty."""
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
    # A reply cut off at the token cap: the `sections` array's strings so far still count.
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
    # A reply that ignored the format usually still holds a list, often under notes about
    # the format (「**Sections**:」, 「구조:」). Only the list under the last header counts.
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
        # 「대상 환경 분석 (어떤 환경에 적용되는지)」: the gloss in brackets is not the title.
        line = re.sub(r"\s{0,3}[(（][^()（）]{1,40}[)）]\s*$", "", line).strip()
        if line and len(line) <= 40:
            headings.append(line)
    return title, headings[:_MAX_SECTIONS]


#: The header a model writes above the list it means as the outline.
_OUTLINE_LIST_HEADER = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])?\s*\**\s*(?:sections?|구조|목차|섹션|outline)\s*\**\s*[:：]\s*\**\s*$", re.I
)
#: A line about the outline rather than a part of it.
_OUTLINE_NOTE = re.compile(
    r"\*\*[^*]{1,30}\*\*\s*[:：]|[:：]\s*$|^(?:title|subject|style|alternatives|제목|주제|스타일|"
    r"요청된 항목|요청 항목|대안)\s*[:：]",
    re.I,
)


def outline_is_json(text: str) -> bool:
    """Whether the outline came as the JSON object asked for, not as a list in prose."""
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
    # `.get` throughout: a hand-typed source carries only a title and a url.
    return "\n".join(
        f"[{s.get('ordinal', i + 1)}] {s.get('title') or s.get('url') or ''}"
        f" ({s.get('publisher') or ''})\n{s.get('quote') or ''}"
        for i, s in enumerate(sources)
    )


async def _draw(figure: dict, image_model: dict | None, api_key: str) -> dict | None:
    """One picture as a stored figure, or `None` when it could not be drawn.

    Embedded as a `data:` URI so exported and mailed copies keep it. Never raises.
    """
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
        # `encode` returns the whole `data:` URI.
        "src": pictures.encode(made.mime, made.data),
        "caption": str(figure.get("caption") or ""),
        "width": made.width,
        "height": made.height,
        # Popped by the caller into the turn's usage.
        "_in": made.input_tokens,
        "_out": made.output_tokens,
    }


#: The two blocks that are nothing but figures, drawn large.
_FIGURE_FENCE = re.compile(r"^```(?:kpi|chart)\b.*?^```\s*$", re.S | re.M)


def _grounded_figures(text: str, grounded: bool) -> str:
    """Figure blocks removed from a section with no material to draw them from; the prose stays."""
    if not grounded:
        return _FIGURE_FENCE.sub("", text).strip()
    return re.sub(r"\n{3,}", "\n\n", _KPI_FENCE.sub(_kpi_or_nothing, text)).strip()


_KPI_FENCE = re.compile(r"^```kpi\b.*?^```\s*$", re.S | re.M)
_UNDETERMINED = re.compile(r"미정|확인 필요|해당 없음|N/A|TBD|\?|\[[가-힣]", re.I)


def _kpi_or_nothing(match: re.Match[str]) -> str:
    """A kpi block removed whole when any of its values is a placeholder rather than a number."""
    body = match.group(0).strip("`").split("\n", 1)[1] if "\n" in match.group(0) else ""
    lines = [
        line for line in body.split("\n") if line.strip() and not line.strip().startswith("```")
    ]
    if any(
        _UNDETERMINED.search(line.split("|")[0]) or not re.search(r"\d", line.split("|")[0])
        for line in lines
    ):
        return ""
    return match.group(0)


def _pasted_without_marker(request: str) -> str:
    """Material pasted after the instruction's first paragraph though no 「---」 or long
    line marks it (120 characters or more); else `""`. Read for its facts only."""
    head, _, rest = (request or "").strip().partition("\n\n")
    rest = rest.strip()
    return rest if head and len(rest) >= 120 else ""


def _request_material(request: str) -> str:
    """What the request itself gives as material, attachments or not: the pasted block,
    a memo after the first paragraph, or else the request's own price lines.

    A one-line 「[견적 메모]」 under an attached RFP was neither pasted (no 「---」, under
    400 characters) nor read, and the writer made the MM up (client-proposal-3)."""
    if pasted := pasted_material(request):
        return pasted
    if memo := _pasted_without_marker(request):
        return f"# 요청에 붙여 넣은 자료\n{memo}"
    lines = [line.strip() for line in (request or "").splitlines()
             if report_checks.price_line(line)]
    return "# 요청에 적은 자료\n" + "\n".join(lines) if lines else ""


def _source_text(row: dict[str, Any]) -> str:
    """A search source's words, so a value it gives traces to it."""
    return " ".join(str(row.get(k) or "") for k in ("title", "snippet", "quote", "content", "text", "excerpt"))


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    #: Model that plans, when an administrator named one; empty plans with `model`.
    outline_model: str = "",
    #: The approved 목차. Absent: plan, emit `proposal` (or `needs`) and stop.
    #: Present: write exactly it.
    approved_plan: dict[str, Any] | None = None,
    #: Whether this pass may stop to ask. False on the pass after 있는 자료로
    #: 진행, or the same question loops.
    may_ask: bool = True,
    #: Approved pictures to draw. `None` on the planning pass; `[]` means 그림 없이.
    figures_plan: list[dict] | None = None,
    #: Model that draws them. Empty disables the figure proposal.
    image_model: dict | None = None,
    #: Whether to research through `services.research` before planning.
    web_search: bool = True,
    project_sources: list[dict[str, Any]] | None = None,
    #: The session's 서식; one in `doc_formats.TEMPLATE_FORMATS` fixes the format and genre.
    template_id: str | None = None,
    #: The agent's bound `doc_formats` id; used when the 서식 fixes none.
    format_id: str | None = None,
    #: The session's design tokens; the page count lays the body out in its face.
    design_tokens: dict[str, str] | None = None,
    #: Check skills the agent and the 서식 attach (`check_skills.attached`); None reads the
    #: 서식's own.
    #: The agent's own 진행 과정 설정 (`Agent.process_settings`), over its 서식's.
    process: dict[str, Any] | None = None,
    check_keys: tuple[str, ...] | None = None,
    #: The agent's `check_options`: how strict the trace is.
    check_options: dict[str, Any] | None = None,
    #: What the server runs for a person's check skill among `check_keys` (`skill_specs`).
    skill_specs: dict[str, Any] | None = None,
    #: Receives one record per stage (`StepRecord.as_dict`), run or not.
    trace: list[dict[str, Any]] | None = None,
    #: The router's settlement of the turn (`Request.turn`): given, `report-save` stores the
    #: written document; absent, the caller does.
    turn: dict[str, Any] | None = None,
    session_id: str = "",
    #: Keeps the bundle after each stage, so the run can be rerun from one later.
    recorder: Any = None,
    #: Receives the work bundle once built, for the caller that stores the run.
    bundle_out: list | None = None,
    #: A composed run: what earlier steps wrote (read, never a source of figures) and what
    #: their code printed (a source).
    derived_context: list[str] | None = None,
    tool_outputs: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `section` and one final `usage` event.

    The caller owns persistence, billing and the artifact — this only writes. The work is
    the format's pipeline (`app/pipelines/*.toml`): its `plan` stages when no outline is
    approved yet, its `write` stages on the approved one; `trace`, when given, receives
    each stage's record.
    """
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
    #: The document's format (`titleBlock.format`); its genre rules hold for the rewrite too.
    doc_format: str = "",
) -> tuple[str, dict]:
    """Rewrites one section with the rest of the document as context.

    `material` is the request's own data — attached files, pasted tables — carried
    again so the rewrite draws its numbers from where the original did.
    """
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
        # The shelf keeps the document's citation numbers valid.
        refs=_refs_block(sources or []),
        request=request[:1500],
        role=role,
        blocks=blocks,
        others=_others_line([str(x.get("heading") or "") for x in sections], position),
        # Numbers already in the document, and in the material it was written from, are allowed too.
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
        # Last and labelled, or it reads as part of the original request.
        prompt += (
            f"\n\nWhy this section is being rewritten (must be applied):\n{note.strip()[:600]}\n"
            "If the reason names a form (three numbered items, one table, three sentences), write "
            "exactly that form. Add no tables or blocks the reason does not ask for, and do not "
            "redraw a table another section already has."
        )
    body, spent = await _complete(
        model,
        build_document_messages(
            SessionKind.report, prompt, request=request, untrusted_context=material
        ),
        api_key,
        1200,
    )
    # The same normalisation `write` applies.
    body = hangul.tidy_spacing(hangul.read_back(_without_own_heading(body, heading))[0])
    target = next((s for s in sections if s.get("id") == target_id), {})
    others = [str(s.get("content") or "") for s in sections if s.get("id") != target_id]
    body = _without_borrowed_tables(body, target.get("content") or "", others, note)
    if fmt is not None and fmt.lists == "gaejo":
        body = doc_formats.gaejo_markdown(doc_formats.official_notation(body))
    return body, spent


def _without_borrowed_tables(body: str, before: str, others: list[str], note: str) -> str:
    """The rewrite's tables, minus any another section draws or the section never had and the note did not ask for."""
    if not _TABLE.search(body):
        return body
    elsewhere = {_table_key(m.group(0)) for text in others for m in _TABLE.finditer(text)}
    had_table = bool(_TABLE.search(before)) or bool(re.search(r"표", note))

    def keep_or_drop(m: re.Match[str]) -> str:
        if _table_key(m.group(0)) in elsewhere or not had_table:
            return ""
        return m.group(0)

    return re.sub(r"\n{3,}", "\n\n", _TABLE.sub(keep_or_drop, body)).strip()


def _table_key(table: str) -> str:
    """A table's header row, spacing and alignment marks removed."""
    head = table.strip().split("\n", 1)[0]
    return re.sub(r"[\s:|-]+", "", head)


_PRODUCT = re.compile(
    r"(?P<a>\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?P<ua>만|억|천)?\s{0,8}(?P<unit>원|명|건|개|대|시간|분)?\s{0,8}"
    r"[×x\*]\s{0,8}(?P<b>\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?P<ub>개월|년|개|명|대|회|일|주|배|시간)?\s{0,8}=\s{0,8}"
    r"(?P<c>\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?P<uc>만|억|천)?\s{0,8}(?P<unitc>원|명|건|개|대|시간|분)?"
)


def _as_number(text: str) -> float:
    return float(text.replace(",", ""))


#: 「16억 9,200만 원 (470만 원 × 36개월)」 — a total written first, its formula after it.
_VALUE_THEN_PRODUCT = re.compile(
    r"(?P<c>\d[\d,]*(?:\.\d+)?\s*억(?:\s*\d[\d,]*\s*만)?|\d[\d,]*(?:\.\d+)?\s*만)\s*원?\s*"
    r"\(\s*(?P<a>\d[\d,]*(?:\.\d+)?)\s*만\s*원?\s*[×x\*]\s*(?P<b>\d[\d,]*)\s*(?:개월|년|개|명|대|회)\s*\)"
)


_AMOUNT_WON = re.compile(
    r"(?<![\d,.])((?:\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}억\s{0,8})?(?:\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}만)|\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}억)\s{0,8}원"
)


def fix_ledger_magnitudes(text: str, request: str) -> str:
    """An amount that is a ledger result off by a power of ten is set back to that result."""
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
    """「16억 9,200만」 → 169200, 「1억」 → 10000, 「5,640만」 → 5640; `None` when unreadable."""
    plain = text.replace(",", "").replace(" ", "")
    match = re.fullmatch(r"(?:(\d{1,15}(?:\.\d{1,15})?)억)?(?:(\d{1,15}(?:\.\d{1,15})?)만)?", plain)
    if not match or not (match.group(1) or match.group(2)):
        return None
    return float(match.group(1) or 0) * 10000 + float(match.group(2) or 0)


#: The same noun twice in a row under different particles — 「방향으로 방향을 잡는다」 —
#: a writer that restarted its phrase mid-sentence. Code and URLs are left alone.
#: The first half must be an adverbial (「방향으로」, 「서울에서」); a subject followed by
#: the same noun as object (「비용이 비용을 낳는다」) is a sentence, not a restart.
_DOUBLED = re.compile(
    r"(?<![가-힣])([가-힣]{2,6})(으로|로|에서|에게|에|의|와|과|도)\s+"
    r"\1(으로|로|에서|에게|에|을|를|이|가|은|는|의|와|과|도)(?![가-힣])"
)


def undouble_words(text: str) -> str:
    """「방향으로 방향을」 → 「방향을」: the restarted phrase keeps its second, finished half.
    Table rows and fenced code are left as written."""
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


#: A difference worked out in prose (「0.521 - 0.345 = 0.176」) is scratch work; a cost
#: working (「470만 원 × 36개월 = …」) is asked for by the prompt and stays.
_SHOWN_ARITHMETIC = re.compile(
    r"(?<![\d.])\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}[-−–]\s{0,8}\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}=\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)"
)
_NUMBER_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: Typed 개조식 markers and the ※ note: list lines, like 「-」 and 「*」.
_GAEJO_MARKS = ("□", "■", "○", "◦", "ㅇ", "ㆍ", "·", "∙", "※")
_SENTENCE_END = re.compile(r"(?<=[.!?。])" + units.NO_NUMBER_DOT_BREAK + r"\s{1,8}|(?<=다\.)\s{0,8}(?=[가-힣A-Za-z(「\d])")


def _number_keys(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUMBER_TOKEN.finditer(text)}


def _collapse_arithmetic(body: str) -> str:
    """「0.521 - 0.345 = 0.176으로 측정되어」 → 「0.176으로 측정되어」 outside tables and code:
    a report states a result; the working belongs in a table note, if anywhere."""
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
    """A sentence whose two or more numbers are all cells of a table in the same section goes.

    Shown arithmetic becomes its result; a section never loses its last prose sentence.
    """
    lines = body.split("\n")
    table_cells: set[str] = set()
    for line in lines:
        if line.lstrip().startswith("|"):
            for cell in line.strip().strip("|").split("|"):
                table_cells |= _number_keys(cell)
    if len(table_cells) < 2:
        # No table to echo; shown arithmetic in prose still becomes its result.
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
        kept = []
        for sentence in _SENTENCE_END.split(line):
            if not sentence.strip():
                continue
            numbers = _number_keys(sentence)
            if len(numbers) >= 2 and numbers <= table_cells:
                continue
            kept.append(sentence.strip())
        prose_kept += len(kept)
        if kept:
            out.append(" ".join(kept))
    if prose_kept == 0:
        return _SHOWN_ARITHMETIC.sub(r"\1", body)
    return "\n".join(out)


_POINT_UNIT = re.compile(r"(?<![\d.])(\d{1,15}(?:\.\d{1,15})?)\s{0,8}(?:%p|퍼센트\s{0,8}포인트|포인트)(?![\w가-힣])")
_BARE_RATIO = re.compile(r"(?<![\d.%])0\.\d{1,15}(?![\d.%])")


def fix_point_units(text: str) -> str:
    """「%p」 after a difference of bare ratios (0.xxx) goes; a sentence about percentages keeps it."""
    # Split with the separators kept, so paragraph breaks and fence lines survive.
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
    """「(14-3) ÷ 14 × 100 = 1,400%」 is checked, not trusted: the result becomes 78.6%, and
    the wrong figure is corrected wherever else the section repeats it."""
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
    """A conclusion moved to the front drops sentences that only restate later sections' numbers.

    Never emptied."""
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
    """「요약은 세 문장」 「결론 두 문장으로」 → {"요약": 3, "결론": 2}."""
    out: dict[str, int] = {}
    for m in _SENTENCE_ASK.finditer(request or ""):
        count = _KO_COUNT.get(m.group(2)) or int(m.group(2)) if m.group(2) else None
        if count:
            out[m.group(1)] = count
    return out


def enforce_sentence_counts(sections: list[dict], request: str) -> list[dict]:
    """A section the request sized in sentences keeps that many: the first N prose
    sentences stay, later ones go; tables, lists and fenced blocks are not sentences and
    stay where they are."""
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
    """A total written before its formula is recomputed; a wrong one is replaced wherever it repeats."""
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
    """`value` written the way `sample` was: thousands separators if it had them, no
    needless decimals."""
    rounded = round(value, 2)
    if abs(rounded - round(rounded)) < 1e-9:
        rounded = int(round(rounded))
    text = f"{rounded:,}" if "," in sample or (isinstance(rounded, int) and rounded >= 1000) else f"{rounded}"
    return text


def fix_products(text: str) -> str:
    """Every 「A × B = C」 in `text` recomputed; a wrong C is replaced everywhere in the text.

    Only plain products with the same scale on both sides are touched."""
    out = text or ""
    corrections: list[tuple[str, str, str, str]] = []
    # The equations first, each result replaced in its own span (right to left so
    # earlier offsets stay valid).
    for match in reversed(list(_PRODUCT.finditer(out))):
        if (match.group("ua") or "") != (match.group("uc") or ""):
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
    # Then the same wrong figure wherever the text repeats it — a table cell below the
    # sentence — matched as a whole number with its scale and unit, never inside another
    # number: 「169,200만 원」 goes, 「2,169,200」 and 「169,2001」 stay.
    for wrong, right, scale, unit in corrections:
        tail = (rf"\s*{re.escape(scale)}" if scale else "") + (rf"\s*{re.escape(unit)}" if unit else "")
        pattern = re.compile(rf"(?<![\d,.]){re.escape(wrong)}(?![\d,.]){tail}" if (scale or unit)
                             else rf"(?<![\d,.]){re.escape(wrong)}(?![\d,.])")
        def _swap(m: re.Match[str], wrong: str = wrong, right: str = right) -> str:
            return m.group(0).replace(wrong, right, 1)

        out = pattern.sub(_swap, out)
    return out


_PLACEHOLDER = re.compile(
    # Value holes only: a 「[담당자]」 is the person's to fill, not the attached data's.
    r"\((?:미정|측정값|값|TBD|TODO|n/a|추후\s*기입|확인\s*필요)\)|\[(?:측정값|값|수치|금액)\]",
    re.I,
)
_FILL_PROMPT = """Below is the draft of the report's "{heading}" section. Its tables and sentences still
have blanks such as [측정값]·[금액]. The attached material (reference data) contains those values.
Rewrite the section, filling the blanks with the material's actual values. Do not make up a
value the material does not have; leave that blank ([측정값] etc.) as it is. Answer with the
body only, no section heading, in Markdown, in Korean. Do not wrap it in JSON.

{body}"""


#: Sentences that carry no fact: the model's way of saying it has nothing to add.
_FILLER = re.compile(
    r"(명시되어 있지 않습니다|확인되지 않았습니다|자료에 (?:다른|별도의) [^.]*없습니다|"
    r"고려할 필요가 있습니다|중요한 역할을 합니다|할 수 있을 것으로 보입니다|"
    r"필요할 것으로 판단됩니다|주목할 만합니다|의미가 있다고 할 수 있습니다)"
)
#: Sentence breaks at whitespace only, for the restatement trimming.
_SPACED_SENTENCE_END = units.SENTENCE_BREAK
#: A line's indent, and its indent with a list marker (Markdown or a typed 개조식 one).
_LINE_LEAD = re.compile(r"^\s{0,24}")
_LIST_LEAD = re.compile(r"^\s{0,24}(?:(?:[-*+]|\d{1,3}[.)]|[□■○◦ㅇㆍ·∙※])\s{1,4})?")
#: A number with the unit glued to it (「742명」 「6.1만 건」 「14분」 「4.1점」 「7%」).
_NUMBER_FACT = re.compile(r"(?<![A-Za-z\d])\d[\d,.]*(?:\s*(?:만|억|천))?[가-힣%]?")


_PARTICLE = re.compile(
    r"(되었습니다|였습니다|이었습니다|했습니다|합니다|됩니다|입니다|습니다|되었고|이었고|였고|"
    r"되었|되어|하였|하여|했다|한다|하는|하고|하며|이며|으며|에서는|에서|으로|은|는|이|가|을|를|"
    r"의|에|로|와|과|도|된|한|며)$"
)
#: Words that carry no fact of their own; a restatement dressed in them is still one.
_CONNECTIVES = frozenset(
    "이번 총 동안 기간 대상 기준 통해 위해 경우 또한 그리고 이후 당시 전체 각각 모두 해당 "
    "진행 운영 실시 수행 소속 포함 전제 결과 과정 중 및 등".split()
)


def _stems(text: str) -> set[str]:
    """Content words with the particle or ending cut off, so 「대상은」 and 「대상」 agree;
    one-letter stems and connective words are not counted as content."""
    out = set()
    for word in re.findall(r"[가-힣A-Za-z]{2,}", text):
        stem = _PARTICLE.sub("", word) or word
        if len(stem) >= 2 and stem not in _CONNECTIVES:
            out.add(stem)
    return out


#: A line of a test (question, choice, answer, scoring): never trimmed as a restatement,
#: since a wrong choice repeats the body's numbers on purpose.
_ASSESSMENT_LINE = re.compile(
    r"[①-⑩]|^\s*(?:\*\*)?(?:서술형\s?)?\d{1,2}\s?[.)번](?:\*\*)?\s|^\s*\*\*서술형|"
    r"\d{1,2}\s?점\)|[?？]\s{0,8}$|(?:정답|모범\s?답|채점|배점|보기)\s?[:：]|시오\.?\s{0,8}$"
)


def trim_restatements(
    body: str, earlier: list[str], *, keep_items: bool = False
) -> tuple[str, list[str]]:
    """`(body without pure restatements and filler, the sentences cut)`.

    A sentence is cut only when it is safe to lose: every number in it (with its unit)
    already appears in an earlier section and it carries no word the earlier sections
    lack, or it is a filler sentence. Tables and blocks are never touched, and a section
    is never emptied. With `keep_items` (a 개조식 body) list items are kept whole too:
    each is a line of its own, and a cut one leaves its parent item hanging."""
    if not body.strip():
        return body, []
    # With no earlier sections the pool starts empty and grows sentence by sentence, so
    # a section that says 「64에서 128로 갈 때 0.853에서 0.912로」 twice says it once.
    earlier_text = " ".join(earlier)
    earlier_numbers = set(_NUMBER_FACT.findall(earlier_text))
    earlier_words = _stems(earlier_text)
    has_table = any(line.strip().startswith("|") for line in body.split("\n"))
    cut: list[str] = []
    out_lines: list[str] = []
    in_block = False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            in_block = not in_block
        if line.strip().startswith("|"):
            # Prose after a table that only reads the table back is a restatement too.
            cells = " ".join(
                c for c in line.split("|") if c.strip() and not set(c.strip()) <= set("-: ")
            )
            earlier_numbers |= set(_NUMBER_FACT.findall(cells))
            earlier_words |= _stems(cells)
        if (in_block or line.strip().startswith("|") or line.strip().startswith("#")
                or not line.strip() or _ASSESSMENT_LINE.search(line)
                or keep_items and _LIST_LEAD.match(line).group(0).strip()):
            out_lines.append(line)
            continue
        sentences = [x for x in _SPACED_SENTENCE_END.split(line) if x.strip()]
        kept: list[str] = []
        for sentence in sentences:
            text = sentence.strip()
            numbers = set(_NUMBER_FACT.findall(text))
            words = _stems(text)
            # A ratio drawn from the numbers already given (「64에서 128로 2배가 될 때」) is
            # not a new fact.
            given = {n for n in numbers if n in earlier_numbers or re.fullmatch(r"\d{1,15}배", n)}
            # Two numbers already given, or one number with nothing else new to say.
            restated = (
                bool(numbers)
                and numbers <= given
                and (
                    len(numbers) >= 2
                    and len(words - earlier_words) <= max(1, len(words) // 5)
                    # Three numbers already given together is the same statement, even
                    # when a few generic words (「데이터에서 확인되는」) changed.
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
            if restated or dangling or _FILLER.search(text):
                cut.append(text)
                continue
            kept.append(sentence)
            # What this sentence said is now said: a later sentence repeating it goes.
            earlier_numbers |= numbers
            earlier_words |= words
        if sentences and not kept and not any(x.strip() for x in out_lines):
            # A section is never emptied: its first paragraph keeps its first sentence.
            kept = [sentences[0]]
            cut.remove(sentences[0].strip())
        if kept and kept == sentences:
            out_lines.append(line)
        elif kept:
            # The line's indent stays, and its list marker when the first sentence went.
            lead = (_LINE_LEAD if sentences[0] in kept else _LIST_LEAD).match(line).group(0)
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
    """The words a diagram shows, without its syntax: node labels, whatever the shape."""
    body = re.sub(r"^```\w*|```$", "", block.strip(), flags=re.M)
    body = re.sub(r"\*[^\n]*\*\s*$", "", body)
    return {w for w in re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9]{1,}", body) if w not in _FIGURE_KEYWORDS}


def drop_repeated_figures(text: str) -> str:
    """A section drawing the same diagram twice keeps one: the captioned one, else the first.

    Two blocks match when four fifths of the smaller's words appear in the larger."""
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
    """A ```kpi block whose every value already sits in a table of the same section says
    the numbers twice; the table stays."""
    match = re.search(r"```kpi\n(.*?)```\n?", text, flags=re.S)
    if not match:
        return text
    values = [line.split("|")[0].strip() for line in match.group(1).splitlines() if "|" in line]
    table_cells = " ".join(line for line in text.splitlines() if line.strip().startswith("|"))
    if values and all(v and v in table_cells for v in values):
        return (text[: match.start()] + text[match.end():]).strip() + "\n"
    return text


def placeholder_heavy(text: str) -> bool:
    """Three or more 「[측정값]」- or 「(미정)」-style holes: a frame the data never reached."""
    return len(_PLACEHOLDER.findall(text or "")) >= 3


_REFERENCE_HEADING = re.compile(r"참고\s{0,3}문헌|references|bibliography|인용\s{0,3}문헌", re.I)
_REF_START = re.compile(r"(?<!\S)\[(\d{1,3})\]\s")


def _title_key(entry: str) -> str:
    """The longest Latin run of an entry, lowercased: the paper's title in practice."""
    body = re.sub(r"https?://\S{1,400}|doi:\S{1,200}|arXiv:\s{0,3}\S{1,40}", " ", entry)
    runs = re.findall(r"[A-Za-z][A-Za-z0-9:,'\- ]{12,300}", body)
    title = max(runs, key=len) if runs else body
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))[:80]


def _references_from_table(body: str) -> str:
    """A reference list written as a table, as numbered lines: a row whose first cell is
    「[n]」 becomes 「[n] cell. cell. cell.」 (empty, 「(미정)」 and 「[저자]」 cells left out); the
    header, the rule and prose around the table stay where they were."""
    if not re.search(r"(?m)^\s{0,3}\|", body):
        return body
    # Rows the writer broke across blank lines (「|\n\n[1] | …」) are joined first.
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
        # An entry ends at a blank line: what follows is the writer's comment on the list.
        (int(m.group(1)), re.split(r"\n\s{0,8}\n", body[m.end() : bounds[i + 1]].strip())[0])
        for i, m in enumerate(starts)
    ]
    return lead, entries


#: An entry never ends like a sentence; a note about the list (「…확인하시기 바랍니다」,
#: 「…근거를 제공합니다」) does.
_PROSE_END = re.compile(r"(?:니다|[이한된있없했였같않]다|이다|이에요|해요)\s{0,2}$")
#: Where one entry ends and the next begins on the same line: before 「박찬승. 『」,
#: 「Schmid, Andre.」, 「Lee, S. G.」 or a 「[저자]」 / 「(저자 확인 필요)」 placeholder.
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
    """The reference section as a list: notes about the list (sentences ending like
    sentences) go, run-together entries are split one per line, and a bold group label
    keeps a line of its own."""
    out = []
    for sec in sections:
        if not (
            _REFERENCE_HEADING.search(str(sec.get("heading") or ""))
            and str(sec.get("format") or "markdown") == "markdown"
        ):
            out.append(sec)
            continue
        text = str(sec.get("content") or "")
        # A number left at a line's end (「… 2025. [11]」) belongs to the entry below it.
        text = re.sub(r"[ \t]{1,4}(\[\d{1,3}\])[ \t]{0,4}\n", r"\n\1 ", text)
        # A bold label glued to the sentence before it (「…싣습니다. **국내 연구**」).
        text = re.sub(r"\s{0,4}(\*\*[^*\n]{2,40}\*\*)\s{0,4}", r"\n\n\1\n\n", text)
        lines: list[str] = []
        for block in re.split(r"\n\s{0,8}\n|\n", text):
            block = block.strip()
            if not block:
                continue
            if re.fullmatch(r"\*\*[^*\n]{2,40}\*\*", block):
                lines.append(block)
                continue
            # Korean sentences end at 「다.」; an entry's 「1988.」 before Korean prose too.
            pieces = re.split(r"(?<=[다요])\.\s{1,4}|(?<=\d{4}\.)\s{1,4}(?=[가-힣])", block)
            for piece in pieces:
                piece = piece.strip()
                if piece and _PROSE_END.search(piece.rstrip(".")):
                    # Entries, then a note (「…Penn Press. 본문에 인용된 … 있습니다」): the
                    # note is the Korean sentence after the last entry's full stop.
                    starts = [m.end() for m in re.finditer(r"[.)]\s{1,4}(?=[가-힣])", piece)]
                    piece = piece[: starts[-1]].strip() if starts else ""
                if not piece:
                    continue
                for entry in _NEXT_ENTRY.split(piece):
                    entry = entry.strip()
                    # An entry opens with its author, a placeholder for one, or its title.
                    if entry and (_ENTRY_START.match(entry) or re.search(r"[『「]", entry[:40])):
                        lines.append(entry)
                    elif entry and lines and re.match(r"[A-Za-z0-9]", entry):
                        # An entry the writer broke across lines continues the last one.
                        lines[-1] = f"{lines[-1]} {entry}"
        # A label left with no entries under it goes too.
        kept = [
            line for i, line in enumerate(lines)
            if not line.startswith("**")
            or (i + 1 < len(lines) and not lines[i + 1].startswith("**"))
        ]
        content = "\n\n".join(kept)
        out.append({**sec, "content": content + "\n"} if content.strip() else sec)
    return out


_AMEND_PROMPT = """Only add to the section below. {instruction}

Rules: do not change or delete a single character of the existing sentences and table rows.
Leave everything but what you add as it is. Do not invent values the material does not give;
leave the place's name in brackets, like 「[담당자]」. Write additions in Korean.
Answer with the amended section body only (no heading or explanation).

절:
{content}"""

_CITATION_ASK = re.compile(r"저자\s{0,2}연도|괄호\s{0,2}인용|본문\s{0,2}인용|(?<![A-Za-z])APA(?![A-Za-z])")
_ENDPOINT = re.compile(r"(GET|POST|PUT|PATCH|DELETE)\s{0,3}\|?\s{0,3}`?(/[\w/{}:.-]{1,120})")


def _works(sections: list[dict], material: str) -> list[str]:
    """Works the document may cite: its reference entries, else the material's lines that
    name an author, a year and a title."""
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
        # A web source on the shelf carries its own link: only works named from memory.
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
    """`(section index, what to add)` for what the instruction asked and the draft left out."""
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


#: A question's own line: numbered (「### 1.」, 「**③ (개념, 중)**」, 「문항 4」) and asking.
_QUESTION_NUMBER = re.compile(
    r"^\s{0,16}(?:#{1,4}\s{0,8})?(?:\*\*)?\s{0,8}(?:\[?\d{1,2}\s?[.)\]번]|[①-⑩]|문항\s?\d{1,2}|Q\d{1,2})"
)
#: 「쓰시오」 asks; 「착용해 주십시오」 is a notice's request, not a question.
_ASKS = re.compile(r"\?|(?<!십)시오\.?|것은\??\s{0,16}(?:\*\*)?\s{0,16}$|무엇인가|설명하라|쓰라")
_ASKED_FOR = re.compile(r"문항|평가|퀴즈|시험|문제")
#: A line that belongs to the question above it.
_QUESTION_TAIL = re.compile(
    r"^(?:[①-⑩]|\(?[1-5가-마]\)|[-*+]\s|\||>|---|#{1,4}\s{0,8}(?:\*\*)?\s{0,8}(?:정답|해설|채점|모범)|"
    r"(?:\*\*)?\s{0,8}(?:정답|해설|풀이|채점|"
    r"모범|예시\s?답|배점|평가\s?기준|부분\s?점수|오답|\d{1,2}\s?점)|.{0,40}\d{1,2}\s?점\)?$)"
)
_ASSESSMENT_HEADING = re.compile(r"평가|문항|퀴즈")


def _question_lines(text: str) -> list[int]:
    """Lines that open a question: numbered and asking, or a bare number (「**문항 1**
    [1차시]」) whose next line asks."""
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
    """The asking words of the question opened at line `i`."""
    return lines[i] if _ASKS.search(lines[i]) or i + 1 >= len(lines) else lines[i + 1]


def _stem_key(line: str) -> str:
    stem = _QUESTION_NUMBER.sub("", line)
    stem = re.sub(r"\([^)]{0,20}\)", "", stem)
    return re.sub(r"[^가-힣A-Za-z0-9]", "", stem)[:12]


def question_set(material: str) -> str:
    """The material's set of questions (five or more), as written: stems, options,
    answers and scoring, from the first question to the end of the last one's lines."""
    lines = (material or "").split("\n")
    stems = _question_lines(material or "")
    if len(stems) < 5:
        return ""
    # The last question runs on through its own lines — options, answer, scoring — and
    # stops at the first line that is none of those (the next answer's 「안내문」).
    end = stems[-1] + 1
    after_blank = False
    answers = 0  # inside a 「## 정답 및 해설」 section: its heading level
    while end < len(lines):
        line = lines[end].strip()
        heading = re.match(r"^(#{1,4})\s", line)
        if re.match(r"^(?:검색어:|본문 발췌:|출처: https?://|\[\d{1,3}\] )", line):
            break
        # A bold title line after a gap that is not the answers' starts the next piece
        # (「**과학 실험 수업 안내문**」).
        if (after_blank and re.fullmatch(r"\*\*[^*]{2,60}\*\*", line)
                and not re.search(r"정답|해설|채점|모범|문항|\d", line)):
            break
        if heading and re.search(r"정답|해설|채점|모범", line):
            answers = answers or len(heading.group(1))
        elif answers:
            # The answers run to the next heading of their own level or above.
            if heading and len(heading.group(1)) <= answers:
                break
        # A plain line ends the question only when a blank line came before it: the
        # sentence under 「**정답**:」 is the answer, a new paragraph is not.
        elif line and not _QUESTION_TAIL.match(line) and after_blank:
            break
        after_blank = not line
        end += 1
    while end > stems[-1] + 1 and not lines[end - 1].strip():
        end -= 1
    block = [line for line in lines[stems[0]:end] if line.strip() != "---"]
    # Headings inside become bold lines: the section keeps its own level.
    block = [re.sub(r"^\s{0,16}#{1,4}\s{0,16}(.{1,2000}?)\s{0,64}$", r"**\1**", line) for line in block]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(block)).strip()


def carry_question_set(sections: list[dict], material: str, request: str) -> list[dict]:
    """Questions asked for and given in the material are carried as written, not summarised."""
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
    """API endpoints the material listed and the document omitted, added as rows to its API section.

    Nothing is rewritten."""
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
    """Whether `after` keeps every line of `before` (whitespace aside) and is longer."""
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
    """A reference section with no entries (the writer put a sentence there, or nothing)
    is rebuilt from the works the text cites: each 「저자 (연도)」 gets the bibliographic
    line the material gives for that author and year, or a marked placeholder."""
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
    """「[n]」 markers that point at nothing go: a number is backed by the n-th source on
    the shelf or by an 「[n]」 entry in the reference section. A pasted note numbered
    「[2]」 is the person's own material, not a source, and is not cited as one."""
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
        # 「… 있습니다 .」 left by a removed marker.
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
    """The reference list one entry per line, each paper once, numbered 1, 2, 3, citations renumbered.

    A citation of a dropped duplicate points at the kept one; a list with no numbered
    entries is left alone."""
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
        # 「[1] [1]」 after a merge is one citation.
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
    """The prose fields of a JSON answer, however deep the model nested them."""
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
    """A section body the model wrapped as a one-key JSON object (`{"결과의 섹션 본문":
    "..."}`) is that string; anything else comes back as it was."""
    stripped = (text or "").strip()
    fence = re.fullmatch(r"```(?:json)?\s{0,64}(\{.{0,200000}\})\s{0,64}```", stripped, re.S)
    if fence:
        stripped = fence.group(1)
    if stripped.startswith("{") and not stripped.endswith("}"):
        # Cut before it closed: the one string it opened is the body so far.
        # 「{"content": "…」 or 「{"section": {"number": 7, "title": "…", "body": "…」.
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
        # Line breaks inside the strings are the model's, not JSON's: read them as written.
        data = json.loads(stripped, strict=False)
    except (json.JSONDecodeError, ValueError):
        return text
    if isinstance(data, dict):
        # {"section": "참고문헌", "content": "…"}: the body is the content, not the label;
        # {"section": {"number": 7, "title": …, "body": "…"}} one level further in.
        # {"sections": [{"title": …, "body": "…"}, …]}: each part's prose, in order.
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
    parts = [f"# {title}"]
    for section in sections:
        parts.append(f"\n## {section['heading']}\n\n{section.get('content') or ''}")
    return "\n".join(parts).strip() + "\n"
