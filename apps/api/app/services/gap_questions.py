"""빠진 정보 질문: what an agent asks before writing when the material does not answer it.

The questions are data, the same shape wherever they live:

- a 서식 carries the ones its format needs (`[[questions]]` in its `template.toml`);
- an agent carries its own (`Agent.gap_questions`, the agent form's 「빠진 정보 질문」; a
  shipped agent's come from `[[questions]]` in `app/agents/<key>.toml`, seeded onto its row).

A question is `{id, label, question, why, placeholder, detector}`. Its detector says whether
the request and material already answer it (`skills/gap-check/scripts/gaps.py`):

- `{"type": "builtin", "name": …}` — one of the shipped detectors (`builtin_names`), some of
  which trust what the server's extractors read (`readings`);
- `{"type": "keyword", "words": [...], "when": [...]}` — any word present answers it; `when`
  asks only when the request has one of those words;
- `{"type": "regex", "pattern": …, "when": …}` — a bounded pattern (`regex_ok`);
- `{"type": "judge"}` — the judge model reads the material once for all such questions and
  must quote the words that answer each (`judged`).

The gap check (`gap-check`, attached through 「빠진 정보 먼저 묻기」) asks at most four, on
one card, before the first draft.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

MAX_QUESTIONS = 12
DETECTOR_TYPES = ("builtin", "keyword", "regex", "judge")
_ID = re.compile(r"[a-z][a-z0-9_]{0,39}")
_TEXT_MAX = {"label": 40, "question": 300, "why": 400, "placeholder": 40}
_PATTERN_MAX = 300
_REPEAT_MAX = 60
_WORDS_MAX = 20

Complete = Callable[[str, list[dict], str, int], Awaitable[tuple[str, dict]]]


class QuestionError(ValueError):
    """A question the form or a file may not store; `code` is the wire detail."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: The built-in detectors as the agent form names them: what each finds in the material.
DETECTOR_LABELS = {
    "meeting_date": "회의 날짜(연도까지)",
    "attendees": "참석자",
    "action_dues": "실행 항목마다 기한",
    "client": "고객사 이름",
    "period": "사업 기간",
    "staffing": "투입 인력·단가·총액",
    "vat": "부가세 포함·별도",
    "prices": "비용으로 고를 때 대안별 금액",
    "musts": "필수 조건이라 했을 때 그 조건",
    "career_periods": "경력 기간(연월)",
    "career_metrics": "성과 수치",
    "length_rule": "자기소개서 글자 수 규칙",
    "deadline_basis": "기한 기준(며칠 이내)",
    "period_split": "비교할 기간의 구분",
    "denominator": "비율의 분모(무응답 포함 여부)",
    "letter_company": "지원 동기를 물을 때 지원 회사·직무",
}


def detector_catalogue() -> list[dict[str, str]]:
    """The built-in detectors the form offers, each with what it looks for."""
    return [{"name": n, "label": DETECTOR_LABELS.get(n, n)} for n in builtin_names()]


def builtin_names() -> tuple[str, ...]:
    """The shipped detectors a question may name."""
    from app.services import skill_scripts

    return tuple(skill_scripts.module("gap-check", "gaps").DETECTORS)


def regex_ok(pattern: str) -> bool:
    """Whether a pattern is safe to run on a person's text: short, every repeat bounded
    (at most `_REPEAT_MAX`), and no repeat inside another — so it cannot backtrack for long."""
    if not pattern or len(pattern) > _PATTERN_MAX:
        return False
    try:
        import re._parser as parser  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover — Python < 3.11
        import sre_parse as parser  # type: ignore[no-redef]
    try:
        parsed = parser.parse(pattern)
    except (re.error, OverflowError, RecursionError):
        return False
    repeats = {parser.MAX_REPEAT, parser.MIN_REPEAT}
    possessive = getattr(parser, "POSSESSIVE_REPEAT", None)
    if possessive is not None:
        repeats.add(possessive)

    def walk(items, inside: bool) -> bool:
        for op, av in items:
            if op in repeats:
                low, high, sub = av
                if high == parser.MAXREPEAT or high > _REPEAT_MAX or inside:
                    return False
                if not walk(sub, True):
                    return False
            elif op is parser.SUBPATTERN:
                if not walk(av[-1], inside):
                    return False
            elif op is parser.BRANCH:
                if not all(walk(branch, inside) for branch in av[1]):
                    return False
            elif op in (parser.ASSERT, parser.ASSERT_NOT):
                if not walk(av[1], inside):
                    return False
            elif op is parser.GROUPREF or op is getattr(parser, "GROUPREF_EXISTS", None):
                return False
        return True

    return walk(parsed, False)


