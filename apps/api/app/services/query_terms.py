"""Search-query hygiene: list parsing, named entities, staying on the request's subject."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

#: Hangul spellings of common tech names → Latin; the planner's own mapping covers the rest.
_HANGUL_LATIN = {
    "오픈라우터": "OpenRouter",
    "오픈에이아이": "OpenAI",
    "허깅페이스": "Hugging Face",
    "랭체인": "LangChain",
    "랭그래프": "LangGraph",
    "라마인덱스": "LlamaIndex",
    "앤트로픽": "Anthropic",
    "앤스로픽": "Anthropic",
    "제미나이": "Gemini",
    "딥시크": "DeepSeek",
    "미스트랄": "Mistral",
    "올라마": "Ollama",
    "챗지피티": "ChatGPT",
    "깃허브": "GitHub",
    "파이토치": "PyTorch",
    "텐서플로": "TensorFlow",
    "쿠버네티스": "Kubernetes",
    "퍼플렉시티": "Perplexity",
    "코파일럿": "Copilot",
}

#: Latin words that name a field or a kind of thing, not a specific entity.
_GENERIC = frozenset("""
llm llms ai ml nlp cv api apis gpu gpus sota rag top tier top-tier conference conferences journal
journals paper papers survey surveys review venue venues model models agent agents benchmark
benchmarks dataset datasets study studies research the and or of for with in on a an to using
use used experiment experiments evaluation pdf url
""".split())

_LATIN_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[.+-][A-Za-z0-9]+)*\+*")
_WORD = re.compile(r"[0-9A-Za-z가-힣]")
_VENUE_CUE = re.compile(
    r"top[\s-]?tier|학회|학술대회|저널|학술지|게재|proceedings|\bconference|\bjournal|\bvenue",
    re.I,
)

#: Shortest usable query: two letters or digits ("p", "[" are not queries).
MIN_QUERY_CHARS = 2


def query_list(value: object) -> list[str]:
    """Queries from a list, a JSON-encoded list, or newline-separated text; never characters."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                value = json.loads(text)
            except ValueError:
                value = re.findall(r'"([^"\n]{2,300})"', text)
        else:
            value = [line for line in re.split(r"\n+", text) if line.strip()]
    if not isinstance(value, list | tuple):
        return []
    out: list[str] = []
    for item in value:
        query = str(item).strip()[:200] if isinstance(item, str | int | float) else ""
        if usable(query) and query not in out:
            out.append(query)
    return out


def usable(query: str) -> bool:
    """Has at least `MIN_QUERY_CHARS` letters or digits."""
    return len(_WORD.findall(query or "")) >= MIN_QUERY_CHARS


