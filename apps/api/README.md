# KloudChat API

The backend. **The only process holding the LiteLLM master key**, and as far as
the browser ever gets.

## Running

```bash
cd ../..                    # repository root
cp .env.example .env        # fill in KCHAT_JWT_SECRET and BACKEND_BASE_URL
make build                  # builds this image from the checkout;
                            # `make up` would run the published one
curl localhost:5173/api/health
```

The API listens on 8100 inside the compose network only; it runs as
`KCHAT_API_REPLICAS` replicas of the `api` service behind the web container's
`/api` and `/llm` proxy, and publishes no host port. `kloudchat-db` is on host
port 5433. Migrations are applied once by the one-shot `migrate` container
(`alembic upgrade head`) before any replica starts.

With `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` set, that account is
created as administrator when the database has no users at all. Without them,
the first account to sign up becomes administrator and later signups follow
`SIGNUP_MODE` (default `approval`).

For local work without Docker, see
[docs/development.md](../../docs/development.md).

## Tests

```bash
pip install -e '.[dev]'
ruff check .
pytest -q
```

Tests run without services: model replies are faked, and database tests use
in-memory SQLite through `aiosqlite` (part of `.[dev]`). Three suites guard the
screens and the guide as well as the code: `test_screens_use_one_set_of_words.py`
(one vocabulary — 아티팩트, 템플릿, 테마), `test_the_app_does_not_narrate_its_own_changes.py`
and `test_app_help.py` (`app/help/docs.json` matches `website/`; rebuild it with
`python scripts/build_help.py`).

## Endpoints

All under `/api` except `/llm`. The complete list is the OpenAPI document at
`/docs` (`ENV=dev`).

