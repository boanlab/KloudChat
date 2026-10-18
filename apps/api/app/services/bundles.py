"""묶음 (bundles): agent pre/post-writing items; shipped or user, compiled alike."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent / "bundles"
SELF = "@self"
#: item kinds; option/flow are bundle-only
KINDS = ("check", "option", "flow", "skill", "human", "review", "export", "agent")
#: option refs: check_skills.CHECK_OPTIONS
OPTIONS = ("tool_numbers", "titles")
FLOWS = ("format-checks", "repair")
#: items needing admin approval before non-authors run them (files out, other model, other agent)
RISKY = ("export", "review")


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    kind: str
    ref: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    default: Any = True

    @property
    def risky(self) -> bool:
        return self.kind in RISKY or (self.kind == "agent" and self.ref != SELF)

    def step(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "settings": dict(self.settings)}

    def wire(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "ref": self.ref,
                "settings": dict(self.settings), "label": self.label or self.ref,
                "default": self.default,
                "targets": {s: target(self, s) for s in ("report", "chat")}}


@dataclass(frozen=True, slots=True)
class Bundle:
    #: shipped key or user:<row id>
    ref: str
    name: str
    description: str = ""
    items: tuple[Item, ...] = ()
    shipped: bool = False
    owner_id: str = ""
    visibility: str = "private"
    review: str = ""
    order: int = 99

    @property
    def risky(self) -> bool:
        return not self.shipped and any(i.risky for i in self.items)

    def item(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def wire(self) -> dict[str, Any]:
        return {"ref": self.ref, "name": self.name, "description": self.description,
                "items": [i.wire() for i in self.items], "shipped": self.shipped,
                "ownerId": self.owner_id or None, "visibility": self.visibility,
                "review": self.review, "risky": self.risky}


def _item(row: dict, n: int) -> Item:
    kind = str(row.get("kind") or "")
    if kind not in KINDS:
        raise ValueError(f"bundle item {n}: unknown kind {kind!r}")
    return Item(id=str(row.get("id") or f"i{n + 1}"), kind=kind, ref=str(row.get("ref") or ""),
                settings=dict(row.get("settings") or {}), label=str(row.get("label") or ""),
                default=row.get("default", True))


@cache
def shipped() -> dict[str, Bundle]:
    """Shipped bundles by key, in order."""
    out = []
    for path in sorted(ROOT.glob("*.toml")):
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        out.append(Bundle(ref=str(raw.get("key") or path.stem), name=str(raw["name"]),
                          description=str(raw.get("description") or ""),
                          items=tuple(_item(r, n) for n, r in enumerate(raw.get("items") or [])),
                          shipped=True, visibility="org", order=int(raw.get("order") or 99)))
    return {b.ref: b for b in sorted(out, key=lambda b: (b.order, b.ref))}


USER = "user:"
#: user check-skill bundle (skills with a stage)
MINE = "mine:checks"
MINE_SKILL = "skill:"


def own_checks(rows) -> Bundle | None:
    """「내 검사 스킬」: user stage skills as items (skill:<id>)."""
    from app.services import skill_specs

    items = []
    for row in rows:
        spec = skill_specs.of_row(row)
        if not spec.user or not spec.stage:
            continue
        items.append(Item(id=row.id[:40], kind="check", ref=spec.key,
                          settings={"stage": spec.stage, "own_stage": spec.own_stage},
                          label=row.name, default=True))
    if not items:
        return None
    return Bundle(ref=MINE, name="내 검사 스킬",
                  description="직접 만든 스킬 중 쓰기 전·후에 서버가 돌리는 검사입니다.",
                  items=tuple(items), shipped=False, owner_id=rows[0].owner_id if rows else "",
                  visibility="private", review="approved", order=50)


def of_row(row) -> Bundle:
    """User bundle from its user_pipelines row."""
    from app.pipeline import compose

    steps = compose.normalize(row.stages)
    items = tuple(Item(id=s["id"], kind=s["kind"], ref=s["ref"], settings=s["settings"],
                       label=compose.step_title(s, {}), default=True) for s in steps)
    return Bundle(ref=f"{USER}{row.id}", name=row.name, description=row.description or "",
                  items=items, owner_id=row.owner_id, visibility=row.visibility,
                  review=row.review)


def target(item: Item, surface: str) -> str | None:
    """Where an item runs here (check/option/flow/before/after/self), or None."""
    from app.services import skill_packages

    if surface not in ("report", "chat"):
        return None
    kind, ref = item.kind, item.ref
    if kind == "check":
        if ref.startswith(MINE_SKILL):
            # user check skill: stage from the item; own-stage ones have no step
            stage = str(item.settings.get("stage") or "")
            if stage not in skill_packages.STAGES or item.settings.get("own_stage"):
                return None
            return "check"
        package = skill_packages.package_for(ref)
        if package is None or not package.stage:
            return None
        if package.own_stage:
            return "after" if surface == "chat" else None
        return "check"
    if kind == "option":
        return "option" if ref in OPTIONS else None
    if kind == "flow":
        if ref == "format-checks":
            return "flow" if surface == "report" else None
        return "flow" if ref in FLOWS else None
    if kind == "skill":
        return "flow" if surface == "report" else "before"
    if kind == "human":
        if ref == "gaps":
            return "check"
        return "before" if ref == "outline" and surface == "report" else None
    if kind == "agent":
        return "self" if item.ref == SELF else "after"
    if kind == "review":
        return "after"
    if kind == "export":
        return "after" if surface == "report" else None
    return None


def export_default(item: Item, doc_format: str | None = None) -> str:
    """Default export file: by_format for the doc format, else first format."""
    by_format = item.settings.get("by_format")
    if isinstance(by_format, dict) and doc_format and by_format.get(doc_format):
        return str(by_format[doc_format])
    return str((item.settings.get("formats") or ["docx"])[0])


def _value(item: Item, ticks: dict[str, Any], doc_format: str | None = None) -> Any:
    """Item tick: user's, else bundle default; export tick is its format."""
    raw = ticks.get(item.id, item.default)
    if item.kind == "export":
        from app.pipeline import compose

        if raw is True:
            raw = export_default(item, doc_format)
        return str(raw) if str(raw or "") in compose.EXPORT_FORMATS else ""
    return bool(raw)


