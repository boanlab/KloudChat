"""Pipeline run kept across requests: per-stage snapshots and evidence for resume/rerun."""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import difflib
import importlib
import re
from typing import Any

from app.pipeline.labels import label as stage_label
from app.pipeline.stage import StepRecord

#: bundle parts snapshot field by field
PARTS = ("spec", "material", "computed", "plan", "document", "findings")
#: whole top-level fields
TOP = ("answers", "files", "usage", "wire")
#: never kept: open row, re-read by id
SKIP = frozenset({"document.artifact"})

#: stage name → rebuild of function values (checks) on restore
REBUILT: dict[str, Any] = {}

#: evidence bounds
_MAX_ITEMS = 20
_MAX_TEXT = 400
_MAX_BLOCK = 4000


def encode(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dt.datetime):
        return {"__dt": value.isoformat()}
    if isinstance(value, dt.date):
        return {"__date": value.isoformat()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, tuple):
        return {"__t": [encode(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {"__s": [encode(v) for v in sorted(value, key=repr)]}
    if isinstance(value, dict):
        if all(isinstance(k, str) and not k.startswith("__") for k in value):
            return {k: encode(v) for k, v in value.items()}
        return {"__d": [[encode(k), encode(v)] for k, v in value.items()]}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        if cls.__module__ == "app.services.doc_formats" and cls.__name__ == "DocFormat":
            from app.services import doc_formats

            # shipped format by id; a person's template by its fields
            if doc_formats.BY_ID.get(value.id) is value:
                return {"__fmt": value.id}
        if cls.__module__.startswith("app."):
            return {"__dc": f"{cls.__module__}:{cls.__qualname__}",
                    "f": {f.name: encode(getattr(value, f.name))
                          for f in dataclasses.fields(value)}}
    # callable, compiled pattern, ORM row: not kept
    return None


def _dataclass(path: str) -> type | None:
    module_name, _, qualname = path.partition(":")
    if not module_name.startswith("app.") or not qualname or "." in qualname:
        return None
    try:
        cls = getattr(importlib.import_module(module_name), qualname)
    except (ImportError, AttributeError):
        return None
    return cls if dataclasses.is_dataclass(cls) else None


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "__t" in value and len(value) == 1:
        return tuple(decode(v) for v in value["__t"])
    if "__s" in value and len(value) == 1:
        return {decode(v) for v in value["__s"]}
    if "__d" in value and len(value) == 1:
        return {_hashable(decode(k)): decode(v) for k, v in value["__d"]}
    if "__dt" in value and len(value) == 1:
        return dt.datetime.fromisoformat(value["__dt"])
    if "__date" in value and len(value) == 1:
        return dt.date.fromisoformat(value["__date"])
    if "__fmt" in value and len(value) == 1:
        from app.services import doc_formats

        return next((f for f in doc_formats.FORMATS if f.id == value["__fmt"]), None)
    if "__dc" in value:
        cls = _dataclass(str(value["__dc"]))
        if cls is None:
            return None
        fields = {k: decode(v) for k, v in (value.get("f") or {}).items()}
        known = {f.name: f for f in dataclasses.fields(cls)}
        init = {k: v for k, v in fields.items() if k in known and known[k].init}
        try:
            out = cls(**init)
        except TypeError:
            return None
        for k, v in fields.items():
            if k in known and not known[k].init:
                object.__setattr__(out, k, v)
        return out
    return {k: decode(v) for k, v in value.items()}


def _hashable(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def paths(bundle: Any) -> list[str]:
    out = []
    for part in PARTS:
        for f in dataclasses.fields(getattr(bundle, part)):
            path = f"{part}.{f.name}"
            if path not in SKIP:
                out.append(path)
    return [*out, *TOP]


def _get(bundle: Any, path: str) -> Any:
    value = bundle
    for name in path.split("."):
        value = getattr(value, name)
    return value


def _set(bundle: Any, path: str, value: Any) -> None:
    head, _, name = path.rpartition(".")
    target = _get(bundle, head) if head else bundle
    setattr(target, name, value)


def _fingerprint(value: Any) -> int:
    try:
        return hash(repr(value))
    except Exception:  # noqa: BLE001 — an unprintable value counts as changed
        return id(value)


def request_params(bundle: Any) -> dict[str, Any]:
    """Request params minus the key (reruns get a fresh one)."""
    row = encode(bundle.request)
    row["f"].pop("api_key", None)
    return row


def restore(request: dict[str, Any], snapshots: dict[str, Any], *, before: str,
            api_key: str) -> Any:
    """Bundle as it stood before stage `before`; `KeyError` if no snapshot that far."""
    from app.pipeline.bundle import Bundle

    if before not in rerunnable_from(snapshots):
        raise KeyError(f"no snapshot before {before!r}")
    req = decode({**request, "f": {**request.get("f", {}), "api_key": api_key}})
    if req is None:
        raise KeyError("request not restorable")
    bundle = Bundle(request=req)
    for path, value in (snapshots.get("start") or {}).items():
        _set(bundle, path, decode(value))
    ran = []
    for entry in snapshots.get("after") or []:
        if entry.get("stage") == before:
            break
        ran.append(entry.get("stage"))
        for path, value in (entry.get("fields") or {}).items():
            _set(bundle, path, decode(value))
    for stage in ran:
        if (rebuild := REBUILT.get(stage)) is not None:
            rebuild(bundle)
    return bundle


def carried(snapshots: dict[str, Any], *, before: str) -> dict[str, Any]:
    """Snapshots of stages before `before`: a rerun's starting state."""
    kept = []
    for entry in snapshots.get("after") or []:
        if entry.get("stage") == before:
            break
        kept.append(entry)
    return {"start": snapshots.get("start") or {}, "after": kept}


def _clip(text: Any, limit: int = _MAX_TEXT) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


_TOKENS = re.compile(r"\S+|\s+")


def text_changes(old: str, new: str, *, limit: int = _MAX_ITEMS) -> list[dict[str, str]]:
    """Word-level old → new pieces."""
    if old == new:
        return []
    a, b = _TOKENS.findall(old or ""), _TOKENS.findall(new or "")
    if len(a) > 6000 or len(b) > 6000:
        return [{"old": _clip(old), "new": _clip(new)}]
    out = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        was, now = "".join(a[i1:i2]).strip(), "".join(b[j1:j2]).strip()
        if not was and not now:
            continue
        out.append({"old": _clip(was), "new": _clip(now)})
        if len(out) >= limit:
            break
    return out


def section_changes(before: list[dict], after: list[dict]) -> list[dict[str, str]]:
    """Per-section old → new text a stage changed."""
    old_by_id = {str(s.get("id") or i): s for i, s in enumerate(before or [])}
    out: list[dict[str, str]] = []
    for i, section in enumerate(after or []):
        key = str(section.get("id") or i)
        was = old_by_id.get(key)
        old_text = str((was or {}).get("content") or "")
        new_text = str(section.get("content") or "")
        if was is not None and old_text == new_text:
            continue
        for change in text_changes(old_text, new_text, limit=_MAX_ITEMS - len(out)):
            out.append({"sectionId": key, "heading": _clip(section.get("heading"), 120),
                        **change})
        if len(out) >= _MAX_ITEMS:
            break
    return out


def _figures(settled: list[Any]) -> list[dict[str, str]]:
    out = []
    for fig in settled[:_MAX_ITEMS]:
        name = getattr(fig, "name", "") or (fig.get("name") if isinstance(fig, dict) else "")
        value = getattr(fig, "value", "") or (fig.get("value") if isinstance(fig, dict) else "")
        if name or value:
            out.append({"name": _clip(name, 120), "value": _clip(value, 120)})
    return out


class Witness:
    """Watches one stage's before/after to describe what it did."""

    WATCHED = ("document.sections", "document.answer", "computed.settled", "computed.values",
               "findings.before", "findings.after", "findings.added_before",
               "findings.added_after")

    def __init__(self) -> None:
        self._before: dict[str, Any] = {}
        self._steps: list[dict] = []

    def before(self, stage: Any, bundle: Any) -> None:
        self._steps = []
        try:
            self._before = {p: copy.deepcopy(_get(bundle, p)) for p in self.WATCHED}
        except Exception:  # noqa: BLE001 — a bundle of another shape is not watched
            self._before = {}

    def saw(self, event: dict) -> None:
        if event.get("type") == "step":
            self._steps.append(event)

    def after(self, stage: Any, record: StepRecord, bundle: Any) -> None:
        record.label = record.label or stage_label(record.stage)
        evidence = dict(record.evidence)
        b = self._before
        if b.get("document.sections") != bundle.document.sections:
            if changes := section_changes(b.get("document.sections") or [],
                                          bundle.document.sections):
                evidence["changes"] = changes
        elif b.get("document.answer") != bundle.document.answer and b.get("document.answer"):
            if changes := text_changes(b["document.answer"], bundle.document.answer):
                evidence["changes"] = [{"sectionId": "answer", "heading": "", **c}
                                       for c in changes]
        if b.get("computed.settled") != bundle.computed.settled and bundle.computed.settled:
            evidence["computed"] = _figures(list(bundle.computed.settled))
        if b.get("computed.values") != bundle.computed.values and bundle.computed.values:
            evidence["computedBlock"] = _clip(bundle.computed.values, _MAX_BLOCK)
        found = {}
        for key, path in (("before", "findings.before"), ("after", "findings.after")):
            was, now = b.get(path) or [], _get(bundle, path) or []
            extra = [*now[len(was):], *[
                x for x in (_get(bundle, f"findings.added_{key}") or [])
                if x not in (b.get(f"findings.added_{key}") or [])]]
            if extra:
                found[key] = [_clip(x, 120) for x in extra[:_MAX_ITEMS]]
        if found:
            evidence["findings"] = found
        code = [{"code": _clip(e.get("code"), _MAX_BLOCK), "stdout": _clip(e.get("stdout"), 2000),
                 "stderr": _clip(e.get("stderr"), 2000)}
                for e in self._steps if e.get("code") or e.get("stdout")]
        if code:
            evidence["code"] = code[:5]
        if branch := next((e["branch"] for e in self._steps
                           if isinstance(e.get("branch"), dict)), None):
            evidence["branch"] = branch
        record.evidence = evidence
        if not record.summary and record.status == "done":
            record.summary = self._summary(record)

    def _summary(self, record: StepRecord) -> str:
        shown = [e for e in self._steps if e.get("status") in ("done", "error") and e.get("label")]
        if shown:
            return _clip(shown[-1]["label"], 120)
        changes = record.evidence.get("changes") or []
        if changes:
            return f"고침 {len(changes)}곳"
        return ""


class Recorder(Witness):
    """`Witness` that also snapshots the bundle per stage, for `restore`."""

    def __init__(self, run_id: str = "") -> None:
        super().__init__()
        self.run_id = run_id
        #: pipeline and flow (set by the caller)
        self.pipeline = ""
        self.flow = ""
        self.start: dict[str, Any] = {}
        self.after_stages: list[dict[str, Any]] = []
        self.request: dict[str, Any] = {}
        self._prints: dict[str, int] = {}
        #: resume stage after a wait or failure
        self.next = ""

    def begin(self, bundle: Any, *, carried: dict[str, Any] | None = None) -> None:
        """Before the first stage; `carried` = snapshots of stages a rerun skipped."""
        self.request = request_params(bundle)
        if carried:
            self.start = dict(carried.get("start") or {})
            self.after_stages = [dict(e) for e in carried.get("after") or []]
        else:
            self.start = {p: encode(_get(bundle, p)) for p in paths(bundle)}
        self._prints = {p: _fingerprint(_get(bundle, p)) for p in paths(bundle)}

    def before(self, stage: Any, bundle: Any) -> None:
        super().before(stage, bundle)
        self.next = stage.name

    def after(self, stage: Any, record: StepRecord, bundle: Any) -> None:
        super().after(stage, record, bundle)
        record.run_id = self.run_id
        record.rerunnable = True
        if record.status not in ("done", "skipped"):
            # state after stop/failure: not resumable
            return
        changed = {}
        for p in paths(bundle):
            now = _fingerprint(_get(bundle, p))
            if now != self._prints.get(p):
                changed[p] = encode(_get(bundle, p))
                self._prints[p] = now
        self.after_stages.append({"stage": stage.name, "fields": changed})
        self.next = ""

    def skipped(self, stage: Any, record: StepRecord) -> None:
        record.label = record.label or stage_label(record.stage)
        record.run_id = self.run_id
        record.rerunnable = True
        self.after_stages.append({"stage": stage.name, "fields": {}})

    def snapshots(self) -> dict[str, Any]:
        return {"start": self.start, "after": self.after_stages,
                **({"next": self.next} if self.next else {})}


def rerunnable_from(snapshots: dict[str, Any] | None) -> list[str]:
    """Rerun points: stages whose predecessors all kept state, plus the stop/failure stage."""
    if not snapshots:
        return []
    names = [str(e.get("stage") or "") for e in snapshots.get("after") or []]
    return [*names, *([snapshots["next"]] if snapshots.get("next") else [])]


__all__ = ["REBUILT", "Recorder", "Witness", "carried", "decode", "encode", "paths",
           "request_params", "rerunnable_from", "restore", "section_changes", "text_changes"]
