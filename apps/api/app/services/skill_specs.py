"""Per-turn server skill specs; fields name shipped code only, user scripts sandbox-only."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from app.services import skill_packages

#: user skill key on turns and Agent.check_skills
USER = "skill:"

#: storable fields, SKILL.md front-matter names
FIELDS = ("stage", "entry", "post_check", "post_judge", "copy_check", "copy_kinds", "tables",
          "own_stage", "uses", "template", "checks", "check_options", "questions", "process")
#: what a skill brings to the turn it is on, as an agent's own fields do
DOCUMENT_FIELDS = ("template", "checks", "check_options", "questions", "process")

_REF = re.compile(r"[a-z0-9-]{1,60}/[a-z0-9_]{1,40}")
_FOCUS_MAX = 400
_USES_MAX = 12
_CHECKS_MAX = 8
#: `check_skills.CHECK_OPTIONS` names (kept here: packages load before that module)
CHECK_OPTION_NAMES = ("tool_numbers", "titles")
_TEMPLATE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,59}")


@dataclass(frozen=True, slots=True)
class Spec:
    key: str
    name: str = ""
    body: str = ""
    #: pre/post server check once attached
    stage: str = ""
    #: shipped script for that check
    entry: str = ""
    #: shipped scripts over chat answers
    post_checks: tuple[str, ...] = ()
    post_judge: bool = False
    judge_focus: str = ""
    copy_check: bool = False
    copy_kinds: tuple[str, ...] = ()
    tables: bool = False
    own_stage: bool = False
    #: shipped scripts via run_skill_script
    uses: tuple[str, ...] = ()
    #: user scripts → first docstring line; sandbox only
    scripts: dict[str, str] = field(default_factory=dict)
    #: 템플릿 a document turn writes into when the person picked none
    template: str = ""
    #: check skills attached while on; trace strictness while on
    checks: tuple[str, ...] = ()
    check_options: tuple[str, ...] = ()
    #: 빠진 정보 질문 asked while on
    questions: tuple = ()
    #: 진행 과정 설정 while on, under an agent's own
    process: dict = field(default_factory=dict)

    @property
    def user(self) -> bool:
        return self.key.startswith(USER)

    def entry_ref(self) -> tuple[str, str] | None:
        owner, _, script = self.entry.partition("/")
        return (owner, script) if owner and script else None

    def fields(self) -> dict[str, Any]:
        """As stored in Skill.server_fields."""
        return {
            "stage": self.stage, "entry": self.entry, "post_check": list(self.post_checks),
            "post_judge": self.judge_focus or self.post_judge, "copy_check": self.copy_check,
            "copy_kinds": list(self.copy_kinds), "tables": self.tables,
            "own_stage": self.own_stage, "uses": list(self.uses),
            "template": self.template, "checks": list(self.checks),
            "check_options": list(self.check_options),
            "questions": [dict(q) for q in self.questions], "process": dict(self.process),
        }


def of_package(package: skill_packages.SkillPackage | None) -> Spec | None:
    if package is None:
        return None
    return Spec(
        key=package.key, name=package.name, body=package.body, stage=package.stage,
        entry=f"{package.key}/{package.entry}" if package.entry else "",
        post_checks=package.post_checks, post_judge=package.post_judge,
        judge_focus=package.judge_focus, copy_check=package.copy_check,
        copy_kinds=package.copy_kinds, tables=package.tables, own_stage=package.own_stage,
        template=package.template, checks=package.checks, check_options=package.check_options,
        questions=tuple(_package_questions(package)), process=dict(package.process or {}))


def _package_questions(package: skill_packages.SkillPackage) -> list[dict[str, Any]]:
    from app.services import gap_questions

    try:
        return gap_questions.clean(list(package.questions))
    except gap_questions.QuestionError:
        return []


def package_fields(package: skill_packages.SkillPackage) -> dict[str, Any]:
    """Shipped package server fields as seeded."""
    spec = of_package(package)
    assert spec is not None
    return spec.fields()


def key_of(row) -> str:
    """Turn key: catalogue key for shipped copies, else skill:<id>."""
    if row.catalog_key and skill_packages.package_for(row.catalog_key) is not None:
        return skill_packages.package_for(row.catalog_key).key  # type: ignore[union-attr]
    return f"{USER}{row.id}"


def of_row(row) -> Spec:
    """Row spec: stored fields, else the package's."""
    key = key_of(row)
    stored = getattr(row, "server_fields", None)
    scripts = {str(f.get("name")): str(f.get("summary") or "")
               for f in getattr(row, "script_files", None) or [] if isinstance(f, dict)}
    if stored is None:
        package = skill_packages.package_for(row.catalog_key) if row.catalog_key else None
        base = of_package(package) or Spec(key=key)
        return Spec(**{**_as_dict(base), "key": key, "name": row.name, "body": row.body,
                       "scripts": scripts})
    clean = normalize(stored)
    focus = clean["post_judge"] if isinstance(clean["post_judge"], str) else ""
    return Spec(
        key=key, name=row.name, body=row.body, stage=clean["stage"], entry=clean["entry"],
        post_checks=tuple(clean["post_check"]), post_judge=bool(clean["post_judge"]),
        judge_focus=focus, copy_check=clean["copy_check"],
        copy_kinds=tuple(clean["copy_kinds"]), tables=clean["tables"],
        own_stage=clean["own_stage"], uses=tuple(clean["uses"]), scripts=scripts,
        template=clean["template"], checks=tuple(clean["checks"]),
        check_options=tuple(clean["check_options"]),
        questions=tuple(clean["questions"]), process=clean["process"])


