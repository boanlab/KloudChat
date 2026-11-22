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
(one vocabulary — artifact, template, theme; 아티팩트, 템플릿, 테마 on Korean screens), `test_the_app_does_not_narrate_its_own_changes.py`
and `test_app_help.py` (`app/help/docs.json` matches `website/`; rebuild it with
`python scripts/build_help.py`).

## Endpoints

All under `/api` except `/llm`. The complete list is the OpenAPI document at
`/docs` (`ENV=dev`).

| | |
| --- | --- |
| `POST /auth/signup` · `login` · `refresh` · `logout` · `password` | Self-hosted authentication and password change; `refresh` rotates the cookie token |
| `GET /auth/config` | Public configuration the sign-in screen reads — branding, enabled surfaces, whether password reset exists |
| `POST /auth/password/forgot` · `reset` · `POST /auth/verify-email` · `verify-email/resend` | Active only when SMTP is configured |
| `GET/PATCH /auth/me` · `GET /auth/me/access` · `GET /auth/me/sessions` · `DELETE /auth/me/sessions/{family}` · `POST /auth/me/sessions/revoke-others` | Profile and preferences, own sign-in history, own refresh-token families and their revocation |
| `GET /models` · `POST /models/refresh` · `GET /credits` | Catalogue (LiteLLM merged with adapter declarations), remaining credits |
| `GET /sessions` · `/sessions/paged` · `POST` · `GET/PATCH/DELETE {id}` · `POST /sessions/delete` | Conversations of all five surfaces; bulk delete removes their artifacts too. `GET /sessions?limit=n` (1–500): every pinned conversation plus the `n` most recent others |
| `POST /sessions/{id}/messages` → SSE · `POST /sessions/{id}/stop` | Streaming turn for chat, report and slides; stop keeps the partial answer |
| `POST /sessions/{id}/messages/{mid}/fork` · `PATCH /messages/{id}/rating` | Branch a conversation before a message; rate an answer |
| `GET /sessions/{id}/runs` · `…/runs/{run}` · `…/rerun` | The stages a document turn ran through, and rerunning one |
| `POST /sessions/{id}/figure-suggestion` · `diagrams` | Figure proposals and flowcharts for a document |
| `GET /sessions/{id}/agent-draft` · `…/agent-draft/decided` | The conversation as an unsaved draft agent (「에이전트로 저장」), and its instructions alone |
| `POST /sessions/{id}/images` · `audio` | Image (also an edit of a stored picture) and audio, generated inside the request |
| `POST /sessions/context-usage` | How much of the model's context a conversation would use |
| `POST /sessions/{id}/jobs` · `POST /jobs/{id}/cancel` | Video — the only asynchronous work |
| `GET /search` | Search across conversations and artifacts |
| `GET /artifacts` · `gallery` · `counts` · `working` · `GET/PATCH/DELETE {id}` · `GET {id}/export` · `GET {id}/versions` · `POST {id}/restore` | Artifacts (`gallery` is the page of cards, `working` the documents in progress), version history and restore, export |
| `POST /artifacts/{hwp,docx,pptx}` · `GET/PUT /artifacts/{id}/{hwp,docx,pptx}` · `POST …/session` | HWP, Word and PowerPoint documents: create or import, read and save the file (a stale save answers 409), attach a conversation |
| `POST /artifacts/{id}/sections/…` · `slides/…` · `blocks/…` · `critique` · `GET {id}/marks` | Rewrite, fact-check, picture and figure on a finished document; review; check marks |
| `GET /projects` · `files` · `skills` · `memory` · `agents` · `designs` | Workspace (`designs` are themes) |
| `GET /projects/{id}/shelf` · `POST …/shelf/text` · `…/shelf/guide/{subject}` · `…/shelf/locate` · `GET/PUT /sessions/{id}/sources` · `…/studio` · `/studio/{id}` · `…/notes` · `/notes/{id}` | The knowledge shelf: sources, guides, per-conversation source selection, Studio items (guides, FAQs, quizzes…) and notes |
| `GET /files/page` · `/issues/page` · `/publications/{id}/conversations/page` · `/admin/publications/{id}/conversations/page` | Long lists by keyset: `cursor` (opaque, from the last page's `next`; malformed → 422), `limit` (default 50, at most 200); `{items, total, next}`, `next` null on the last page. Files add `box`, `origin`, `q`, `projectId`/`sessionId` filters and per-origin `counts`; issues add `unread`; the admin list needs `reason` (audited per page) and takes `date_from`/`date_to` (inclusive UTC days, on last activity) |
| `GET /files/{id}/content` · `text` · `POST /files/{id}/open-as-document` · `POST /designs/extract` | File bytes and extracted text (these accept `?t=<token>` for media elements); `open-as-document` answers 410 — open an `.hwpx` with `POST /artifacts/hwp` and `fileId`; draft a theme from a file or URL |
| `POST /uploads` · `PUT/GET/DELETE /uploads/{id}` · `POST …/finish` · `GET /files/{id}/media` · `stream` · `frames/{n}` · `poster` · `GET /media/estimate` | Large audio and video uploaded in pieces, then read through the media worker (transcript with frames); playback by signed URL, with Range |
| `POST /forms` · `/forms/from-file` · `/design-brief/read` | Form files (양식) and a design brief read from a file |
| `POST /projects/{id}/knowledge/url` · `…/reindex` · `POST /agents/{id}/knowledge` · `…/url` · `…/from-files` · `…/reindex` | Project and agent knowledge, and backfilling the vector index |
| `GET /skills/store` · `POST /skills/{id}/install` · `POST /agents/{id}/install` · `POST /skills/import` · `GET /skills/{id}/export` | What others shared, copying from it, skill packages |
| `GET /pipelines` · `GET /agents/{id}/pipeline` · `GET /agent-bases` · `PUT /agents/{id}/process` | Built-in processes and an agent's process (How it works) |
| `/user-pipelines…` · `/bundles…` | Composed processes and bundles; sharing ones that export or review goes through administrator approval |
| `GET /design-templates` · `usage` · `{id}/preview` · `{id}/style` · `{id}/form` · `/user-templates…` | Templates: the shipped catalogue (preview is unauthenticated) and users' own |
| `GET /connectors` · `/connectors/catalog` · `POST /connectors/install/{slug}` | MCP connectors |
| `/integrations/{google,slack}…` · `/admin/integrations…` | A person's own Google Drive and Slack connections; the administrator's OAuth clients; confirm cards for actions the agent proposes ([docs/integrations.md](../../docs/integrations.md)) |
| `/push/{config,subscriptions,unsubscribe,test}` | Web Push devices (needs the VAPID keys) |
| `POST /client-errors` | Errors the browser reports about itself |
| `POST /shares` · `GET /shared/{token}` · `GET /shares/{id}/views` | Read-only sharing. Reading a `link`-scoped share needs no authentication |
| `/publications…` · `/bots` · `/bots/{slug}/copy` · `/public/bots` · `/b/{slug}…` | Published chatbots: the owner's screens, members' list and copy (shown as shared agents), and the visitors' |
| `/bots/v1/models` · `/bots/v1/chat/completions` · `/bots/mcp/{slug}` · `/embed/frame-policy` | A bot's API (OpenAI-compatible), MCP and embed channels |
| `GET /keys` · `POST` · `DELETE {id}` | API keys users take away with them |
| `GET /me/usage` | Own usage |
| `POST /transcriptions` | Composer microphone → speech-to-text |
| `/issues…` | Questions and reports sent from the app, and the answers |
| `/admin/users…` · `settings` · `models` · `usage` · `audit` · `governance` · `storage` · `publications` · `issues` · `skills` · `user-pipelines` · `connector-catalog` · `perf` | Administration; `POST /admin/storage/reclaim` and `POST /credits/refill` are its manual triggers |
| `POST /admin/branding/logo` · `DELETE` | Logo upload and removal (`GET /branding/logo` is public) |
| `/llm/*` | LiteLLM passthrough. Authenticates **by API key, not by session** |
| `GET /health` (also `HEAD`) | Reports KloudChat and LiteLLM status separately |

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
rather than re-cutting everyone's allowance. The default grant of 500,000
credits is about $5/month.

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
`quality_gate` looks for form defects in the finished report or deck. What is
still found is listed with the answer.

**Sign-in survives LiteLLM being down.** A provisioning failure is logged and
stepped over, and the catalogue returns adapter models with
`litellmAvailable: false`, because the UI has to be able to distinguish "empty
list" from "the proxy is broken".

## Layout

```
app/
├── main.py                 App assembly, CORS, /health
├── core/                   config.py (settings — the only place the master key lives), db.py,
│                           security.py (argon2id, JWT, refresh hashing), deps.py (current_identity
│                           allows pending / current_viewer also takes ?t= / current_user /
│                           require_admin), request_id.py, logs.py (sanitising outside text)
├── models/                 SQLAlchemy tables: user, chat, workspace, shelf, publishing, integrations,
│                           push, issues, metrics, governance, settings
├── schemas/                camelCase wire format (1:1 with apps/web/src/types.ts)
├── routers/                One module per area of the endpoint table; hwp, docx and pptx are
│                           instances of office_docs.py
├── pipeline/ · pipelines/  Stage runner and the stages (chat, report, deck, page; check, export,
│                           revise, save) and the TOML process each surface runs
├── agents/ · skills/ · bundles/ · patterns/ · design_templates/
│                           Shipped data: agents, skill packages (apps/api/app/skills/README.md),
│                           bundles, deck patterns, the template catalogue
├── help/docs.json          The user guide, as the in-app help reads it (scripts/build_help.py)
└── services/               Logic; the main groups:
    ├── litellm.py · models.py · adapters.py · credits.py     LiteLLM client (the only contact),
    │                       catalogue merge and USD-to-credit conversion, model facts LiteLLM lacks,
    │                       allowance, monthly refill, pre-flight check, settlement
    ├── chat.py · agent.py · context.py · workspace_context.py   Streamed turn, tool-calling loop,
    │                       system prompt assembly
    ├── super_agent.py · super_api.py · start_mode.py · adaptive_routing.py   Super Agent routing
    ├── governance.py · netguard.py · ratelimit.py            Policy, PII masking, privacy guard,
    │                       where an outside fetch may go, 429 backoff
    ├── report.py · deck.py · page.py · outline.py · research.py   Document producers and planning
    ├── verify.py · checks.py · fact_check.py · key_figures.py · quality_gate.py   Verify-and-repair
    ├── report_export.py · deck_export.py · page_export.py · printing.py   DOCX/PDF/HWPX, PPTX/PDF,
    │                       PDF through the print service (None when absent)
    ├── hwp_doc.py · docx_doc.py · pptx_doc.py · office_*.py    Office documents and their editing
    ├── diagrams.py · flow_layout.py · diagram_shapes.py · diagram_render.py   Figures: a planned
    │                       flowchart is laid out by code and drawn with python-pptx
    ├── imagegen.py · audiogen.py · videogen.py · video_*.py · media_client.py   Media producers
    │                       (video is the one job kind) and the media worker client
    ├── shelf_*.py · studio.py · source_*.py · knowledge.py · index_client.py   Knowledge shelf,
    │                       Studio, lexical retrieval and the vector index
    ├── files.py · storage.py · ocr.py · ocr_jobs.py · transcribe.py   Uploads, text extraction,
    │                       scanned pages, speech-to-text
    ├── publishing.py · bundles.py · skill_*.py · check_skills.py · post_checks.py   Published bots,
    │                       bundles, skills and the checks they attach
    ├── integrations/ · push.py · mail.py · mcp.py            Google Drive and Slack, Web Push, account
    │                       mail, MCP client (stdio and streamable-http)
    ├── perf/ · geoip.py                                      Admin performance figures, offline region lookup
    ├── tools/              Built-in tools, MCP tools, connector catalogue
    ├── settings_store.py   Database-first settings — swap the proxy without a restart
    └── starter.py · bootstrap.py   Shipped items seeded at approval, first administrator
```

See [docs/architecture.md](../../docs/architecture.md) for how these fit
together and why.
