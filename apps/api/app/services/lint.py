"""Deterministic word checks on generated documents; P0 wrong, P1 look."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services import arithmetic, internal_words

#: below this: unwritten block
_MIN_BLOCK_CHARS = 12

#: slide bounds (prompt asks 5 × 40 chars)
_MAX_BULLETS = 7
_MAX_BULLET_CHARS = 45

#: ideograph run; bracketed glosses allowed
_HANJA = re.compile(r"[\u4e00-\u9fff]+")
#: ideographs inside brackets
_GLOSSED = re.compile(r"[(\[][^)\]]*[\u4e00-\u9fff][^)\]]*[)\]]")


def _stray_hanja(text: str) -> str:
    """First ideograph run outside a gloss, or `''`."""
    glosses = [(m.start(), m.end()) for m in _GLOSSED.finditer(text)]
    for found in _HANJA.finditer(text):
        if any(start <= found.start() < end for start, end in glosses):
            continue
        return found.group(0)
    return ""


#: template/draft leftovers
_PLACEHOLDER = re.compile(
    # 「여기에 입력하세요」 at line end only
    r"lorem ipsum|\{\{|\[REPLACE\]|\bTODO\b|\bTBD\b|"
    r"여기에\s*(?:\S+\s*)?(?:입력|작성|붙여\s*넣)"
    r"(?=(?:하세요|하십시오|해\s*주세요|하시오|하기|기)?\s*(?:$|[.…)\]」』<>:]|[-—]))|"
    r"내용을\s*(입력|작성)|항목\s*[1-3]\s*$|샘플\s*(텍스트|내용)",
    re.I | re.M,
)

#: unsourced marketing figures; deliberately narrow
_INVENTED_METRIC = re.compile(
    r"(\d+\s*배\s*(빠르|향상|개선|증가|절감|단축))|"
    r"(99[.,]9\s*%)|(100\s*%\s*(보장|정확|안전))|(24/7)|"
    r"(업계\s*(최고|최초|유일))",
)

#: unrendered `**` pairs on one line with a space or Hangul (not code/stars)
_STRAY_MARKDOWN = re.compile(r"\*\*(?=\S)(?=[^*\n]*[\s\uac00-\ud7a3])[^*\n]{1,80}?(?<=\S)\*\*")

#: line-anchored JSON envelope start: block cut at the token limit
_ENVELOPE = re.compile(r'^\s*\{\s*"(?:layout|body)"\s*:')

#: filler words; P1 (needs rewrite)
_FILLER = re.compile(
    r"혁신적|차별화된|최적의|획기적|압도적|최고의\s*품질|seamless|cutting[- ]edge|"
    r"game[- ]chang|state[- ]of[- ]the[- ]art",
    re.I,
)

#: emoji leading a heading or list item
_LEADING_EMOJI = re.compile(r"^\s*[\U0001F300-\U0001FAFF✀-➿☀-⛿⬀-⯿]")

_TAGS = re.compile(r"<[^>]{1,2000}>")

#: HTML line end; `h3` labels lifted by `_LABEL`
_LINE_END = re.compile(r"</(?:li|p|blockquote|td)>")
_LABEL = re.compile(r"<h3\b[^>]*>(.*?)</h3\s*>", re.S | re.I)


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    rule: str
    message: str
    #: part heading; empty = whole document
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
    """One heading and its lines, any surface."""

    title: str
    lines: list[str] = field(default_factory=list)
    #: inner headings; read by word rules, not counted as items
    labels: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join([self.title, *self.labels, *self.lines]).strip()


def _plain(markup: str) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub(" ", markup or "")).strip()


def from_sections(sections: list[dict]) -> list[Part]:
    """Markdown report: part per section, line per line."""
    parts = []
    for section in sections:
        body = str(section.get("content") or "")
        lines = [re.sub(r"^[-*\d.>#\s]+", "", one).strip() for one in body.splitlines()]
        parts.append(Part(str(section.get("heading") or ""), [one for one in lines if one]))
    return parts


def _line_key(line: str) -> str:
    """Line normalised for 「repeat」 comparison."""
    return re.sub(r"\W", "", re.sub(r"^[-*\d.>#\s]+", "", line).strip())


def drop_repeats(sections: list[dict]) -> tuple[list[dict], list[str]]:
    """`(sections, dropped)`: later verbatim prose repeats removed."""
    seen: set[str] = set()
    out: list[dict] = []
    dropped: list[str] = []
    for section in sections:
        body = str(section.get("content") or "")
        kept: list[str] = []
        cut: list[str] = []
        fenced = False
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("```"):
                fenced = not fenced
            key = _line_key(line)
            prose = not fenced and stripped and not stripped.startswith(("|", "#", "```"))
            if prose and len(key) >= 8 and key in seen:
                cut.append(stripped)
                continue
            kept.append(line)
        if cut and any(_line_key(x) for x in kept if not x.strip().startswith("#")):
            body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
            dropped += cut
        seen |= {k for k in map(_line_key, body.split("\n")) if len(k) >= 8}
        out.append({**section, "content": body} if body != section.get("content") else section)
    return out, dropped


def from_slides(slides: list[dict]) -> list[Part]:
    """JSON deck: part per slide, every word/number field."""
    parts = []
    for slide in slides:
        lines = [str(b) for b in (slide.get("bullets") or []) if str(b).strip()]
        if body := str(slide.get("body") or "").strip():
            lines.append(body)
        # table row = one claim
        for row in slide.get("rows") or []:
            if line := " ".join(str(cell) for cell in row if str(cell).strip()):
                lines.append(line)
        for pair in slide.get("metrics") or []:
            if isinstance(pair, list) and len(pair) >= 2:
                lines.append(f"{pair[0]} {pair[1]}")
        if chart := slide.get("chart"):
            lines.extend(_chart_lines(chart))
        # paired layouts read as rows
        for key in ("bands", "tiles", "timeline", "steps", "cards"):
            for pair in slide.get(key) or []:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    if line := " ".join(str(half) for half in pair[:2] if str(half).strip()):
                        lines.append(line)
                elif isinstance(pair, dict):
                    if line := " ".join(str(v) for v in pair.values() if str(v).strip()):
                        lines.append(line)
        # other pattern fields hold words too
        for key, value in slide.items():
            if key in _NOT_WORDS or key in ("bullets", "body", "rows", "metrics", "chart",
                                            "bands", "tiles", "timeline", "steps", "cards"):
                continue
            lines.extend(_leaf_lines(value))
        if slide.get("layout") in ("section", "agenda"):
            # divider: title only, below emptiness floor
            continue
        parts.append(Part(str(slide.get("title") or ""), lines))
    return parts


#: slide meta fields, not content
_NOT_WORDS = frozenset({
    "id", "layout", "title", "notes", "accent", "image", "diagram", "textScale", "number",
    "pattern", "theme", "visualStyle", "density", "factCheck", "status",
})


def _leaf_lines(value) -> list[str]:
    """Field words as lines; list entry, pair, or record = one line."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        words = [str(v) for v in value.values() if isinstance(v, (str, int, float))]
        line = " ".join(w for w in words if w.strip())
        return [line] if line else []
    if isinstance(value, (list, tuple)):
        out = []
        for entry in value:
            if isinstance(entry, (list, tuple)):
                if line := " ".join(str(c) for c in entry if str(c).strip()):
                    out.append(line)
            else:
                out.extend(_leaf_lines(entry))
        return out
    return []


def _chart_lines(chart: dict) -> list[str]:
    """Chart numbers as `<category> <series> <value><unit>` lines."""
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
    """HTML artifact: part per block."""
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
    """Every finding, worst first; `slides` enables shape rules, `limits` overrides bounds."""
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
        # per line: joined text hides line-end leftovers
        if found := _PLACEHOLDER.search("\n".join([part.title, *part.labels, *part.lines])):
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
        if found := internal_words.PLAN_NOTE.search(text):
            findings.append(
                Finding(
                    "P0",
                    "plan",
                    "쓸 내용 대신 작성 계획이나 지시문이 들어갔습니다 — "
                    f"“{found.group(0).strip()}”.",
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
