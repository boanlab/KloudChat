# Configuration

KloudChat is configured in two places, and the split matters:

| | Where | When it applies |
| --- | --- | --- |
| **Bootstrap** | `.env`, read by `docker compose` | At container start |
| **Runtime** | Admin screens, stored in `system_settings` | Immediately, no restart |

Resolution order is **database → environment → code default**. The environment
is a working bootstrap so that a fresh deployment runs before anyone opens the
admin screen; anything an administrator saves afterwards takes precedence and
keeps taking precedence. Editing `.env` will not undo a value set in the UI.

Runtime values are cached in-process — they are read on every model call — and
the cache is invalidated on write. With more than one API replica, a change
reaches the others within the cache TTL rather than instantly.

---

## Environment variables

These are consumed by `docker-compose.yml` and passed to the API container.
The variable names in `.env` are prefixed `KCHAT_`; the names the application
itself reads are in the second column.

### Required

| `.env` | Container | Notes |
| --- | --- | --- |
| `KCHAT_JWT_SECRET` | `JWT_SECRET` | Access token signing key. Generate with `openssl rand -hex 32`. Compose refuses to start without it. Rotating it invalidates every issued access token — the intended response to a suspected compromise. |

### Backend integration

| `.env` | Container | Default | Notes |
| --- | --- | --- | --- |
| `BACKEND_BASE_URL` | `BACKEND_BASE_URL` | — | The KloudChat-LLM gateway. Setting this alone derives all six feature endpoints by appending paths. `/tools/index` is optional: without it, agent knowledge is searched lexically. |
| `LITELLM_BASE_URL` | `LITELLM_BASE_URL` | derived | Only when LiteLLM is not behind the gateway. |
| `LITELLM_MASTER_KEY` | `LITELLM_MASTER_KEY` | — | Never leaves the API process. Not returned by any route, including admin routes. |

The gateway derives these paths:

```
<BACKEND_BASE_URL>/litellm          models
<BACKEND_BASE_URL>/tools/search     web search
<BACKEND_BASE_URL>/tools/fetch      document fetch
<BACKEND_BASE_URL>/tools/exec       code execution
<BACKEND_BASE_URL>/tools/research   deep research (MCP)
<BACKEND_BASE_URL>/tools/stt        speech-to-text
<BACKEND_BASE_URL>/tools/index      retrieval index for agent knowledge
```

A feature with no address drops out of the tool list. It does not break
anything else.

### Accounts and policy

| `.env` | Container | Default | Notes |
| --- | --- | --- | --- |
| `KCHAT_ADMIN_EMAIL` | `BOOTSTRAP_ADMIN_EMAIL` | — | Applied only when the database has no accounts at all. Blank means the first signup becomes administrator. |
| `KCHAT_ADMIN_PASSWORD` | `BOOTSTRAP_ADMIN_PASSWORD` | — | Set both to create an administrator on first boot. Never commit a value — this file is published. |
| `KCHAT_SIGNUP_MODE` | `SIGNUP_MODE` | `approval` | `open` (active immediately), `approval` (admin approves), `closed` (signup disabled). |
| `KCHAT_DEFAULT_MONTHLY_CREDITS` | `DEFAULT_MONTHLY_CREDITS` | `1000000` | Assigned at approval unless the administrator overrides it. 1 credit = $0.00001, so 1,000,000 ≈ $10/month. |
| `KCHAT_DEFAULT_CHAT_MODEL` | `DEFAULT_CHAT_MODEL` | `local/qwen3.6-35b` | Falls back to the surface's cheapest model when absent from the catalogue. |

### Transport

| `.env` | Container | Default | Notes |
| --- | --- | --- | --- |
| `KCHAT_COOKIE_SECURE` | `COOKIE_SECURE` | `false` | **Set to `true` behind TLS.** The refresh cookie is httpOnly; over plain HTTP it is readable on the wire. Also switches `SameSite` from `Lax` to `None`. |
| `KCHAT_CORS_ORIGINS` | `CORS_ORIGINS` | `["http://localhost:5173"]` | JSON array of exact origins. Credentialed requests make a wildcard impossible. |
| `KCHAT_WEB_PORT` | — | `5173` | Host port for the web container. |
| `KCHAT_API_URL` | `KCHAT_API_URL` | `http://kloudchat-api:8100` | nginx upstream, resolved at run time so the API can move hosts without an image rebuild. |

### Database

| `.env` | Container | Default |
| --- | --- | --- |
| `KCHAT_DB_USER` | part of `DATABASE_URL` | `kchat` |
| `KCHAT_DB_PASSWORD` | part of `DATABASE_URL` | `kchat` |

Postgres is published on host port **5433** because 5432 is commonly already
taken by a vector database on the same host. Change both credentials before
exposing the port beyond the compose network.

### Advanced

Not exposed in `.env.example`; set them directly on the `api` service in
`docker-compose.yml` when you need to. Defaults live in
[`apps/api/app/core/config.py`](../apps/api/app/core/config.py).

`ENV` is the one exception — compose already passes it through as `KCHAT_ENV`,
so it can be set in `.env` like the variables above.

