"""모델이 흘린 한자를 한글 독음으로 되돌린다.

The models this product runs on are trained heavily on Chinese and leak it one
word at a time: `全自動化 시스템`, `傳統的인 방화벽`, `動的 엔드포인트`. All
real samples from generated reports. To a Korean reader it is a typo; to a
Korean reviewer it is the clearest possible sign the document was written by a
machine, and on a submitted report that costs more than a weak argument does.

`lint` has caught these for a while. Catching is not the same as fixing: the
badge said P0 and the person had to press 모두 고치기, which spends a model call
per section to rewrite prose that was fine apart from one word. Nobody
submitting a 계획서 at midnight wants a homework assignment about ideographs.

So the reading is substituted where the reading is the answer, deterministically
and for free, and `lint` is left to speak only about what is left. Two rules
decide what "where the reading is the answer" means:

* **A parenthesised gloss is not a leak.** `분산(分散)`, a legal term, a name in
  its original script — these belong in exactly the documents this product is
  for, and `분산(분산)` would be worse than what it replaced. The judgement is
  `lint`'s own, imported rather than copied, so the two can never drift into
  disagreeing about what a leak is.
* **Code is not prose.** A Chinese string literal in a sample is the sample.

What this cannot do is know a wrong word from a right one. `試點` reads 시점,
which is a real Korean word meaning something else entirely — the leak was a
mistranslation before it was a script problem. So every substitution is
reported back, and the caller says so rather than claiming the document is
clean. That is the honest division: the machine fixes the script, the person
checks the meaning, and the document on screen is readable either way.
"""

from __future__ import annotations

import re

import hanja

from app.services.lint import _GLOSSED, _HANJA

#: The fences this product writes that hold prose rather than code: a figure
#: and its name, a step and what it is, a chart's own axis labels.
#:
#: Excluded from the protection below, because they are the document. `3개社`
#: reached a 보고서 through a ```steps block — protected as if it were a sample
#: of code, which it is not; it is a line somebody reads off the page.
_PROSE_FENCES = ("kpi", "steps", "chart", "table", "mermaid")

#: `<code>`/`<pre>` and Markdown's fences and spans. A Chinese identifier in a
#: sample is not a leak, and rewriting it breaks the sample.
_CODE = re.compile(
    r"<(code|pre)\b[^>]*>.*?</\1\s*>"
    rf"|```(?!\s*(?:{'|'.join(_PROSE_FENCES)})\b).*?```"
    r"|`[^`\n]+`",
    re.S | re.I,
)


def _protected(text: str) -> list[tuple[int, int]]:
    """Spans the substitution must not enter: glosses and code."""
    return [(m.start(), m.end()) for m in (*_GLOSSED.finditer(text), *_CODE.finditer(text))]


def read_back(text: str) -> tuple[str, list[str]]:
    """`(text, replaced)` — the prose with its stray ideographs read in Hangul.

    `replaced` is what was substituted, in the order it was found, so a caller
    can tell the reader which words to check the meaning of. Empty means
    nothing was touched, which is the ordinary case.
    """
    if not text or not _HANJA.search(text):
        return text, []

    keep = _protected(text)
    replaced: list[str] = []
    out: list[str] = []
    at = 0
    for found in _HANJA.finditer(text):
        if any(start <= found.start() < end for start, end in keep):
            continue
        run = found.group(0)
        read = hanja.translate(run, "substitution")
        if read == run:
            # No reading known. Leaving it is better than dropping it, and
            # `lint` still has something to say about it.
            continue
        out.append(text[at : found.start()])
        out.append(read)
        replaced.append(run)
        at = found.end()
    out.append(text[at:])
    return "".join(out), replaced


#: A digit, a space the tokenizer left, and the counter that belongs to it:
#: `주 3 일`, `5,000 만 원`, `1 단계`. Joined, because no Korean writer puts
#: that space there — it is the single most visible tell of machine text in a
#: document read at presentation zoom, 44 occurrences in one live 보고서.
#: Two shapes. A multi-syllable counter (단계, 개월, 만 원) is unambiguous —
#: no Korean word starts with one after a digit — so it joins whatever follows.
#: A single syllable (일, 개, 주…) could open a different word, so it joins
#: only when what follows is not another Hangul syllable, or is one of the
#: particles that attach to a counter: 3 일까지 → 3일까지, but 3 일반인 stays.
_COUNTER_LONG = re.compile(
    r"([0-9][0-9,.]*) (만 원|억 원|천 원|단계|시간|개월|가지|학점|과목|개소|개교)"
)
_COUNTER_SHORT = re.compile(
    r"([0-9][0-9,.]*) (일|명|원|건|개|주|년|월|회|차|기|분|초|점|배|층|호|번|매|부|장|쪽|판)"
    r"(?=[^가-힣]|$|까지|부터|간|째|씩|마다|이|은|을|의|에|로)"
)
#: A Latin letter or digit, a space, and the particle that belongs to the word
#: before it: `대안 A 는`, `ResNet 은`. Same tell, same join.
_PARTICLE = re.compile(
    r"([A-Za-z0-9)]) (은|는|이|가|을|를|의|와|과|로|으로|에|에서|도|만|보다|처럼|까지|부터)"
    r"(?![가-힣])"
)


def tidy_spacing(text: str) -> str:
    """The tokenizer's stray spaces closed up, and nothing else touched.

    Two joins only, each anchored on both sides, because a spacing rule that
    guesses is worse than the tell it removes: a counter after a digit and a
    particle after a Latin word are the two places Korean never has a space and
    the models this product runs on constantly put one.
    """
    if not text:
        return text
    text = _COUNTER_LONG.sub(r"\1\2", text)
    text = _COUNTER_SHORT.sub(r"\1\2", text)
    return _PARTICLE.sub(r"\1\2", text)


__all__ = ["read_back", "tidy_spacing"]
