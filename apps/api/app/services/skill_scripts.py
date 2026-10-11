"""Shipped stdlib skill scripts imported for server use, same file the sandbox runs."""

from __future__ import annotations

import importlib.util
import sys
from functools import cache
from types import ModuleType

from app.services import skill_packages


@cache
def module(key: str, script: str) -> ModuleType:
    """`app/skills/<key>/scripts/<script>.py` as a module, cached per process."""
    package = skill_packages.package_for(key)
    if package is None or script not in package.scripts:
        raise LookupError(f"no script {key}/{script}")
    path = package.root / "scripts" / f"{script}.py"
    name = f"_skill_{key.replace('-', '_')}_{script}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise LookupError(f"cannot load {path}")
    loaded = importlib.util.module_from_spec(spec)
    # dataclasses look the module up by name during exec
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def dates() -> ModuleType:
    return module("date-calc", "dates")


def quotes() -> ModuleType:
    return module("quote-calc", "quote")


def text_fit() -> ModuleType:
    return module("text-fit", "fit")


def entry(key: str) -> ModuleType:
    """Check skill's `entry` script, for the server's own run."""
    package = skill_packages.package_for(key)
    if package is None or not package.stage or not package.entry:
        raise LookupError(f"{key} is not a check skill")
    return module(package.key, package.entry)