def _words(raw: Any) -> list[str]:
    items = [raw] if isinstance(raw, str) else list(raw or []) if isinstance(raw, list) else []
    out = [" ".join(str(w).split())[:40] for w in items if str(w or "").strip()]
    return list(dict.fromkeys(out))[:_WORDS_MAX]


def _detector(raw: Any, n: int) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    kind = str(data.get("type") or "")
    if kind not in DETECTOR_TYPES:
        raise QuestionError("gap_detector_unknown", f"질문 {n}: 판단 방법을 고르세요.")
    if kind == "builtin":
        name = str(data.get("name") or "")
        if name not in builtin_names():
            raise QuestionError("gap_detector_unknown",
                                f"질문 {n}: 모르는 기본 판단 「{name[:40]}」")
        return {"type": kind, "name": name}
    if kind == "keyword":
        words = _words(data.get("words"))
        if not words:
            raise QuestionError("gap_keywords_empty",
                                f"질문 {n}: 답이 있다고 볼 낱말을 하나 이상 적으세요.")
        out: dict[str, Any] = {"type": kind, "words": words}
        if when := _words(data.get("when")):
            out["when"] = when
        return out
    if kind == "regex":
        pattern = str(data.get("pattern") or "")
        when = str(data.get("when") or "")
        for text in (pattern, *([when] if when else [])):
            if not regex_ok(text):
                raise QuestionError(
                    "gap_pattern_unsafe",
                    f"질문 {n}: 패턴은 {_PATTERN_MAX}자 안에서 반복 횟수를 {{0,{_REPEAT_MAX}}}처럼 "
                    "정해서 쓰고, 반복 안에 반복을 넣지 않습니다.")
        return {"type": kind, "pattern": pattern, **({"when": when} if when else {})}
    return {"type": "judge"}


def clean(raw: Any) -> list[dict[str, Any]]:
    """Questions as stored, each checked; raises `QuestionError` on the first bad one."""
    rows = raw if isinstance(raw, list) else []
    if len(rows) > MAX_QUESTIONS:
        raise QuestionError("gap_too_many", f"질문은 {MAX_QUESTIONS}개까지입니다.")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n, row in enumerate(rows, 1):
        data = row if isinstance(row, dict) else {}
        qid = str(data.get("id") or "").strip() or f"q{n}"
        if not _ID.fullmatch(qid) or qid in seen:
            raise QuestionError("gap_id_invalid",
                                f"질문 {n}: 영문 소문자로 시작하는 겹치지 않는 id 가 필요합니다.")
        seen.add(qid)
        texts = {key: " ".join(str(data.get(key) or "").split())[:limit]
                 for key, limit in _TEXT_MAX.items()}
        if not texts["question"]:
            raise QuestionError("gap_question_empty", f"질문 {n}: 물을 문장을 적으세요.")
        texts["label"] = texts["label"] or texts["question"][:40]
        out.append({"id": qid, **texts, "detector": _detector(data.get("detector"), n)})
    return out


def of_template(template) -> list[dict[str, Any]]:
    return [dict(q) for q in getattr(template, "questions", ()) or ()]


def for_format(doc_format: str | None) -> list[dict[str, Any]]:
    """The questions of the 서식 that fixes `doc_format`, for an agent bound to the format
    without a 서식 of its own."""
    from app.services import design_templates

    if not doc_format:
        return []
    for template in design_templates.all_templates():
        if template.doc_format == doc_format and template.questions:
            return of_template(template)
    return []


def resolve(*, own: list | None, base_key: str | None = None, template_id: str | None = None,
            doc_format: str | None = None) -> list[dict[str, Any]]:
    """What the agent asks before writing: its 서식's questions (or those of the 서식 bound to
    its format), then its own — the row's, else (none stored) the official agent it runs
    as (`base_key`). Each id once."""
    from app.services import design_templates, starter

    template = design_templates.get(template_id) if template_id else None
    shipped = of_template(template) if template is not None and template.questions \
        else for_format(doc_format)
    if own is None:
        own = list((starter.agent_spec(base_key) or {}).get("questions") or [])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*shipped, *own]:
        if isinstance(row, dict) and row.get("id") and row["id"] not in seen:
            seen.add(row["id"])
            out.append(dict(row))
    return out


