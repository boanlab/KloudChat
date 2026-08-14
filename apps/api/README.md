# kchat API

The backend. **The only process holding the LiteLLM master key**, and as far as
the browser ever gets.

## Running

```bash
cd ../..                    # repository root
cp .env.example .env        # fill in KCHAT_JWT_SECRET and BACKEND_BASE_URL
docker compose up -d --build
curl localhost:8100/api/health
```

`kchat-db` listens on 5433, the API on 8100. Migrations are applied on
container start with `alembic upgrade head`.

With `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` set, that account is
created as administrator when the database has no users at all. Without them,
the first account to sign up becomes administrator and later signups follow
`SIGNUP_MODE` (default `approval`).

For local work without Docker, see
[docs/development.md](../../docs/development.md).

## Endpoints

| | |
| --- | --- |
| `POST /api/auth/signup` · `login` · `refresh` · `logout` | Self-hosted authentication |
| `GET /api/auth/config` | Public configuration the sign-in screen reads — branding, enabled surfaces, whether password reset exists |
| `POST /api/auth/password/forgot` · `reset` | Active only when SMTP is configured |
| `GET /api/models` · `POST /api/models/refresh` | Catalogue: LiteLLM merged with adapter declarations |
| `GET /api/sessions` · `POST` · `GET/PATCH/DELETE {id}` | Shared by all five surfaces. The list returns titles; the detail returns the conversation |
| `POST /api/sessions/{id}/messages` → SSE | Streaming turn for chat, report and slides |
| `POST /api/sessions/{id}/images` · `audio` | Image and audio, generated inside the request |
| `POST /api/sessions/{id}/jobs` · `POST /api/jobs/{id}/cancel` | Video — the only asynchronous work |
| `GET /api/artifacts` · `PATCH {id}` · `GET {id}/export` | Outputs, version history, document export |
| `GET /api/projects` · `skills` · `agents` · `memories` · `connectors` | Workspace |
| `POST /api/shares` · `GET /api/shared/{token}` | Read-only sharing. Reading a `link`-scoped share needs no authentication |
| `GET /api/keys` · `POST` · `DELETE {id}` | API keys users take away with them |
| `GET /api/usage` · `GET /api/credits` | Own usage, remaining credits |
| `GET /api/admin/users` · `settings` · `usage` · `governance` | Administration |
| `POST /api/admin/branding/logo` · `DELETE` | Logo upload and removal (`GET /api/branding/logo` is public) |
| `/llm/*` | LiteLLM passthrough. Authenticates **by API key, not by session** |
| `GET /api/health` | Reports kchat and LiteLLM status separately |

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
rather than re-cutting everyone's allowance. The default grant of 1,000,000
credits is about $10/month.

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
│   └── deps.py             current_identity (allows pending) / current_user / require_admin
├── models/
│   ├── user.py             users, refresh_tokens, credit_ledger, audit_events
│   ├── chat.py             sessions, messages, jobs
│   ├── workspace.py        projects, files, artifacts, skills, memories, agents, connectors
│   └── settings.py         Runtime settings an administrator edits
├── schemas/                camelCase wire format (1:1 with src/types.ts)
├── routers/                auth, admin, models, sessions, jobs, workspace, connectors,
│                           keys, shares, usage, branding, llm
└── services/
    ├── credits.py          Allowance, monthly refill (1st, KST), pre-flight check, settlement
    ├── litellm.py          Master-key client — the only point of contact with LiteLLM
    ├── chat.py             chat/completions stream → SSE events, title generation
    ├── report.py · deck.py Report and slide producers
    ├── imagegen.py · audiogen.py   Synchronous media producers
    ├── videogen.py         Video: submit, poll, fetch — the one job kind
    ├── factcheck.py        Slide claim verification — no evidence downgrades the verdict
    ├── adapters.py         Model facts LiteLLM does not know (adapters and overrides)
    ├── models.py           Catalogue merge and USD→credit conversion
    ├── agent.py            Tool-calling loop (model ↔ tool round trips)
    ├── files.py            Upload storage and per-format text extraction
    ├── report_export.py    DOCX, PDF and HWPX export
    ├── mcp.py              MCP client (stdio and streamable-http)
    ├── starter.py          Default agents and skills seeded into an account at approval
    ├── bootstrap.py        First administrator, created when there are no users
    ├── governance.py       Prohibited categories, PII masking, body retention
    ├── settings_store.py   Database-first settings — swap the proxy without a restart
    ├── workspace_context.py  Projects, skills and memories → system prompt blocks
    └── tools/              Built-in tools, MCP tools, connector catalogue
```

See [docs/architecture.md](../../docs/architecture.md) for how these fit
together and why.