def _as_dict(spec: Spec) -> dict[str, Any]:
    return {f.name: getattr(spec, f.name) for f in dataclasses.fields(spec)}


def wire(spec: Spec) -> dict[str, Any]:
    """Spec from JSON (composed run's agent, stored request)."""
    return {k: list(v) if isinstance(v, tuple) else v for k, v in _as_dict(spec).items()}


def of_wire(raw: dict[str, Any]) -> Spec:
    known = {f.name: f for f in dataclasses.fields(Spec)}
    values = {k: (tuple(v) if isinstance(v, list) else v) for k, v in raw.items() if k in known}
    return Spec(**values)


async def for_turn(db, user_id: str, *, agent_checks: list | None = None,
                   agent_owner: str = "") -> tuple[dict[str, Spec], dict[str, dict[str, Any]]]:
    """Turn skill specs (enabled + agent-attached checks) and user scripts by key."""
    from sqlmodel import col, select

    from app.models.workspace import Skill

    rows = list((await db.exec(select(Skill).where(
        Skill.owner_id == user_id, Skill.enabled == True,  # noqa: E712
        col(Skill.server_fields).is_not(None) | col(Skill.script_files).is_not(None)))).all())
    scripts = {
        f"{USER}{row.id}": {"name": row.name, "files": {
            str(f.get("name")): str(f.get("source") or "")
            for f in row.script_files if isinstance(f, dict) and f.get("name")}}
        for row in rows if row.script_files}
    attached = [str(k).removeprefix(USER) for k in (agent_checks or [])
                if str(k).startswith(USER)]
    known = {row.id for row in rows}
    if (wanted := [i for i in attached if i not in known]) and agent_owner:
        rows += list((await db.exec(select(Skill).where(
            col(Skill.id).in_(wanted), Skill.owner_id == agent_owner))).all())
    out: dict[str, Spec] = {}
    for row in rows:
        spec = of_row(row)
        if row.server_fields is not None or spec.user:
            out[spec.key] = spec
    return out, scripts


def resolve(key: str, specs: Mapping[str, Spec] | None = None) -> Spec | None:
    """Turn spec for key, else the shipped package's."""
    if specs and key in specs:
        return specs[key]
    if key.startswith(USER):
        return None
    return of_package(skill_packages.package_for(key))


