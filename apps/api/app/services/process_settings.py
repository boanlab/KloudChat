"""진행 과정 설정: 서식/agent process settings as data; `clean` raises `ProcessError`."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

#: 쓰기 전에 계산 steps (`report_checks.PREPASS_STEPS`)
PREPASS = {
    "date-calc": "날짜 계산(상대 날짜·납기)",
    "proposal-rfp": "제안요청서 조건 읽기(고객사·기간·쪽수)",
    "quote-calc": "견적 합계 계산(두 번 읽어 맞춰 봄)",
    "staffing": "투입 인력·MM 읽기",
    "compare-cost": "대안별 총비용 계산",
    "compare-check": "필수 조건·가중 점수 계산",
    "career-periods": "경력 기간 계산",
}
#: 서식 보정 stages by registry name
STAGES = {
    "minutes-repair": "실행 항목 칸 보정(출처·비고 열, 풀린 기한, 자료에 없는 담당·수량)",
    "minutes-reground": "고친 뒤 담당·수량 다시 확인",
    "career-periods-tidy": "경력 기간 표기 통일",
    "page-limit": "쪽수 제한 맞추기",
}
#: 계산 확인 code checks (`report_checks.CHECK_FAMILIES`)
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
    "count-labels": "지적 건수와 대상 확인",
}
#: 판정 검사 (`report_checks.JUDGED_CHECKS`)
JUDGED = {
    "judge-owners": "담당자 확인",
    "judge-bindings": "수치와 대상 연결 확인",
    "judge-repeats": "되풀이된 경험 찾기",
}
#: 빠진 것 채우기: added after writing
AMENDS = {
    "requirement-ids": "요구사항 ID 빠짐 채우기",
    "quote-validity": "견적 유효기간 적기",
    "option-status": "탈락·조건 미확인 표시",
    "conflict-values": "자료끼리 다른 값 나란히 적기",
    "unverified-options": "조건 미확인 대안과 확인할 것 적기",
}
#: head values filled from a calculation
HEADS = {
    "meeting-date": "머리 일시를 회의 날짜로(날짜 계산)",
    "rfp-client": "표지 고객사를 제안요청서대로(제안요청서 조건 읽기)",
}
DATE_BASES = ("text", "meeting")
DRAWS = ("auto", "when-asked")
STYLES = ("", "gaejo")

#: computation part each check/stage/fill reads (form hint)
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
#: storable keys
KEYS = ("prepass", "date_base", "stages", "checks", "judged", "amend", "head", "rules",
        "structure", "search", "deep_search", "code_calc", "fixed_outline", "draws",
        "prepass_always",
        "prepass_quiet", "conflict_rule")
#: list settings a turned-on skill adds to (never replaces) the 서식's
LIST_KEYS = ("prepass", "stages", "checks", "judged", "amend", "head", "rules")
#: key in an agent's settings carrying its skills' list additions (`skill_turn.Brought`)
ADDED = "added_by_skills"


class ProcessError(ValueError):
    """Unstorable setting; `code` = wire detail."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Structure:
    sections: tuple[str, ...] = ()
    #: `{"columns": [...], "heading": "..."}` each
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
    #: generic report default: pre-pass dates checked
    checks: tuple[str, ...] = ("lead-dates",)
    judged: tuple[str, ...] = ()
    amend: tuple[str, ...] = ()
    head: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    structure: Structure = field(default_factory=Structure)
    search: bool = True
    #: search with twice the queries and sources (a research skill on a document)
    deep_search: bool = False
    #: derived values computed by code before writing even when the request names no
    #: calculation (a calculation skill on a document)
    code_calc: bool = False
    fixed_outline: bool = False
    draws: str = "auto"
    prepass_always: bool = False
    prepass_quiet: bool = False
    conflict_rule: str = ""


    def has_stage(self, name: str) -> bool:
        """Whether an optional stage runs (named, or page limit implied)."""
        if name == "page-limit" and (self.structure.page_limit or self.structure.page_limit_rfp):
            return True
        return name in self.stages

    @property
    def runs_prepass(self) -> bool:
        return bool(self.prepass) or self.prepass_always

    @property
    def code_checks(self) -> tuple[str, ...]:
        """Code checks in order: structure-implied (개조식), then named."""
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
                "search": self.search, "deep_search": self.deep_search,
                "code_calc": self.code_calc,
                "fixed_outline": self.fixed_outline,
                "draws": self.draws, "prepass_always": self.prepass_always,
                "prepass_quiet": self.prepass_quiet, "conflict_rule": self.conflict_rule}


DEFAULT = Process()


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
    """Stored setting: only set keys, each checked; `{}` sets nothing."""
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    if "prepass" in data:
        names = _names(data["prepass"], PREPASS, "process_prepass", "쓰기 전에 계산")
        if len(names) > MAX_PREPASS:
            raise ProcessError("process_prepass", f"쓰기 전 계산은 {MAX_PREPASS}개까지입니다.")
        # shipped 서식 order
        out["prepass"] = tuple(n for n in PREPASS if n in names)
    if "date_base" in data:
        base = str(data.get("date_base") or "text")
        if base not in DATE_BASES:
            raise ProcessError("process_date_base", "날짜 기준은 자료의 날짜나 회의 날짜입니다.")
        out["date_base"] = base
    if "stages" in data:
        names = _names(data["stages"], STAGES, "process_stage", "값 맞추기")
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
    for key in ("search", "deep_search", "code_calc", "fixed_outline", "prepass_always",
                "prepass_quiet"):
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
    """Cleaned setting as row JSON (lists)."""

    def plain(value: Any) -> Any:
        if isinstance(value, (tuple, list)):
            return [plain(v) for v in value]
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        return value

    return plain(cleaned)


