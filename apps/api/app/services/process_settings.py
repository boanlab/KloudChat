"""진행 과정 설정: what a document's writing does besides writing, as data.

A 서식 (`[process]` in its `template.toml`, or a person's template row) and an agent (the
agent form's 진행 과정 › 고급, `Agent.process`) carry the same settings; the report flow reads
nothing else (`app/pipelines/report.toml` lists every stage, and each optional one runs only
when these settings name it). The shipped 서식 declare exactly what their formats did, so a
person who sets the same values gets the same run.

The settings, each optional (an unset one inherits the 서식's, then the defaults below):

- `prepass` — 쓰기 전에 계산: built-in calculations, by name (`PREPASS`), in the order run;
- `date_base` — `meeting`: relative dates count from the meeting day (회의록), with no meeting
  day nothing is dated; `text`: from the date the material states;
- `stages` — named 서식 보정 stages (`STAGES`): the minutes' row repair, the career periods
  written one way, the page limit;
- `checks` / `judged` — attachable built-in checks by name (계산 확인, `CHECKS`, and 판정 검사,
  `JUDGED`): code checks repaired once and found again; judge calls shown, never rewritten;
- `amend` — what the document must carry, added by code or one model call (`AMENDS`);
- `head` — head values code fills (`HEADS`);
- `rules` — 검토 규칙 a person writes in words: one judge call quotes the sentences that break
  them; shown to the person, never rewritten;
- `structure` — declarative shape rules checked by code: `sections` (in order; the outline
  when `fixed_outline`), `tables` (a table with these columns), `summary_lines` (a 「핵심」
  callout of N lines at the top), `style = "gaejo"` (개조식: no prose lines, noun endings),
  `page_limit` (N pages, condensed to fit), `page_limit_rfp` (the RFP's limit, counted);
- `search` (false: 웹 검색 안 함), `fixed_outline`, `draws` (`when-asked`: 그림은 요청할 때만),
  `prepass_always`, `prepass_quiet`;
- `conflict_rule` — how the writer treats two values the material gives for one thing.

Every name is from a closed list the code ships, labelled as a built-in calculation; every text
is bounded. `clean` raises `ProcessError` on the first bad value.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

#: 쓰기 전에 계산: the pre-pass steps (`report_checks.PREPASS_STEPS`), by name.
PREPASS = {
    "date-calc": "날짜 계산(상대 날짜·납기)",
    "proposal-rfp": "제안요청서 조건 읽기(고객사·기간·쪽수)",
    "quote-calc": "견적 합계 계산(두 번 읽어 맞춰 봄)",
    "staffing": "투입 인력·MM 읽기",
    "compare-cost": "대안별 총비용 계산",
    "compare-check": "필수 조건·가중 점수 계산",
    "career-periods": "경력 기간 계산",
}
#: 서식 보정 stages a 서식 or an agent switches on, by registry name.
STAGES = {
    "minutes-repair": "실행 항목 칸 보정(출처·비고 열, 풀린 기한, 자료에 없는 담당·수량)",
    "minutes-reground": "고친 뒤 담당·수량 다시 확인",
    "career-periods-tidy": "경력 기간 표기 통일",
    "page-limit": "쪽수 제한 맞추기",
}
#: 계산 확인: the format code checks (`report_checks.CHECK_FAMILIES`), by name.
CHECKS = {
    "quote-totals": "견적 합계 확인",
    "proposal-schedule": "제안 일정 확인",
    "gaejo-style": "개조식 문체 확인",
    "lead-dates": "날짜 확인",
    "minutes-rows": "할 일의 담당자·기한 확인",
    "compare-cells": "비교표 빈칸·점수 확인",
    "career-numbers": "경력 수치 확인",
    "staff-counts": "투입 인원 확인",
    "material-blanks": "자료로 채울 빈칸 찾기",
}
#: 판정 검사: the judged checks (`report_checks.JUDGED_CHECKS`), by name.
JUDGED = {
    "judge-owners": "담당자 확인",
    "judge-bindings": "수치와 대상 연결 확인",
    "judge-repeats": "되풀이된 경험 찾기",
}
#: 빠진 것 채우기: what the document must carry, added after writing.
AMENDS = {
    "requirement-ids": "요구사항 ID 빠짐 채우기",
    "quote-validity": "견적 유효기간 적기",
    "option-status": "탈락·조건 미확인 표시",
    "conflict-values": "자료끼리 다른 값 나란히 적기",
    "unverified-options": "조건 미확인 대안과 확인할 것 적기",
}
#: Head values code fills from a calculation.
HEADS = {
    "meeting-date": "머리 일시를 회의 날짜로(날짜 계산)",
    "rfp-client": "표지 고객사를 제안요청서대로(제안요청서 조건 읽기)",
}
DATE_BASES = ("text", "meeting")
DRAWS = ("auto", "when-asked")
STYLES = ("", "gaejo")

#: Which part of a computation each check, stage and fill reads, said beside it in the form.
NEEDS = {
    "lead-dates": "date-calc", "minutes-rows": "date-calc", "quote-totals": "quote-calc",
    "proposal-schedule": "proposal-rfp", "compare-cells": "compare-check",
    "career-numbers": "career-periods", "staff-counts": "staffing",
    "minutes-repair": "date-calc", "career-periods-tidy": "career-periods",
    "meeting-date": "date-calc", "rfp-client": "proposal-rfp",
    "option-status": "compare-check", "unverified-options": "compare-check",
    "requirement-ids": "", "quote-validity": "",
}

MAX_PREPASS = 5
MAX_RULES = 8
MAX_SECTIONS = 12
MAX_TABLES = 3
MAX_COLUMNS = 8
_RULE_MAX = 300
_SECTION_MAX = 40
_COLUMN_MAX = 20
_CONFLICT_MAX = 600
MAX_PAGES = 30
MAX_SUMMARY = 4
#: Every key a stored setting may carry.
KEYS = ("prepass", "date_base", "stages", "checks", "judged", "amend", "head", "rules",
        "structure", "search", "fixed_outline", "draws", "prepass_always", "prepass_quiet",
        "conflict_rule")


class ProcessError(ValueError):
    """A setting the form or a file may not store; `code` is the wire detail."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Structure:
    sections: tuple[str, ...] = ()
    #: `{"columns": [...], "heading": "<words a heading has>"}` each.
    tables: tuple[dict, ...] = ()
    summary_lines: int = 0
    style: str = ""
    page_limit: int = 0
    page_limit_rfp: bool = False

    def wire(self) -> dict[str, Any]:
        return {"sections": list(self.sections), "tables": [dict(t) for t in self.tables],
                "summary_lines": self.summary_lines, "style": self.style,
                "page_limit": self.page_limit, "page_limit_rfp": self.page_limit_rfp}

    @property
    def empty(self) -> bool:
        return self == Structure()


