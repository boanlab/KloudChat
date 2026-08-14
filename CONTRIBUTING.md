# Contributing to KloudChat

Thanks for taking the time. This document covers how to get a working
environment, what the review looks for, and the few conventions that are not
obvious from the code.

## Table of contents

- [Getting a stack running](#getting-a-stack-running)
- [Working on the web app](#working-on-the-web-app)
- [Working on the API](#working-on-the-api)
- [Tests](#tests)
- [Conventions](#conventions)
- [Commits and pull requests](#commits-and-pull-requests)
- [Where things live](#where-things-live)

## Getting a stack running

KloudChat is two containers and a database. Models and tools are **not** part of
this repository — they live in [`KloudChat-LLM`][backend], and KloudChat
reaches them through a single gateway URL configured at runtime.

```bash
git clone https://github.com/boanlab/KloudChat.git
cd KloudChat

cp .env.example .env
sed -i "s/^KCHAT_JWT_SECRET=.*/KCHAT_JWT_SECRET=$(openssl rand -hex 32)/" .env

make build      # docker compose -f docker-compose.yml \
                #                -f docker-compose.build.yml up -d --build
curl localhost:8100/api/health
```

`docker compose up -d` on its own pulls the published images, which is what a
deployment wants. As a contributor you want the build overlay above, so that
both images come from your checkout.

The first account to sign up becomes the administrator, unless
`KCHAT_ADMIN_EMAIL` / `KCHAT_ADMIN_PASSWORD` are set, in which case that
account is created on first boot.

You can develop the whole workspace — projects, skills, memory, agents,
history, admin — without a backend. Model calls and tools will report
"not connected", and everything else works. Point at a backend when you need
to exercise a generation path.

## Working on the web app

The web image serves a static bundle, so source edits do not appear. Overlay
the dev compose file to swap in Vite with hot reload:

```bash
make dev        # docker compose -f docker-compose.yml \
                #                -f docker-compose.build.yml \
                #                -f docker-compose.dev.yml up -d --build
```

Or run it directly against a containerised API:

```bash
cd apps/web
npm ci
npm run dev        # http://localhost:5173, proxies /api to :8100
npm run lint
npm run build      # tsc -b && vite build — the typecheck is part of the build
```

`npm run build` is the gate. `oxlint` is fast and shallow; a change that lints
clean can still fail to compile.

## Working on the API

```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

ruff check .
pytest -q
```

The API talks to Postgres over asyncpg, so the unit tests deliberately cover
only the pure parts — binary format parsing, pricing arithmetic. Anything that
needs a database is covered by the integration scripts in [`scripts/`](scripts)
or by Playwright.

Migrations are applied on container start (`alembic upgrade head`). To add one:

```bash
docker compose exec api alembic revision --autogenerate -m "short description"
docker compose exec api alembic upgrade head
```

Read the generated file before committing. Autogenerate does not see server
defaults, and it will happily emit a `DROP COLUMN` for a column it could not
reflect.

## Tests

Three layers, in increasing cost:

| Layer | Command | Needs |
| --- | --- | --- |
| Unit | `pytest -q` in `apps/api` | nothing |
| API integration | `bash scripts/smoke-test.sh` | a running stack + an admin account |
| Browser | `npx playwright test` in `apps/web` | a running stack + a seeded account |

The integration scripts are **non-destructive**. Each run creates its own
accounts and workspace objects and deletes them at the end; they do not touch
existing users or conversations.

They read `ADMIN_EMAIL` and `ADMIN_PASS` from the repository's `.env` when they
are not already set, falling back to `KCHAT_ADMIN_EMAIL` / `KCHAT_ADMIN_PASSWORD`
— so with a stock `.env` there is nothing to pass:

```bash
bash scripts/smoke-test.sh      # auth, approval, rotation, suspension
bash scripts/workspace-test.sh  # workspace CRUD
bash scripts/context-test.sh    # context assembly reaches the answer
bash scripts/e2e-seed.sh        # create and approve the Playwright account
```

Override either of them in the environment when the account the checks should
use is not the bootstrap administrator:

```bash
ADMIN_EMAIL=you@example.com ADMIN_PASS=… bash scripts/smoke-test.sh
```

For the browser suite:

```bash
cd apps/web
npx playwright install chromium   # once
npm run test:e2e
npx playwright test --project=desktop
```

Two things about Playwright here:

**Do not override `--workers`.** The config pins `workers: 1`. Every spec signs
in as the same seeded account and several of them pick "the most recent X";
running in parallel produces failures that have nothing to do with the app.

**Some specs spend real money** — roughly 4,400 credits for an image, 1,000 for
audio, 12,000 for a video. Each creates exactly one artifact, and the number is
there to check that the quote and the charge agree. Exclude them with
`--grep-invert` if you are iterating.

One rule that matters more than the layer: **watch the test fail before you fix
the code.** A spec that passes identically before and after your change is
guarding nothing.

## Conventions

**Comments carry decisions, not descriptions.** The code says what it does. A
comment earns its place by recording why it is that way — a unit, an ordering
constraint, a fail-closed default, a failure that was actually observed. All
comments and documentation are written in English.

**Fail closed on anything priced.** A provider reporting a cost of zero means
"unknown", not "free". Unknown prices are dropped from the catalogue with a
reason surfaced in the admin screen, rather than shown as a 0-credit model.

**Modality units are not interchangeable.** Chat is priced per 1k tokens,
images per image, audio per call, video per second × (resolution, audio). Any
UI that renders a price renders its unit with it.

**User-facing strings go through `useT()`.** The i18n dictionary uses the
Korean source string as the key (`lib/i18n.ts`); a new string needs an English
entry, and untranslated strings fall back to the Korean original.

**The master key never leaves the API process.** No route returns it, no log
line prints it, and the browser has no path to LiteLLM. Per-user virtual keys
are what upstream calls are made with.

**Adding a sixth output kind** starts at `SessionKind` in
`apps/web/src/types.ts` and the metadata table in `apps/web/src/lib/kinds.ts`.

## Commits and pull requests

- Branch from `main`. One logical change per pull request.
- Write the commit subject in the imperative, under ~72 characters. The body
  explains why, if that is not obvious.
- Fill in the pull request template — particularly the verification section.
  "Tested locally" is not a verification.
- CI must be green: lint and build for the web app, `ruff` and `pytest` for the
  API, `shellcheck` for the scripts, and both images must build.

## Where things live

```
KloudChat/
├── apps/
│   ├── web/          React 19 + Vite + Tailwind v4 single-page app
│   └── api/          FastAPI — auth, sessions, jobs, artifacts, the master key
├── docs/             Architecture and operator guides
├── scripts/          Integration checks against a running stack
├── mcp/              MCP stdio server scripts mounted into the API container
└── docker-compose*.yml
```

Start with [`docs/architecture.md`](docs/architecture.md). It documents what the
code currently does and why the load-bearing decisions are the way they are.

[backend]: https://github.com/boanlab/KloudChat-LLM