| | |
| --- | --- |
| `POST /auth/signup` · `login` · `logout` · `password` | Self-hosted authentication and password change |
| `GET /auth/config` | Public configuration the sign-in screen reads — branding, enabled surfaces, whether password reset exists |
| `POST /auth/password/forgot` · `reset` · `POST /auth/verify-email` · `verify-email/resend` | Active only when SMTP is configured |
| `GET/PATCH /auth/me` · `GET /auth/me/access` · `GET /auth/me/sessions` · `DELETE /auth/me/sessions/{family}` · `POST /auth/me/sessions/revoke-others` | Profile and preferences, own sign-in history, own refresh-token families and their revocation |
| `GET /models` · `POST /models/refresh` · `GET /credits` | Catalogue (LiteLLM merged with adapter declarations), remaining credits |
| `GET /sessions` · `/sessions/paged` · `POST` · `GET/PATCH/DELETE {id}` · `POST /sessions/delete` | Conversations of all five surfaces; bulk delete removes their artifacts too |
| `POST /sessions/{id}/messages` → SSE · `POST /sessions/{id}/stop` | Streaming turn for chat, report and slides; stop keeps the partial answer |
| `POST /sessions/{id}/messages/{mid}/fork` · `PATCH /messages/{id}/rating` | Branch a conversation before a message; rate an answer |
| `GET /sessions/{id}/runs` · `…/runs/{run}` · `…/rerun` | The stages a document turn ran through, and rerunning one |
| `POST /sessions/{id}/figure-suggestion` · `diagrams` | Figure proposals and flowcharts for a document |
| `POST /sessions/{id}/images` · `audio` | Image and audio, generated inside the request |
| `POST /sessions/{id}/jobs` · `POST /jobs/{id}/cancel` | Video — the only asynchronous work |
| `GET /search` | Search across conversations and artifacts |
| `GET /artifacts` · `counts` · `GET/PATCH/DELETE {id}` · `GET {id}/export` · `GET {id}/versions` · `POST {id}/restore` | Artifacts as a page of cards, version history and restore, export |
| `POST /artifacts/{id}/sections/…` · `slides/…` · `blocks/…` · `critique` · `GET {id}/marks` | Rewrite, fact-check, picture and figure on a finished document; review; check marks |
| `GET /projects` · `files` · `skills` · `memory` · `agents` · `designs` | Workspace (`designs` are 테마) |
| `POST /files/{id}/open-as-document` · `POST /designs/extract` | Open an uploaded `.hwpx` as an editable report; draft a theme from a file or URL |
| `POST /projects/{id}/knowledge/url` · `…/reindex` · `POST /agents/{id}/knowledge` · `…/url` · `…/from-files` · `…/reindex` | Project and agent knowledge, and backfilling the vector index |
| `GET /skills/store` · `POST /skills/{id}/install` · `POST /agents/{id}/install` · `POST /skills/import` · `GET /skills/{id}/export` | What others shared, copying from it, skill packages |
| `GET /pipelines` · `GET /agents/{id}/pipeline` · `GET /agent-bases` · `PUT /agents/{id}/process` | Built-in processes and an agent's 진행 과정 |
| `/user-pipelines…` · `/bundles…` | Composed processes and 묶음 (bundles); sharing ones that export or review goes through administrator approval |
| `GET /design-templates` · `usage` · `{id}/preview` · `{id}/style` · `{id}/form` · `/user-templates…` | 템플릿: the shipped catalogue (preview is unauthenticated) and users' own |
| `GET /connectors` · `/connectors/catalog` · `POST /connectors/install/{slug}` | MCP connectors |
| `POST /shares` · `GET /shared/{token}` · `GET /shares/{id}/views` | Read-only sharing. Reading a `link`-scoped share needs no authentication |
| `/publications…` · `/bots` · `/public/bots` · `/b/{slug}…` | Published chatbots: the owner's screens and the visitors' |
| `/bots/v1/models` · `/bots/v1/chat/completions` · `/bots/mcp/{slug}` · `/embed/frame-policy` | A bot's API (OpenAI-compatible), MCP and embed channels |
| `GET /keys` · `POST` · `DELETE {id}` | API keys users take away with them |
| `GET /me/usage` | Own usage |
| `POST /transcriptions` | Composer microphone → speech-to-text |
| `/issues…` | Questions and reports sent from the app, and the answers |
| `/admin/users…` · `settings` · `models` · `usage` · `audit` · `governance` · `storage` · `publications` · `issues` · `skills` · `user-pipelines` · `connector-catalog` · `perf` | Administration; `POST /admin/storage/reclaim` and `POST /credits/refill` are its manual triggers |
| `POST /admin/branding/logo` · `DELETE` | Logo upload and removal (`GET /branding/logo` is public) |
| `/llm/*` | LiteLLM passthrough. Authenticates **by API key, not by session** |
| `GET /health` | Reports KloudChat and LiteLLM status separately |

`/docs` serves interactive documentation when `ENV=dev`, and is disabled in
`prod`.

## Design notes

**Refresh token rotation.** Every `/refresh` issues a new token and revokes the
presented one. A token returning after rotation means a copy leaked, so the
whole family is revoked.

`revoked_reason` is what makes that actionable: an `alert` is written only when
a token revoked with reason `rotated` comes back. Without it, an administrator
suspending an account and a user signing out would both log as token reuse.

**Model prices fail closed.** For commercial models routed through OpenRouter,
`/model/info` returns no `mode`, no context length and no capability flags, and
image models arrive with `output_cost_per_token: 0` — which at face value lists
a paid model in the picker at zero credits.

A model reported at zero is therefore treated as free only where self-hosting is
certain: a known free provider, an internal `api_base`, an explicit
`MODEL_OVERRIDES` entry, or an OpenRouter `:free` suffix. Everything else is
**removed from the catalogue**, and `GET /api/admin/settings` reports what was
dropped and why.

**Credit conversion.** `credits_per_usd` (default 100,000, so 1 credit =
$0.00001) is the single exchange rate. When provider prices move, adjust this
rather than re-cutting everyone's allowance. The default grant of 5,000,000
credits (code, `.env.example` and the `docker-compose.yml` fallback) is about
$50/month.