def clean(selections: Any) -> list[dict[str, Any]]:
    """Stored selections, each bundle once."""
    out, seen = [], set()
    for row in selections if isinstance(selections, list) else []:
        if not isinstance(row, dict):
            continue
        ref = str(row.get("bundle") or "")[:80]
        if not ref or ref in seen:
            continue
        seen.add(ref)
        items = row.get("items") if isinstance(row.get("items"), dict) else {}
        out.append({"bundle": ref, "items": {
            str(k)[:40]: (v if isinstance(v, (bool, str)) else bool(v))
            for k, v in list(items.items())[:24]}})
    return out


@dataclass(slots=True)
class Compiled:
    """Agent row fields and pipeline steps from ticked bundles."""

    check_skills: list[str] = field(default_factory=list)
    check_options: dict[str, bool] = field(default_factory=dict)
    steps: list[dict[str, Any]] | None = None
    #: user bundle in use needing approval for others
    unapproved: list[str] = field(default_factory=list)


def compile(surface: str, selections: list[dict[str, Any]],
            known: dict[str, Bundle], *, doc_format: str | None = None) -> Compiled:
    """Agent built from ticked bundles on this surface; unknown refs skipped."""
    from app.pipeline import compose

    out = Compiled()
    before: list[dict] = []
    after: list[dict] = []
    for selection in clean(selections):
        bundle = known.get(selection["bundle"])
        if bundle is None:
            continue
        if bundle.risky and bundle.review != "approved":
            out.unapproved.append(bundle.ref)
        placed_after = False
        for item in bundle.items:
            where = target(item, surface)
            value = _value(item, selection["items"], doc_format)
            if where is None or not value:
                continue
            if where == "check":
                key = "gap-check" if item.kind == "human" else item.ref
                if key not in out.check_skills:
                    out.check_skills.append(key)
            elif where == "option":
                out.check_options[item.ref] = True
            elif where == "self":
                placed_after = True
            elif where in ("before", "after"):
                step = item.step()
                if item.kind == "export":
                    step["settings"] = {"formats": [value]}
                (after if where == "after" or placed_after else before).append(step)
    if before or after:
        steps: list[dict] = []
        if "gap-check" in out.check_skills:
            # bound pipeline asks via its own card
            steps.append({"kind": "human", "ref": "gaps"})
        steps += before
        if surface == "report" and not any(
                s["kind"] == "human" and s["ref"] == "outline" for s in before):
            # report flow and its pipeline offer the outline first
            steps.append({"kind": "human", "ref": "outline"})
        steps.append({"kind": "agent", "ref": SELF})
        steps += after
        out.steps = compose.normalize(steps)
    return out


def settled(surface: str, selections: list[dict[str, Any]],
            known: dict[str, Bundle], *, doc_format: str | None = None
            ) -> list[dict[str, Any]]:
    """Stored selections; unrunnable items stored off."""
    out = []
    for row in clean(selections):
        bundle = known.get(row["bundle"])
        items = dict(row["items"])
        if bundle is not None:
            for item in bundle.items:
                if item.kind == "export" and target(item, surface) is None \
                        and _value(item, items, doc_format):
                    items[item.id] = False
        out.append({"bundle": row["bundle"], "items": items})
    return out


def view(selections: list[dict[str, Any]], known: dict[str, Bundle], *,
         check_skills: list | None = None,
         check_options: dict | None = None,
         doc_format: str | None = None) -> list[dict[str, Any]]:
    """Ticked bundles with ticks filled; pre-bundle agents map from checks."""
    rows = clean(selections)
    if not rows and check_skills:
        checks = set(check_skills)
        options = check_options or {}
        for bundle in shipped().values():
            row_items = [i for i in bundle.items if i.kind in ("check", "option")]
            hits = {i.id: (i.ref in checks if i.kind == "check" else bool(options.get(i.ref)))
                    for i in row_items}
            if any(hits.values()):
                rows.append({"bundle": bundle.ref, "items": {
                    **{i.id: False for i in bundle.items if i.kind in ("export", "skill")},
                    **hits}})
    out = []
    for row in rows:
        bundle = known.get(row["bundle"])
        if bundle is None:
            continue
        out.append({"bundle": bundle.ref, "items": {
            i.id: _value(i, row["items"], doc_format) for i in bundle.items}})
    return out


def lines(surface: str, selections: list[dict[str, Any]],
          known: dict[str, Bundle], *, doc_format: str | None = None) -> list[str]:
    """One plain line per running item."""
    out = []
    for row in clean(selections):
        bundle = known.get(row["bundle"])
        if bundle is None:
            continue
        for item in bundle.items:
            value = _value(item, row["items"], doc_format)
            if not value or target(item, surface) in (None, "self"):
                continue
            label = item.label or item.ref or item.kind
            if item.kind == "export":
                label = f"{label} ({str(value).upper()})"
            out.append(label)
    return list(dict.fromkeys(out))


__all__ = ["MINE", "MINE_SKILL", "Bundle", "Compiled", "Item", "SELF", "USER", "clean", "compile",
           "export_default", "lines", "of_row", "own_checks", "settled", "shipped", "target",
           "view"]
