"""Design wishes in words → the style parameters every writer reads.

A project's or agent's 「디자인 지침」 and this turn's request are read by the same code: colour
words and hex codes, font names, table manners, density, page and numbering. What code does not
read stays in the text, which still reaches the model as guidance. Tone sentences (「공문체」,
「존댓말」) are not style: `tone` collects them for the prompt.

Precedence (`merged`): this turn's wishes, the agent's 디자인 지침, the project's, the pattern's
default. A form file decides before all of these where it says something (`design.md` §2.0).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.revise import _COLOURS

MAX_BRIEF = 2000

#: fonts a writer may name: shipped in the image or standard in Korean Office
FONTS: dict[str, str] = {
    "맑은 고딕": "맑은 고딕", "맑은고딕": "맑은 고딕", "malgun gothic": "맑은 고딕",
    "바탕": "바탕", "바탕체": "바탕", "batang": "바탕",
    "돋움": "돋움", "굴림": "굴림",
    "나눔고딕": "나눔고딕", "나눔 고딕": "나눔고딕", "nanumgothic": "나눔고딕",
    "나눔명조": "나눔명조", "나눔 명조": "나눔명조", "nanummyeongjo": "나눔명조",
    "나눔바른고딕": "나눔바른고딕", "나눔스퀘어": "나눔스퀘어",
    "pretendard": "Pretendard", "프리텐다드": "Pretendard",
    "함초롬바탕": "함초롬바탕", "함초롬 바탕": "함초롬바탕",
    "함초롬돋움": "함초롬돋움", "함초롬 돋움": "함초롬돋움",
    "noto sans kr": "Noto Sans KR", "본고딕": "Noto Sans KR",
    "noto serif kr": "Noto Serif KR", "본명조": "Noto Serif KR",
}
#: the face family a named font belongs to
_SERIF_FONTS = {"바탕", "나눔명조", "함초롬바탕", "Noto Serif KR"}

_FACE_WORD = r"(?P<face>명조|세리프|serif|바탕체?|고딕|산세리프|sans[- ]?serif|돋움)"
_HEADING = re.compile(r"(?:제목|헤드라인|머리|heading)[^.。\n]{0,12}?" + _FACE_WORD, re.I)
_BODY = re.compile(r"(?:본문|내용|body)[^.。\n]{0,12}?" + _FACE_WORD, re.I)
_ANY_FACE = re.compile(_FACE_WORD + r"\s{0,2}(?:체|계열|서체|글꼴|폰트)?", re.I)
_FONT_NAME = re.compile(
    "|".join(sorted(map(re.escape, FONTS), key=len, reverse=True)), re.I)

_HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")
_COLOUR_WORD = re.compile(
    r"(?P<colour>" + "|".join(sorted(map(re.escape, _COLOURS), key=len, reverse=True)) + r")"
    r"\s{0,2}(?:색|빛)?", re.I)
_INK_CUE = re.compile(r"글자\s{0,2}색|글씨\s{0,2}색|본문\s{0,2}색|text colou?r", re.I)

_COMPACT = re.compile(r"빽빽|촘촘|조밀|꽉\s{0,2}차|한\s{0,2}쪽에\s{0,2}많이|compact|dense", re.I)
_AIRY = re.compile(r"여백\s{0,2}(?:을\s{0,2})?(?:넉넉|많이|충분|넓게)|시원하게|여유\s{0,2}있게|"
                   r"듬성|airy|spacious|글머리\s{0,2}적게|적게\s{0,2}담", re.I)
_BULLETS = re.compile(r"(?:글머리|불릿|bullet)[^.\n]{0,12}?(\d{1,2})\s{0,2}개", re.I)

_TABLE = re.compile(r"표|table", re.I)
_HEADER_FILL_OFF = re.compile(
    r"머리\s{0,2}(?:행|줄)?[^.\n]{0,8}?(?:칠하지|색\s{0,2}없|채우지)", re.I)
_HEADER_FILL_ON = re.compile(r"머리\s{0,2}(?:행|줄)[^.\n]{0,10}?(?:칠|색|음영|채워|채우)", re.I)
_VRULES_OFF = re.compile(r"세로\s{0,2}선[^.\n]{0,6}?(?:없|빼|지우)", re.I)
_VRULES_ON = re.compile(r"세로\s{0,2}선[^.\n]{0,6}?(?:넣|있게|긋)", re.I)
_ZEBRA = re.compile(r"줄무늬|한\s{0,2}줄\s{0,2}걸러|zebra|banded", re.I)

_NUMBERING = (
    (re.compile(r"Ⅰ|로마\s{0,2}숫자|roman", re.I), "roman"),
    (re.compile(r"가\.\s{0,2}나\.|가나다", re.I), "korean"),
    (re.compile(r"공문\s{0,2}(?:식|번호)|1\.\s{0,2}가\.\s{0,2}1\)", re.I), "official"),
    (re.compile(r"1\.1|십진|decimal", re.I), "decimal"),
    (re.compile(r"번호\s{0,2}(?:없이|빼)", re.I), "none"),
)
_PAGE_SIZE = re.compile(r"\b(A4|A3|B5|Letter)\b|레터", re.I)
_LANDSCAPE = re.compile(r"가로\s{0,2}(?:방향|로\s{0,2}길게|형)|landscape", re.I)
_PORTRAIT = re.compile(r"세로\s{0,2}(?:방향|형)|portrait", re.I)
_RATIO = re.compile(r"16\s{0,1}:\s{0,1}9|4\s{0,1}:\s{0,1}3")

_TONE = re.compile(
    r"말투|어조|문체|공문체|개조식|존댓말|반말|합쇼체|해요체|~습니다|~함\b|톤\s{0,2}앤", re.I)
_TABLE_PART = re.compile(r"머리\s{0,2}행|세로\s{0,2}선")

#: clauses: split at sentence ends and commas, never inside 「1.1」 or 「3,000」
_CLAUSE = re.compile(r"(?<!\d)[.。!?,，;](?!\d)|\n")


@dataclass
class TableStyle:
    header_fill: bool | None = None
    #: header row fill colour
    colour: str | None = None
    vertical_rules: bool | None = None
    zebra: bool | None = None


@dataclass
class StyleSpec:
    """Concrete style parameters; None means 「not said」, and the next source decides."""

    accent: str | None = None
    ink: str | None = None
    #: `serif` / `gothic`, or a named face from `FONTS`
    heading_face: str | None = None
    body_face: str | None = None
    table: TableStyle = field(default_factory=TableStyle)
    #: `compact` / `normal` / `airy`
    density: str | None = None
    #: most bullets on one slide
    max_bullets: int | None = None
    #: `design.VISUAL_STYLES` look
    visual_style: str | None = None
    #: `doc_formats` numbering
    numbering: str | None = None
    page_size: str | None = None
    #: `portrait` / `landscape`
    orientation: str | None = None
    #: `16:9` / `4:3`
    ratio: str | None = None
    #: sentences about tone, for the prompt, not the writers
    tone: list[str] = field(default_factory=list)

    def wire(self) -> dict[str, Any]:
        """Set values only, for the screen and storage."""
        out: dict[str, Any] = {}
        for key, value in asdict(self).items():
            if key == "table":
                value = {k: v for k, v in value.items() if v is not None}
            if value not in (None, [], {}):
                out[key] = value
        return out

    @classmethod
    def of(cls, raw: dict[str, Any] | None) -> StyleSpec:
        raw = dict(raw or {})
        table = TableStyle(**{k: v for k, v in (raw.pop("table", None) or {}).items()
                              if k in TableStyle.__dataclass_fields__})
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(table=table, **known)

    def face_family(self, which: str = "body") -> str | None:
        """`serif` / `gothic` for a face, named or not."""
        face = self.body_face if which == "body" else self.heading_face
        if face in (None, "serif", "gothic"):
            return face
        return "serif" if face in _SERIF_FONTS else "gothic"


def _face(word: str) -> str:
    word = word.lower()
    if word in ("고딕", "산세리프", "sans-serif", "sans serif", "sansserif", "돋움"):
        return "gothic"
    return "serif"


def _colour(sentence: str) -> str | None:
    found = _HEX.search(sentence)
    if found:
        return found.group(0).upper()
    named = _COLOUR_WORD.search(sentence)
    return _COLOURS.get(named.group("colour").lower()) if named else None


def parse(text: str) -> StyleSpec:
    """The style parameters a 디자인 지침 or a request names."""
    from app.services import design

    spec = StyleSpec()
    if not (text or "").strip():
        return spec
    for sentence in (s.strip() for s in _CLAUSE.split(text[:MAX_BRIEF * 2])):
        if not sentence:
            continue
        if _TONE.search(sentence):
            spec.tone.append(sentence)
        colour = _colour(sentence)
        tabled = bool(_TABLE.search(sentence) or _TABLE_PART.search(sentence))
        if colour:
            if tabled and not re.search(r"강조|포인트|accent", sentence, re.I):
                spec.table.colour = spec.table.colour or colour
                if spec.table.header_fill is None:
                    spec.table.header_fill = True
            elif _INK_CUE.search(sentence):
                spec.ink = spec.ink or colour
            else:
                spec.accent = spec.accent or colour
        named = _FONT_NAME.search(sentence)
        if (head := _HEADING.search(sentence)) and spec.heading_face is None:
            spec.heading_face = _face(head.group("face"))
        if (body := _BODY.search(sentence)) and spec.body_face is None:
            spec.body_face = _face(body.group("face"))
        if named:
            face = FONTS.get(named.group(0).lower(), named.group(0))
            if re.search(r"제목|헤드라인|heading", sentence, re.I) and not re.search(
                    r"본문", sentence):
                spec.heading_face = face
            else:
                spec.body_face = face
        elif not head and not body and (any_face := _ANY_FACE.search(sentence)) and \
                spec.body_face is None and not _TONE.search(sentence):
            spec.body_face = _face(any_face.group("face"))
        if _COMPACT.search(sentence):
            spec.density = "compact"
        elif _AIRY.search(sentence):
            spec.density = "airy"
        if (bullets := _BULLETS.search(sentence)) and 1 <= int(bullets.group(1)) <= 12:
            spec.max_bullets = int(bullets.group(1))
        if tabled:
            if _HEADER_FILL_OFF.search(sentence):
                spec.table.header_fill = False
            elif _HEADER_FILL_ON.search(sentence):
                spec.table.header_fill = True
            if _VRULES_OFF.search(sentence):
                spec.table.vertical_rules = False
            elif _VRULES_ON.search(sentence):
                spec.table.vertical_rules = True
            if _ZEBRA.search(sentence):
                spec.table.zebra = True
        for pattern, numbering in _NUMBERING:
            if spec.numbering is None and pattern.search(sentence) and re.search(
                    r"번호|numbering|Ⅰ|가\.|1\.1", sentence, re.I):
                spec.numbering = numbering
        if (size := _PAGE_SIZE.search(sentence)) and spec.page_size is None:
            spec.page_size = "Letter" if (size.group(1) or "").lower() in ("letter", "") \
                else size.group(1).upper()
        if _LANDSCAPE.search(sentence):
            spec.orientation = "landscape"
        elif _PORTRAIT.search(sentence):
            spec.orientation = "portrait"
        if (ratio := _RATIO.search(sentence)) and spec.ratio is None:
            spec.ratio = "16:9" if "16" in ratio.group(0) else "4:3"
    # 「여백 넉넉히」 is density, not the minimal look
    style = design.visual_style_for(_AIRY.sub(" ", _COMPACT.sub(" ", text)))
    if style != "editorial":
        spec.visual_style = style
    return spec


def merged(*specs: StyleSpec | None) -> StyleSpec:
    """First said wins, in the order given (this turn, agent, project, pattern)."""
    out = StyleSpec()
    for spec in specs:
        if spec is None:
            continue
        for key in StyleSpec.__dataclass_fields__:
            if key == "table":
                for name in TableStyle.__dataclass_fields__:
                    if getattr(out.table, name) is None:
                        setattr(out.table, name, getattr(spec.table, name))
            elif key == "tone":
                out.tone += [s for s in spec.tone if s not in out.tone]
            elif getattr(out, key) is None:
                setattr(out, key, getattr(spec, key))
    return out


def tokens(spec: StyleSpec, base: dict[str, str] | None = None) -> dict[str, str]:
    """Design tokens (`design.normalise_tokens` shape) with the spec laid over `base`."""
    from app.services import design

    raw = dict(base or {})
    if spec.accent:
        raw["accent"] = spec.accent
    if spec.ink:
        raw["ink"] = spec.ink
    family = spec.face_family("body")
    if family:
        raw["font"] = family
    if spec.visual_style:
        raw["visualStyle"] = spec.visual_style
    return design.normalise_tokens(raw)
