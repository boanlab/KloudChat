"""Writer prompts by model tier (`model_tier.strong`).

A strong writer gets only what it cannot know: the date, the tools and their results, the
structure the renderer reads, the person's settings and emergency guidance (round 7: the same
model wrote worse inside KloudChat than called directly, following writing rules to the
letter). Document prompts keep one template for both tiers; `for_writer` drops the rule
bullets below for a strong writer, so a rule is written once and its light form is a list
of openings here."""

from __future__ import annotations

import re

from app.services import model_tier

#: The one anti-fabrication line a strong writer gets in place of the many rules.
ANTI_FABRICATION = (
    "Don't invent specific facts, names, figures, quotations or sources; when you assume "
    "something, say so."
)

#: How the light setup is named in the turn's step log (evaluation reads it).
SETUP_LABEL = "가벼운 설정"
SETUP_DETAIL = "light setup"

#: Openings of document-prompt rule bullets ("- " at the start of a line) a strong writer does
#: not get: how to write, not what the renderer or the pipeline needs. Matched after an
#: optional "**", so a person's own text is never matched by accident.
_LIGHT_DROPS: tuple[str, ...] = (
    # report section and one-pass draft (`report._SECTION_PROMPT`, `report._DRAFT_PROMPT`)
    "Prose is the default: usually",
    "Use only the figures in",
    "Write at the level a practitioner",
    "Write each section at the level a practitioner",
    "Never invent a specific value",
    "Break into paragraphs where the point changes",
    "The reader has only this report",
    "Cite a source once for the sentences",
    "When the request names three or more things",
    "A business plan or proposal states its assumptions once",
    "A figure that shows a classification",
    "When the request says a part must include",
    "Do not leave a blank for a figure",
    "Do not add causes, reasons or judgements",
    "Each middle section answers a different question",
    "If the request has a table of measured data",
    "To state a difference, compute both numbers",
    "Numbers in a comparison table are the values computed earlier",
    # report outline (`report._OUTLINE_PROMPT`)
    "Sections do not overlap",
    "If the request lists three or more things to analyse",
    "A business plan or proposal puts a section near the front",
    # deck outline and draft (`deck._OUTLINE_PROMPT`, `deck._DRAFT_PROMPT`)
    "When a title promises subjects such as",
    "Do not make slides that frighten or rush",
    "Put on each slide only what that slide says",
    "Each slide says only its own thing",
    "Names from the material stay as in the original",
    "Figures only from the usable figures",
    "On a slide that teaches rules or commands",
    "If the request gives only the **name** of a rule",
    "Do not invent names, dates, amounts or figures absent",
    "No quiz, O/X or exercise slide",
    "Do not fill slides with topics the request lacks",
    # page outline (`page._OUTLINE_PROMPT`): the anti-fabrication line stands for it
    "요청에 없는 소재를 지어내지 마라.",
    # single-slide prompts (`deck._BULLETS_PROMPT` and the others)
    "A slide is a screen to look at",
    "Do not write made-up figures. Without a basis, write without numbers.",
    "Item names and identifiers from the material",
)

#: Rule bullets a strong writer gets in a shorter form: opening → replacement bullet.
_LIGHT_SWAPS: dict[str, str] = {
    "Prose is the default. Values by item go in tables": (
        "- Values by item go in tables (no blank lines between rows). Two or three numbers "
        "that conclude the document may go once in a ```kpi block (`값 | 이름` per line, at "
        "most 4); the one premise everything rests on once in a ```callout block (first line "
        "title, next line text). No other blocks."
    ),
}


#: Whole lines a strong writer gets in a shorter form (`report._facts_line` with no figures).
_LIGHT_LINES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?m)^쓸 수 있는 수치: none\. .*$"),
     "쓸 수 있는 수치: none — the request and material give no figures."),
)


def light(model: str | None) -> bool:
    """Whether `model` writes with the light setup."""
    return model_tier.strong(model)


def _opening(line: str) -> str:
    return line[2:].lstrip("*").lstrip()


def for_writer(prompt: str, *, light: bool) -> str:
    """`prompt` for its writer: unchanged for a small writer; for a strong one without the
    writing-rule bullets (each with its indented continuation lines), and with the one
    anti-fabrication line after the rules when any were dropped."""
    if not light or not prompt:
        return prompt
    for pattern, line in _LIGHT_LINES:
        prompt = pattern.sub(line, prompt)
    out: list[str] = []
    dropping = dropped = False
    for line in prompt.split("\n"):
        if line.startswith("- "):
            opening = _opening(line)
            swap = next((v for k, v in _LIGHT_SWAPS.items() if opening.startswith(k)), None)
            dropping = swap is not None or any(opening.startswith(p) for p in _LIGHT_DROPS)
            if dropping:
                if not dropped:
                    # where the first dropped rule stood, so it reads as one of the rules
                    out.append("- " + ANTI_FABRICATION)
                dropped = True
                if swap:
                    out.append(swap)
                continue
        elif dropping and line.startswith("  "):
            continue
        else:
            dropping = False
        out.append(line)
    return "\n".join(out)