def norm(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def _latin_names(text: str) -> list[str]:
    """Latin tokens that look like names: mixed case, digits, Capitalized, or acronyms."""
    names: list[str] = []
    for token in _LATIN_TOKEN.findall(text or ""):
        if token.lower() in _GENERIC or len(token) < 2:
            continue
        shaped = (
            any(c.isdigit() for c in token)
            or (any(c.isupper() for c in token[1:]) and any(c.islower() for c in token))
            or (token[0].isupper() and len(token) >= 3)
            or (token.isupper() and len(token) >= 2)
        )
        if shaped and token not in names:
            names.append(token)
    return names


def key_entities(request: str, proposed: Iterable[object] = ()) -> list[str]:
    """Named things the request is about, in Latin form.

    From the request's own Latin names, the small Hangul lookup, and planner proposals
    `{"name", "as_written"}` kept only when the name or its quoted span is in the request.
    """
    text = request or ""
    folded = norm(text)
    out: list[str] = []

    def add(name: str) -> None:
        name = name.strip()
        if name and usable(name) and norm(name) not in {norm(n) for n in out}:
            out.append(name)

    for item in proposed:
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            written = str(item.get("as_written") or "")
        elif isinstance(item, str):
            name, written = item, ""
        else:
            continue
        if not name or name.lower() in _GENERIC:
            continue
        if norm(name) in folded or (len(norm(written)) >= 2 and norm(written) in folded):
            add(name)
    for hangul, latin in _HANGUL_LATIN.items():
        if hangul in text:
            add(latin)
    for name in _latin_names(text):
        add(name)
    return out[:4]


def mentions(query: str, entity: str) -> bool:
    """`entity` in `query`, ignoring case, spaces and punctuation."""
    return bool(norm(entity)) and norm(entity) in norm(query)


def on_request(queries: Iterable[str], request: str, entities: list[str]) -> list[str]:
    """Usable queries on the request's subject.

    With entities, each query must name one; otherwise it must share a non-generic
    Latin term with the request when the request has any (Korean-only requests pass).
    """
    kept: list[str] = []
    anchors = {norm(t) for t in _latin_names(request) if t.lower() not in _GENERIC}
    for query in queries:
        if not usable(query) or query in kept:
            continue
        if entities:
            if not any(mentions(query, e) for e in entities):
                continue
        elif anchors and not anchors & {norm(t) for t in _LATIN_TOKEN.findall(query)}:
            continue
        kept.append(query)
    return kept


def keep_entities(query: str, request: str) -> str:
    """A model-written query naming none of the request's named things gets the first one."""
    # in an English request a capitalized word may only start a sentence (「Summarize」)
    hangul = bool(re.search(r"[가-힣]", request or ""))
    entities = [e for e in key_entities(request) if hangul or any(c.isdigit() for c in e)
                or any(c.isupper() for c in e[1:])]
    if not query.strip() or not entities or any(mentions(query, e) for e in entities):
        return query
    return f"{entities[0]} {query}"[:200]


def asks_for_venues(request: str) -> bool:
    """Wants work published at a conference or journal."""
    return bool(_VENUE_CUE.search(request or ""))


def entity_queries(request: str, entities: list[str], *, scholarly: bool) -> list[str]:
    """Fallback queries built from the request's entities."""
    if not entities:
        return []
    head = entities[0]
    topic = [
        t for t in _LATIN_TOKEN.findall(request or "")
        if t.lower() in {"llm", "llms", "ai", "agent", "agents", "rag"}
    ][:2]
    rest = " ".join(entities[1:3])
    out = [" ".join(x for x in (head, rest, *topic) if x)]
    if scholarly:
        out.append(" ".join(x for x in (head, *topic, "experiments evaluation") if x))
        if asks_for_venues(request):
            out += [f"{head} ACL EMNLP NAACL", f"{head} NeurIPS ICLR ICML"]
        out.append(f"{head} arXiv")
    return list(dict.fromkeys(q for q in out if usable(q)))


#: a particle closing a name in a list: 「네이버웍스를」「Copilot을」「경쟁사로」
_PARTICLE_END = re.compile(r"(?:을|를|와|과|으로|로|이랑|랑|은|는|도|만|의|에게|에)$")
#: a clause ending, not a name: 「만드는지」「정리하고」
_CLAUSE_END = re.compile(r"(?:지|다|요|고|며|서|면|데|해|줘)[.!?]?$")
_CLOSED = re.compile(r"[.!?:;。]$")
#: words that frame a list item, not name it
_FRAME_WORDS = frozenset("위주 대상 중심 관련 주요 최근 같은 등 및 기업용".split())


def _tokens(words: list[str]) -> list[str]:
    out = []
    for word in words:
        word = re.sub(r"[「」『』\"'.!?]", "", word).strip()
        if len(word) >= 2 and not word.isdigit() and word not in _FRAME_WORDS:
            out.append(word)
    return out


def listed_names(request: str) -> list[tuple[str, list[str]]]:
    """The first run of things a request lists one after another (「잔디, 플로우, 네이버웍스를
    대상으로」): (name as written, its words) each; a run of two to six, else none. What the
    request says in brackets is left out (a product's own description)."""
    # the ask only: the request's first paragraph, not a pasted memo's lines
    ask = re.split(r"\n[ \t]*\n", (request or "").strip(), maxsplit=1)[0]
    text = re.sub(r"\([^()\n]*\)", "", "\n".join(
        line for line in ask.split("\n") if not re.match(r"\s*(?:[-*•]|\[)", line)))
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        parts = [p.strip() for p in re.split(r"[,，、]", sentence)]
        if len(parts) < 2:
            continue
        run: list[list[str]] = []
        for i, part in enumerate(parts):
            words = part.split()
            if i == 0:
                lead: list[str] = []
                for word in reversed(words):
                    if _CLOSED.search(word) or _PARTICLE_END.search(word):
                        break
                    lead.insert(0, word)
                run.append(lead)
                continue
            stop = next((k for k, w in enumerate(words)
                         if _PARTICLE_END.search(w) or _CLOSED.search(w)), None)
            if stop is None and len(words) <= 4:
                run.append(words)
                continue
            if stop is not None and stop <= 3:
                run.append([*words[:stop], _PARTICLE_END.sub("", words[stop])])
            break
        items = [(" ".join(w), _tokens(w)) for w in run if w]
        if (2 <= len(items) <= 6 and all(t for _, t in items)
                and not any(_CLAUSE_END.search(name) for name, _ in items)):
            return items
    return []


def unsearched(request: str, queries: Iterable[str]) -> list[str]:
    """Names the request lists that no query has a word of (r10 SK-competitor-1: 네이버웍스,
    one of three competitors, never searched)."""
    listed = listed_names(request)
    # a query naming two of them is the request searched whole (r10 LFLF4 SK-competitor-1:
    # one query of the whole ask counted all three as searched); each is searched on its own
    asked = [q for q in (norm(q) for q in queries)
             if sum(1 for _, tokens in listed if any(norm(t) in q for t in tokens)) < 2]
    return [name for name, tokens in listed
            if not any(norm(t) and norm(t) in q for t in tokens for q in asked)]
