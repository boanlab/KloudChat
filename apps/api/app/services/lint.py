"""Deterministic read-back checks on generated documents.

Visual rules are enforced by construction (the seed owns styling and `sanitise`
drops `class`/`style`), so only the words are checked. `P0` means the document
is wrong; `P1` means a person should look.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services import arithmetic

#: Text below this is an unwritten block, not a terse one.
_MIN_BLOCK_CHARS = 12

#: Slide bounds; the deck prompt asks for five bullets of forty characters.
_MAX_BULLETS = 7
_MAX_BULLET_CHARS = 45

#: A run of ideographs. Counted only when it is not a parenthesised gloss such as
#: `분산(分散)`, which academic and legal documents use legitimately.
_HANJA = re.compile(r"[\u4e00-\u9fff]+")
#: A gloss: ideographs inside brackets, wherever the brackets sit.
_GLOSSED = re.compile(r"[(\[][^)\]]*[\u4e00-\u9fff][^)\]]*[)\]]")


def _stray_hanja(text: str) -> str:
    """The first run of ideographs that is not a parenthesised gloss, or `''`."""
    glosses = [(m.start(), m.end()) for m in _GLOSSED.finditer(text)]
    for found in _HANJA.finditer(text):
        if any(start <= found.start() < end for start, end in glosses):
            continue
        return found.group(0)
    return ""


#: Template or draft leftovers nobody meant to submit.
_PLACEHOLDER = re.compile(
    r"lorem ipsum|\{\{|\[REPLACE\]|\bTODO\b|\bTBD\b|여기에\s*\S*\s*(입력|작성|붙여)|"
    r"내용을\s*(입력|작성)|항목\s*[1-3]\s*$|샘플\s*(텍스트|내용)",
    re.I | re.M,
)

#: Unsourced marketing figures. Narrow on purpose: ordinary "12% 증가" is fine.
_INVENTED_METRIC = re.compile(
    r"(\d+\s*배\s*(빠르|향상|개선|증가|절감|단축))|"
    r"(99[.,]9\s*%)|(100\s*%\s*(보장|정확|안전))|(24/7)|"
    r"(업계\s*(최고|최초|유일))",
)

#: Markdown emphasis left unrendered in HTML output: paired `**`, closed on the
#: same line, carrying a space or a Hangul syllable so `<code>` samples,
#: multiplication signs and footnote stars do not match.
_STRAY_MARKDOWN = re.compile(r"\*\*(?=\S)(?=[^*\n]*[\s\uac00-\ud7a3])[^*\n]{1,80}?(?<=\S)\*\*")

#: A line that begins as the JSON envelope the answer should have filled, which
#: is what a block cut off at the token limit looks like. Line-anchored: a JSON
#: fragment inside a sentence is legitimate.
_ENVELOPE = re.compile(r'^\s*\{\s*"(?:layout|body)"\s*:')

#: Filler words. `P1`: the fix is a rewrite, not a correction.
_FILLER = re.compile(
    r"혁신적|차별화된|최적의|획기적|압도적|최고의\s*품질|seamless|cutting[- ]edge|"
    r"game[- ]chang|state[- ]of[- ]the[- ]art",
    re.I,
)

#: Emoji leading a heading or a list item.
_LEADING_EMOJI = re.compile(r"^\s*[\U0001F300-\U0001FAFF✀-➿☀-⛿⬀-⯿]")

_TAGS = re.compile(r"<[^>]+>")

#: Where one line of an HTML block ends. `h3` is a column label, not an item,
#: so it is lifted out by `_LABEL` instead of counted here.
_LINE_END = re.compile(r"</(?:li|p|blockquote|td)>")
_LABEL = re.compile(r"<h3\b[^>]*>(.*?)</h3\s*>", re.S | re.I)


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    rule: str
    message: str
    #: Heading of the part concerned; empty for a whole-document finding.
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
    #: Headings inside the part (column labels, sub-headings). Read by the word
    #: rules, never counted as items by the shape rules.
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
        parts.append(Part(str(section.get("heading") or ""), [one for one in lines if one]))
    return parts


def from_slides(slides: list[dict]) -> list[Part]:
    """A JSON deck: one part per slide, reading every field that can hold words or numbers."""
    parts = []
    for slide in slides:
        lines = [str(b) for b in (slide.get("bullets") or []) if str(b).strip()]
        if body := str(slide.get("body") or "").strip():
            lines.append(body)
        # A table row is one line: a claim in a table is the row, not the cell.
        for row in slide.get("rows") or []:
            if line := " ".join(str(cell) for cell in row if str(cell).strip()):
                lines.append(line)
        for pair in slide.get("metrics") or []:
            if isinstance(pair, list) and len(pair) >= 2:
                lines.append(f"{pair[0]} {pair[1]}")
        if chart := slide.get("chart"):
            lines.extend(_chart_lines(chart))
        # Paired layouts read like table rows: the claim is the pair.
        for key in ("bands", "tiles", "timeline", "steps", "cards"):
            for pair in slide.get(key) or []:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    if line := " ".join(str(half) for half in pair[:2] if str(half).strip()):
                        lines.append(line)
        if slide.get("layout") in ("section", "agenda"):
            # A divider carries only its title, which is shorter than the emptiness floor.
            continue
        parts.append(Part(str(slide.get("title") or ""), lines))
    return parts


def _chart_lines(chart: dict) -> list[str]:
    """A chart's numbers as `<category> <series> <value><unit>` lines the checks can read."""
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

    `slides` enables the two shape rules. `limits` (`max_bullets`,
    `max_bullet_chars`) lets a template override the general bounds.
    """
    max_bullets = (limits or {}).get("max_bullets") or _MAX_BULLETS
    max_bullet_chars = (limits or {}).get("max_bullet_chars") or _MAX_BULLET_CHARS
    findings: list[Finding] = []
    seen: dict[str, str] = {}

    for part in parts:
        where = part.title
        text = part.text

        if len(re.sub(r"\s", "", text)) < _MIN_BLOCK_CHARS:
            findings.append(Finding("P0", "empty", "내용이 비어 있습니다.", where))
            continue

        if stray := _stray_hanja(text):
            findings.append(
                Finding(
                    "P0",
                    "hanja",
                    f"한국어 문장에 중국어 한자가 섞였습니다 — “{stray}”.",
                    where,
                )
            )
        for wrong in arithmetic.findings(text, where=where):
            findings.append(Finding("P0", "arithmetic", wrong["message"], where))
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
