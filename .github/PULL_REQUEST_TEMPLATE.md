<!--
Keep the description about behaviour. A diff shows what changed; this box is
for what it changes for someone using the app.
-->

## What this changes

<!-- One or two sentences. Link the issue it closes, if there is one. -->

## Why

<!--
If the change encodes a non-obvious decision — a unit, an ordering, a
fail-closed default — say so here and leave the reason in a comment next to
the code. The next person to touch it will not have this pull request open.
-->

## How it was verified

<!-- Tick what you actually ran, and paste the failing-before output if there is one. -->

- [ ] `npm run lint && npm run build` in `apps/web`
- [ ] `ruff check . && pytest -q` in `apps/api`
- [ ] `npx playwright test --project=desktop` (say which specs)
- [ ] `bash scripts/smoke-test.sh` against a live stack
- [ ] Manual: <!-- what you clicked through -->

## Checklist

- [ ] A test fails without this change and passes with it, or the change is not testable and the description says why.
- [ ] No credential, token, or master key is logged, returned in a response, or committed.
- [ ] User-facing strings go through `useT()`; new Korean source strings have an English entry in `lib/i18n.ts`.
- [ ] Schema changes ship with an Alembic migration that runs on a populated database.
- [ ] Anything priced (a model, a modality, a unit) reports the unit it is actually billed in.
- [ ] Documentation under `docs/` and the READMEs still describes what the code does.
