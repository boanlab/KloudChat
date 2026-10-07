"""Form defects a finished report or deck must not carry, checked on the final artifact.

Each finding is `{"code", "where", "detail"}`; an empty list is a pass.
"""

from __future__ import annotations

import re
from typing import Any

from app.services import units

_IMAGE = re.compile(r"!\[[^\]]{0,2000}\]\(data:[^)]{0,9000000}\)")
_FENCE_GLUED = re.compile(r"(?m)^(?!\s*```).*\S[ \t]*```")
_STRAY_CAPTION = re.compile(r"(?m)^\s*표\s?\d{0,3}\s?[:：]|^\s*표\s?\d{1,3}\s*$")
_PIPE_ROW = re.compile(r"(?m)^(?!\s*\|)[^\n|`]+ \| [^\n|]+ \| [^\n]+$")
_LEFTOVER = re.compile(r"〔(?:그림|표)〕|\[\[\s*그림|〔그림 준비 중")
_MIXED = re.compile(r"[가-힣][Ѐ-ӿ]|[Ѐ-ӿ][가-힣]")
_BR = re.compile(r"<\s*br\s*/?\s*>", re.I)
_BROKEN = re.compile(r"\[\s*\]|\u2212|□")
_PLACEHOLDER = re.compile(
    r"\((?:미정|TBD|N/A)\)|\[(?:TBD|미정|xx+|XX+)\]|○○|XXX"
    r"|\(여기에[^)\n]{0,60}\)|\[여기에[^\]\n]{0,60}\]|여기에\s?[가-힣 ]{0,20}(?:입력|작성)하"
)
#: A model's JSON wrapper left in the words: 「{ "slide_notes": …」, a closing 「" }」.
_JSON_RESIDUE = re.compile(r'\{\s*\\?"[a-z_]{3,30}\\?"\s*:|\\?"\s*\}\s*$', re.M)
_INNARDS = re.compile(
    r"(?:매뉴얼|보고서|백서|가이드라인|가이드|안내서|지침)\s?(?:의\s?)?제\s?\d{1,3}\s?(?:장|절)"
)
_NUMBERED = re.compile(r"^\s*(?:\d{1,2}|[IVX]{1,4})\s?[.)．]")
_CODA = {"0": 1, "1": 2, "2": 0, "3": 1, "4": 0, "5": 0, "6": 1, "7": 2, "8": 2, "9": 0}


def _wrong_particle(number: str, particle: str) -> bool:
    coda = 1 if len(number) > 1 and number.endswith("0") else _CODA[number[-1]]
    if particle in ("로", "으로"):
        return (particle == "으로") != (coda == 1)
    return (particle in ("은", "이", "을", "과")) != (coda > 0)


