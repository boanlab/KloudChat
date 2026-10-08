"""Document and chat flows as pipelines of stages over one work bundle.

`bundle` — the typed work a turn carries; `stage` — the stage contract; `runner` — runs a
pipeline's stages in order, recording each; `registry` — stages by name and the pipelines
declared in `app/pipelines/*.toml`. See docs/architecture.md § Pipelines.
"""
