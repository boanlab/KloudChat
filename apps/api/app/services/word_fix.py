"""Odd-word fixes on a self-hosted writer's Korean: a checker model names {from, to} pairs,
code applies only the exact-match ones that keep every number, citation, name and acronym; a
whole word swapped stands when the writer, asked, agrees its own word was wrong. Also one value
offered as its own alternative (「A 또는 A」) put once, by code."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable

from app.services import model_tier
from app.services.hangul import _PROSE_FENCES

log = logging.getLogger(__name__)

PROMPT = """You proofread Korean prose a writer model produced. List only its odd words:
- fused: English or Chinese glued into a Korean word (「안정적ly」 → 「안정적으로」,
  「检查한」 → 「점검한」);
- english: an everyday lowercase English word, not a technical term, standing where a Korean word
  belongs, given with the Korean word next to it (「sample 크기」 → 「표본 크기」);
- misspelling: a non-word or a misspelled word (「몇일」 → 「며칠」, 「되요」 → 「돼요」);
- wrong_word: a real word that cannot mean what the sentence needs, a literal mistranslation of
  an English term or a sound-alike slip, whose intended word is clear from the context or from a
  gloss beside it (「집단 간 공백」 where 격차 is meant).

List a wrong_word only when you are certain; when a real word could fit, leave it. Never list an
acronym or technical term written in English (RAG, BM25, API), English in parentheses after a
Korean term, an English technical term or a model's or tool's name even in lowercase, a person's,
organization's or product's name, a loanword in common use (데이터, 모델),
a number, a date or a citation mark such as [3]. Never swap a term for a different concept, change
a negation or a claim, or fix phrasing that is merely plain, long, awkward or repeated.

{candidates}Answer with JSON only: {{"fixes": [{{"from": "...", "to": "...", "kind": "..."}}]}}.
"from" is copied character for character from the text: the odd word with at most its particle
and one neighbouring word, never a whole sentence. "to" is the same span with only the odd word
corrected; every other word and ending in it stays as written. At most {limit} fixes;
{{"fixes": []}} when there is none.

Text:
{text}"""

CONFIRM_PROMPT = """A proofreader suggests swapping a word in Korean prose you wrote. For each
numbered suggestion, decide whether the swap gives the word the sentence means. Answer yes only
when the original word is wrong in that sentence (a slip, or a real word that cannot mean what is
meant); no when the original word is a correct, natural choice, even if the suggestion also fits.

{items}