Resolution matters more than magnitude: provider prices span four orders of
magnitude, and a coarse unit rounds the cheapest models up onto the same floor
as models twenty times their price.

**Input is billed too.** The picker's headline figure is the output price, but
long context is where the money goes — counting output alone would make a
100k-token prompt effectively free.

**Limits attach to accounts.** The LiteLLM budget is set on the user, never on
a key: a per-key budget would block one key while the account still had
allowance. The proxy stamps a default budget on every new key, which is cleared
immediately after issue.

**SSE event order.** `step` and `delta` → `usage` → `title` (first turn only) →
`done`. The assistant message and the credit deduction commit in **one
transaction**, so a turn cannot be billed but unsaved or the reverse. Nothing is
debited for a turn that produced no output.

**Reports are verified before they are shown.** After the sections are
written, `verify.verify_and_repair` runs the checks in `checks.py` (code:
numeric issues, one metric stated two ways, references to a source's
chapters the reader does not have, settled-figure conflicts, scaled metrics) and, where a model is
available, `checks.judge_figures` and `fact_check.web_facts` /
`solve_questions`. Each check only names sentences; one `repair` call rewrites
those, a rewrite is kept only when its check no longer fires, and the code
checks run again. `key_figures` settles the figures a document set fixes
(price, costs, targets) once and holds sections and slides to them;
`quality_gate` reads the finished report or deck for form defects. What is
still found is reported in the turn's 품질 점검 step.

**Sign-in survives LiteLLM being down.** A provisioning failure is logged and
stepped over, and the catalogue returns adapter models with
`litellmAvailable: false`, because the UI has to be able to distinguish "empty
list" from "the proxy is broken".

## Layout