def report_findings(
    sections: list[dict[str, Any]], material: str = "", settled: list | None = None
) -> list[dict[str, str]]:
    """Findings on a finished report's sections (Markdown bodies)."""
    from app.services import report  # the report's own readers, so the two never drift

    out: list[dict[str, str]] = []

    def add(code: str, where: str, detail: str = "") -> None:
        out.append({"code": code, "where": where, "detail": detail[:160]})

    whole = "\n\n".join(_IMAGE.sub("![…](…)", str(s.get("content") or "")) for s in sections)
    for stem, lost in lost_question_parts(material, whole):
        add("question_part_lost", stem, f"자료에 있던 {', '.join(lost)}이(가) 빠짐")
    for sentence, name, written, usual in scaled_metric_slips(whole):
        add("scaled_metric", name, f"{written:,.0f} — 다른 곳은 모두 {usual:,.0f}: {sentence[:80]}")
    for first, total, minutes in lesson_time_gaps(whole):
        add("lesson_time_gap", first[:60], f"활동 시간 합 {total}분, 차시는 {minutes}분")
    figures = re.findall(r"!\[(그림 (\d+))\.", whole)
    figures += re.findall(r"^\*(그림 (\d+))\.", whole, re.M)
    tables = re.findall(r"\*\*(표 (\d+))\.", whole)
    for kind, found in (("figure", figures), ("table", tables)):
        numbers = [int(n) for _, n in found]
        if numbers != list(range(1, len(numbers) + 1)):
            add(f"{kind}_numbering", "document", str(numbers))
    labels = (
        r"!\[[^\]]{0,2000}\]\([^)]{0,9000000}\)"
        r"|\*\*표 \d{1,3}\.[^*\n]{0,2000}\*\*|^\*그림 \d{1,3}\.[^\n]{0,2000}\*$"
    )
    body_only = re.sub(labels, "", whole, flags=re.M)
    for name, _ in figures + tables:
        if not re.search(re.escape(name) + r"(?![.\d])", body_only):
            add("unmentioned", name)
    particle = r"(?:그림|표) (\d{1,3})(으로|로|은|는|이|가|을|를|과|와)(?=[\s,.)])"
    for m in re.finditer(particle, body_only):
        if _wrong_particle(m.group(1), m.group(2)):
            add("particle", m.group(0))
    for index, sec in enumerate(sections):
        heading = str(sec.get("heading") or f"{index + 1}절")
        body = _IMAGE.sub("![…](…)", str(sec.get("content") or ""))
        if report._REFERENCE_HEADING.search(heading):
            continue
        if not body.strip():
            add("empty_section", heading)
            continue
        lines = body.split("\n")
        if sum(1 for line in lines if line.strip().startswith("```")) % 2:
            add("unbalanced_fence", heading)
        raw = str(sec.get("content") or "")
        if report.unfence_pictures(raw) != raw:
            add("picture_in_code", heading)
        rule = re.compile(r"^\s{0,16}\|?\s{0,16}:?-{2}")
        table_starts = [i for i, line in enumerate(lines[:-1])
                        if line.strip().startswith("|") and rule.match(lines[i + 1])]
        for start in table_starts:
            width = len(report._cells(lines[start]))
            for row in lines[start + 2:]:
                if not row.lstrip().startswith("|"):
                    break
                if not row.rstrip().endswith("|") and len(report._cells(row)) < width:
                    add("cut_table_row", heading, row)
            above = [line for line in lines[max(0, start - 3):start] if line.strip()]
            if not above or not above[-1].strip().startswith("**표 "):
                add("table_without_caption", heading, lines[start])
        for code, pattern in (("fence_glued", _FENCE_GLUED), ("stray_caption", _STRAY_CAPTION),
                              ("pipe_rows_not_a_table", _PIPE_ROW), ("leftover_mark", _LEFTOVER),
                              ("mixed_script", _MIXED), ("literal_br", _BR),
                              ("placeholder", _PLACEHOLDER), ("source_innards", _INNARDS),
                              ("broken_glyph_or_cite", _BROKEN),
                              ("prompt_talk", report._PROMPT_TALK),
                              ("fence_language_apart", report._LONE_LANGUAGE)):
            fenced = body
            if code == "pipe_rows_not_a_table":  # a ```kpi row is 「value | label」
                fenced = re.sub(r"(?ms)^```.*?^```", "", body)
            if m := pattern.search(fenced):
                add(code, heading, fenced[max(0, m.start() - 30): m.end() + 30])
        for block in re.split(r"\n\s*\n", body):
            text = block.strip()
            if (len(text) > 650 and "\n" not in text
                    and not re.match(r"^(?:\||#|>|[-*+]\s|\d{1,3}[.)]\s|```|!\[)", text)):
                add("long_paragraph", heading, f"{len(text)}자")
        heads = ("|", "!", "```", "#", "**표", "*그림")
        prose = [b for b in re.split(r"\n\s*\n", body)
                 if b.strip() and "\n" not in b.strip() and not b.strip().startswith(heads)]
        for block in prose:
            sentences = re.split(r"(?<=[.!?])\s+", block)
            cites = [report._cited(s) for s in sentences]
            for i in range(len(sentences) - 1):
                bare = report._TRAILING_CITE.sub("", sentences[i])
                if cites[i] and cites[i] == cites[i + 1] and not re.search(r"\d", bare):
                    add("repeated_citation", heading, sentences[i])
    # One row label, two different figures in two tables: one of them is wrong.
    seen: dict[str, tuple[str, str]] = {}
    for sec in sections:
        for label, value, _, _ in report._table_rows(str(sec.get("content") or "")):
            if not re.search(r"\d", value) or report._UNDECIDED.match(value):
                continue
            number = re.sub(r"[^\d.]", "", value)
            # Generic labels repeat by design (도입·전개·정리, month numbers); skip them.
            generic = r"[\d.]+(?:개월|차시|주|월|일)?(?:차)?|도입|전개|정리|합계|소계|계|평균"
            if re.fullmatch(generic, label):
                continue
            if label in seen and seen[label][0] != number:
                add("table_value_conflict", label, f"{seen[label][1]} / {value}")
            seen.setdefault(label, (number, value))
    from app.services import key_figures

    for sec in sections:
        if report._REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            continue
        for piece, figure, _ in key_figures.conflicts(str(sec.get("content") or ""), settled or []):
            add("settled_figure_changed", str(sec.get("heading") or ""),
                f"{figure.name} {figure.value}: {piece}")
    if material:
        for index, written, said in scaled_numbers(sections, material):
            add("carried_number_changed", str(sections[index].get("heading") or ""),
                f"{written} (자료: {said})")
    for sec in sections:
        for formula in units.slipped_formulas(_IMAGE.sub("", str(sec.get("content") or ""))):
            add("formula_unit_slip", str(sec.get("heading") or ""), formula)
    for index, sentence, why in report.numeric_issues(sections):
        heading = str(sections[index].get("heading") or "")
        add("numeric_inconsistency", heading, f"{why}: {sentence}")
    for group in report.metric_conflicts(sections):
        add("metric_conflict", "document", " / ".join(s[:60] for _, s in group))
    return out