# ── what the server reads for the detectors ───────────────────────────────


def readings(questions: list[dict], request: str, material: str, today) -> dict[str, Any]:
    """What the server's extractors already read for the built-in detectors the questions
    name, so the script trusts them over its own patterns."""
    from app.services import report_checks

    names = {str((q.get("detector") or {}).get("name") or "") for q in questions
             if (q.get("detector") or {}).get("type") == "builtin"}
    out: dict[str, Any] = {}
    if "meeting_date" in names:
        base, _ = report_checks.meeting_base(request, material, today)
        out["meeting_date"] = base.isoformat() if base else ""
    if names & {"client", "period", "staffing", "vat"}:
        terms = report_checks.rfp_terms(material, request) or {}
        if terms.get("clients"):
            out["clients"] = terms["clients"]
        out["period"] = bool(terms.get("end") or terms.get("months"))
        out["staff"] = bool(report_checks.staffing(material))
        out["priced"] = any(report_checks.price_line(line)
                            for line in f"{request}\n{material}".splitlines())
        out["vat"] = report_checks.price_basis(material)
    if "career_periods" in names:
        periods = report_checks.career_periods(material or request, request, today)
        if periods:
            out["periods"] = True
    return out


_JUDGE = """Below are a request, the material given with it, and questions the writer must \
have answered before writing. For each question, decide whether the request or the material \
already answers it, and copy the words that answer it exactly as written there. Leave \
"evidence" empty when nothing there answers it; a guess or a general statement does not.

Reply with JSON only:
{"answers": [{"id": "<question id>", "evidence": "<exact words from the request or material, \
or empty>"}]}

# Request
<<REQUEST>>

# Material
<<MATERIAL>>

# Questions
<<QUESTIONS>>
"""
_THINK = re.compile(r"<think>[\s\S]{0,60000}?</think>", re.I)
_MATERIAL_CHARS = 16000


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


async def judged(questions: list[dict], *, request: str, material: str, complete: Complete,
                 model: str, api_key: str, usage: dict | None = None) -> dict[str, bool]:
    """Whether the material answers each judge-type question: the model must quote the words
    that answer it, and code checks the quote is there. A question the model could not settle
    (no reply, no model) counts as answered: nothing is asked on a guess."""
    asked = [q for q in questions if (q.get("detector") or {}).get("type") == "judge"]
    if not asked:
        return {}
    if not model:
        return {str(q["id"]): True for q in asked}
    listed = "\n".join(f'- id "{q["id"]}": {q.get("question") or q.get("label")}' for q in asked)
    prompt = (_JUDGE.replace("<<REQUEST>>", (request or "")[:4000] or "(none)")
              .replace("<<MATERIAL>>", (material or "")[:_MATERIAL_CHARS] or "(none)")
              .replace("<<QUESTIONS>>", listed))
    try:
        reply, spent = await complete(model, [{"role": "user", "content": prompt}], api_key, 800)
    except Exception as exc:  # noqa: BLE001 — nothing is asked on a failed reading
        log.info("gap judge failed: %r", exc)
        return {str(q["id"]): True for q in asked}
    if usage is not None:
        for key in ("inputTokens", "outputTokens"):
            usage[key] = usage.get(key, 0) + int((spent or {}).get(key, 0))
    text = _THINK.sub("", reply or "")
    block = text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""
    try:
        data = json.loads(block, strict=False)
    except (json.JSONDecodeError, ValueError):
        return {str(q["id"]): True for q in asked}
    source = _flat(f"{request}\n{material}")
    quoted = {str(row.get("id") or ""): str(row.get("evidence") or "").strip().strip("「」\"'")
              for row in (data.get("answers") or []) if isinstance(row, dict)}
    return {str(q["id"]): len(quoted.get(str(q["id"]), "")) >= 2
            and _flat(quoted[str(q["id"])]) in source for q in asked}


def needs_judge(questions: list[dict]) -> bool:
    return any((q.get("detector") or {}).get("type") == "judge" for q in questions)


__all__ = ["DETECTOR_LABELS", "DETECTOR_TYPES", "MAX_QUESTIONS", "QuestionError",
           "builtin_names", "clean", "detector_catalogue",
           "for_format", "judged", "needs_judge", "of_template", "readings", "regex_ok",
           "resolve"]
