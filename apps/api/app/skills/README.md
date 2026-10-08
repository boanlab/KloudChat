# Skill packages

Each folder is one catalogue skill: `SKILL.md` (YAML front matter + instructions) and fixed
`scripts/*.py` (standard library only). The sandbox runs a script through `run_skill_script`;
the server imports the same file through `services/skill_scripts.py`, so both compute the same.

## Check skills

A package with `stage` in its front matter is a check the server runs itself, around writing,
for every agent or 서식 that attaches it — the model is not left to call it.

| field   | meaning                                                                 |
|---------|-------------------------------------------------------------------------|
| `stage` | `pre` (before writing: what to ask) or `post` (after writing: what to fix) |
| `entry` | the script under `scripts/` the server imports                          |

Attach a check by key: `checks = ["gap-check", "grounding-check"]` in `app/agents/<key>.toml`, or
`check_skills = [...]` in a 서식's `template.toml` (`checks` there is already the checklist).
`services/check_skills.py` resolves the attached keys and runs them; the pipelines run them as
`check` stages (`app/pipeline/check_stages.py`: `check:gaps`, `check:grounding`,
`check:answer-grounding`; docs/architecture.md § Pipelines). `copy-check` runs as a stage of its
own, `check:copy`, whenever a copy skill is on a chat turn (`services/copy_check.py`).

The entry script's functions, by stage:

- `pre`: `check(request, material, computed=None, options=None) -> list[dict]` with findings
  `{"kind": "gap", "fact", "label", "question", "why", "placeholder"}`, and `rule(findings) -> str`
  (the writer's line when the person goes on without the facts). The server puts the findings up
  as one question card, never twice in a row; answers are merged into the request as material.
- `post`: `check(text, material, computed=None, options=None) -> list[dict]` with findings
  `{"kind", "text", "line", "start", "end", "placeholder"}` (`line` indexes `text.split("\n")`,
  `start`/`end` are offsets in that line), `fix(text, findings) -> str`, and
  `summary(findings) -> str` for the step record. The server checks, fixes once, checks again,
  and records both counts in the format-checks step (reports) or a step of its own (chat).

`material` is what the person gave (request, answers, attachments); `computed` is what code
worked out (pre-pass blocks, settled figures, tool output). A check that needs a model's
judgment ships its prompt as a file in the package with a fixed JSON reply schema; the server
runs it with the judge model and passes the parsed reply in `computed`.

Every script also has a CLI: `python scripts/<entry>.py '<JSON>'`, the last line of output
being the result JSON.