def _strings(value: Any) -> list[str]:
    """Every string inside a slide, so a check reads the words and not JSON quoting."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


#: A counted or priced amount: 「12만 가구」, 「864억 원」, 「2,500박스」.
_COUNTED = re.compile(rf"(?P<amount>{units._TERM})\s?(?P<unit>가구|명|박스|개|건|원)")


def _counted(text: str) -> list[tuple[str, str, float]]:
    found = []
    for m in _COUNTED.finditer(text):
        try:
            found.append((m.group(0), m.group("unit"), units._ko_number(m.group("amount"))))
        except ValueError:
            continue
    return found


def scaled_numbers(slides: list[dict[str, Any]], material: str) -> list[tuple[int, str, str]]:
    """`(slide index, slide figure, material figure)` for amounts a power of ten off."""
    known: dict[str, dict[float, str]] = {}
    for written, unit, value in _counted(material or ""):
        if value:
            known.setdefault(unit, {}).setdefault(round(value, 2), written)
    out: list[tuple[int, str, str]] = []
    for index, slide in enumerate(slides):
        words = {k: v for k, v in slide.items() if k not in ("image", "id", "accent", "layout")}
        for written, unit, value in _counted(" ".join(_strings(words))):
            have = known.get(unit) or {}
            if not value or round(value, 2) in have:
                continue
            for theirs, said in have.items():
                if any(abs(value - theirs * 10**k) < 1e-6 * value
                       or abs(theirs - value * 10**k) < 1e-6 * theirs for k in (1, 2, 3, 4)):
                    out.append((index, written, said))
                    break
    return out


def deck_findings(slides: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Findings on a finished deck's slides (the stored JSON)."""
    out: list[dict[str, str]] = []

    def add(code: str, where: str, detail: str = "") -> None:
        out.append({"code": code, "where": where, "detail": detail[:160]})

    from app.services import deck as deck_service

    for index, slide in enumerate(slides):
        where = f"{index + 1}장 {str(slide.get('title') or '')[:30]}"
        layout = str(slide.get("layout") or "")
        if str(slide.get("body") or "").strip() == deck_service.UNWRITTEN:
            add("unwritten_slide", where)
        # Joined with spaces so a placeholder split across cells still matches.
        words = {k: v for k, v in slide.items() if k not in ("image", "diagram")}
        text = " ".join(_strings(words))
        if layout == "section" and _NUMBERED.match(str(slide.get("title") or "")):
            add("numbered_divider", where)
        if layout == "agenda" and any(_NUMBERED.match(str(b)) for b in slide.get("bullets") or []):
            add("numbered_agenda", where)
        if slide.get("diagram") and layout not in ("title", "section", "closing") and not (
            slide.get("bullets") or slide.get("body") or slide.get("cards") or slide.get("items")
        ):
            add("figure_without_words", where)
        text_checks = (("mixed_script", _MIXED), ("literal_br", _BR),
                       ("placeholder", _PLACEHOLDER), ("json_residue", _JSON_RESIDUE))
        for code, pattern in text_checks:
            if m := pattern.search(text):
                add(code, where, text[max(0, m.start() - 20): m.end() + 20])
    return out


__all__ = ["deck_findings", "report_findings"]


