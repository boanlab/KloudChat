"""Shipped skill packages: `app/skills/<key>/SKILL.md` plus fixed `scripts/*.py`.

The front matter (YAML) carries the catalogue fields; the markdown body is the
instruction text. A package's directory name is its catalogue key, so an
installed copy finds its scripts through `catalog_key` without a migration.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent / "skills"

#: Keys renamed when a skill became a package; old rows and copies still resolve.
RENAMED = {"calculation-unit-check": "calc-check"}

_FIELDS = ("name", "description", "when_to_use", "kinds", "required_tools", "version", "status",
           "stage", "entry", "post_check", "post_judge", "copy_check", "copy_kinds", "tables",
           "own_stage", "uses")
#: A check skill's stage: run by the server before (`pre`) or after (`post`) writing.
STAGES = ("pre", "post")

#: `draft` packages are kept out of the catalogue and `run_skill_script` unless
#: `settings.skill_drafts` is on, and then seeded private to the administrator.
_STATUSES = ("published", "draft")


class PackageError(ValueError):
    """A SKILL.md that cannot be read as a package."""


@dataclass(frozen=True, slots=True)
class SkillPackage:
    key: str
    name: str
    description: str
    when_to_use: str
    kinds: tuple[str, ...]
    required_tools: tuple[str, ...]
    version: str
    body: str
    root: Path
    status: str = "published"
    #: Script name (file stem) → its first docstring line.
    scripts: dict[str, str] = field(default_factory=dict)
    #: Paths relative to `root`, in upload order.
    files: tuple[str, ...] = ()
    #: `pre`/`post` for a check skill the server runs around writing; empty otherwise.
    stage: str = ""
    #: The script a check skill's server run imports.
    entry: str = ""
    #: Scripts the server runs on a chat answer whenever this skill is on the turn
    #: (`services/post_checks.py`), each `<key>/<script>`; a bare name is this package's.
    post_checks: tuple[str, ...] = ()
    #: Whether the post check also asks the judge model for statements the material lacks.
    post_judge: bool = False
    #: `copy_check: true` — its answers are copy: the copy check (`services/copy_check.py`)
    #: runs over them; `copy_kinds` narrows what it checks (a content plan: only claims,
    #: health claims and guarantees).
    copy_check: bool = False
    copy_kinds: tuple[str, ...] = ()
    #: `tables: true` — its answers are comparison tables: `source-check/post` checks cells.
    tables: bool = False
    #: `own_stage: true` — a `post` check that runs in a stage of its own, not in `ground`.
    own_stage: bool = False
    #: What the judge looks at in particular for this skill (`post_judge: "<focus>"`).
    judge_focus: str = ""

    @property
    def draft(self) -> bool:
        return self.status == "draft"

    def spec(self) -> dict:
        """The catalogue entry `starter._SKILLS` expects."""
        return {
            "draft": self.draft,
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "kinds": list(self.kinds),
            "required_tools": list(self.required_tools),
            "version": self.version,
            "body": self.body,
        }

    def script_path(self, script: str) -> str | None:
        """`skills/<key>/scripts/<script>.py` as mounted in the sandbox, or None."""
        stem = script.strip().removesuffix(".py")
        if stem not in self.scripts:
            return None
        return f"skills/{self.key}/scripts/{stem}.py"

    def upload_files(self) -> list[tuple[str, bytes]]:
        """`(sandbox name, bytes)` for every file of the package."""
        return [(f"skills/{self.key}/{rel}", (self.root / rel).read_bytes()) for rel in self.files]

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for name, data in self.upload_files():
            digest.update(name.encode())
            digest.update(hashlib.sha256(data).digest())
        return digest.hexdigest()[:16]


def split_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise PackageError("SKILL.md must open with a --- front matter block")
    _, _, rest = text.partition("\n")
    head, marker, body = rest.partition("\n---")
    if not marker:
        raise PackageError("front matter is not closed")
    meta = yaml.safe_load(head) or {}
    if not isinstance(meta, dict):
        raise PackageError("front matter must be a mapping")
    return meta, body.partition("\n")[2].strip()


def _summary(path: Path) -> str:
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    except SyntaxError as exc:
        raise PackageError(f"{path.name} does not parse") from exc
    return doc.strip().splitlines()[0] if doc.strip() else ""


def load(root: Path) -> SkillPackage:
    meta, body = split_front_matter((root / "SKILL.md").read_text(encoding="utf-8"))
    missing = [name for name in ("name", "description", "when_to_use") if not meta.get(name)]
    if missing:
        raise PackageError(f"{root.name}: missing {', '.join(missing)}")
    unknown = set(meta) - set(_FIELDS)
    if unknown:
        raise PackageError(f"{root.name}: unknown fields {', '.join(sorted(unknown))}")
    status = str(meta.get("status") or "published").strip()
    if status not in _STATUSES:
        raise PackageError(f"{root.name}: status must be one of {', '.join(_STATUSES)}")
    stage = str(meta.get("stage") or "").strip()
    if stage and stage not in STAGES:
        raise PackageError(f"{root.name}: stage must be one of {', '.join(STAGES)}")
    post_checks = _post_checks(root.name, meta.get("post_check"))
    scripts_dir = root / "scripts"
    scripts = {
        path.stem: _summary(path)
        for path in sorted(scripts_dir.glob("*.py"))
        if not path.name.startswith("_")
    } if scripts_dir.is_dir() else {}
    files = ["SKILL.md"] + [
        str(path.relative_to(root))
        for folder in ("scripts", "resources")
        if (root / folder).is_dir()
        for path in sorted((root / folder).rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    ]
    return SkillPackage(
        key=root.name,
        name=str(meta["name"]).strip(),
        description=" ".join(str(meta["description"]).split()),
        when_to_use=" ".join(str(meta["when_to_use"]).split()),
        kinds=tuple(meta.get("kinds") or ("chat",)),
        required_tools=tuple(meta.get("required_tools") or ()),
        version=str(meta.get("version") or "1.0.0"),
        body=body,
        root=root,
        status=status,
        scripts=scripts,
        files=tuple(files),
        stage=stage,
        entry=str(meta.get("entry") or "").strip(),
        post_checks=post_checks,
        post_judge=bool(meta.get("post_judge")),
        copy_check=bool(meta.get("copy_check")),
        copy_kinds=tuple(str(k) for k in meta.get("copy_kinds") or ()),
        tables=bool(meta.get("tables")),
        own_stage=bool(meta.get("own_stage")),
        judge_focus=(" ".join(str(meta["post_judge"]).split())
                     if isinstance(meta.get("post_judge"), str) else ""),
    )


def _post_checks(key: str, raw) -> tuple[str, ...]:
    """`post_check: preserve` or a list, each named `<key>/<script>` (own key when bare)."""
    items = [raw] if isinstance(raw, str) else list(raw or [])
    out = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        if not re.fullmatch(r"(?:[a-z0-9-]{1,60}/)?[a-z0-9_]{1,40}", text):
            raise PackageError(f"{key}: post_check {text!r} must be <script> or <key>/<script>")
        out.append(text if "/" in text else f"{key}/{text}")
    return tuple(dict.fromkeys(out))


@lru_cache(maxsize=1)
def packages() -> dict[str, SkillPackage]:
    """Every package under `app/skills`, by key."""
    if not ROOT.is_dir():
        return {}
    return {
        path.name: load(path)
        for path in sorted(ROOT.iterdir())
        if path.is_dir() and (path / "SKILL.md").is_file()
    }


def package_for(key: str | None) -> SkillPackage | None:
    """The package behind a catalogue key (renamed keys included)."""
    if not key:
        return None
    key = key.strip()
    return packages().get(RENAMED.get(key, key))


def drafts_enabled() -> bool:
    from app.core.config import settings

    return bool(settings.skill_drafts)


def offered() -> list[SkillPackage]:
    """Packages the catalogue and `run_skill_script` offer: drafts only while enabled."""
    drafts = drafts_enabled()
    return [package for package in packages().values() if drafts or not package.draft]


def find(skill: str) -> SkillPackage | None:
    """An offered package by key or by its shipped name, as a model may write either."""
    text = skill.strip().strip("「」\"'")
    found = package_for(text)
    if found is None:
        found = next((pkg for pkg in packages().values() if pkg.name == text), None)
    if found is not None and found.draft and not drafts_enabled():
        return None
    return found


def post_checked() -> list[SkillPackage]:
    """Packages that name post-check scripts, for the server's run on a chat answer."""
    return [package for package in packages().values() if package.post_checks]


def checks(stage: str) -> list[SkillPackage]:
    """Check skills of one stage, for the server's own runs."""
    return [package for package in packages().values() if package.stage == stage]


def catalog_specs() -> list[dict]:
    return [package.spec() for package in offered()]


def scripted() -> list[SkillPackage]:
    """Offered packages that ship at least one script, for `run_skill_script`."""
    return [package for package in offered() if package.scripts]


def script_names(key: str | None) -> list[str]:
    package = package_for(key)
    return sorted(package.scripts) if package else []
