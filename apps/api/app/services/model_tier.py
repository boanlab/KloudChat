"""How much scaffolding a writer model gets.

Small self-hosted models need the guard rails (rules in the prompt, checks that rewrite);
strong models write better without them, and a weaker judge must not rewrite a stronger
writer's answer (round 7: the same model scored lower inside KloudChat than called
directly). Strong writers get tools, material, deterministic checks and marks only."""

from __future__ import annotations

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


def strong(model: str | None) -> bool:
    """True when the writer is a strong model: light prompt, marks instead of rewrites."""
    name = (model or "").strip().lower()
    if not name or name.startswith(("local/", "strict-local/")):
        return False
    if any(s in name for s in _SMALL_SUFFIXES):
        return False
    return any(name == m or name.startswith(m) for m in STRONG_MODELS)