#: A numbered run a plan is made of: lessons, months, steps.
_SEQUENCES = (
    ("차시", re.compile(r"(?<!\d)([1-9])\s?차시")),
    ("월", re.compile(r"(?<!\d)(1[0-2]|[1-9])\s?월(?![가-힣])")),
    ("개월 차", re.compile(r"(?<!\d)([1-9]|1[0-2])\s?개월\s?차")),
    ("M", re.compile(r"(?<![A-Za-z0-9])M([1-9]|1[0-2])(?![0-9])")),
)


def _member(kind: str, n: int) -> str:
    if kind == "M":
        return f"M{n}"
    return f"{n} {kind}" if kind == "개월 차" else f"{n}{kind}"


def missing_sequence(material: str, written: str) -> list[tuple[str, list[str]]]:
    """`(kind, missing members)` for each run (1~3차시, M1~M6) the document drops from."""
    out = []
    for kind, pattern in _SEQUENCES:
        given = {int(n) for n in pattern.findall(material or "")}
        if len(given) < 3:
            continue
        run = sorted(given)
        if run[-1] - run[0] + 1 != len(run):  # not a consecutive run: dates, not a plan
            continue
        have = {int(n) for n in pattern.findall(written or "")}
        missing = [n for n in run if n not in have]
        if missing and len(missing) < len(run):
            out.append((kind, [_member(kind, n) for n in missing]))
    return out


#: A question's stem: 「**4.** (2차시) 이산화 탄소 분자를 옳게 설명한 것은?」.
_QUESTION_STEM = re.compile(
    r"^\s*(?:\*\*)?(?:서술형\s?)?\d{1,2}\s?[.)번]?(?:\*\*)?\s*(?:\([^)]{0,20}\)\s*)?"
    r"(?P<stem>[^\n①]{8,300}?(?:[?？]|시오\.?|은\?|는\?))", re.M)