@cache
def post_check_refs() -> tuple[str, ...]:
    """Shipped answer scripts a skill may run."""
    return tuple(sorted({ref for package in skill_packages.packages().values()
                         for ref in package.post_checks}))


@cache
def entry_refs() -> dict[str, tuple[str, ...]]:
    """Shipped check entries by stage."""
    out: dict[str, set[str]] = {stage: set() for stage in skill_packages.STAGES}
    for package in skill_packages.packages().values():
        if package.stage and package.entry:
            out[package.stage].add(f"{package.key}/{package.entry}")
    return {stage: tuple(sorted(refs)) for stage, refs in out.items()}


def script_refs() -> tuple[str, ...]:
    """Shipped scripts run_skill_script offers."""
    return tuple(f"{p.key}/{s}" for p in skill_packages.scripted() for s in p.scripts)


@cache
def copy_kinds() -> tuple[str, ...]:
    """Copy-check narrowing kinds."""
    from app.services import skill_scripts

    try:
        return tuple(skill_scripts.module("copy-check", "claims").ACTIONS)
    except (LookupError, AttributeError):
        return ()


def catalogue() -> dict[str, Any]:
    """Skill-form choices with the shipped skill using each."""
    packages = skill_packages.packages()

    def label(ref: str) -> dict[str, str]:
        owner, _, script = ref.partition("/")
        package = packages.get(owner)
        summary = package.scripts.get(script, "") if package else ""
        return {"ref": ref, "skill": package.name if package else owner, "summary": summary}

    return {
        "postChecks": [label(r) for r in post_check_refs()],
        "entries": {stage: [label(r) for r in refs] for stage, refs in entry_refs().items()},
        "scripts": [label(r) for r in script_refs()],
        "copyKinds": [{"kind": k, "label": _kind_words().get(k, k)} for k in copy_kinds()],
        "checks": [{"ref": key, "skill": packages[key].name,
                    "summary": packages[key].description} for key in check_keys()],
        "checkOptions": [{"option": k, "label": _OPTION_WORDS[k]} for k in CHECK_OPTION_NAMES],
        "gapDetectors": _detectors(),
    }


#: as the 「다 쓴 뒤 검토하기」 bundle words them
_OPTION_WORDS = {"tool_numbers": "수치는 도구로 계산한 결과에서만 인정",
                 "titles": "직함·제품명까지 근거 확인"}


def _detectors() -> list[dict[str, str]]:
    from app.services import gap_questions

    return gap_questions.detector_catalogue()


@cache
def _kind_words() -> dict[str, str]:
    from app.services import skill_scripts

    try:
        return dict(skill_scripts.module("copy-check", "claims").KIND_WORDS)
    except (LookupError, AttributeError):
        return {}


