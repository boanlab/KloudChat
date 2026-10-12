"""Follow-up changes propagated document-wide; conflicting values found and repaired once."""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services import internal_words, key_figures, report_checks, units, verify
from app.services.verify import Doc, Finding

log = logging.getLogger(__name__)

#: follow-ups changing content, not only form
_CONTENT_CHANGE = re.compile(
    r"\d|기준|가중치|배점|필수|조건|입사|퇴사|기간|경력|예산|단가|금액|비용|가격|날짜|일정|기한|마감"
    r"|결정|권고|추천|선정|대안|바뀌|바꿨|변경|아니라|틀렸|틀려|다시\s{0,2}(?:봐|계산|확인|따져)")
#: parts restating/resting on the rest
_DEPENDS = re.compile(
    r"요약|개요|핵심|결론|권고|제언|종합|결정|추천|시사점|맺음|총평|요청\s{0,2}사항")
_SUMMARY = re.compile(r"요약|개요|핵심|총평")
#: follow-up words binding to nothing specific
_FILLER = frozenset({"다시", "그리고", "그럼", "근데", "그냥", "이걸로", "봐", "줘", "해줘",
                     "맞는지", "있어", "없어", "이야", "이었어", "거야", "부탁", "문서", "보고서",
                     "내용"})


#: a follow-up reshaping what is there: a column or row added, a list made a table, a summary
_RESHAPE = re.compile(
    r"열\s{0,2}(?:을|를|도|만)?\s{0,2}(?:추가|넣|더|붙)|행\s{0,2}(?:을|를|도|만)?\s{0,2}(?:추가|넣|더|붙)"
    r"|표로\s{0,2}(?:바꿔|바꾸|만들|정리|그려|보여)|요약\s{0,2}(?:을|를|도)?\s{0,2}(?:넣|추가|달|붙|써)"
    r"|제목\s{0,2}(?:을|를)?|합계")
#: a value the follow-up states (「3열」, 「3줄」, 「(%)」 are shapes, not values)
_STATED_VALUE = re.compile(
    r"\d[\d,.]*\s{0,2}(?:원|만|억|명|점|개월|년|일|시간|주|%(?!\s?\)))"
    r"|\d{4}[.-]\s?\d{1,2}|바뀌|바꿨|변경|아니라|틀렸|틀려|다시\s{0,2}(?:봐|계산|확인|따져)")


def changes_content(instruction: str) -> bool:
    """Whether a follow-up changes facts others restate, not only the shape of a part."""
    text = instruction or ""
    if _RESHAPE.search(text) and not _STATED_VALUE.search(text):
        return False
    return bool(_CONTENT_CHANGE.search(text))


def dependents(
    instruction: str, names: list[str], texts: list[str], targets: list[int]
) -> list[int]:
    """Extra parts a content follow-up must revise (summaries, mentions); body first."""
    if not changes_content(instruction):
        return []
    keys = report_checks._words(instruction) - _FILLER
    body, restating = [], []
    for index, (name, text) in enumerate(zip(names, texts, strict=False)):
        if index in targets:
            continue
        if _DEPENDS.search(name or ""):
            restating.append(index)
        elif keys & report_checks._words(f"{name}\n{text}"):
            body.append(index)
    # top summary rewritten last
    restating.sort(key=lambda i: bool(_SUMMARY.search(names[i] or "")))
    return body + restating


def dependent_note(instruction: str) -> str:
    return (
        f"This revision request (「{' '.join(instruction.split())[:300]}」) changed criteria, "
        "figures, dates or decisions in other sections. Fix the sentences in this section that "
        "state those values or rely on them (including the summary, conclusion, "
        "recommendation and requests) to match the change and the preceding sections. Use "
        "values the user already gave without asking again. Leave unrelated sentences as "
        "they are."
    )


#: labels for totals and deciding figures
_TOTAL_LABELS = ("총 경력", "총경력", "총비용", "총 비용", "총액", "합계", "공급가액",
                 "예산 상한", "상한", "초과액", "차액", "총점", "가중 점수", "점수")
