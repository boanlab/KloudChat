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

    @property
    def text(self) -> str:
        return " ".join([self.title, *self.lines]).strip()


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
    """A JSON deck: one part per slide."""
    parts = []
    for slide in slides:
        lines = [str(b) for b in (slide.get("bullets") or []) if str(b).strip()]
        if body := str(slide.get("body") or "").strip():
            lines.append(body)
        parts.append(Part(str(slide.get("title") or ""), lines))
    return parts


def from_blocks(blocks: list[dict]) -> list[Part]:
    """An HTML artifact: one part per block, its markup reduced to lines."""
    parts = []
    for block in blocks:
        markup = str(block.get("html") or "")
        lines = [
            _plain(piece)
            for piece in re.split(r"</(?:li|p|blockquote|h3|td)>", markup)
        ]
        parts.append(
            Part(str(block.get("title") or ""), [one for one in lines if one])
        )
    return parts


def check(parts: list[Part], *, slides: bool = False) -> list[Finding]:
    """Every finding, worst first.

    `slides` turns on the two shape rules that only mean something on a screen
    somebody reads from the back of a room.
    """
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

        if found := _PLACEHOLDER.search(text):
            findings.append(
                Finding(
                    "P0",
                    "placeholder",
                    f"채우지 않은 자리가 남았습니다 — “{found.group(0).strip()}”.",
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

        for line in [part.title, *part.lines]:
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
            if len(part.lines) > _MAX_BULLETS:
                findings.append(
                    Finding(
                        "P1",
                        "crowded",
                        f"한 장에 {len(part.lines)}줄입니다. 화면은 읽는 글이 아닙니다.",
                        where,
                    )
                )
            elif any(len(line) > _MAX_BULLET_CHARS for line in part.lines):
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
