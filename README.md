<div align="center">

# KloudChat

**An open-source generative AI workspace that does not end at the conversation.**

Chat · Reports · Slides · Images · Audio &amp; Video · Agents · Chatbots

[![CI](https://github.com/boanlab/KloudChat/actions/workflows/ci.yml/badge.svg)](https://github.com/boanlab/KloudChat/actions/workflows/ci.yml)
[![Release](https://github.com/boanlab/KloudChat/actions/workflows/release.yml/badge.svg)](https://github.com/boanlab/KloudChat/actions/workflows/release.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab.svg)](apps/api/pyproject.toml)
[![React 19](https://img.shields.io/badge/react-19-61dafb.svg)](apps/web/package.json)

[Quick start](#quick-start) · [Architecture](docs/architecture.md) · [Configuration](docs/configuration.md) · [Deployment](docs/deployment.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## What it is

KloudChat is a self-hosted generative AI workspace for an organisation. It
brings its own authentication, credit accounting and workspace, and reaches
every model and tool through one backend gateway address configured at
runtime ([KloudChat-LLM][backend]). The browser only ever talks to the
KloudChat API.

**Five surfaces, one workspace.** Chat, Report, Slides,
Image and Audio / Video share the same projects, knowledge files, memories,
agents and skills. Everything a turn produces — a document, a deck, a chart, a
picture, a clip — is an **artifact**, kept in its own gallery and exportable:

| Artifact | Exports |
| --- | --- |
| Report / document | PDF, DOCX, HWPX, Markdown |
| Deck | PDF, PPTX, Markdown |
| Chart | PNG, SVG, CSV |

**Structure and look are separate choices.** A **template** fixes a document's
structure (sections, title block, numbering — minutes, bullet-style briefing, comparison,
official draft, briefing deck and so on), and code keeps the model inside it. A
**theme** fixes the look — colour, type, voice — and the same theme dresses a
report, a deck and a cover image alike. Flowcharts and structure diagrams in
reports and decks are laid out by code and drawn with python-pptx: native
shapes in the `.pptx`, the same drawing as a picture in the report.

**Documents are checked, and the checks are shown.** Code checks (numbers,
figures stated two ways, placeholders nobody filled, references to a source the
reader lacks) and model checks (claims against the material, figure sentences)
name the sentences they find wrong; one repair pass rewrites only those, and
whatever is still found is listed with the answer rather than passed off as
done. Facts missing from the material are asked for before writing, or left as
`[bracketed]` blanks — never invented.

**Agents are the way into a job.** An agent fixes its instructions, model,
knowledge files, templates, skills and **How it works** process (answer directly, start from a
built-in process, or compose one), plus the bundles of extra steps around the
writing (prepare · review · finish). Skills are scripts and checks that change
the result (calculation check, date and quote calculation, spreadsheet clean-up,
PDF tables, source check, PII masking…). An agent's knowledge is searched
through a tool when it needs it — lexically by default, adding vector search
when the backend serves an embedding model. Everything shipped is built with
the same features a user has, so a user can build the same thing.

**Agents can be published.** An agent opens as a chatbot to every member of
the instance at once; opening it to people without an account — by link or on
the public bot list — and its embed, OpenAI-compatible API and MCP channels
are approved by an administrator. A bot answers on the strict-local model with
its approved version, under the owner's credits and policies.

**Data stays inside when it should.** Models are labelled by boundary in the
LiteLLM catalogue (self-hosted, `strictLocal`, hybrid, external). Turns that
carry the organisation's own material — attachments, project files, memories —
run on strict-local models by default; a privacy guard and PII masking decide
what may go to an external model. Scanned PDFs and document photos are read by
an OCR engine and, for pages it is unsure of, a strict-local vision model —
never anything else.

**Accounting is per person.** An approved account gets its own LiteLLM
virtual key, so spend and audit logs on the proxy are per person; the master
key never leaves the API container. Credits are an administrator-assigned
monthly allowance that refills — no top-up, no rollover, and failed jobs are
not charged. KloudChat enforces the limit before every turn; the LiteLLM-side
budget is a backstop 20% above it, attached to the account rather than to a
key. An administrator can also offer **Auto** routing, which a person selects
per conversation: a strict-local classifier sends clearly simple turns to a
cheaper model (**Auto · Save credits**) or clearly hard ones to a stronger
model (**Auto · Favour quality**); the requested and executed models stay
visible on the answer.

### The self-hosted models

The default configuration expects two self-hosted models behind the gateway:

| Model | Does |
| --- | --- |
| `local/qwen3.8-27b` | Chat answers, report and deck prose, file-reading code (`run_code`) |
| `local/qwen3.8-flash` | Outlines and plans, judging and checks, the Auto classifier, titles, memories and conversation summaries, re-reading scanned pages (as `strict-local/qwen3.8-flash`) |

Which model does which job is chosen in the admin screen (**System › Model**
and **System › Routing**, 시스템 › 모델 / 라우팅 on a Korean screen); the
`.env` values only seed a fresh install. See
[Which model does what](docs/configuration.md#which-model-does-what).

## Deployment shape

```
┌─ KloudChat (this repository) ──────┐        ┌─ KloudChat-LLM ─────────────────────┐
│  kloudchat-web   :5173  /api /llm  │        │  gateway :8080                      │
│   └▶ api × N     (internal :8100)  │──URL──▶│   /litellm  /tools/{search,fetch,   │
│  kloudchat-db    :5433             │        │    exec,research,stt,index,ocr}     │
│  kloudchat-print (internal :8200)  │        │                                     │
└────────────────────────────────────┘        └─────────────────────────────────────┘
```

The web container is the only entry point: it serves the bundle and proxies
`/api` and `/llm` to the `api` service, which runs as `KCHAT_API_REPLICAS`
replicas (default 1) and publishes no host port. A one-shot `migrate`
container applies database migrations before any replica starts.

The boundary is **one address, one master key and one tools key**. If the backend goes down,
sign-in, history, workspace and settings keep working; only model calls and
tools fail, and they fail honestly with "not connected".

## Quick start

Requirements: Docker with Compose v2, and about 2 GB of free disk for images.

```bash
git clone https://github.com/boanlab/KloudChat.git
cd KloudChat

cp .env.example .env
sed -i "s/^KCHAT_JWT_SECRET=.*/KCHAT_JWT_SECRET=$(openssl rand -hex 32)/" .env

docker compose up -d
curl localhost:5173/api/health
```

This pulls the published images from Docker Hub. To build them from the
checkout instead, add the build overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Open <http://localhost:5173>.

**The first account to sign up becomes the administrator.** Later signups land
in a pending state; approve them at `/admin/users` and the waiting screen
advances on its own.

To create the administrator without a signup, set `KCHAT_ADMIN_EMAIL` and
`KCHAT_ADMIN_PASSWORD` in `.env`. They apply only when the database has no
accounts at all, so changing them later never resets an existing password.

### Connecting the backend

In the account menu, open **System › Features** and paste the backend gateway
address into **Integrations › Server address**, then save. The LiteLLM address and the
seven tool endpoints (web search, document fetch, code execution, deep
research, speech-to-text, retrieval index, OCR) are derived from it by
appending their paths. Print the address from the backend:

```bash
./scripts/setup.sh urls    # run this in KloudChat-LLM
```

If you host one feature elsewhere, override that single field. Each field has a
connection test, and a feature with an empty address drops quietly out of the
tool list — conversation, files, projects, memory and agents are unaffected.

The LiteLLM master key is entered under **System › Proxy**, which also holds
an explicit LiteLLM address when it is not behind the gateway. The tool
endpoints are authenticated by a shared key set in `.env`
(`KCHAT_TOOLS_GATEWAY_KEY`), never in the screen.

## What an administrator controls

The account menu's **Manage** (관리) section:

| Entry | Route | Contents |
| --- | --- | --- |
| Users · credits | `/admin/users` | Signup approval, monthly credit allowance, suspension, API keys of an account |
| Usage | `/admin/usage` | Organisation-wide usage by person, model and surface |
| Published chatbots | `/admin/publications` | Review queue for bots opened to people without an account, and their channels |
| Security · audit | `/admin/governance` | Privacy guard and PII masking, internal material on strict-local, intent filter, retention, idle sign-out, audit log |
| Feedback & issues | `/admin/issues` | Reports and questions sent from the app |
| System | `/admin/system` | The tabs below |
| Performance | `/admin/performance` | API, host, GPU node and per-model latency figures |

**System** tabs:

| Tab | Contents |
| --- | --- |
| Proxy | LiteLLM address and master key, connection test, model list refresh, models hidden for an unknown price |
| Model | The default model of each surface, the models for side work (titles and memories, `run_code` programs), whether models think before answering |
| Routing | Auto routing — the classifier, economy and upgrade models — and the outline model that plans documents |
| Features | Enabled surfaces; feature integrations (server address, per-feature endpoints); OCR mode, vision model and page limit |
| Branding | Name and logo for the sidebar and the sign-in screen |
| Mail | SMTP for password reset and email verification |
| Signup | Signup mode, allowed mail domains, email verification, a new account's monthly credits |
| Connector catalogue | The MCP connectors users can install |

Chat is always on. Images and audio/video cost credits per generation, so they
default to off; a disabled surface disappears from the UI *and* the server
refuses to create sessions of that kind.

## Connecting coding agents

Tools like Claude Code and Codex can use this instance's models. The account
menu's **Agent setup** page has the configuration to paste.

```bash
export ANTHROPIC_BASE_URL=https://<this-server>/llm      # Claude Code
export OPENAI_BASE_URL=https://<this-server>/llm/v1      # Codex and friends
```

Authenticate with a key issued at `/settings/keys`. That key *is* a LiteLLM
virtual key, so spend and the model allow-list follow it, and usage is
aggregated under "API keys" on `/usage`. The monthly limit is attached to the
account rather than to the key, so issuing several keys does not raise it.
LiteLLM itself is reachable only on the private network, which makes this route
the only way in. **API access** (`/api-setup`) covers calling the KloudChat API
itself.

## Language

Korean and English, switched from the top right. The choice is stored in the
browser, and a first-time visitor follows their browser language. Strings
without a translation fall back to Korean. The user guide is published at
<https://boanlab.github.io/KloudChat> (source under `website/`).

## Screens

| Route | Contents |
| --- | --- |
| `/` · `/new/:kind` | Home and a new conversation on a surface (`chat`, `report`, `slides`, `image`, `av`) |
| `/s/:id` | A conversation, with its artifacts in the right-hand panel |
| `/projects` · `/projects/:id` | Project instructions, knowledge files, conversations and memories |
| `/artifacts` | Every artifact, filtered by kind, with a way back to its conversation |
| `/bots` | Old link to the members' chatbot list; redirects to **Customize** → **Agents** → **Explore** (shared agents), where members' published bots show as agents. `/b/:slug` is a bot's own page, `/b` the public list, `/embed/b/:slug` the embeddable frame |
| `/customize` | **Customize** — agents, skills and connectors (`?tab=agents\|skills\|connectors`), including what others shared; bundles at `/customize/bundles` |
| `/history` | Conversation history — selective and bulk deletion |
| `/usage` | Your own usage, by day, model and surface |
| `/issues` | Questions and reports you sent, with the answers |
| `/settings` · `/preferences` · `/personalization` · `/memory` · `/themes` · `/keys` · `/access` | Profile and password / defaults / about-me and response style / memories / themes / API keys / sign-ins |
| `/agent-setup` · `/api-setup` | Coding-agent and API connection guides |
| `/share/:token` | A read-only shared link |
| `/admin/…` | See [What an administrator controls](#what-an-administrator-controls) |

### What each surface does differently

- **Chat** — real streaming. Tool calls appear inline while they run and
  collapse to one line when the turn settles. A file, chart or HTML page the
  model makes opens in the right-hand panel.
- **Report** — the outline is proposed first and writing starts once you
  approve it; sections stream in order. The document is edited in place on
  page-sized paper, and saving keeps versions.
- **Slides** — the outline is approved first, then each slide is filled in.
  Thumbnail grid, speaker notes, per-slide editing; preview and exports share
  one 16:9 geometry, so what you saw is what the file contains.
- **Image** — an option bar above the composer (aspect ratio, style, count).
  The prompt and the picture read as an ordinary turn.
- **Audio / Video** — the same turn shape, with a job card standing in the
  answer's place while a clip is made. Picking resolution, audio and duration
  updates the quote in place. A failure states the cause and that nothing was
  charged.

**Artifacts can be shared by link** — to anyone with an account on this
instance or to people without one. Links are read-only, revocable at any time,
and record who opened them. Project files and memories are never included.

**The microphone in the composer** transcribes through the backend's
speech-to-text. The transcript fills the composer rather than being sent.

**A turn outlives the tab it was started in.** Closing the page or losing the
connection does not throw the answer away — it is still written, charged and
titled. Stopping is a separate act: **Stop** tells the server before it closes the
connection, and the partial answer is kept and marked as cut off.

## Repository layout

```
KloudChat/
├── apps/
│   ├── web/                          React 19 + Vite + Tailwind v4 — see apps/web/README.md
│   ├── api/                          FastAPI — see apps/api/README.md
│   └── print/                        Headless-browser HTML-to-PDF renderer
├── docs/                             Architecture and operator guides
├── website/                          User guide (Docusaurus, Korean and English)
├── scripts/                          Integration checks against a running stack
├── mcp/                              MCP stdio server scripts
├── docker-compose.yml                Runs the published images
├── docker-compose.build.yml          Builds the images from this checkout
├── docker-compose.dev.yml            Serves the web app from Vite
└── .env.example
```

```
apps/web/src/
├── components/
│   ├── artifacts/                    Right-hand artifact panel, ribbon, versions, checks
│   ├── chat/                         Composer, MessageItem, StepTimeline, ModelPicker
│   ├── report/                       Document editor, paged preview, figures
│   ├── slides/                       Deck renderer, slide patterns, type scale
│   ├── chart/ChartPanel.tsx          Chart, underlying data, PNG/SVG/CSV
│   ├── media/                        Generation job card and media results
│   ├── customize/                    Agent and skill forms, the Customize hub
│   ├── publishing/                   Publish dialog, audience, bot channels
│   ├── layout/                       AppShell, Sidebar, AccountMenu, shortcuts
│   └── ui/                           Button, Modal, Dropdown, Badge, …
├── lib/
│   ├── api.ts                        The single backend seam
│   ├── kinds.ts                      The five surfaces
│   ├── i18n.ts                       Dictionary keyed on the Korean source string
│   └── useT.ts                       Hook translating into the current language
├── pages/                            One per route
├── store/useStore.ts                 Single zustand store
└── types.ts                          Domain types
```

React 19 · TypeScript · Vite · Tailwind v4 · zustand · react-router ·
lucide-react · react-markdown.

## Documentation

| | |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | What the system does and why the load-bearing decisions are the way they are |
| [docs/configuration.md](docs/configuration.md) | Every environment variable and runtime setting |
| [docs/deployment.md](docs/deployment.md) | Production deployment, TLS, backups, upgrades |
| [docs/development.md](docs/development.md) | Local setup, tests, migrations |
| [apps/web/README.md](apps/web/README.md) | Frontend development and end-to-end tests |
| [apps/api/README.md](apps/api/README.md) | API layout, endpoints and tests |
| [User guide](https://boanlab.github.io/KloudChat) | How to use the product (source in `website/`) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [SECURITY.md](SECURITY.md) | Reporting vulnerabilities, and what is in scope |

## License

Apache-2.0 — see [LICENSE](LICENSE).

[backend]: https://github.com/boanlab/KloudChat-LLM