Answer with JSON only: {{"swap": [numbers answered yes]}}."""

#: fixes asked for and applied per call
LIMIT = 12
#: whole-word swaps put to the writer per call
_CONFIRM_LIMIT = 6
#: context shown around a swap put to the writer
_CONTEXT = 70
#: below this many Hangul syllables: no prose to proofread
_MIN_HANGUL = 40
#: text sent to the checker
_MAX_CHARS = 8000

_HANGUL = re.compile(r"[가-힣]")
#: Chinese or kana
_HANJA = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'-]{0,40}")
_DIGITS = re.compile(r"\d")
#: Markdown and slide-draft (JSON) marks a word fix never adds, removes or crosses
_MARKS = re.compile(r"[\[\](){}<>|#*`_~=:\n\\/\"]")
#: spans no fix touches besides code fences: inline code, figure markers, links, glossed ideographs
_PROTECTED = re.compile(
    r"`[^`\n]{1,400}`|\[\[[^\]\n]{0,2000}\]\]|\]\([^)\s]{1,2000}\)|https?://[^\s)\]]{1,2000}"
    r"|[(\[][^)\]\n]{0,200}[\u4e00-\u9fff][^)\]\n]{0,200}[)\]]",
)
#: a slip out of Korean: a lowercase Latin word, Chinese or kana (an acronym such as GPU is none)
_FOREIGN = re.compile(r"(?<![A-Za-z])[a-z][a-z'-]{0,40}[a-z]|[\u4e00-\u9fff\u3040-\u30ff]")
_HANGUL_RUN = re.compile(r"[가-힣]{1,12}")
#: what may follow a word in the same Hangul run: a particle or a short ending
_ENDING = re.compile(
    r"(?:이|가|을|를|은|는|의|에|와|과|로|으로|도|만|에서|에게|까지|부터|보다|처럼|이나|나|이며|며|"
    r"이다|다|이고|고|라는|이라는|과의|와의|로서|로써|으로서|으로써|하는|한|하고|하여|해|했다|한다|"
    r"된|되는|되어|된다|적|적인|적으로|성|화|상|별|들|간|용)"
    r"(?:은|는|이|가|을|를|의|에|도|만|과|와|로|으로)?"
)
#: a parenthesized gloss a pair may carry through unchanged (「지표(metric)」)
_GLOSS = re.compile(r"\([^()\[\]\n]{1,60}\)")
#: negation: a fix never adds or drops one
_NEGATION = re.compile(r"아니|않|없|못")
#: Hangul glued to Latin or Chinese: a fused fragment, never the person's word
_FUSED = re.compile(
    r"[가-힣][A-Za-z\u4e00-\u9fff\u3040-\u30ff]|[A-Za-z\u4e00-\u9fff\u3040-\u30ff][가-힣]")


#: a Korean word with an English gloss: whether the word means the gloss is worth a look
_GLOSSED_WORD = re.compile(r"[가-힣]{1,12}\([a-z][a-z -]{1,40}\)")
#: punctuation around a token
_EDGES = "\"'“”‘’「」『』.,;:!?·…"
#: candidate spans named in the prompt
_MAX_CANDIDATES = 20


def candidates(text: str) -> list[str]:
    """Spans code finds worth a look, outside code, links and figure markers: a word mixing
    Hangul with Latin, Chinese or kana, Chinese or kana alone, a lowercase English word with the
    word after it in a Korean line, a Korean word with its English gloss."""
    keep = _protected(text)
    out: list[str] = []
    at = 0
    for line in text.splitlines(keepends=True):
        korean = _HANGUL.search(line) is not None
        tokens = [(m.start() + at, m.group(0)) for m in re.finditer(r"\S+", line)]
        for n, (start, raw) in enumerate(tokens):
            if any(a <= start < b for a, b in keep):
                continue
            word = raw.strip(_EDGES)
            if not word or _MARKS.search(word):
                continue
            mixed = _HANGUL.search(word) and _FOREIGN.search(word)
            if mixed or _HANJA.search(word):
                out.append(word)
            elif korean and re.fullmatch(r"[a-z][a-z-]{2,40}", word):
                after = tokens[n + 1][1].strip(_EDGES) if n + 1 < len(tokens) else ""
                out.append(f"{word} {after}".strip() if _HANGUL.search(after) else word)
        at += len(line)
    out += [m.group(0) for m in _GLOSSED_WORD.finditer(text)
            if not any(a <= m.start() < b for a, b in keep)]
    return list(dict.fromkeys(out))[:_MAX_CANDIDATES]


def prompt(text: str) -> str:
    """The checker's prompt for `text`, with the spans code found named first."""
    found = candidates(text)
    named = ("Code found these spans worth a look (a gloss: does the Korean word mean the "
             "English beside it?); list each that is odd, then any other odd word:\n"
             + "\n".join(f"- {c}" for c in found) + "\n\n") if found else ""
    return PROMPT.format(limit=LIMIT, candidates=named, text=text[:_MAX_CHARS])


def checker(writer: str, judge: str) -> str:
    """The model that proofreads: the judge, kept inside a strict-local writer's boundary (its
    `strict-local/` twin, else the writer itself)."""
    if writer.startswith("strict-local/") and not judge.startswith("strict-local/"):
        if judge.startswith("local/"):
            return "strict-local/" + judge.removeprefix("local/")
        return writer
    return judge or writer


def wanted(group: str, text: str) -> bool:
    """Whether a text gets the pass: a self-hosted writer's (`model_tier.group`), with Korean
    prose in it. Wrong words and misspellings have no code signature, so any prose of such a
    writer is proofread; an external writer's never is."""
    return group == model_tier.SELF_HOSTED and len(_HANGUL.findall(text or "")) >= _MIN_HANGUL


def parse(answer: str) -> list[tuple[str, str]]:
    """(from, to) pairs of the checker's JSON answer; [] when it is not one."""
    start, end = (answer or "").find("{"), (answer or "").rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(answer[start : end + 1])
    except json.JSONDecodeError:
        return []
    fixes = data.get("fixes") if isinstance(data, dict) else None
    if not isinstance(fixes, list):
        return []
    out = []
    for fix in fixes[: LIMIT * 2]:
        if not (isinstance(fix, dict) and isinstance(fix.get("from"), str)
                and isinstance(fix.get("to"), str)):
            continue
        # a fused or English slip has its Latin or Chinese in it
        if fix.get("kind") in ("fused", "english") and not _FOREIGN.search(fix["from"]):
            continue
        out.append((fix["from"], fix["to"]))
    return out


