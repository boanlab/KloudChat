"""디자인 지침: the look of a project's or agent's documents in words, in place of a theme.

The writers still read a design the way they read a theme (`design.prompt_block`, `tokens_of`,
`image_clause`): `BriefDesign` is that design made from the 디자인 지침 text. Its tokens come
from what the text says (`style_spec.parse`): a colour, a face, a look; the whole text reaches
the writer as guidance. An agent's 지침 comes before its project's.

`render` writes a theme (the old `design_systems` row) as such a text, for the move from themes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services import design, style_spec

#: a theme's look (`design.VISUAL_STYLES`) as the words `design.visual_style_for` reads
LOOK_WORDS = {
    "poster": "포스터처럼 강렬하게", "minimal": "미니멀하게", "dark": "다크 배경으로",
    "split": "색면 분할로", "warm": "따뜻한 색감으로", "mono": "흑백으로",
    "pastel": "파스텔 톤으로",
    "forest": "숲 느낌으로", "slate": "슬레이트 톤으로", "paper": "종이 서식처럼",
}
#: the line that carries the picture style for image prompts
_IMAGE = re.compile(r"^\s{0,4}그림\s{0,2}스타일\s{0,2}[:：]\s{0,4}(.+)$", re.M)


@dataclass
class BriefDesign:
    """A design read from 디자인 지침 text, in the shape the writers read a theme."""

    body: str
    tokens: dict[str, str] | None = None
    image_style: str = ""
    craft: list[str] = field(default_factory=list)
    name: str = "디자인 지침"
    #: read by `design.prompt_block`: the 지침 is guidance as written, not a theme's rules
    is_brief: bool = True


def render(tokens: dict | None, body: str = "", image_style: str = "",
           craft: list | None = None) -> str:
    """A theme as 디자인 지침 text that reads back to the same look."""
    raw = tokens or {}
    lines: list[str] = []
    if raw.get("accent"):
        lines.append(f"강조색 {str(raw['accent']).upper()}.")
    if raw.get("ink"):
        lines.append(f"글자색 {str(raw['ink']).upper()}.")
    if raw.get("font") in ("serif", "gothic"):
        lines.append("본문은 명조." if raw["font"] == "serif" else "본문은 고딕.")
    if (look := LOOK_WORDS.get(str(raw.get("visualStyle") or ""))):
        lines.append(f"모양은 {look}.")
    if body.strip():
        lines.append(body.strip())
    for key in design.craft_keys(craft):
        lines.append(design.CRAFT[key]["text"])
    if image_style.strip():
        lines.append(f"그림 스타일: {image_style.strip()}")
    return "\n".join(lines)[: style_spec.MAX_BRIEF]


def of(*texts: str | None) -> BriefDesign | None:
    """The design the 지침 texts make, first first (agent, then project); None for none."""
    given = [t.strip() for t in texts if t and t.strip()]
    if not given:
        return None
    spec = style_spec.merged(*(style_spec.parse(t) for t in given))
    looks = any(getattr(spec, key) for key in ("accent", "ink", "heading_face", "body_face",
                                                "visual_style"))
    tokens = None
    if looks:
        tokens = design.normalise_tokens({
            "accent": spec.accent or design.DOC_ACCENT, "ink": spec.ink or "",
            "font": spec.face_family("body") or spec.face_family("heading") or "",
            "visualStyle": spec.visual_style or "",
        })
    image = next((m.group(1).strip() for t in given for m in [_IMAGE.search(t)] if m), "")
    body = "\n\n".join(given)
    return BriefDesign(body=body, tokens=tokens, image_style=image)