@dataclass(frozen=True, slots=True)
class Process:
    prepass: tuple[str, ...] = ()
    date_base: str = "text"
    stages: tuple[str, ...] = ()
    #: The generic report's default: the dates a pre-pass worked out are checked.
    checks: tuple[str, ...] = ("lead-dates",)
    judged: tuple[str, ...] = ()
    amend: tuple[str, ...] = ()
    head: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    structure: Structure = field(default_factory=Structure)
    search: bool = True
    fixed_outline: bool = False
    draws: str = "auto"
    prepass_always: bool = False
    prepass_quiet: bool = False
    conflict_rule: str = ""

    # ── what the stages read ──────────────────────────────────────────

    def has_stage(self, name: str) -> bool:
        """Whether an optional stage runs: named, or (the page limit) implied by a limit."""
        if name == "page-limit" and (self.structure.page_limit or self.structure.page_limit_rfp):
            return True
        return name in self.stages

    @property
    def runs_prepass(self) -> bool:
        return bool(self.prepass) or self.prepass_always

    @property
    def code_checks(self) -> tuple[str, ...]:
        """The code checks in order: what the structure implies (개조식 문체), then the named."""
        implied = ("gaejo-style",) if self.structure.style == "gaejo" else ()
        return tuple(dict.fromkeys([*implied, *self.checks]))

    @property
    def outline(self) -> tuple[str, ...]:
        return self.structure.sections if self.fixed_outline else ()

    def wire(self) -> dict[str, Any]:
        return {"prepass": list(self.prepass), "date_base": self.date_base,
                "stages": list(self.stages), "checks": list(self.checks),
                "judged": list(self.judged), "amend": list(self.amend), "head": list(self.head),
                "rules": list(self.rules), "structure": self.structure.wire(),
                "search": self.search, "fixed_outline": self.fixed_outline,
                "draws": self.draws, "prepass_always": self.prepass_always,
                "prepass_quiet": self.prepass_quiet, "conflict_rule": self.conflict_rule}