def _latin(text: str) -> list[str]:
    return _LATIN_WORD.findall(text)


def _distance(a: str, b: str) -> int:
    """Letters (Hangul syllables) inserted, dropped or changed between two short spans."""
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def refusal(old: str, new: str, given: str = "", glossed: bool = False,
            confirmed: bool = False) -> str:
    """Why the pair (old → new) is not applied, or "" when it may be. A Korean-only pair is a
    slip of one letter (「날자」 → 「날짜」); a whole word swapped stands only where an English
    gloss beside it pins the meaning (`glossed`) or the writer agreed its word was wrong
    (`confirmed`): the checker's reading alone is no evidence."""
    if not old.strip() or not new.strip() or old == new:
        return "empty or unchanged"
    if len(old) > 60 or len(new) > 60 or len(new) > 2 * len(old) + 6 or len(old.split()) > 4:
        return "not a word"
    # numbers and their counters stay as written (「3구성」 left alone too)
    if _DIGITS.search(old) or _DIGITS.search(new):
        return "digits"
    if _GLOSS.findall(old) != _GLOSS.findall(new):
        return "marks"
    if _MARKS.search(_GLOSS.sub("", old)) or _MARKS.search(_GLOSS.sub("", new)):
        return "marks"
    if _NEGATION.findall(old) != _NEGATION.findall(new):
        return "negation"
    # the odd word replaced, never a neighbouring word dropped
    if len(new.split()) < len(old.split()) and any(
            len(w) > 1 and _HANGUL.match(w) and w not in new for w in old.split()):
        return "word dropped"
    old_latin = _latin(old)
    # a name or acronym kept as written; no English brought in
    if any(w not in new for w in old_latin if any(c.isupper() for c in w)):
        return "name or acronym"
    if any(w not in old_latin for w in _latin(new)):
        return "English added"
    if _HANJA.search(new):
        return "Chinese added"
    if not _HANGUL.search(new):
        return "no Korean"
    if not _HANGUL.search(old) and not _HANJA.search(old):
        # English alone may be a title or a reference; the Korean around it shows it is prose
        return "no Korean around"
    slips = _FOREIGN.findall(old)
    if (not slips or all(w in new for w in slips)) and not glossed and not confirmed \
            and _distance(old, new) > 1:
        return "word swapped"
    # a word of the person's or the material's (a name, their term) is theirs to keep
    if given and any(len(w) > 1 and _HANGUL.match(w) and not _FUSED.search(w) and w not in new
                     and w in given for w in old.split()):
        return "the person's words"
    return ""


def _core(fix: tuple[str, str]) -> tuple[str, str, bool]:
    """A pair carrying one gloss through (「지표(metric)」 → 「척도(metric)」) as the word alone, so
    every place the word stands gets it."""
    old, new = fix
    gloss = _GLOSS.search(old)
    if gloss and gloss.end() == len(old) and new.endswith(gloss.group(0)) \
            and len(_GLOSS.findall(old)) == 1:
        core_old, core_new = old[: gloss.start()], new[: -len(gloss.group(0))]
        if core_old.strip() and core_new.strip() and core_old != core_new:
            return core_old, core_new, True
    return old, new, False


#: particles that change with the last syllable (vowel / consonant)
_PAIRS = {"를": ("를", "을"), "을": ("를", "을"), "는": ("는", "은"), "은": ("는", "은"),
          "가": ("가", "이"), "이": ("가", "이"), "와": ("와", "과"), "과": ("와", "과"),
          "로": ("로", "으로"), "으로": ("로", "으로")}
_PARTICLE = re.compile(r"(\([^()\n]{1,60}\))?(으로|를|을|는|은|가|이|와|과|로)(?![가-힣])")


def _agreeing(word: str, particle: str) -> str:
    """`particle` in the form `word`'s last syllable takes (「색인」 + 와 → 과)."""
    last = word[-1:]
    if not _HANGUL.match(last):
        return particle
    final = (ord(last) - 0xAC00) % 28
    vowel, consonant = _PAIRS[particle]
    if particle in ("로", "으로"):
        # ㄹ takes 로 like a vowel
        return vowel if final in (0, 8) else consonant
    return vowel if final == 0 else consonant


