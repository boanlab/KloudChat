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
│   ├── agents/         An agent's knowledge, 진행 과정 and gap questions
│   ├── customize/      사용자 지정 hub: agent, skill and 서식 forms, sharing, 체험
│   ├── publishing/     Publish dialog, channels (link, embed, API, MCP), scope
│   ├── issues/         문의·신고 dialog, thread, read-only session copy
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

**Focused configs** (`playwright.<name>.config.ts`) need no stack. Each starts its
own Vite server (dev, or `build` + `preview`) on a fixed port, points
`API_BASE_URL` at an unused port and answers the API with `page.route` fixtures
(`e2e/*-fixture.ts`, `e2e/shell-mocks.ts`, `e2e/fixtures/`). CI runs them one by
one (`.github/workflows/ci.yml`):

```bash
npx playwright test --config playwright.freshness.config.ts --workers=1
```

Exceptions: `playwright.chat-errors.config.ts` signs in to a running app and fakes
only the turn's stream; `playwright.admin-models.config.ts` is fixture-only but
not in CI. `ocr` and `ux` share port 5361, so run them one after the other.

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
