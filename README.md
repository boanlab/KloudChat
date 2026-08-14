<div align="center">

# kchat

**An open-source generative AI workspace that does not end at the conversation.**

Chat · Reports · Slides · Images · Audio &amp; Video

[![CI](https://github.com/boanlab/kloudchat/actions/workflows/ci.yml/badge.svg)](https://github.com/boanlab/kloudchat/actions/workflows/ci.yml)
[![Release](https://github.com/boanlab/kloudchat/actions/workflows/release.yml/badge.svg)](https://github.com/boanlab/kloudchat/actions/workflows/release.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab.svg)](apps/api/pyproject.toml)
[![React 19](https://img.shields.io/badge/react-19-61dafb.svg)](apps/web/package.json)

[Quick start](#quick-start) · [Architecture](docs/architecture.md) · [Configuration](docs/configuration.md) · [Deployment](docs/deployment.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## What it is

kchat is a self-hosted workspace for generative AI that treats the *output* as
the unit of work rather than the conversation. It brings its own
authentication, credit accounting and workspace, and delegates every model call
and tool to a single backend URL you configure at runtime.

Three decisions shape it.

**1. Output kinds are first-class.** Chat is not the only axis with everything
else buried inside it. Five surfaces sit side by side: reports stream section
by section, slides export as `.pptx`, audio and video report progress on job
cards. All five share the same project context — instructions, knowledge files
and memories.

**2. External systems attach as MCP connectors.** GitHub, Notion, Drive,
Zotero, arXiv, a CRM: added as tools, with read and write permissions revocable
per tool. Credentials stay on the server.

**3. Authentication is ours; models and tools are one URL.** Users, credits and
sessions live in kchat's own Postgres. Models and tools all live in
[`kloudchat-backend`][backend] and are connected by pasting one gateway address
into the admin screen. The browser only ever sees the kchat API.

When an account is approved it gets its own LiteLLM virtual key, and every
model call afterwards goes out under that key — so spend and audit logs on the
proxy side are per person. The master key is used only to create users and
keys, and is never included in any response. Monthly limits are enforced by
kchat, checked before every turn; the LiteLLM-side budget tracks the same
number plus 20% headroom. That budget is a backstop against an accounting bug,
not a limit anyone is meant to hit, and it is attached to the account rather
than to a key — issuing extra keys for coding agents splits one allowance, it
does not multiply it.

Credits are an administrator-assigned monthly allowance that refills. There is
no top-up, no refund and no rollover, and failed jobs are never charged in the
first place.

## Deployment shape

```
┌─ kchat (this repository) ┐        ┌─ kloudchat-backend ─────────────────┐
│  kchat-web   :5173       │        │  gateway :8080                      │
│  kchat-api   :8100       │──URL──▶│   /litellm  /tools/{search,fetch,   │
│  kchat-db    :5433       │        │        exec,research,stt}           │
│                          │        └─────────────────────────────────────┘
│  /llm  ← coding agents   │
└──────────────────────────┘
```

The boundary is **one address and one master key**. If the backend goes down,
sign-in, history, workspace and settings keep working; only model calls and
tools fail, and they fail honestly with "not connected".

## Quick start

Requirements: Docker with Compose v2, and about 2 GB of free disk for images.

```bash
git clone https://github.com/boanlab/kloudchat.git
cd kloudchat

cp .env.example .env
sed -i "s/^KCHAT_JWT_SECRET=.*/KCHAT_JWT_SECRET=$(openssl rand -hex 32)/" .env

docker compose up -d --build
curl localhost:8100/api/health
```

Open <http://localhost:5173>.

**The first account to sign up becomes the administrator.** Later signups land
in a pending state; approve them at `/admin/users` and the waiting screen
advances on its own.

To create the administrator without a signup, set `KCHAT_ADMIN_EMAIL` and
`KCHAT_ADMIN_PASSWORD` in `.env`. They apply only when the database has no
accounts at all, so changing them later never resets an existing password.

### Connecting the backend

In **Settings → System → Integrations**, paste the backend gateway address and
save. The six feature endpoints are filled in automatically by appending their
paths. Print the address from the backend:

```bash
./scripts/setup.sh urls    # run this in kloudchat-backend
```

If you host one feature elsewhere, override that single field. Each field has a
connection test, and a feature with an empty address drops quietly out of the
tool list — conversation, files, projects, memory and agents are unaffected.

The LiteLLM master key is entered separately on the same screen. The tool
endpoints need no key.

## What an administrator controls

Three things, all under **Settings → System**:

| | |
|---|---|
| **Enabled surfaces** | Turn reports, slides, images and audio/video on or off. Chat is always on. Images and audio/video cost credits per generation, so they default to off. A disabled surface disappears from the UI *and* the server refuses to create sessions of that kind — hiding it alone leaves it enabled for anyone who types the URL. |
| **Branding** | Name and logo for the sidebar and the sign-in screen. PNG, JPG or WebP up to 2 MB. |
| **Integrations** | Backend gateway address, LiteLLM master key, SMTP. |

## Connecting coding agents

Tools like Claude Code and Codex can use this instance's models. The account
menu has an **AI agent integration** page with the configuration to paste.

```bash
export ANTHROPIC_BASE_URL=https://<this-server>/llm      # Claude Code
export OPENAI_BASE_URL=https://<this-server>/llm/v1      # Codex and friends
```

Authenticate with a key issued at `/settings/keys`. That key *is* a LiteLLM
virtual key, so spend and the model allow-list follow it, and usage is
aggregated under "API keys" on `/usage`. The monthly limit is attached to the
account rather than to the key, so issuing several keys does not raise it.
LiteLLM itself is reachable only on the private network, which makes this route
the only way in.

## Language

Korean and English, switched from the top right. The choice is stored in the
browser, and a first-time visitor follows their browser language. Strings
without a translation fall back to Korean.

## Screens

| Route | Contents |
| --- | --- |
| `/` | Home — entry to the five surfaces, running jobs, recent work |
| `/new/:kind` · `/s/:id` | The shared work surface. `kind` = chat, report, slides, image, av |
| `/projects` · `/projects/:id` | Project instructions, knowledge files, member sessions, linked skills and memories |
| `/artifacts` | Gallery of every output. Filter by kind, jump back to the originating session |
| `/agents` | System prompt, model, tool permissions, and which surfaces an agent applies to |
| `/skills` | `SKILL.md` front matter, applicable surfaces, enable toggle |
| `/memory` | user / feedback / project / reference types, global or project scope, `[[links]]` |
| `/connectors` | MCP servers — verified catalogue, per-tool permissions, custom server registration |
| `/history` | Conversation history — selective and bulk deletion |
| `/usage` | Your own usage, by day, model and surface |
| `/admin/users` | Signup approval, monthly credit allowance, suspension (admin) |
| `/admin/usage` | Organisation-wide usage (admin) |
| `/admin/governance` | PII masking, intent filters, no-training flags, retention, audit log (admin) |
| `/settings` · `/settings/preferences` · `/settings/keys` | Profile and password / default model and behaviour / API key issue and revoke |
| `/agent-setup` | Coding agent connection — address, key, model (account menu) |
| `/admin/system` | Enabled surfaces, branding, backend integration, LiteLLM, SMTP (admin) |

### What each surface does differently

- **Chat** — real streaming. Tool calls appear inline while they run
  (`searching…`, `reading document…`) and collapse to one line when the turn
  settles. When the model calls `create_artifact` or `create_chart`, the result
  opens in the right-hand panel.
- **Report** — a table-of-contents sidebar with section-by-section streaming.
  The **whole document is editable as Markdown**, and saving accumulates
  versions. Exports to docx, PDF, **HWPX** and Markdown.
- **Slides** — the outline is settled first, then each slide is filled in.
  Thumbnail grid, speaker notes, per-slide text editing. Exports to pptx, PDF
  and Markdown — preview and both exports share one 960×540 geometry, so what
  you saw is what the file contains.
- **Image** — an option bar above the composer (aspect ratio, style, count),
  with results inline in the conversation.
- **Audio / video** — chosen with a type toggle. Only video shows a job card.
  Picking resolution, audio and duration updates the quote in place. A failure
  states the cause and that **nothing was charged**.

**Outputs can be shared by link** — either to workspace members or to people
without an account. Links are read-only and revocable at any time. Project
files and memories are never included.

**The microphone in the composer** transcribes through the backend's
speech-to-text. The transcript fills the composer rather than being sent.

Chat has a **model comparison** mode: the same question goes to two or three
models at once, each column showing its credit cost, and the conversation
continues from whichever answer you pick.

## Repository layout

```
kchat/
├── apps/
│   ├── web/                          React 19 + Vite + Tailwind v4
│   └── api/                          FastAPI — see apps/api/README.md
├── docs/                             Architecture and operator guides
├── scripts/                          Integration checks against a live stack
├── mcp/                              MCP stdio server scripts
├── docker-compose.yml
├── docker-compose.dev.yml            Serves the web app from Vite
└── .env.example
```

```
apps/web/src/
├── components/
│   ├── artifacts/ArtifactPanel.tsx   Right-hand panel, branching by kind
│   ├── chat/                         Composer, MessageItem, StepTimeline, Markdown
│   ├── media/JobCard.tsx             Asynchronous generation card (progress → result)
│   ├── report/ReportPanel.tsx        TOC, section streaming, sources, export
│   ├── slides/DeckPanel.tsx          Slide renderer, thumbnail grid, per-slide editing
│   ├── chart/ChartPanel.tsx          Chart, underlying-data tab, PNG/SVG/CSV
│   ├── layout/                       AppShell, Sidebar, TopBar, Brand
│   └── ui/index.tsx                  Button, Modal, Dropdown, Badge, …
├── lib/
│   ├── api.ts                        ★ the single backend seam
│   ├── kinds.ts                      Single source of truth for the five surfaces
│   ├── i18n.ts                       Dictionary keyed on the Korean source string
│   ├── useT.ts                       Hook translating into the current language
│   ├── clipboard.ts                  Copy, with a fallback outside secure contexts
│   └── reportMarkdown.ts             Markdown round-trip for the document editor
├── pages/                            One per route
├── store/useStore.ts                 Single zustand store
└── types.ts                          Domain types (discriminated unions)
```

React 19 · TypeScript · Vite · Tailwind v4 · zustand · react-router ·
lucide-react · react-markdown.

Adding a sixth surface starts at `SessionKind` in `types.ts` and the metadata
table in `lib/kinds.ts`.

## Documentation

| | |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | What the system does and why the load-bearing decisions are the way they are |
| [docs/configuration.md](docs/configuration.md) | Every environment variable and runtime setting |
| [docs/deployment.md](docs/deployment.md) | Production deployment, TLS, backups, upgrades |
| [docs/development.md](docs/development.md) | Local setup, tests, migrations |
| [apps/api/README.md](apps/api/README.md) | API endpoints and backend design notes |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [SECURITY.md](SECURITY.md) | Reporting vulnerabilities, and what is in scope |

## License

Apache-2.0 — see [LICENSE](LICENSE).

[backend]: https://github.com/boanlab/kloudchat-backend