def _protected(text: str) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in _PROTECTED.finditer(text)]
    opened: int | None = None
    prose, at = False, 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            if opened is None:
                # prose fences (steps, table, ...) are read like the text around them
                opened = at
                prose = line.lstrip()[3:].strip().lower().startswith(_PROSE_FENCES)
            else:
                if not prose:
                    spans.append((opened, at + len(line)))
                opened = None
        at += len(line)
    if opened is not None and not prose:
        spans.append((opened, len(text)))
    return spans


def _bounded(text: str, start: int, end: int, whole: bool = False) -> bool:
    """Whether text[start:end] is not a piece of a larger Hangul or Latin word or English
    phrase; after a Hangul end anything may follow, for a `whole` word only a particle or ending."""
    before, after = text[start - 1 : start], text[end : end + 1]
    first, last = text[start], text[end - 1]
    fused = _FOREIGN.search(text[start:end]) is not None
    if not fused and _HANGUL.match(first) and _HANGUL.match(before):
        return False
    if whole and not fused and _HANGUL.match(last):
        # a gloss's word carried across: a particle or ending may follow, not the rest of a
        # longer word (「지수함수」)
        rest = _HANGUL_RUN.match(text, end)
        if rest and not _ENDING.fullmatch(rest.group(0)):
            return False
    if first.isascii() and first.isalpha() and before.isascii() and before.isalpha():
        return False
    if last.isascii() and last.isalpha() and after.isascii() and after.isalpha():
        return False
    # nor one word of an English phrase (「dense retrieval」 taken apart)
    near_before, near_after = text[max(start - 2, 0) : start].strip()[-1:], \
        text[end : end + 2].strip()[:1]
    if first.isascii() and first.isalpha() and near_before.isascii() and near_before.isalpha():
        return False
    return not (last.isascii() and last.isalpha() and near_after.isascii()
                and near_after.isalpha())


def apply(text: str, fixes: list[tuple[str, str]], given: str = "",
          confirmed: frozenset[tuple[str, str]] = frozenset()
          ) -> tuple[str, list[tuple[str, str]], list[tuple[str, str, str]]]:
    """(text, applied, refused): each pair replaced where it stands verbatim outside code, links,
    figure markers and glosses; a refused pair carries the reason. A pair in `confirmed` the
    writer agreed to may swap a whole word."""
    applied: list[tuple[str, str]] = []
    refused: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for old, new, glossed in sorted(map(_core, fixes), key=lambda f: -len(f[0])):
        if old in seen:
            continue
        seen.add(old)
        if len(applied) >= LIMIT:
            refused.append((old, new, "too many"))
            continue
        if why := refusal(old, new, given, glossed, (old, new) in confirmed):
            refused.append((old, new, why))
            continue
        keep = _protected(text)
        out, at, hits = [], 0, 0
        start = text.find(old)
        while start >= 0:
            end = start + len(old)
            if _bounded(text, start, end, glossed) and \
                    not any(a < end and start < b for a, b in keep):
                out.append(text[at:start])
                out.append(new)
                at, hits = end, hits + 1
                if glossed and (particle := _PARTICLE.match(text, end)):
                    # the particle after the word carried across (and its gloss) agrees with
                    # the word's last syllable
                    out.append(particle.group(1) or "")
                    out.append(_agreeing(new, particle.group(2)))
                    at = particle.end()
            start = text.find(old, end)
        if not hits:
            refused.append((old, new, "not in the text"))
            continue
        out.append(text[at:])
        text = "".join(out)
        applied.append((old, new))
    return text, applied, refused


Complete = Callable[[str, list[dict[str, str]], str, int], Awaitable[tuple[str, dict]]]


def confirm_prompt(text: str, pairs: list[tuple[str, str]]) -> str:
    """The writer's prompt for whole-word swaps, each with the text around its first place."""
    items = []
    for n, (old, new) in enumerate(pairs, 1):
        at = text.find(old)
        around = text[max(at - _CONTEXT, 0):at + len(old) + _CONTEXT].replace("\n", " ").strip()
        items.append(f"{n}. text: …{around}…\n   「{old}」 → 「{new}」")
    return CONFIRM_PROMPT.format(items="\n".join(items))


