"""How much scaffolding a writer model gets.

Small self-hosted models need the guard rails (rules in the prompt, checks that rewrite);
strong models write better without them, and a weaker judge must not rewrite a stronger
writer's answer. Strong writers get tools, material, deterministic checks and marks only."""

from __future__ import annotations

import os

#: Writer models that get the light setup. Exact names or prefixes ending in "/" or "-".
STRONG_MODELS: tuple[str, ...] = (
    "anthropic/",
    "openai/gpt-6",
    "openai/gpt-5.",
    "google/gemini-3.1-pro",
    "deepseek/deepseek-v4-pro",
    "qwen/qwen3.8-max",
    "z-ai/glm-5.3",
    "moonshotai/kimi-k3",
    "x-ai/grok-4",
)

#: Names that look strong by prefix but are the small variant.
_SMALL_SUFFIXES: tuple[str, ...] = ("-flash", "-nano", "-mini", "-lite", "-luna", ":free")


#: Model groups each post-writing mechanism decides by (docs/architecture.md, "Mechanisms by
#: model group"): the self-hosted models (27B, flash), small external models, strong external
#: models.
SELF_HOSTED, SMALL_EXTERNAL, STRONG_EXTERNAL = "self_hosted", "small_external", "strong_external"
GROUPS: tuple[str, ...] = (SELF_HOSTED, SMALL_EXTERNAL, STRONG_EXTERNAL)


def self_hosted(model: str | None) -> bool:
    """True for the self-hosted models (`local/`, `strict-local/`) and for no model."""
    name = (model or "").strip().lower()
    return not name or name.startswith(("local/", "strict-local/"))


def group(model: str | None) -> str:
    """The writer's model group. Self-hosted stays self-hosted under the light-setup evaluation
    switch (`KCHAT_LIGHT_WRITERS`): the switch changes the prompt (`strong`), not who the
    model is."""
    if self_hosted(model):
        return SELF_HOSTED
    return STRONG_EXTERNAL if strong(model) else SMALL_EXTERNAL


def strong(model: str | None) -> bool:
    """True when the writer is a strong model: light prompt, marks instead of rewrites."""
    name = (model or "").strip().lower()
    # evaluation switch: exact model names (comma-separated) treated as strong; empty by default
    forced = {n.strip().lower() for n in os.environ.get("KCHAT_LIGHT_WRITERS", "").split(",")}
    if name and name in forced:
        return True
    if self_hosted(name):
        return False
    if any(s in name for s in _SMALL_SUFFIXES):
        return False
    return any(name == m or name.startswith(m) for m in STRONG_MODELS)