_AMOUNT = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(\s{0,2}(?:억|천만|만)(?:\s{0,2}\d[\d,]*\s{0,2}(?:만|천))?)?"
    r"\s{0,2}(원|%p|%|명|점|개월)"
    r"|(?<![\d.,])(\d{1,2})\s{0,2}년\s{0,2}(\d{1,2})\s{0,2}개월")
#: sentences about another case (scenario, before, if)
_OTHER_CASE = re.compile(
    r"시나리오|가정|라면|일\s{0,2}경우|늘면|줄면|만약|이전|기존|변경\s{0,2}전|v1|에서\s{1,3}[^.]{0,30}(?:으로|로)\s"
    r"|대비|보다")


@dataclass(slots=True)
class Stated:
    section: int
    line: str
    key: str
    value: float
    shown: str


def _value(m: re.Match[str]) -> tuple[float, str] | None:
    if m.group(4):
        return float(int(m.group(4)) * 12 + int(m.group(5))), "개월"
    raw = m.group(1) + (m.group(2) or "")
    try:
        number = units._ko_number(raw.replace(" ", ""))
    except ValueError:
        return None
    return number, m.group(3)


def _after(text: str, start: int) -> tuple[float, str, str] | None:
    """First value within 30 chars after start, no label between."""
    m = _AMOUNT.search(text, start, min(len(text), start + 30))
    if not m:
        return None
    got = _value(m)
    return (got[0], got[1], m.group(0).strip()) if got else None


def stated(sections: list[dict], labels: list[str], options: list[str]) -> list[Stated]:
    """Each label (or option + label) with its following value; other-case sentences excluded."""
    out: list[Stated] = []
    for index, sec in enumerate(sections):
        content = str(sec.get("content") or "")
        for table in report_checks.tables([{"content": content}]):
            header = table.header
            for line, row in table.rows:
                name = row[0] if row else ""
                for k, cell in enumerate(row[1:], start=1):
                    if k >= len(header) or not header[k]:
                        continue
                    head = re.sub(r"\([^()]{0,20}\)", "", header[k]).strip()
                    if not any(x in head for x in (*labels, *options)):
                        continue
                    m = _AMOUNT.search(cell)
                    got = _value(m) if m else None
                    if got:
                        out.append(Stated(index, line, f"{name}·{head}·{got[1]}", got[0],
                                          m.group(0).strip()))
        for raw in content.split("\n"):
            if raw.lstrip().startswith(("|", "#", "```", "!")):
                continue
            for sentence in units.SENTENCE_BREAK.split(raw):
                if _OTHER_CASE.search(sentence):
                    continue
                named = [o for o in options if o and o in sentence]
                if len(named) > 1:
                    continue
                for label in labels:
                    at = sentence.find(label)
                    if at < 0:
                        continue
                    got = _after(sentence, at + len(label))
                    if got:
                        key = f"{named[0] if named else ''}·{label.replace(' ', '')}·{got[1]}"
                        out.append(Stated(index, sentence.strip(), key, got[0], got[2]))
    return out


def recommended(sections: list[dict], options: list[str]) -> list[tuple[int, str, str]]:
    """(section, line, option) per unconditional recommendation line."""
    out = []
    for index, sec in enumerate(sections):
        for line in str(sec.get("content") or "").split("\n"):
            for sentence in units.SENTENCE_BREAK.split(line):
                if report_checks._CONDITIONAL.search(sentence):
                    continue
                pick = report_checks.recommended_option(sentence, options)
                if pick:
                    out.append((index, sentence.strip(), pick))
    return out


@dataclass(slots=True)
class Conflict:
    key: str
    #: expected value, or None if unsettled
    want: str | None
    #: (section, sentence/row, written value) differing from want
    off: list[tuple[int, str, str]]
    seen: list[str]