def stored(raw: Any) -> dict[str, Any]:
    """`clean` for stored values: bad value sets nothing instead of failing the turn."""
    try:
        return clean(raw)
    except ProcessError:
        return {}


def apply(base: Process, settings: dict[str, Any] | None) -> Process:
    """`base` overlaid with cleaned `settings`; structure merged per key."""
    if not settings:
        return base
    changes = {k: v for k, v in settings.items() if k in KEYS and k != "structure"}
    if "structure" in settings:
        changes["structure"] = dataclasses.replace(base.structure, **settings["structure"])
    return dataclasses.replace(base, **changes)


def add(base: Process, settings: dict[str, Any] | None) -> Process:
    """`base` with cleaned `settings`' lists joined to its own, in the shipped order."""
    if not settings:
        return base
    order = {"prepass": PREPASS, "stages": STAGES, "checks": CHECKS, "amend": AMENDS}
    changes: dict[str, Any] = {}
    for key in LIST_KEYS:
        if extra := settings.get(key):
            joined = dict.fromkeys([*getattr(base, key), *extra])
            changes[key] = (tuple(n for n in order[key] if n in joined) if key in order
                            else tuple(joined))
    return dataclasses.replace(base, **changes) if changes else base


#: Steps that read one format's own parts (회의록 실행 항목 표): elsewhere they repair what is
#: not there (r7 A01-T2: an agenda's 담당·시간 칸 all 「미정」 under a minutes agent).
FORMAT_ONLY = {
    "minutes": {"stages": ("minutes-repair", "minutes-reground"), "checks": ("minutes-rows",)},
}


def only_for_format(process: Process, format_id: str | None) -> Process:
    """`process` without the steps of formats other than `format_id`."""
    changes: dict[str, Any] = {}
    for fmt, keys in FORMAT_ONLY.items():
        if fmt == format_id:
            continue
        for key, names in keys.items():
            kept = tuple(n for n in changes.get(key, getattr(process, key)) if n not in names)
            if kept != getattr(process, key):
                changes[key] = kept
    return dataclasses.replace(process, **changes) if changes else process


def of_template(template) -> dict[str, Any]:
    """서식's stored `[process]`."""
    return dict(getattr(template, "process", None) or {})


def governing_template(template_id: str | None, format_id: str | None):
    """서식 governing the document: picked one if format-fixing, else the format's own."""
    from app.services import design_templates

    template = design_templates.get(template_id) if template_id else None
    if template is not None and template.doc_format:
        return template
    if format_id:
        return design_templates.for_format(format_id)
    return None


#: format defaults; text-only formats draw pictures only when asked
FORMAT_DEFAULTS: dict[str, dict[str, Any]] = {
    fmt: {"draws": "when-asked"}
    for fmt in ("official", "press", "minutes", "result_report", "internal_draft")
}
# 공문: one untitled 「본문」 section of 1. 가. items
FORMAT_DEFAULTS["official"] = {**FORMAT_DEFAULTS["official"], "fixed_outline": True,
                               "structure": {"sections": ("본문",)}}

# 공모·지원사업 계획서: the funder's skeleton, not a decision memo's 요약 → 권고안
FORMAT_DEFAULTS["grant"] = {"fixed_outline": True, "structure": {"sections": (
    "추진 배경 및 필요성", "사업 목표 및 성과지표", "세부 프로그램", "추진 일정", "추진 체계",
    "예산 계획", "기대 효과 및 지속 방안")}}


def resolve(*, template_id: str | None, format_id: str | None,
            own: dict[str, Any] | None = None) -> Process:
    """Effective settings: defaults, then 서식, then agent (`Agent.process`)."""
    template = governing_template(template_id, format_id)
    out = apply(DEFAULT, FORMAT_DEFAULTS.get(format_id or ""))
    out = apply(out, of_template(template) if template is not None else None)
    out = apply(out, stored(own) if own else None)
    # turned-on skills' steps and checks join the 서식's
    return add(out, stored((own or {}).get(ADDED)))


def for_format(format_id: str | None) -> Process:
    """Settings of a stored document by format only (revisions)."""
    return resolve(template_id=None, format_id=format_id)


def catalogue() -> dict[str, Any]:
    """Form options: name, label, what it reads."""

    def rows(names: dict[str, str]) -> list[dict[str, str]]:
        return [{"name": k, "label": v, "needs": NEEDS.get(k, "")} for k, v in names.items()]

    return {"prepass": rows(PREPASS), "stages": rows(STAGES), "checks": rows(CHECKS),
            "judged": rows(JUDGED), "amend": rows(AMENDS), "head": rows(HEADS),
            "dateBases": list(DATE_BASES), "draws": list(DRAWS),
            "limits": {"prepass": MAX_PREPASS, "rules": MAX_RULES, "sections": MAX_SECTIONS,
                       "tables": MAX_TABLES, "columns": MAX_COLUMNS, "pages": MAX_PAGES,
                       "rule": _RULE_MAX, "summary": MAX_SUMMARY},
            "defaults": DEFAULT.wire()}


__all__ = ["ADDED", "AMENDS", "CHECKS", "DEFAULT", "FORMAT_DEFAULTS", "HEADS", "JUDGED", "KEYS",
           "LIST_KEYS", "PREPASS", "STAGES", "Process", "ProcessError", "Structure", "add",
           "apply", "catalogue", "clean",
           "for_format", "governing_template", "of_template", "resolve", "stored", "stored_form"]