```
app/
├── main.py                 App assembly, CORS, /health
├── core/
│   ├── config.py           Settings — the only place the master key lives
│   ├── db.py               Async engine and session dependency
│   ├── security.py         argon2id, JWT, refresh token hashing
│   ├── deps.py             current_identity (allows pending) / current_viewer (also ?t=)
│   │                       / current_user / require_admin
│   └── logs.py             Sanitising outside text for log lines
├── models/
│   ├── user.py             users, refresh_tokens, credit_ledger, audit_events
│   ├── chat.py             sessions, messages, jobs
│   ├── workspace.py        projects, files, artifacts, skills, memories, agents, connectors
│   ├── publishing.py       Published chatbots, versions, visitors, keys
│   ├── issues.py           Questions and reports from the app
│   ├── metrics.py          Performance samples
│   ├── governance.py       Instance-wide policy, one row
│   └── settings.py         Runtime settings an administrator edits
├── schemas/                camelCase wire format (1:1 with src/types.ts)
├── routers/                auth, admin, models, sessions, runs, search, jobs, workspace,
│                           pipelines, user_pipelines, user_templates, skill_files,
│                           connectors, keys, shares, publications, bot_api, issues,
│                           usage, perf, branding, transcriptions, llm
├── agents/ · skills/ · bundles/ · pipelines/   Shipped agents, skills, bundles and processes (data)
├── design_templates/       템플릿 catalogue: template.toml, seed.html, sample.html per entry
├── help/docs.json          The user guide, as the in-app help reads it (scripts/build_help.py)
└── services/
    ├── credits.py          Allowance, monthly refill (1st, KST), pre-flight check, settlement
    ├── litellm.py          Master-key client — the only point of contact with LiteLLM
    ├── models.py           Catalogue merge and USD→credit conversion
    ├── adapters.py         Model facts LiteLLM does not know (adapters and overrides)
    ├── chat.py             chat/completions stream → SSE events, title generation
    ├── agent.py            Tool-calling loop (model ↔ tool round trips)
    ├── calculation_policy.py   Required-arithmetic detection
    ├── context.py          System prompt assembly — surface defaults and tool rules
    ├── freshness.py · current_evidence.py   Grounded-answer guidance and the no-evidence display contract
    ├── workspace_context.py  Projects, skills, memories and design → system prompt blocks
    ├── governance.py       Prohibited categories, PII masking, privacy guard, retention
    ├── netguard.py         Where a fetch on someone's behalf may go — the public internet, nothing behind it
    ├── ratelimit.py        Backoff from a model gateway's 429
    ├── adaptive_routing.py Auto cost routing for sessions that opted in
    ├── outline.py          Plan rules shared by the report, deck and HTML tracks
    ├── report.py · deck.py · page.py   Report, deck and HTML-template producers
    ├── research.py         Web research before a document is written
    ├── grounding.py · revise.py · lint.py · critique.py   Request check, targeted rewrite, linter, review
    ├── verify.py · checks.py · fact_check.py   Report verify-and-repair stage and its checks
    ├── key_figures.py · quality_gate.py · calc.py · units.py   Settled figures, final-form gate, code-computed values, unit labels
    ├── credibility.py      Source trust tiers for citations
    ├── answer_plan.py · conversation_summary.py · stop_signal.py   Chat answer plan, long-conversation summary, cross-replica stop
    ├── slide_patterns.py · doc_formats.py   Slide pattern table; per-purpose document heads and numbering
    ├── design.py · design_templates.py · design_extract.py   테마, the 템플릿 catalogue, theme extraction
    ├── doc_type.py · deck_type.py   Type scale shared by every renderer of a report or a deck
    ├── report_export.py · deck_export.py · page_export.py   DOCX/PDF/HWPX, PPTX/PDF, HTML read-back
    ├── printing.py         PDF via the print service; None when it is absent
    ├── pictures.py · figures.py · diagrams.py · diagram.py · flow_layout.py · diagram_shapes.py · diagram_render.py   Pictures and document figures: a planned flowchart is laid out by code and drawn with python-pptx (native shapes in a deck, a rendered picture in a report)
    ├── charts.py · chart_code.py   Charts
    ├── richtext.py · hwpx_import.py · hangul.py · arithmetic.py   Editing and text fixes
    ├── imagegen.py · audiogen.py   Synchronous media producers
    ├── videogen.py         Video: submit, poll, fetch — the one job kind
    ├── factcheck.py        Claim verification — no evidence downgrades the verdict
    ├── artifact_extract.py Hoisting substantial code out of a transcript — after the turn
    ├── auto_memory.py      Durable facts written from a finished conversation
    ├── thinking.py         Reasoning-model answer starvation
    ├── transcribe.py       Composer microphone → internal Whisper only
    ├── files.py · storage.py   Upload storage, text extraction, reclaim of deleted accounts' files
    ├── ocr.py · ocr_jobs.py   Scanned pages: OCR engine, then a strict-local vision model; background queue
    ├── korean_papers.py    Korean scholarly papers (KCI, DBpia, RISS) through the ordinary search backend
    ├── publishing.py       A published bot's turn: approved version, strict-local model, owner's credits
    ├── bundles.py · process_settings.py   묶음 and an agent's 진행 과정
    ├── knowledge.py · index_client.py   Agent shelves: lexical retrieval and the vector index
    ├── fonts.py            The Korean face every reportlab PDF embeds
    ├── mcp.py              MCP client (stdio and streamable-http)
    ├── mail.py             Account mail — one message, no queue, no retry
    ├── geoip.py            Offline region lookup from a MaxMind file
    ├── starter.py          Shipped agents, skills and themes; seeded at approval
    ├── bootstrap.py        First administrator, created when there are no users
    ├── settings_store.py   Database-first settings — swap the proxy without a restart
    └── tools/              Built-in tools, MCP tools, connector catalogue
```

See [docs/architecture.md](../../docs/architecture.md) for how these fit
together and why.