class FieldError(ValueError):
    """Server field a person may not set; code is the wire detail."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _refs(raw: Any) -> list[str]:
    items = [raw] if isinstance(raw, str) else list(raw or []) if isinstance(raw, list) else []
    return [str(i).strip() for i in items if str(i or "").strip()]


def normalize(raw: Any) -> dict[str, Any]:
    """Stored fields read back; unknowns dropped, never raises."""
    data = raw if isinstance(raw, dict) else {}
    judge = data.get("post_judge")
    return {
        "stage": str(data.get("stage") or "") if data.get("stage") in skill_packages.STAGES
        else "",
        "entry": str(data.get("entry") or ""),
        "post_check": [r for r in _refs(data.get("post_check")) if _REF.fullmatch(r)],
        "post_judge": (" ".join(judge.split())[:_FOCUS_MAX] or True)
        if isinstance(judge, str) and judge.strip() else bool(judge),
        "copy_check": bool(data.get("copy_check")),
        "copy_kinds": [str(k) for k in _refs(data.get("copy_kinds"))],
        "tables": bool(data.get("tables")),
        "own_stage": bool(data.get("own_stage")),
        "uses": [r for r in _refs(data.get("uses")) if _REF.fullmatch(r)][:_USES_MAX],
        **_document_stored(data),
    }


def _document_stored(data: dict) -> dict[str, Any]:
    """Stored template/checks/options/questions/process read back; a bad one sets nothing."""
    from app.services import gap_questions, process_settings

    template = str(data.get("template") or "").strip()
    try:
        questions = gap_questions.clean(data.get("questions") or [])
    except gap_questions.QuestionError:
        questions = []
    return {
        "template": template if _TEMPLATE.fullmatch(template) else "",
        "checks": [k for k in _refs(data.get("checks"))
                   if _CHECK_KEY.fullmatch(k)][:_CHECKS_MAX],
        "check_options": [k for k in CHECK_OPTION_NAMES
                          if k in _refs(data.get("check_options"))],
        "questions": questions,
        "process": process_settings.stored_form(
            process_settings.stored(data.get("process") or {})),
    }


_CHECK_KEY = re.compile(r"(?:skill:)?[a-z0-9-]{1,60}")


def validate(raw: Any, *,
             own_scripts: tuple[str, ...] = ()) -> tuple[dict[str, Any], list[FieldError]]:
    """User server fields vs allowlists: (kept, refusals); uses may name own scripts (sandbox)."""
    data = raw if isinstance(raw, dict) else {}
    errors: list[FieldError] = []
    out = normalize({})
    stage = str(data.get("stage") or "").strip()
    entry = str(data.get("entry") or "").strip()
    if stage and stage not in skill_packages.STAGES:
        errors.append(FieldError("skill_stage_unknown",
                                 f"stage 는 pre 또는 post 입니다: {stage[:20]}"))
    elif stage:
        allowed = entry_refs().get(stage, ())
        if entry not in allowed:
            errors.append(FieldError(
                "skill_entry_unknown",
                f"{stage} 검사로 쓸 수 있는 기본 제공 스크립트가 아닙니다: "
                f"{entry[:80] or '(없음)'}"))
        else:
            out["stage"], out["entry"] = stage, entry
    elif entry:
        errors.append(FieldError("skill_entry_without_stage", "entry 는 stage 와 함께 씁니다."))
    for ref in _refs(data.get("post_check")):
        if ref in post_check_refs():
            out["post_check"].append(ref)
        elif ref in own_scripts or ref.removesuffix(".py") in own_scripts:
            errors.append(FieldError(
                "skill_post_check_own_script",
                f"post_check 는 기본 제공 스크립트만 씁니다. 직접 올린 「{ref[:40]}」은 "
                "run_skill_script 로 샌드박스에서만 실행됩니다."))
        else:
            errors.append(FieldError("skill_post_check_unknown",
                                     f"모르는 post_check 스크립트입니다: {ref[:80]}"))
    out["post_check"] = list(dict.fromkeys(out["post_check"]))
    judge = data.get("post_judge")
    if isinstance(judge, str) and judge.strip():
        out["post_judge"] = " ".join(judge.split())[:_FOCUS_MAX]
    else:
        out["post_judge"] = bool(judge)
    out["copy_check"] = bool(data.get("copy_check"))
    kinds = _refs(data.get("copy_kinds"))
    known_kinds = copy_kinds()
    bad = [k for k in kinds if k not in known_kinds]
    if bad:
        shown = ", ".join(b[:20] for b in bad[:5])
        errors.append(FieldError("skill_copy_kind_unknown", f"모르는 copy_kinds 값입니다: {shown}"))
    out["copy_kinds"] = [k for k in dict.fromkeys(kinds) if k in known_kinds]
    out["tables"] = bool(data.get("tables"))
    out["own_stage"] = bool(data.get("own_stage")) and bool(out["stage"])
    shipped = set(script_refs())
    for ref in _refs(data.get("uses")):
        stem = ref.removesuffix(".py")
        if ref in shipped or stem in own_scripts:
            out["uses"].append(ref if ref in shipped else stem)
        else:
            errors.append(FieldError("skill_script_unknown",
                                     f"모르는 스크립트입니다: {ref[:80]}"))
    out["uses"] = list(dict.fromkeys(out["uses"]))[:_USES_MAX]
    _validate_document(data, out, errors, packaged=False)
    return out, errors


def validate_document(raw: Any) -> tuple[dict[str, Any], list[FieldError]]:
    """A package's document fields (no check-key lookup while packages load)."""
    out = {"template": "", "checks": [], "check_options": [], "questions": [], "process": {}}
    errors: list[FieldError] = []
    _validate_document(raw if isinstance(raw, dict) else {}, out, errors, packaged=True)
    return out, errors