def _key(text: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", text)[:24]


def question_choices(material: str) -> list[tuple[str, str, list[str], list[tuple[str, str]]]]:
    """`(stem, key, choice lines, [(mark, text)])` for each question with choices ①, ② …"""
    stems = list(_QUESTION_STEM.finditer(material or ""))
    out = []
    for i, m in enumerate(stems):
        key = _key(m.group("stem"))
        if len(key) < 8:
            continue
        end = stems[i + 1].start() if i + 1 < len(stems) else len(material)
        rest = material[m.end():end].split("\n")
        # The stem's own line may run on (「…것은? (관찰·하)」) or hold the choices.
        lines = ([rest[0][rest[0].index("①"):]] if "①" in rest[0] else []) + rest[1:9]
        block: list[str] = []
        for line in lines:
            # A choice line starts with its mark; an answer line like 「정답 ③」 does not.
            if re.match(r"\s{0,16}(?:[-*]\s{0,8})?[①-⑩]", line):
                block.append(line)
            elif block or line.strip():
                break
        choices = re.findall(r"([①-⑩])\s*([^①-⑩\n]{2,})", "\n".join(block))
        if len(choices) >= 3 and [c[0] for c in choices] == list("①②③④⑤⑥⑦⑧⑨⑩")[:len(choices)]:
            out.append((m.group("stem"), key, block, choices))
    return out


def lost_question_parts(material: str, written: str) -> list[tuple[str, list[str]]]:
    """`(stem, lost parts)` for each question whose stem `written` keeps but not all its choices."""
    if not material or not written:
        return []
    flat = re.sub(r"[^가-힣A-Za-z0-9①-⑩]", "", written)
    questions = question_choices(material)
    out = []
    for i, (stem, key, _, choices) in enumerate(questions):
        at = flat.find(key)
        if at < 0:
            continue  # a test the document did not carry, or rewrote: not this check's
        after = flat[at + len(key):]
        # Only as far as the next question: its choices are not this one's.
        following = questions[i + 1][1] if i + 1 < len(questions) else ""
        stop = after.find(following) if following else -1
        after = after[:stop] if stop >= 0 else after[:600]
        lost = [mark for mark, text in choices
                if _key(text)[:10] and (mark + _key(text)[:10]) not in after]
        if lost:
            out.append((stem[:60], [f"보기 {x}" for x in lost]))
    return out


#: Metrics a plan states as one amount each: 「CAC 5만 원」, 「TAM 7.2조 원」.
_NAMED_METRIC = re.compile(
    r"(?P<name>CAC|LTV|ARPU|ARPPU|TAM|SAM|SOM|객단가|구독료|고객\s?획득\s?비용|고객\s?생애\s?가치)"
    r"[^0-9\n.|×]{0,14}?(?P<value>\d[\d,]{0,15}(?:\.\d{1,6})?\s?(?:조|억|천만|만|천)?(?:\s?\d[\d,]{0,15}\s?(?:만|천))?)"
    r"\s?(?P<unit>원|가구|명)(?!\s?[×x*])"
)
#: A sentence about a total, not the per-unit figure: 「CAC 총액」, 「누적 CAC」.
_TOTAL_WORDS = re.compile(r"총|합계|누적|전체|연간|한 해|월간 총|예산|집행|×")


def scaled_metric_slips(text: str) -> list[tuple[str, str, float, float]]:
    """`(sentence, metric, written, usual)` for a named metric 100× or more off its usual value."""
    from app.services.units import _ko_number

    seen: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for line in (text or "").split("\n"):
        if line.lstrip().startswith(("```", "!", "#")):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\s\|\s", line):
            for m in _NAMED_METRIC.finditer(sentence):
                # A total's words (누적, 총액) sit right by the name.
                if _TOTAL_WORDS.search(sentence[max(0, m.start() - 6):m.start("value")]):
                    continue
                try:
                    value = _ko_number(m.group("value"))
                except ValueError:
                    continue
                if value > 0:
                    name = re.sub(r"\s", "", m.group("name"))
                    name = {"고객획득비용": "CAC", "고객생애가치": "LTV"}.get(name, name)
                    seen.setdefault((name, m.group("unit")), []).append((value, sentence.strip()))
    out = []
    for (name, _), entries in seen.items():
        values = [v for v, _ in entries]
        usual = max(set(values), key=values.count)
        if values.count(usual) < 2:
            continue
        for value, sentence in entries:
            ratio = value / usual
            if any(abs(ratio - 10**k) < 1e-6 * 10**k or abs(ratio - 10**-k) < 1e-6 * 10**-k
                   for k in range(2, 9)):
                out.append((sentence, name, value, usual))
    return out


#: A table column header with its unit: 「CAC(천 원)」.
_COLUMN_UNIT = re.compile(r"^(?P<name>[^()]{1,20}?)\s?\((?P<scale>천|만|백만|억)\s?원\)$")


def mislabelled_columns(text: str) -> list[tuple[str, str]]:
    """`(header, name)` for a 「(천 원)」-style column whose cells hold the prose's won amounts."""
    out = []
    lines = (text or "").split("\n")
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|") or i + 1 >= len(lines) or "---" not in lines[i + 1]:
            continue
        headers = [c.strip() for c in line.strip().strip("|").split("|")]
        rows = []
        for row in lines[i + 2:]:
            if not row.lstrip().startswith("|"):
                break
            rows.append([c.strip() for c in row.strip().strip("|").split("|")])
        for col, header in enumerate(headers):
            m = _COLUMN_UNIT.match(header)
            if not m:
                continue
            name = re.escape(m.group("name").strip())
            cells = {re.sub(r"[^\d]", "", r[col]) for r in rows
                     if col < len(r) and re.search(r"\d{4,}", r[col].replace(",", ""))}
            prose = {re.sub(r"[^\d]", "", v) for v in re.findall(
                rf"{name}[^0-9\n|]{{0,10}}(\d[\d,]{{3,15}})\s?원", text)}
            if cells & prose:
                out.append((header, m.group("name").strip()))
    return out


def lesson_time_gaps(text: str) -> list[tuple[str, int, int]]:
    """`(first row, summed minutes, lesson length)` for a lesson table off the set length."""
    length = re.search(r"(?:각\s?차시|한\s?차시|차시당|수업)[은는이가]?\s?(\d{2})\s?분", text or "")
    if not length:
        return []
    minutes = int(length.group(1))
    out = []
    lines = (text or "").split("\n")
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and "---" in lines[i + 1]:
            headers = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            col = next((k for k, h in enumerate(headers)
                        if re.fullmatch(r"(?:소요\s?)?시간(?:\(분\))?", h)), None)
            j, total, first = i + 2, 0, ""
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if col is not None and col < len(cells):
                    if t := re.fullmatch(r"(\d{1,2})\s?분?", cells[col]):
                        total += int(t.group(1))
                        first = first or lines[j].strip()
                j += 1
            table = "\n".join(lines[i:j])
            if (col is not None and total and total != minutes and abs(total - minutes) <= 20
                    and len(re.findall(r"도입|전개|정리|마무리|활동", table)) >= 2):
                out.append((first, total, minutes))
            i = j
        else:
            i += 1
    return out