| Variable | Default | Notes |
| --- | --- | --- |
| `ENV` | `dev` | `prod` disables `/docs`. Set through `KCHAT_ENV`. |
| `ACCESS_TOKEN_TTL_MIN` | `15` | |
| `REFRESH_TOKEN_TTL_DAYS` | `30` | |
| `REFRESH_GRACE_SEC` | `15` | Window in which a just-rotated refresh token may be replayed without being treated as theft. Two tabs restoring a session at once send the same cookie; without the leeway, the loser is logged out of everything. |
| `CHAT_TIMEOUT_SEC` | `900` | A tool-using turn on a local 122B model genuinely runs for minutes. |
| `TOOL_TIMEOUT_SEC` | `300` | Per-tool ceiling. `MAX_TOOL_HOPS` is what bounds the turn. |
| `MAX_TOOL_HOPS` | `5` | Model↔tool round trips per turn. Past five, a model is almost always in a retry loop. |
| `MAX_UPLOAD_MB` | `200` | Exists so one upload cannot fill the disk. |
| `FILE_CONTEXT_CHARS` | `24000` | Characters of a file injected per turn before excerpting. |
| `CREDITS_PER_USD` | `100000` | The single exchange rate. Adjust this when provider prices move, rather than re-cutting everyone's allowance. |
| `LITELLM_BUDGET_HEADROOM` | `0.2` | How far above the KloudChat allowance the proxy-side budget sits. A backstop that sits exactly on the limit fires first, blocking someone with a number no screen shows them. |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_COST` / `ARGON2_PARALLELISM` | `3` / `65536` / `4` | `memory_cost` is in KiB. |
| `TITLE_MODEL` | `local/glm-4.7-flash` | Names conversations. Empty falls back to the session's own model — correct, but wasteful on an expensive one. |
| `WEB_SEARCH_RESULTS` / `WEB_SEARCH_SCRAPE` | `5` / `3` | Each scrape is a page fetch; this trades answer quality against turn latency. |
| `STT_OR_MODEL` | `mistralai/voxtral-small-24b-2507` | Fallback transcription model for hosts that cannot run Whisper. **Microphone audio leaves the network.** Set to `""` to keep dictation internal-only. |
| `APP_BASE_URL` | — | Origin used to build password reset links. Never taken from the request `Host`, which is attacker-controlled. |

---

## Runtime settings (Settings → System)

Stored in `system_settings`, editable by administrators, applied without a
restart. Secrets in this table are encrypted at rest with a key derived from
`JWT_SECRET`.

### Integrations

- **Backend gateway address** — one field. Saving it fills in the six feature
  endpoints. Each field has its own connection test, and any one of them can be
  overridden if you host that feature elsewhere.
- **LiteLLM master key** — entered separately. The tool endpoints need no key.
- **SMTP** — host, port, security (`starttls` / `ssl` / `none`), username,
  password, envelope sender. Named modes rather than a boolean because the two
  encrypted modes use different ports and a different handshake, and picking
  the wrong one produces a timeout with no clue which.

  With no SMTP host, outbound mail is disabled and password reset is hidden.
  Telling people to contact an administrator beats a reset link that never
  arrives. The only mail this system sends is a password reset the person asked
  for.

### Enabled surfaces

Report, slides, image and audio/video can be turned on and off. Chat cannot —
without conversation there is nothing this instance can do.

Report and slides default to on. Image and audio/video default to **off**,
because each generation spends credits.

Disabling a surface removes it from the UI **and** makes the server refuse to
create sessions of that kind. Hiding it alone leaves it enabled for anyone who
types the URL.

### Branding

Service name and logo for the sidebar and the sign-in screen. PNG, JPG or WebP,
up to 2 MB.

**SVG is rejected.** The logo is served without authentication — the sign-in
screen has to render it before anyone is authenticated — and SVG can carry
script. The stored filename contains a content hash, so replacing the logo
changes its URL and no cache serves the old one.

### Governance

Prohibited-intent categories, PII masking, a no-training flag, and message-body
retention. Policy is applied **before anything reaches the model**: blocks
happen before the write and masking happens before the write, because masking
after sending leaves the original text in the database.

---

## Per-user settings

| Screen | Contents |
| --- | --- |
| `/settings` | Profile, password |
| `/settings/preferences` | Default model, interface behaviour |
| `/settings/keys` | API key issue and revocation |

An issued API key **is** a LiteLLM virtual key: spend and the model allow-list
follow it. The monthly limit is attached to the account rather than to the key,
so issuing more keys splits one allowance instead of multiplying it.

---

## Credits

One number governs everything: `credits_per_usd`, default 100,000, so
1 credit = $0.00001.

Each modality is sold in a different unit, and the UI always renders the unit
alongside the number:

| Modality | Unit |
| --- | --- |
| Chat, report, slides | per 1k tokens (input and output priced separately) |
| Image | per image |
| Audio | per call |
| Video | per second × (resolution, audio) |

Input is billed as well as output. What a picker shows prominently is the
output price, but on a shared proxy the money actually goes on long context —
count output only, and a 100k-token prompt is effectively free.

**A model whose price is unknown is dropped from the catalogue.** Upstream
reporting 0 means "price unknown", not "free". Models reported at zero are
treated as free only when self-hosting is certain — a known free provider, an
internal `api_base`, an explicit override, or an OpenRouter `:free` suffix.
Everything else is removed, and `GET /api/admin/settings` reports what was
dropped and why.