def check_keys() -> tuple[str, ...]:
    """Shipped check skills a skill may attach."""
    return tuple(sorted(p.key for p in skill_packages.packages().values() if p.stage))


def _validate_document(data: dict, out: dict[str, Any], errors: list[FieldError], *,
                       packaged: bool) -> None:
    """template/checks/check_options/questions/process; `packaged` skips the check-key lookup
    (packages are still loading)."""
    from app.services import gap_questions, process_settings

    template = str(data.get("template") or "").strip()
    if template and packaged:
        # the catalogue test checks shipped ids; templates load after packages
        if _TEMPLATE.fullmatch(template):
            out["template"] = template
        else:
            errors.append(FieldError("skill_template_unknown",
                                     f"모르는 템플릿입니다: {template[:60]}"))
    elif template:
        from app.services import design_templates

        found = design_templates.get(template)
        if found is None and not template.startswith(design_templates.USER_PREFIX):
            errors.append(FieldError("skill_template_unknown",
                                     f"모르는 템플릿입니다: {template[:60]}"))
        elif found is not None and found.kind not in ("document", "deck"):
            errors.append(FieldError("skill_template_kind",
                                     "스킬에는 보고서·발표 템플릿만 붙일 수 있습니다."))
        else:
            out["template"] = template
    known = None if packaged else set(check_keys())
    for key in _refs(data.get("checks")):
        if not _CHECK_KEY.fullmatch(key) or key.startswith(USER) or (
                known is not None and key not in known):
            errors.append(FieldError("skill_check_unknown",
                                     f"검사 스킬로 쓸 수 없습니다: {key[:60]}"))
        else:
            out["checks"].append(key)
    out["checks"] = list(dict.fromkeys(out["checks"]))[:_CHECKS_MAX]
    options = _refs(data.get("check_options"))
    if bad := [o for o in options if o not in CHECK_OPTION_NAMES]:
        errors.append(FieldError("skill_check_option_unknown",
                                 f"모르는 검사 강도입니다: {', '.join(b[:20] for b in bad[:3])}"))
    out["check_options"] = [k for k in CHECK_OPTION_NAMES if k in options]
    if packaged:
        # detectors name package scripts: checked when the spec is built (`of_package`)
        out["questions"] = [dict(q) for q in data.get("questions") or [] if isinstance(q, dict)]
    else:
        try:
            out["questions"] = gap_questions.clean(data.get("questions") or [])
        except gap_questions.QuestionError as exc:
            errors.append(FieldError(exc.code, str(exc)))
    try:
        out["process"] = process_settings.stored_form(
            process_settings.clean(data.get("process") or {}))
    except process_settings.ProcessError as exc:
        errors.append(FieldError(exc.code, str(exc)))


def empty(fields: dict[str, Any] | None) -> bool:
    """Whether stored fields request nothing."""
    clean = normalize(fields)
    return not any(clean.values())


__all__ = ["CHECK_OPTION_NAMES", "DOCUMENT_FIELDS", "FIELDS", "USER", "FieldError", "Spec",
           "catalogue", "check_keys",
           "copy_kinds", "empty", "entry_refs", "for_turn", "key_of", "normalize", "of_package",
           "of_row", "of_wire", "package_fields", "post_check_refs", "resolve", "script_refs",
           "validate", "validate_document", "wire"]