DEFAULT = Process()


# ── reading a stored setting ──────────────────────────────────────────────


def _text(raw: Any, limit: int) -> str:
    return " ".join(str(raw or "").split())[:limit]


def _names(raw: Any, allowed: dict[str, str], code: str, what: str) -> tuple[str, ...]:
    rows = raw if isinstance(raw, list) else []
    out = []
    for item in rows:
        name = str(item or "")
        if name not in allowed:
            raise ProcessError(code, f"{what}: 모르는 항목 「{name[:40]}」")
        out.append(name)
    return tuple(dict.fromkeys(out))


def _int(raw: Any, low: int, high: int, code: str, what: str) -> int:
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        raise ProcessError(code, f"{what}: 숫자로 적으세요.") from None
    if value and not low <= value <= high:
        raise ProcessError(code, f"{what}: {low}~{high} 사이로 적으세요.")
    return value


def _structure(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    if "sections" in data:
        rows = [_text(s, _SECTION_MAX) for s in data.get("sections") or [] if _text(s, 1)]
        if len(rows) > MAX_SECTIONS:
            raise ProcessError("process_sections", f"목차는 {MAX_SECTIONS}개까지입니다.")
        out["sections"] = tuple(dict.fromkeys(rows))
    if "tables" in data:
        tables = []
        for n, row in enumerate(data.get("tables") or [], 1):
            row = row if isinstance(row, dict) else {}
            columns = [_text(c, _COLUMN_MAX) for c in row.get("columns") or [] if _text(c, 1)]
            if not 2 <= len(columns) <= MAX_COLUMNS:
                raise ProcessError("process_table",
                                   f"표 {n}: 열 이름을 2~{MAX_COLUMNS}개 적으세요.")
            table = {"columns": list(dict.fromkeys(columns))}
            if heading := _text(row.get("heading"), _SECTION_MAX):
                table["heading"] = heading
            tables.append(table)
        if len(tables) > MAX_TABLES:
            raise ProcessError("process_table", f"필수 표는 {MAX_TABLES}개까지입니다.")
        out["tables"] = tuple(tables)
    if "summary_lines" in data:
        out["summary_lines"] = _int(data.get("summary_lines"), 1, MAX_SUMMARY,
                                    "process_summary", "핵심 요약 줄 수")
    if "style" in data:
        style = str(data.get("style") or "")
        if style not in STYLES:
            raise ProcessError("process_style", "문체는 개조식만 고를 수 있습니다.")
        out["style"] = style
    if "page_limit" in data:
        out["page_limit"] = _int(data.get("page_limit"), 1, MAX_PAGES, "process_pages",
                                 "쪽수 제한")
    if "page_limit_rfp" in data:
        out["page_limit_rfp"] = bool(data.get("page_limit_rfp"))
    return out


def clean(raw: Any) -> dict[str, Any]:
    """A stored setting: only the keys it sets, each checked. `{}` sets nothing."""
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    if "prepass" in data:
        names = _names(data["prepass"], PREPASS, "process_prepass", "쓰기 전에 계산")
        if len(names) > MAX_PREPASS:
            raise ProcessError("process_prepass", f"쓰기 전 계산은 {MAX_PREPASS}개까지입니다.")
        # Run in the order the shipped 서식 run them.
        out["prepass"] = tuple(n for n in PREPASS if n in names)
    if "date_base" in data:
        base = str(data.get("date_base") or "text")
        if base not in DATE_BASES:
            raise ProcessError("process_date_base", "날짜 기준은 자료의 날짜나 회의 날짜입니다.")
        out["date_base"] = base
    if "stages" in data:
        names = _names(data["stages"], STAGES, "process_stage", "서식 보정")
        out["stages"] = tuple(n for n in STAGES if n in names)
    if "checks" in data:
        names = _names(data["checks"], CHECKS, "process_check", "계산 확인")
        out["checks"] = tuple(n for n in CHECKS if n in names)
    if "judged" in data:
        out["judged"] = _names(data["judged"], JUDGED, "process_judged", "판정 검사")
    if "amend" in data:
        names = _names(data["amend"], AMENDS, "process_amend", "빠진 것 채우기")
        out["amend"] = tuple(n for n in AMENDS if n in names)
    if "head" in data:
        out["head"] = _names(data["head"], HEADS, "process_head", "머리 채우기")
    if "rules" in data:
        rules = [_text(r, _RULE_MAX) for r in data.get("rules") or [] if _text(r, 1)]
        if len(rules) > MAX_RULES:
            raise ProcessError("process_rules", f"검토 규칙은 {MAX_RULES}개까지입니다.")
        out["rules"] = tuple(dict.fromkeys(rules))
    if "structure" in data:
        out["structure"] = _structure(data.get("structure"))
    for key in ("search", "fixed_outline", "prepass_always", "prepass_quiet"):
        if key in data:
            out[key] = bool(data[key])
    if "draws" in data:
        draws = str(data.get("draws") or "auto")
        if draws not in DRAWS:
            raise ProcessError("process_draws", "그림 설정을 고르세요.")
        out["draws"] = draws
    if "conflict_rule" in data:
        out["conflict_rule"] = _text(data.get("conflict_rule"), _CONFLICT_MAX)
    if out.get("fixed_outline") and not (out.get("structure") or {}).get("sections") \
            and "structure" in data:
        raise ProcessError("process_outline", "고정 목차를 쓰려면 목차를 적으세요.")
    return out


def stored_form(cleaned: dict[str, Any]) -> dict[str, Any]:
    """A cleaned setting as a row's JSON holds it (lists, not tuples)."""

    def plain(value: Any) -> Any:
        if isinstance(value, (tuple, list)):
            return [plain(v) for v in value]
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        return value

    return plain(cleaned)


def stored(raw: Any) -> dict[str, Any]:
    """`clean` for a value read back from a row or a file: a bad stored value sets nothing
    rather than failing the turn."""
    try:
        return clean(raw)
    except ProcessError:
        return {}


def apply(base: Process, settings: dict[str, Any] | None) -> Process:
    """`base` with what `settings` (cleaned) sets; the structure merges key by key."""
    if not settings:
        return base
    changes = {k: v for k, v in settings.items() if k in KEYS and k != "structure"}
    if "structure" in settings:
        changes["structure"] = dataclasses.replace(base.structure, **settings["structure"])
    return dataclasses.replace(base, **changes)


def of_template(template) -> dict[str, Any]:
    """A 서식's `[process]` as it is stored (shipped: its `template.toml`)."""
    return dict(getattr(template, "process", None) or {})


def governing_template(template_id: str | None, format_id: str | None):
    """The 서식 whose settings the document follows: the one picked when it fixes a format
    (a person's template always does), else the shipped one bound to the format."""
    from app.services import design_templates

    template = design_templates.get(template_id) if template_id else None
    if template is not None and template.doc_format:
        return template
    if format_id:
        return design_templates.for_format(format_id)
    return None


def resolve(*, template_id: str | None, format_id: str | None,
            own: dict[str, Any] | None = None) -> Process:
    """What a document's writing does: the defaults, then its 서식's settings, then the
    agent's own (`Agent.process`)."""
    template = governing_template(template_id, format_id)
    out = apply(DEFAULT, of_template(template) if template is not None else None)
    return apply(out, stored(own) if own else None)


def for_format(format_id: str | None) -> Process:
    """A stored document's settings when only its format is known (a revision)."""
    return resolve(template_id=None, format_id=format_id)


def catalogue() -> dict[str, Any]:
    """What the forms offer, each name with its label and what it reads."""

    def rows(names: dict[str, str]) -> list[dict[str, str]]:
        return [{"name": k, "label": v, "needs": NEEDS.get(k, "")} for k, v in names.items()]

    return {"prepass": rows(PREPASS), "stages": rows(STAGES), "checks": rows(CHECKS),
            "judged": rows(JUDGED), "amend": rows(AMENDS), "head": rows(HEADS),
            "dateBases": list(DATE_BASES), "draws": list(DRAWS),
            "limits": {"prepass": MAX_PREPASS, "rules": MAX_RULES, "sections": MAX_SECTIONS,
                       "tables": MAX_TABLES, "columns": MAX_COLUMNS, "pages": MAX_PAGES,
                       "rule": _RULE_MAX, "summary": MAX_SUMMARY},
            "defaults": DEFAULT.wire()}


__all__ = ["AMENDS", "CHECKS", "DEFAULT", "HEADS", "JUDGED", "KEYS", "PREPASS", "STAGES",
           "Process", "ProcessError", "Structure", "apply", "catalogue", "clean", "for_format",
           "governing_template", "of_template", "resolve", "stored", "stored_form"]