def conflicts(sections: list[dict], *, changed: set[int], instruction: str = "",
              settled: list | None = None, options: list[str] | None = None,
              leader: str | None = None) -> list[Conflict]:
    """One fact with two values; changed parts win, then settled, then follow-up."""
    options = [o for o in options or [] if o]
    settled = list(settled or [])
    labels = list(dict.fromkeys([*_TOTAL_LABELS, *[f.name for f in settled if len(f.name) <= 12]]))
    groups: dict[str, list[Stated]] = {}
    for item in stated(sections, labels, options):
        groups.setdefault(item.key, []).append(item)
    out: list[Conflict] = []
    for key, items in groups.items():
        values = {round(i.value, 4) for i in items}
        if len(values) < 2 or len({i.section for i in items}) < 2:
            continue
        mine = {round(i.value, 4) for i in items if i.section in changed}
        want: float | None = next(iter(mine)) if len(mine) == 1 else None
        if want is None:
            said = [v for v in values if any(_digits(i.shown) in _digits(instruction)
                                             for i in items if round(i.value, 4) == v)]
            want = said[0] if len(said) == 1 else None
        shown = next((i.shown for i in items
                      if want is not None and round(i.value, 4) == want), None)
        off = [(i.section, i.line, i.shown) for i in items
               if want is None or round(i.value, 4) != want]
        out.append(Conflict(key, shown, off, sorted({i.shown for i in items})))
    if options:
        picks = recommended(sections, options)
        named = {p for _, _, p in picks}
        if len(named) > 1:
            mine = {p for s, _, p in picks if s in changed}
            want_pick = leader if leader in named else (
                next(iter(mine)) if len(mine) == 1 else None)
            out.append(Conflict("권고", want_pick,
                                [(s, line, p) for s, line, p in picks if p != want_pick],
                                sorted(named)))
    for fig in settled:
        for index, sec in enumerate(sections):
            for line in str(sec.get("content") or "").split("\n"):
                for sentence in units.SENTENCE_BREAK.split(line):
                    if key_figures.conflicts(sentence, [fig]):
                        out.append(Conflict(fig.name, str(fig.value),
                                            [(index, sentence.strip(), "")], [str(fig.value)]))
    return out


def _digits(text: str) -> str:
    return re.sub(r"[\s,]", "", text or "")


def check(*, changed: set[int], instruction: str = "", settled: list | None = None,
          options: list[str] | None = None, leader: str | None = None):
    """Cross-part check as a verify Check; off-value sentence rewritten once."""
    async def cross_section(doc: Doc):
        found: list[Finding] = []
        for c in conflicts(doc.sections, changed=changed, instruction=instruction,
                           settled=settled, options=options, leader=leader):
            label = c.key.strip("·").replace("·", " ")
            for index, sentence, written in c.off:
                if c.want is None:
                    why = (f"「{label}」을 문서 안에서 {', '.join(c.seen)}로 다르게 적었다. "
                           "이번 수정 요청과 자료에 맞는 하나로 맞춰라.")
                    found.append(Finding(index, sentence, "cross_section", why, rank=1, auto=False))
                    continue
                if c.key == "권고":
                    why = (f"이 문장은 「{written}」을 권고하지만 문서의 다른 곳(이번 수정 반영)은 "
                           f"「{c.want}」을 권고한다. 「{c.want}」 권고로 고치고 근거도 맞춰라.")
                else:
                    why = (f"「{label}」을 다른 절에서는 {c.want}로 적었다(이번 수정 반영). "
                           f"이 문장의 {written or '값'}을 {c.want}로 맞추고, 그 값으로 계산한 "
                           "수치도 맞춰라.")

                def still(text: str, written=written, want=c.want, key=c.key) -> bool:
                    if key == "권고":
                        return written in text and want not in text
                    return bool(written) and _digits(written) in _digits(text)

                found.append(Finding(index, sentence, "cross_section", why, rank=0, still=still))
        return found, {}

    return cross_section