def parse_confirm(answer: str, pairs: list[tuple[str, str]]) -> frozenset[tuple[str, str]]:
    """The pairs the writer's JSON answer agrees to; none when it is not one."""
    start, end = (answer or "").find("{"), (answer or "").rfind("}")
    try:
        data = json.loads(answer[start:end + 1]) if 0 <= start < end else {}
    except json.JSONDecodeError:
        return frozenset()
    numbers = data.get("swap") if isinstance(data, dict) else None
    if not isinstance(numbers, list):
        return frozenset()
    return frozenset(pairs[n - 1] for n in numbers
                     if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(pairs))


async def fix(text: str, *, model: str, complete: Complete, api_key: str, given: str = "",
              writer: str = "") -> tuple[str, dict]:
    """(text, usage): `text` with its odd words fixed by one `model` call, and the whole-word
    swaps it refused put to the `writer` once; the text as it was on any failure."""
    usage = {"inputTokens": 0, "outputTokens": 0}
    try:
        answer, usage = await complete(
            model,
            [{"role": "user", "content": prompt(text)}],
            api_key,
            900,
        )
        fixed, applied, refused = apply(text, parse(answer), given)
    except Exception as exc:  # noqa: BLE001 - the text stands on any failure
        log.info("word fix failed: %r", exc)
        return text, usage
    swapped = [(old, new) for old, new, why in refused if why == "word swapped"][:_CONFIRM_LIMIT]
    if writer and swapped:
        try:
            said, more = await complete(
                writer, [{"role": "user", "content": confirm_prompt(fixed, swapped)}], api_key, 120)
            agreed = parse_confirm(said, swapped)
            usage = {k: usage.get(k, 0) + more.get(k, 0) for k in ("inputTokens", "outputTokens")}
        except Exception as exc:  # noqa: BLE001 - the refusals stand on any failure
            log.info("word swap confirm failed: %r", exc)
            agreed = frozenset()
        if agreed:
            fixed, more_applied, _ = apply(fixed, sorted(agreed), given, agreed)
            applied += more_applied
            refused = [r for r in refused if (r[0], r[1]) not in set(more_applied)]
    if applied or refused:
        log.info("word fix: %d applied %s, %d refused %s", len(applied), applied[:6],
                 len(refused), [(o, n, why) for o, n, why in refused[:6]])
    return fixed, usage


#: words that offer an alternative: 「A 또는 A」, 「A 혹은 A」, 「A나 A」
_ALTERNATIVE = re.compile(r" (?:또는|혹은|내지) |(?<=[가-힣])(?:이나|나) ")
#: what may end a value before its twin's end: a particle, then no more Hangul
_TWIN_END = re.compile(
    r"(?:으로|에서|까지|부터|에|의|로|은|는|이|가|을|를|와|과|도)?(?![가-힣A-Za-z0-9])")
#: longest value compared
_TWIN_MAX = 80
#: words a value may have
_TWIN_WORDS = 6
#: what may stand before a value
_TWIN_START = frozenset(" \t\n|(「『\"'")
_TWIN_STARTS = re.compile(r"[ \t\n|(「『\"']")


def twin_alternatives(text: str) -> str:
    """`text` with each value offered as its own alternative (「2026-10-21(수) 또는
    2026-10-21(수)」, 「구글폼이나 구글폼」) put once."""
    if not text or not _ALTERNATIVE.search(text):
        return text
    out, at = [], 0
    for m in _ALTERNATIVE.finditer(text):
        if m.start() < at:
            continue
        first = text[m.end():m.end() + 1]
        lo = max(m.start() - _TWIN_MAX, at)
        # within its cell or line, at most six words
        lo = max(lo, text.rfind("|", lo, m.start()) + 1, text.rfind("\n", lo, m.start()) + 1)
        words = text[lo:m.start()].split(" ")
        lo = m.start() - len(" ".join(words[-_TWIN_WORDS:]))
        starts = [lo] + [b.end() for b in _TWIN_STARTS.finditer(text, lo, m.start())]
        for start in starts:
            # a value starts a word and begins as its twin does
            if start >= m.start() - 1 or text[start] != first \
                    or (start and text[start - 1] not in _TWIN_START):
                continue
            size = m.start() - start
            value = text[start:m.start()]
            if not value.strip() or value != text[m.end():m.end() + size]:
                continue
            if _TWIN_END.match(text, m.end() + size):
                out.append(text[at:m.start()])
                at = m.end() + size
                break
    out.append(text[at:])
    return "".join(out)
