# KloudChat web

The single-page app. It talks to exactly one thing — the KloudChat API — and has no
route to LiteLLM, the model backend, or any credential belonging to them.

React 19 · TypeScript · Vite · Tailwind v4 · zustand · react-router ·
lucide-react · react-markdown.

## Running

```bash
npm ci
npm run dev        # http://localhost:5173, proxying /api to :8100
npm run lint       # oxlint
npm run build      # tsc -b && vite build
```

`npm run build` is the gate: the type check is part of it, so code that lints
clean can still fail here.

The dev server proxies `/api` to `API_BASE_URL` (default
`http://localhost:8100`), so a containerised API works unchanged. From the
repository root, `make dev` does the same thing inside Docker.

## Layout

```
src/
├── components/
│   ├── artifacts/ArtifactPanel.tsx   Right-hand panel, branching by artifact kind
│   ├── chat/                         Composer, MessageItem, StepTimeline, Markdown
│   ├── media/JobCard.tsx             Asynchronous generation card (progress → result)
│   ├── report/ReportPanel.tsx        TOC, section streaming, sources, export
│   ├── slides/DeckPanel.tsx          Slide renderer, thumbnail grid, per-slide editing
│   ├── chart/ChartPanel.tsx          Chart, underlying-data tab, PNG/SVG/CSV
│   ├── share/ShareButton.tsx         Read-only link creation and revocation
│   ├── layout/                       AppShell, Sidebar, TopBar, Brand
│   └── ui/index.tsx                  Button, Modal, Dropdown, Badge, …
├── lib/
│   ├── api.ts                        ★ the single backend seam
│   ├── kinds.ts                      Single source of truth for the five surfaces
│   ├── i18n.ts                       Dictionary keyed on the Korean source string
│   ├── useT.ts                       Hook translating into the current language
│   ├── clipboard.ts                  Copy, with a fallback outside secure contexts
│   ├── reportMarkdown.ts             Markdown round-trip for the document editor
│   ├── templates.ts                  Prompt starter templates
│   └── brand.ts                      Branding resolution and logo URL
├── pages/                            One per route
├── store/useStore.ts                 Single zustand store
├── types.ts                          Domain types (discriminated unions)
└── e2e/                              Playwright specs
```

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
npm run test:e2e
npx playwright test --project=desktop
```

The suite needs a running stack and a seeded account —
`bash scripts/e2e-seed.sh` from the repository root.

**Do not override `--workers`.** The config pins `workers: 1`; every spec signs
in as the same account and several pick "the most recent X".

**Some specs spend real money** — roughly 4,400 credits for an image, 1,000 for
audio, 12,000 for a video. Exclude them with `--grep-invert` while iterating.

See [docs/development.md](../../docs/development.md) for the rest.