async def settle_again(format_id: str, request: str, instruction: str, material: str, *,
                       model: str, api_key: str) -> report_checks.Prepass | None:
    """Format pre-pass rerun with the follow-up, so changed values arrive settled."""
    # late import: report is heavy and imports this tree
    from app.services import process_settings, report

    # only 서식 with pre-pass steps
    process = process_settings.for_format(format_id)
    if not process.prepass:
        return None
    # request-listed options are material
    material = material if (material or "").strip() else request
    try:
        return await report_checks.prepare(
            format_id, f"{request}\n\n추가 요청: {instruction}", material,
            complete=report._complete, model=model, api_key=api_key,
            today=dt.datetime.now(ZoneInfo(settings.timezone)).date(),
            tiebreak_model=report._tiebreak_model(model), process=process,
            deliberate=report._complete_thinking,
        )
    except Exception:  # noqa: BLE001 — the rewrite goes ahead without settled values
        log.exception("revision pre-pass failed")
        return None


def blocks_for_rewrite(pre: report_checks.Prepass | None) -> list[str]:
    """Settled blocks plus internal-use note for the rewrite."""
    if pre is None or not pre.blocks:
        return []
    return [*pre.blocks, internal_words.WRITER_NOTE]


def _options(pre: report_checks.Prepass | None) -> list[str]:
    if pre is None:
        return []
    names: list[str] = []
    for source in (pre.musts, pre.scores, pre.costs):
        names.extend((source or {}).get("options") or {})
    return list(dict.fromkeys(names))


def _leader(pre: report_checks.Prepass | None) -> str | None:
    """Top-scored option meeting every must-have."""
    if pre is None or not pre.scores:
        return None
    status = (pre.musts or {}).get("options") or {}
    eligible = {o: r for o, r in pre.scores["options"].items()
                if status.get(o, {}).get("status", "충족") == "충족"}
    if not eligible:
        return None
    best = max(r["total"] for r in eligible.values())
    leaders = [o for o, r in eligible.items() if r["total"] == best]
    return leaders[0] if len(leaders) == 1 else None


async def align(parts: list[dict], *, changed: set[int], instruction: str, request: str,
                material: str, pre: report_checks.Prepass | None, model: str,
                api_key: str) -> tuple[list[dict], dict, dict]:
    """Revised document checks, repaired once and rechecked: (parts, step, usage)."""
    from app.services import report

    run = [check(changed=changed, instruction=instruction, settled=(pre.figures if pre else None),
                 options=_options(pre), leader=_leader(pre)),
           report_checks.material_blanks, *report_checks.checks(pre)]
    known = "\n\n".join(x for x in (f"추가 요청: {instruction}", *blocks_for_rewrite(pre),
                                       material) if x)
    doc = Doc(parts, known, request, [], list(pre.figures) if pre else [])
    first, spent = await verify.find(doc, run)
    usage = dict(spent)
    if not first:
        return parts, {"type": "step", "id": "consistency", "status": "done",
                       "label": "앞뒤 맞춤 검사: 어긋남 없음"}, usage
    fixed, left, spent = await verify.verify_and_repair(
        doc, run, complete=report._complete, model=model, api_key=api_key,
        normalize=lambda secs: [
            {**sec, "content": internal_words.scrub(str(sec.get("content") or ""))}
            for sec in secs])
    for key in ("inputTokens", "outputTokens"):
        usage[key] = usage.get(key, 0) + int(spent.get(key, 0))
    before = [f.code for f in first]
    after = [f.code for f in left]
    labels = {**report_checks.LABELS, "cross_section": "절끼리 어긋난 값·권고"}
    detail = ", ".join(f"{labels.get(k, k)} {before.count(k)} → {after.count(k)}"
                       for k in dict.fromkeys(before + after))
    step = {"type": "step", "id": "consistency", "status": "error" if left else "done",
            "label": f"앞뒤 맞춤 검사: {len(before)}건 → {len(left)}건", "detail": detail[:600]}
    return fixed, step, usage


__all__ = ["Conflict", "align", "blocks_for_rewrite", "changes_content", "check", "conflicts",
           "dependent_note", "dependents", "settle_again"]
