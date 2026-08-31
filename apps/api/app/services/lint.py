"""Checking what was written, rather than asking for it and hoping.

Three surfaces already carry the same rules in their prompts — do not invent
figures, do not pad, do not put emoji in headings — stated in `craft`, in the
per-surface system prompts, and in the starter skills. Nothing read the answer
back to see whether they held.

This does. It is deterministic and costs no model call, which is what makes it
affordable to run on every document: the check is free, and acting on it stays
explicit.

**Half of the rules OpenDesign's `lint-artifact` carries are absent here on
purpose.** Its P0 list is mostly visual — default indigo accents, two-stop
gradients, rounded cards with a coloured left border — because there the model
writes CSS. Here it cannot: the seed owns every colour and face, and
`design_templates.sanitise` drops `class` and `style` before the markup is
stored. Those rules are enforced by construction, and re-stating them would be
a check that can never fire.

What remains is what the model does choose: the words.

Severity is the same two-tier idea. `P0` means the document is wrong — a
placeholder nobody replaced, a figure nobody could have. `P1` means it reads
badly and a person should look.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Text below this is a block that never got written, not a terse one.
_MIN_BLOCK_CHARS = 12

#: A slide is a screen, not a page. Both bounds are the deck prompt's own — it
#: asks for five bullets of forty characters and is not always obeyed.
_MAX_BULLETS = 7
_MAX_BULLET_CHARS = 45

#: Chinese characters that Korean prose has no business carrying.
#:
#: The models this product runs on are trained heavily on Chinese and leak it
#: one word at a time: `全自動化`, `動的 엔드포인트`, `傳統的인 방화벽`, `試點
#: 프로젝트`. All real samples from generated reports. To a Korean reader it is
#: a typo; to a Korean reviewer it is the clearest possible sign the document
#: was written by a machine, and on a submitted report that costs more than a
#: weak argument does.
#:
#: The surface prompt asks the model not to, and a prompt is advice. This is
#: the read-back — deterministic, free, and it fires on the text that was
#: actually written rather than on the text that was asked for.
#:
#: **Parenthesised glosses are allowed on purpose.** `분산(分散)` in an academic
#: paper, a legal term, a name in its original script: banning the block
#: outright would be its own kind of wrong in exactly the documents this product
#: is for. So a run of ideographs counts only when nothing around it says it is
#: a gloss.
_HANJA = re.compile(r"[\u4e00-\u9fff]+")
#: A gloss: the ideographs sit inside brackets. The Korean that a gloss
#: explains is usually right before it — `분산(分散)` — but not always: a term
#: can be introduced after a space, or stand alone in a citation, and treating
#: `우회 [傳統]` as a leak because of one space is a false alarm on exactly the
#: documents that gloss the most.
_GLOSSED = re.compile(r"[(\[][^)\]]*[\u4e00-\u9fff][^)\]]*[)\]]")


def _stray_hanja(text: str) -> str:
    """The first run of ideographs that is not a parenthesised gloss, or `''`."""
    glosses = [(m.start(), m.end()) for m in _GLOSSED.finditer(text)]
    for found in _HANJA.finditer(text):
        if any(start <= found.start() < end for start, end in glosses):
            continue
        return found.group(0)
    return ""


#: Left in from a template or a draft. Unambiguous: none of these is something
#: somebody meant to submit.
_PLACEHOLDER = re.compile(
    r"lorem ipsum|\{\{|\[REPLACE\]|\bTODO\b|\bTBD\b|여기에\s*\S*\s*(입력|작성|붙여)|"
    r"내용을\s*(입력|작성)|항목\s*[1-3]\s*$|샘플\s*(텍스트|내용)",
    re.I | re.M,
)

#: Figures with the shape of a claim nobody sourced. Deliberately narrow: an
#: ordinary "12% 증가" is what a report is *for*, so only the round marketing
#: numbers and the multiplier-with-a-verb form are flagged.
_INVENTED_METRIC = re.compile(
    r"(\d+\s*배\s*(빠르|향상|개선|증가|절감|단축))|"
    r"(99[.,]9\s*%)|(100\s*%\s*(보장|정확|안전))|(24/7)|"
    r"(업계\s*(최고|최초|유일))",
)

#: Markdown emphasis that never became emphasis.
#:
#: The document surfaces ask for markup and a small model sometimes answers in
#: Markdown, so `**발표 노트**` reaches the screen with its own asterisks on it.
#: `sanitise` cannot help — there is no tag to drop, only characters — which
#: leaves this as the only place that can see it.
#:
#: Paired, closed on the same line, and carrying a space or a Hangul syllable
#: between them. That last condition is what keeps a `<code>` sample reading
#: `**bold**` out of it, along with a multiplication sign and a footnote star.
_STRAY_MARKDOWN = re.compile(r"\*\*(?=\S)(?=[^*\n]*[\s\uac00-\ud7a3])[^*\n]{1,80}?(?<=\S)\*\*")

#: A line that opens with the envelope the answer was supposed to fill.
#:
#: `sanitise` unwraps one that parses. One that does not — a block cut off at
#: the token limit — arrives whole, and this is what says so rather than
#: letting `{"layout":"bullets","body":"…` reach the screen.
#:
#: Anchored at the start of a line: a slide may legitimately show a JSON
#: fragment inside a sentence, and only a line that *begins* as the envelope is
#: the envelope.
_ENVELOPE = re.compile(r'^\s*\{\s*"(?:layout|body)"\s*:')

#: Words that fill a line without saying anything. `P1`: a person may have
#: meant one, and the fix is a rewrite rather than a correction.
_FILLER = re.compile(
    r"혁신적|차별화된|최적의|획기적|압도적|최고의\s*품질|seamless|cutting[- ]edge|"
    r"game[- ]chang|state[- ]of[- ]the[- ]art",
    re.I,
)

#: Emoji leading a heading or a list item — the icon tell.
_LEADING_EMOJI = re.compile(
    r"^\s*[\U0001F300-\U0001FAFF✀-➿☀-⛿⬀-⯿]"
)

_TAGS = re.compile(r"<[^>]+>")

#: Where one line of an HTML block ends. `h3` is not here: a column label is a
#: signpost over the items, not one of them, and counting it made an editorial
#: `split` slide of two labels over four items read as six lines — `crowded`
#: against a bound the slide was well inside.
_LINE_END = re.compile(r"</(?:li|p|blockquote|td)>")
#: The label itself, lifted out before the lines are cut so its words are still
#: read — a placeholder or an emoji in a column heading is exactly the kind of
#: thing this file is for — and so it does not run into the line after it.
_LABEL = re.compile(r"<h3\b[^>]*>(.*?)</h3\s*>", re.S | re.I)


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    rule: str
    message: str
    #: Which part of the document, by its heading. Empty for a whole-document
    #: finding, which is how the panel decides whether to offer a jump.
    where: str = ""

    def wire(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "rule": self.rule,
            "message": self.message,
            "where": self.where,
        }


@dataclass(slots=True)
class Part:
    """One heading and the lines under it, whatever surface it came from."""

    title: str
    lines: list[str] = field(default_factory=list)
    #: Headings *inside* the part — a column label, a sub-heading. Read like
    #: every other word, but never counted as an item: what the shape rules
    #: bound is how much a reader has to get through, and a two-word signpost
    #: is what makes the items under it readable rather than another of them.
    labels: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join([self.title, *self.labels, *self.lines]).strip()


def _plain(markup: str) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub(" ", markup or "")).strip()


def from_sections(sections: list[dict]) -> list[Part]:
    """A markdown report: one part per section, one line per markdown line."""
    parts = []
    for section in sections:
        body = str(section.get("content") or "")
        lines = [re.sub(r"^[-*\d.>#\s]+", "", one).strip() for one in body.splitlines()]
        parts.append(
            Part(str(section.get("heading") or ""), [one for one in lines if one])
        )
    return parts


def from_slides(slides: list[dict]) -> list[Part]:
    """A JSON deck: one part per slide.

    Every field a slide can hold words or numbers in, not just the two it used
    to. `bullets` and `body` were the whole vocabulary once; a slide can now be
    a table, a row of figures or a chart, and those are precisely the shapes
    that carry the numbers worth checking. Read as it was, a chart slide with
    eight invented quarters on it arrived at the checks as an empty slide and
    every rule passed.
    """
    parts = []
    for slide in slides:
        lines = [str(b) for b in (slide.get("bullets") or []) if str(b).strip()]
        if body := str(slide.get("body") or "").strip():
            lines.append(body)
        # A table row reads as one line: the checks are looking for claims, and
        # a claim in a table is the row rather than the cell.
        for row in slide.get("rows") or []:
            if line := " ".join(str(cell) for cell in row if str(cell).strip()):
                lines.append(line)
        for pair in slide.get("metrics") or []:
            if isinstance(pair, list) and len(pair) >= 2:
                lines.append(f"{pair[0]} {pair[1]}")
        if chart := slide.get("chart"):
            lines.extend(_chart_lines(chart))
        parts.append(Part(str(slide.get("title") or ""), lines))
    return parts


def _chart_lines(chart: dict) -> list[str]:
    """A chart's numbers as sentences the checks can read.

    Paired with their labels rather than listed, because a bare column of
    numbers is not a claim and a rule looking for one would find nothing. The
    unit rides along: `1분기 처리 건수 120건` is what a reader would have to
    have written to make the same assertion in prose, and it is that assertion
    the checks are for.
    """
    if not isinstance(chart, dict):
        return []
    categories = [str(c) for c in (chart.get("categories") or [])]
    unit = str(chart.get("unit") or "")
    lines: list[str] = []
    for item in chart.get("series") or []:
        name, values = (
            (item.get("name"), item.get("values"))
            if isinstance(item, dict)
            else (item[0], item[1])
            if isinstance(item, (list, tuple)) and len(item) >= 2
            else (None, None)
        )
        for position, value in enumerate(values or []):
            if position < len(categories):
                lines.append(f"{categories[position]} {name or ''} {value}{unit}".strip())
    return lines


def from_blocks(blocks: list[dict]) -> list[Part]:
    """An HTML artifact: one part per block, its markup reduced to lines."""
    parts = []
    for block in blocks:
        markup = str(block.get("html") or "")
        labels = [_plain(one) for one in _LABEL.findall(markup)]
        lines = [_plain(piece) for piece in _LINE_END.split(_LABEL.sub(" ", markup))]
        parts.append(
            Part(
                str(block.get("title") or ""),
                [one for one in lines if one],
                [one for one in labels if one],
            )
        )
    return parts


def check(
    parts: list[Part],
    *,
    slides: bool = False,
    limits: dict[str, int] | None = None,
) -> list[Finding]:
    """Every finding, worst first.

    `slides` turns on the two shape rules that only mean something on a screen
    somebody reads from the back of a room. `limits` is where a template's own
    promise overrides the general bound: a lecture deck asks for four items of
    twenty-five characters, and a checker that only knows about seven and
    forty-five would pass the slide the template itself calls too long.
    """
    max_bullets = (limits or {}).get("max_bullets") or _MAX_BULLETS
    max_bullet_chars = (limits or {}).get("max_bullet_chars") or _MAX_BULLET_CHARS
    findings: list[Finding] = []
    seen: dict[str, str] = {}

    for part in parts:
        where = part.title
        text = part.text

        if len(re.sub(r"\s", "", text)) < _MIN_BLOCK_CHARS:
            findings.append(
                Finding("P0", "empty", "내용이 비어 있습니다.", where)
            )
            continue

        if stray := _stray_hanja(text):
            findings.append(
                Finding(
                    # Wrong, not merely awkward. `_stray_hanja` has already let
                    # every legitimate use through — a gloss in brackets, a
                    # name — so what reaches here is a Chinese word standing in
                    # a Korean sentence where a Korean word exists: 独自 for
                    # 단독, 指的 for 가리키는. A reader takes it for a typo, and
                    # a submitted document does not get a second reading. Filed
                    # as P1 it showed on the badge as 볼 곳 — "have a look" —
                    # which is not what somebody about to hand this in needs to
                    # be told.
                    "P0",
                    "hanja",
                    f"한국어 문장에 중국어 한자가 섞였습니다 — “{stray}”.",
                    where,
                )
            )
        if found := _PLACEHOLDER.search(text):
            findings.append(
                Finding(
                    "P0",
                    "placeholder",
                    f"채우지 않은 자리가 남았습니다 — “{found.group(0).strip()}”.",
                    where,
                )
            )
        if any(_ENVELOPE.match(line) for line in part.lines):
            findings.append(
                Finding(
                    "P0",
                    "envelope",
                    "답이 내용이 아니라 그것을 담을 껍데기로 왔습니다 — "
                    "이 블록은 다시 써야 합니다.",
                    where,
                )
            )
        if found := _STRAY_MARKDOWN.search(text):
            findings.append(
                Finding(
                    "P0",
                    "markup",
                    f"마크다운이 렌더되지 않고 그대로 남았습니다 — “{found.group(0).strip()}”. "
                    "이 자리는 HTML 입니다.",
                    where,
                )
            )
        if found := _INVENTED_METRIC.search(text):
            findings.append(
                Finding(
                    "P0",
                    "invented-metric",
                    f"출처 없이 쓰기 어려운 수치입니다 — “{found.group(0).strip()}”. "
                    "근거를 밝히거나 숫자를 빼세요.",
                    where,
                )
            )
        if found := _FILLER.search(text):
            findings.append(
                Finding(
                    "P1",
                    "filler",
                    f"채움말이 있습니다 — “{found.group(0).strip()}”. "
                    "확인할 수 있는 사실로 바꾸세요.",
                    where,
                )
            )

        for line in [part.title, *part.labels, *part.lines]:
            if _LEADING_EMOJI.search(line):
                findings.append(
                    Finding("P1", "emoji", "제목이나 항목이 이모지로 시작합니다.", where)
                )
                break

        for line in part.lines:
            key = re.sub(r"\W", "", line)
            if len(key) < 8:
                continue
            if key in seen and seen[key] != where:
                findings.append(
                    Finding(
                        "P1",
                        "repeat",
                        f"“{seen[key]}”에서 한 말이 그대로 되풀이됩니다.",
                        where,
                    )
                )
                break
            seen.setdefault(key, where)

        if slides:
            if len(part.lines) > max_bullets:
                findings.append(
                    Finding(
                        "P1",
                        "crowded",
                        f"한 장에 {len(part.lines)}줄입니다. 화면은 읽는 글이 아닙니다.",
                        where,
                    )
                )
            elif any(len(line) > max_bullet_chars for line in part.lines):
                findings.append(
                    Finding("P1", "long-line", "한 줄이 화면에서 두 행을 넘깁니다.", where)
                )

    order = {"P0": 0, "P1": 1}
    return sorted(findings, key=lambda f: order.get(f.severity, 9))


def wire(findings: list[Finding]) -> list[dict[str, str]]:
    return [f.wire() for f in findings]


__all__ = [
    "Finding",
    "Part",
    "check",
    "from_blocks",
    "from_sections",
    "from_slides",
    "wire",
]
