# KloudChat web

The single-page app. It talks to exactly one thing — the KloudChat API — and has no
route to LiteLLM, the model backend, or any credential belonging to them.

React 19 · TypeScript · Vite · Tailwind v4 · zustand · react-router ·
lucide-react · react-markdown.

## Running

```bash
npm ci
npm run dev        # Vite on :5173, proxying /api to API_BASE_URL
npm run lint       # oxlint
npm run test:config  # node --test for the Vite config
npm run build      # tsc -b && vite build
```

`npm run build` is the gate: the type check is part of it, so code that lints
clean can still fail here.

The dev server proxies `/api` to `API_BASE_URL` (default
`http://localhost:8100`). The compose stack publishes no API port, so against a
running stack point it at the web container instead and take another port:
`API_BASE_URL=http://localhost:5173 npm run dev -- --port 5174`. From the
repository root, `make dev` runs Vite inside Docker in place of the web
container, with the proxy already pointed at the `api` service.

## Layout

```
src/
├── components/
│   ├── artifacts/      ArtifactPanel (branching by kind), ribbon, lint and fact-check results, version history
│   ├── chat/           Composer, MessageItem, StepTimeline, Markdown
│   ├── report/         ReportPanel (TOC, section streaming, sources, export), DocumentEditor, block renderers
│   ├── slides/         DeckPanel (renderer, thumbnail grid, per-slide editing), slide patterns, type scale
│   ├── chart/          Chart, underlying-data tab, PNG/SVG/CSV
│   ├── media/          Asynchronous generation card (progress → result)
│   ├── files/          File box: list, picker, preview, delete choice
│   ├── agents/         An agent's knowledge, process (How it works) and gap questions
│   ├── customize/      Customize hub: agent, skill and template forms, sharing, Use now
│   ├── publishing/     Publish dialog, channels (link, embed, API, MCP), scope
│   ├── issues/         Feedback & issues dialog, thread, read-only session copy
│   ├── share/          Read-only link creation and revocation
│   ├── layout/         AppShell, Sidebar, TopBar, Brand
│   └── ui/index.tsx    Button, Modal, Dropdown, Badge, …
├── lib/
│   ├── api.ts          the single backend seam
│   ├── kinds.ts        single source of truth for the five surfaces
│   ├── i18n.ts         dictionary keyed on the Korean source string
│   ├── useT.ts         hook translating into the current language
│   ├── shortcuts.ts    keyboard shortcuts and their help list
│   └── …               small helpers (clipboard, brand, paging, Markdown round-trip, …)
├── pages/              one per route, plus customize/ and settings/ sections
├── store/useStore.ts   single zustand store
└── types.ts            domain types (discriminated unions)
```

Playwright specs live in `e2e/`, beside `src/`.

## Conventions

**One store.** `store/useStore.ts` holds application state. Workspace writes
call `touchWorkspace()` so the epoch guard rejects stale `loadWorkspace()`
responses.

**One backend seam.** Every call to the API goes through `lib/api.ts`. Nothing
else constructs a request.

**Interface strings go through `useT()`.** The dictionary in `lib/i18n.ts` is
keyed on the Korean source string. A new string needs an English entry;
without one it renders as the Korean original, which is the intended fallback.

**`lib/kinds.ts` decides what a surface is.** Icon, label, route, default
model, whether an artifact panel opens — all of it. Adding a sixth surface
starts there and at `SessionKind` in `types.ts`.

**Media elements fetch with `?t=<token>`.** `<img>`, `<audio>` and `<video>`
cannot attach an `Authorization` header, and the access token lives in memory
rather than in a cookie. The exception is deliberate and confined to
`GET /api/files/{id}/content`.

## Tests

```bash
npx playwright install chromium   # once
```

**The fixture suite** (`playwright.ci.config.ts`) needs no stack. It builds the
app, serves it once with `vite preview` on port 5390 (`E2E_PORT` to change it),
points `API_BASE_URL` at an unused port and answers the API with `page.route`
fixtures (`e2e/*-fixture.ts`, `e2e/shell-mocks.ts`, `e2e/fixtures/`). Its projects
are `<surface>/<viewport>` (`customize/desktop`, `customize/phone`, …); each
surface lists its spec files and the viewports it is checked at. CI splits the
projects over four jobs with `--shard` (`.github/workflows/ci.yml`):

```bash
npm run test:e2e:ci                                                   # the whole suite
npx playwright test -c playwright.ci.config.ts e2e/publishing.spec.ts # one file, all its viewports
npx playwright test -c playwright.ci.config.ts --project='customize/*' # one surface
npx playwright test -c playwright.ci.config.ts --shard=2/4            # what CI job 2 runs
E2E_PREBUILT=1 npx playwright test -c playwright.ci.config.ts         # serve the dist/ you already built
```

A new fixture spec goes into a surface in `playwright.ci.config.ts`, or CI never
runs it. A test is checked once, at desktop width; one about the phone
(layout, the drawer, touch, widths) also carries `{ tag: '@phone' }`, and the
`phone`/`mobile` projects run only those. The report is in `playwright-report/` (`npx playwright show-report`).

Exception: `playwright.chat-errors.config.ts` signs in to a running app and fakes
only the turn's stream, so it stays out of CI.

**The default config** (`playwright.config.ts`) runs the remaining specs against a
running stack at `http://localhost:5173` with a seeded account
(`bash scripts/e2e-seed.sh` from the repository root):

```bash
npx playwright test --project=desktop e2e/chat.spec.ts
```

It pins `workers: 1`; do not override it. Every spec signs in as the same account
and several pick "the most recent X". Some specs spend real credits (roughly
4,400 for an image, 1,000 for audio, 12,000 for a video); exclude them with
`--grep-invert` while iterating.

`tests/vite-config.test.mjs` (`npm run test:config`) checks that the Vite config
resolves source aliases in workspace paths with spaces and non-ASCII characters.

See [docs/development.md](../../docs/development.md) for the rest.
