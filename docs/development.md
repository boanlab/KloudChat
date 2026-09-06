# Development

- [Setup](#setup)
- [Web app](#web-app)
- [API](#api)
- [Migrations](#migrations)
- [Tests](#tests)
- [Adding things](#adding-things)
- [Troubleshooting](#troubleshooting)

## Setup

```bash
git clone https://github.com/boanlab/KloudChat.git
cd KloudChat

cp .env.example .env
sed -i "s/^KCHAT_JWT_SECRET=.*/KCHAT_JWT_SECRET=$(openssl rand -hex 32)/" .env
sed -i "s/^KCHAT_SECRET_KEY=.*/KCHAT_SECRET_KEY=$(openssl rand -hex 32)/" .env

make build       # or: docker compose -f docker-compose.yml \
                 #                    -f docker-compose.build.yml up -d --build
```

`make up` runs the images published to Docker Hub, which is what a deployment
does. `make build` builds every image from this checkout, which is what you
want while working on any of them.

`make help` lists everything below as a target. `make check` runs exactly what
CI runs, in the same order.

You do not need a model backend to work on most of the app. Projects, skills,
memory, agents, history, sharing and administration all function without one;
model calls and tools report "not connected". Point `BACKEND_BASE_URL` at a
running [`KloudChat-LLM`](https://github.com/boanlab/KloudChat-LLM)
when you need to exercise a generation path.

## Web app

The web image serves a compiled bundle, so editing source has no effect on it.
Two ways to get hot reload:

```bash
make dev
# docker compose -f docker-compose.yml \
#                -f docker-compose.build.yml \
#                -f docker-compose.dev.yml up -d --build
```

or run Vite on the host against the containerised API:

```bash
cd apps/web
npm ci
npm run dev          # http://localhost:5173, proxying /api to :8100
```

Checks:

```bash
npm run lint         # oxlint
npm run build        # tsc -b && vite build
```

`npm run build` is the real gate — `oxlint` is fast and shallow, and code that
lints clean can still fail to compile.

**Stack:** React 19, TypeScript, Vite, Tailwind v4, zustand, react-router,
lucide-react, react-markdown.

**One store.** `src/store/useStore.ts` is a single zustand store. Workspace
writes call `touchWorkspace()` so the epoch guard rejects stale
`loadWorkspace()` responses.

**One backend seam.** Everything that talks to the API goes through
`src/lib/api.ts`.

**Interface strings.** The i18n dictionary in `src/lib/i18n.ts` is keyed on the
Korean source string, and `useT()` translates into the current language. A new
string needs an English entry; without one it renders as the Korean original,
which is the intended fallback rather than a bug.

## API

```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

ruff check .
pytest -q
```

Python 3.12, FastAPI, SQLModel over asyncpg, Alembic.

Unit tests deliberately cover only the pure parts — binary format parsing,
pricing arithmetic. Anything that needs a database is covered by the
integration scripts or by Playwright, because a mocked async session tends to
test the mock.

Iterating against the container without rebuilding:

```bash
docker compose logs -f api
docker compose restart api
docker compose exec api python -c "from app.core.config import settings; print(settings.env)"
```

Interactive API docs are served at <http://localhost:8100/docs> when `ENV=dev`.
They are disabled in `prod`.

## Migrations

Applied on container start. To add one:

```bash
make revision m="add widget table"
# docker compose exec api alembic revision --autogenerate -m "…"
make migrate
```

**Read the generated file before committing it.** Autogenerate does not see
server defaults, and it will emit a `DROP COLUMN` for anything it failed to
reflect. Then check it against a populated database, not an empty one — a
`NOT NULL` column added without a default is fine on an empty table and fails
on a real one.

Migration files are exempt from the line-length rule (`pyproject.toml`,
`per-file-ignores`): reformatting generated files by hand makes the next
generated file conflict with the last hand-edited one.

## Tests

Three layers, in increasing cost:

### Unit

```bash
cd apps/api && pytest -q
```

No services required.

### API integration

Non-destructive: each run creates its own accounts and workspace objects and
removes them at the end.

```bash
bash scripts/smoke-test.sh      # auth, approval, rotation, suspension
bash scripts/workspace-test.sh  # workspace CRUD
bash scripts/context-test.sh    # context assembly reaches the answer
bash scripts/e2e-seed.sh        # create and approve the Playwright account
bash scripts/load-test.sh       # concurrent reads from two accounts; 5xx and isolation
```

`context-test.sh` makes real model calls, so it is slow and needs a backend.
The others do not.

**Credentials.** All five sign in as an administrator, resolved in this order
(see [`scripts/lib/env.sh`](../scripts/lib/env.sh)):

1. `ADMIN_EMAIL` / `ADMIN_PASS` already exported
2. `ADMIN_EMAIL` / `ADMIN_PASS` in `.env`
3. `KCHAT_ADMIN_EMAIL` / `KCHAT_ADMIN_PASSWORD` in `.env`
4. the script's own default

So a stock `.env` needs nothing passed. Step 2 exists because the bootstrap
administrator and the account the checks use are not always the same one — an
instance whose first admin arrived by signing up has no `KCHAT_ADMIN_*` at all.
Override for one run with:

```bash
ADMIN_EMAIL=you@example.com ADMIN_PASS=… bash scripts/smoke-test.sh
```

`.env` is parsed rather than sourced, and only credential keys are read: it
holds values that are not shell (`KCHAT_CORS_ORIGINS` is a JSON array), and
executing it would be a surprising thing for a test script to do. Set
`KCHAT_ENV_FILE` to read a different file, or point it at `/dev/null` to skip
the file entirely.

### Browser

```bash
cd apps/web
npx playwright install chromium   # once
npm run test:e2e
npx playwright test --project=desktop
npx playwright test e2e/auth.spec.ts --project=desktop --reporter=list
```

Run `scripts/e2e-seed.sh` first — the suite signs in as a seeded, approved
account.

**Do not override `--workers`.** The config pins `workers: 1`. Every spec uses
the same account and several pick "the most recent X"; in parallel they produce
failures that have nothing to do with the app. Isolation would mean an account
per file, and an account needs administrator approval — one worker is the
cheaper honest answer.

**Some specs spend real money**: roughly 4,400 credits for an image, 1,000 for
audio, 12,000 for a video. Each creates exactly one artifact, and the numbers
exist to check that the quote and the charge agree. Skip them while iterating:

```bash
npx playwright test --project=desktop --grep-invert "video|audio|image"
```

Three projects are defined — `desktop` (1440×900), `laptop` (1280×800) and
`tablet` (820×1180 with touch). All three run on Chromium, so
`npx playwright install chromium` is enough for the whole suite.

### The rule that matters

**Watch the test fail before you fix the code.** A spec that passes identically
before and after a change is guarding nothing.

## Adding things

**A sixth output kind.** Start at `SessionKind` in `apps/web/src/types.ts` and
the metadata table in `apps/web/src/lib/kinds.ts`; those two are the single
source of truth the UI branches on. Server-side, the kind has to be accepted by
the session router and appear in `OPTIONAL_KINDS` in
`app/services/settings_store.py` so an administrator can toggle it.

**A built-in tool.** `app/services/tools/builtin.py` — the function and its
`Tool` schema sit together there; `registry.py` only assembles the turn's list
and namespaces connector tools. Tools are attached only to models that support
function calling; giving them to a model that does not produces either a 400
from upstream or an invented call.

Keep the total small. Every active tool ships its full schema on every turn,
and model selection accuracy degrades well before twenty of them.

**A connector.** `app/services/tools/catalog.py`. Only add servers you have
actually started and whose tool count you have checked.

**A model the proxy does not know about.** `app/services/adapters.py`. Video
models live there because OpenRouter serves them from a separate endpoint that
`/model/info` does not cover — and only models `videogen.submit` can actually
call belong in that list.

## Troubleshooting

**`docker compose up` fails with "set KCHAT_JWT_SECRET".** That is the
intended error. Generate one: `openssl rand -hex 32`.

**Uploads fail with "permission denied" on a first boot.** The `init`
container exists to fix exactly this — Docker creates bind-mount directories as
root and the API runs as uid 1000. Check that it completed:
`docker compose ps -a | grep init`.

**Every `/api/` call 502s after recreating `kloudchat-api`.** The web container's
nginx resolves the upstream through a variable specifically to avoid this, so
if it happens, check that `KCHAT_API_URL` is set on `kloudchat-web`.

**The model list is empty.** `GET /api/admin/settings` reports which models
were dropped and why. A model whose price is unknown is removed deliberately —
upstream reporting 0 means "price unknown", not "free", and showing it would
bill real money against a 0-credit counter.

**Playwright fails on unrelated specs.** Check that you have not overridden
`--workers`, and that `scripts/e2e-seed.sh` has run.

**A generated image or video does not display.** Media elements fetch with the
access token in the query string (`?t=…`) because `<img>` and `<video>` cannot
send an `Authorization` header. If a proxy in front strips query strings, this
is what breaks.
