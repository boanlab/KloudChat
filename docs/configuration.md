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

Most runtime values are cached in-process — they are read on every model call —
and the cache is invalidated on write. With more than one API replica, those
display and feature settings reach the others within the cache TTL. Two values
that can authorize external data movement deliberately bypass that rule:
governance is read fresh for every egress decision, and model-boundary metadata
is refreshed from LiteLLM before strict-local eligibility is trusted. If the
authoritative governance read fails, chat, comparison and the legacy
report/slides send path return `503 governance_unavailable` before reading the
model catalogue, creating a message, issuing a key, charging credit or calling
an upstream model. A cached policy remains useful for display, but never
authorizes raw, masked or strict-local egress.

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
| `BACKEND_BASE_URL` | `BACKEND_BASE_URL` | — | The KloudChat-LLM gateway. Setting this alone derives the LiteLLM address and the six tool endpoints by appending paths. `/tools/index` is optional: without it, agent knowledge is searched lexically. |
| `LITELLM_BASE_URL` | `LITELLM_BASE_URL` | derived | Only when LiteLLM is not behind the gateway. |
| `LITELLM_MASTER_KEY` | `LITELLM_MASTER_KEY` | — | Never leaves the API process. Not returned by any route, including admin routes. |
| `KCHAT_PRINT_BASE_URL` | `PRINT_BASE_URL` | `http://kloudchat-print:8200` | The printer — a headless browser that turns a finished document into a PDF that looks like the screen. Compose runs one, on a network with no route out. Blank turns it off: exports still produce a PDF, drawn by the structural renderer, carrying the words without the 서식's own layout. |

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
| `KCHAT_DEFAULT_REPORT_MODEL` / `KCHAT_DEFAULT_SLIDES_MODEL` | `DEFAULT_REPORT_MODEL` / `DEFAULT_SLIDES_MODEL` | — | Per-surface defaults for 보고서 and 발표 자료. Empty falls back to the chat default. |
| `KCHAT_DEFAULT_IMAGE_MODEL` | `DEFAULT_IMAGE_MODEL` | `google/gemini-2.5-flash-image` | Default picture model. Gemini's image models take the aspect ratio as a parameter; the OpenAI ones return a square whatever is asked. Absent from the catalogue → cheapest image model. |
| `KCHAT_DEFAULT_AUDIO_MODEL` / `KCHAT_DEFAULT_VIDEO_MODEL` | `DEFAULT_AUDIO_MODEL` / `DEFAULT_VIDEO_MODEL` | `openai/gpt-audio-mini` / `google/veo-3.1-lite` | The 오디오/동영상 surface keeps one default per modality. Absent from the catalogue → cheapest model of that modality. |

### Auto cost routing

Auto routing is off until an administrator completes **Admin → System → Model
routing**. It requires:

- one live model declared by LiteLLM as `self_hosted` and `strictLocal`, with
  zero input and output credit cost, for classification;
- one to three ordered economy models declared as either `self_hosted` or
  `external`, with known prices and no `privacyOnly` flag.

`hybrid` and `unknown` models are deliberately not economy candidates. A
local-looking alias can fall back to an external provider, so its advertised
zero cost is not sufficient evidence that the turn is cheaper. The classifier
may be `privacyOnly`; answer models may not, which keeps privacy capacity
reserved for protected traffic.

Auto is an explicit, per-conversation choice. The selected real model remains
the quality ceiling and a new conversation starts in manual mode. It applies
only to ordinary chat without attachments, web search, selected skills,
agents, projects or comparison. A classifier outage or uncertain verdict keeps
the quality model. Classification includes the complete answer-visible message
envelope and tool definitions; if that bounded payload exceeds 8,000 characters,
Auto keeps the quality model instead of truncating context. Once a cheaper
answer model is selected, the answer runs without tools and disables LiteLLM
fallback, so a later tool result cannot overflow the smaller context window and
an error is shown instead of silently retrying the premium model.

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

Four of them compose already passes through from `.env`: `ENV` as
`KCHAT_ENV`, `TITLE_MODEL` as `KCHAT_TITLE_MODEL`, `TIMEZONE` as
`KCHAT_TIMEZONE` and `GEOIP_DATABASE` as `KCHAT_GEOIP_DATABASE`.

| Variable | Default | Notes |
| --- | --- | --- |
| `ENV` | `dev` | `prod` disables `/docs`. Set through `KCHAT_ENV`. |
| `ACCESS_TOKEN_TTL_MIN` | `15` | |
| `REFRESH_TOKEN_TTL_DAYS` | `30` | |
| `REFRESH_GRACE_SEC` | `15` | Window in which a just-rotated refresh token may be replayed without being treated as theft. Two tabs restoring a session at once send the same cookie; without the leeway, the loser is logged out of everything. |
| `CHAT_TIMEOUT_SEC` | `900` | A tool-using turn on a local 122B model genuinely runs for minutes. |
| `TOOL_TIMEOUT_SEC` | `300` | Per-tool ceiling. `MAX_TOOL_HOPS` is what bounds the turn. |
| `MAX_TOOL_HOPS` | `8` | Model↔tool round trips per turn; past eight a model is usually in a retry loop. The last hop runs without tools so the turn still ends in an answer. |
| `MAX_UPLOAD_MB` | `200` | Exists so one upload cannot fill the disk. |
| `STORAGE_RECLAIM_AT` | `0.8` | Disk fill (used ÷ total) past which the files of deleted accounts are removed, oldest first, until the volume is back under it. Checked every 30 minutes and from the usage screen's 지금 정리 button. `0` disables the sweep. Living accounts are never touched. |
| `FILE_CONTEXT_CHARS` | `24000` | Characters of a file injected per turn before excerpting. |
| `CREDITS_PER_USD` | `100000` | The single exchange rate. Adjust this when provider prices move, rather than re-cutting everyone's allowance. |
| `LITELLM_BUDGET_HEADROOM` | `0.2` | How far above the KloudChat allowance the proxy-side budget sits. A backstop that sits exactly on the limit fires first, blocking someone with a number no screen shows them. |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_COST` / `ARGON2_PARALLELISM` | `3` / `65536` / `4` | `memory_cost` is in KiB. |
| `TITLE_MODEL` | `local/qwen3.6-35b` | Names conversations and extracts memories. Empty falls back to the session's own model — correct, but wasteful on an expensive one. Set through `KCHAT_TITLE_MODEL`. |
| `WEB_SEARCH_RESULTS` / `WEB_SEARCH_SCRAPE` | `5` / `3` | Each scrape is a page fetch; this trades answer quality against turn latency. |
| `STT_OR_MODEL` | `mistralai/voxtral-small-24b-2507` | Fallback transcription model for hosts that cannot run Whisper. **Microphone audio leaves the network.** Set to `""` to keep dictation internal-only. |
| `APP_BASE_URL` | — | Origin used to build password reset links. Never taken from the request `Host`, which is attacker-controlled. |
| `TIMEZONE` | `Asia/Seoul` | IANA name. Used only for the date given to the model on every turn — every timestamp in the database stays UTC. Set through `KCHAT_TIMEZONE`. |
| `GEOIP_DATABASE` | — | Path to a MaxMind GeoLite2 City `.mmdb`. Empty disables region lookup; see [Where an address is](#where-an-address-is). Set through `KCHAT_GEOIP_DATABASE`. |
| `TITLE_TIMEOUT_SEC` | `20` | Naming a conversation and extracting memories. Both are best effort, so this is short: a title that takes longer than the turn did is not worth waiting for. |
| `LITELLM_TIMEOUT_SEC` / `LITELLM_PROBE_TIMEOUT_SEC` | `20` / `4` | The master-key client — provisioning a user, issuing a key, listing the catalogue — and the probe behind the admin connection test. **Not the model call**, which is `CHAT_TIMEOUT_SEC` above; raising this one will not help a generation that is being cut off. |
| `AUTO_ROUTING_CLASSIFIER_TIMEOUT_SEC` | `8` | Auto's classification call. On timeout the quality model answers. |
| `FILE_STORAGE_DIR` | `/srv/data/files` | Where uploads and generated media are written. Mount it, or a container rebuild loses every picture. |

---

## Behind a reverse proxy

The web container serves the app and proxies `/api/`. When something else
answers the internet in front of it — the usual arrangement — two things have
to line up or every audit row, share visit and 접속기록 line records the proxy
instead of the person.

**The proxy in front must send the header.** nginx:

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Host $host;
```

Nothing on the KloudChat side can invent an address that was never sent.

**The web container must be told which hops to believe.** It resolves the
client from `X-Forwarded-For` for trusted hops only, and ignores the header
from anywhere else — which is what stops a client from naming its own address.
`KCHAT_TRUSTED_PROXIES` is the trusted set, one `set_real_ip_from` directive
per entry:

| `.env` | Default |
| --- | --- |
| `KCHAT_TRUSTED_PROXIES` | the private ranges plus loopback |

The default suits a proxy on the same host or Docker network. **Narrow it
wherever the web port is reachable by anyone else** — every address in the set
can name itself anything:

```
KCHAT_TRUSTED_PROXIES="set_real_ip_from 10.1.2.3;"
```

Also set `KCHAT_COOKIE_SECURE=true` behind TLS.

### Where an address is

Three screens show a region beside an address: the visits on a shared link, an
account's own 접속기록, and the admin audit trail.

The lookup is **offline only**. Resolving addresses through a third-party
service would send every visitor's address off the instance, so KloudChat reads
a MaxMind DB file from disk or says nothing:

1. Download `GeoLite2-City.mmdb` (free, needs a MaxMind account).
2. Mount it into the API container.
3. Set `GEOIP_DATABASE` to its path.

With no file configured, those screens show the address alone. Private ranges
never reach the database and read `내부망`.

---

## Runtime settings (Settings → System)

Stored in `system_settings`, editable by administrators, applied without a
restart. Secrets in this table are encrypted at rest with a key derived from
`JWT_SECRET`.

### Integrations

- **Backend gateway address** — one field. Saving it fills in the LiteLLM
  address and the six tool endpoints. Each field has its own connection test,
  and any one of them can be overridden if you host that feature elsewhere.
- **LiteLLM master key** — entered separately. The tool endpoints need no key.
- **SMTP** — host, port, security (`starttls` / `ssl` / `none`), username,
  password, envelope sender. Named modes rather than a boolean because the two
  encrypted modes use different ports and a different handshake, and picking
  the wrong one produces a timeout with no clue which.

  With no SMTP host, outbound mail is disabled and password reset is hidden.
  Telling people to contact an administrator beats a reset link that never
  arrives. The only mail this system sends is a password reset the person asked
  for.

### Signup

- **Mode** — `open` / `approval` / `closed`, overriding `SIGNUP_MODE` when set.
- **Allowed domains** — a comma-separated list of mail domains that may
  register; empty allows any. Subdomains are not implied.
- **Email verification** — when on, a new account is `pending` until the link
  in the confirmation mail is clicked (24 hours, single use); then `open`
  activates it and `approval` hands it to an administrator. Approving an
  account counts as confirming its address. Needs SMTP: without a mail server
  the switch is inert and signups go through unverified, and the screen says
  so.

### Model routing

The Auto cost routing classifier and economy models — see
[Auto cost routing](#auto-cost-routing) — and the outline model, which plans
a document before the surface's own model writes it.

### Shared templates

Starting points published to every account by an administrator.

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

Prohibited-intent categories, message-body retention and two compatible privacy
policies are available:

- **PII masking** is the legacy organisation-wide upper bound. When enabled,
  matching content is always masked and raw external delivery cannot be
  enabled.
- **External data guard** protects chat and model comparison. Administrators
  choose a set of models whose live catalogue metadata explicitly says
  `self_hosted` and `strictLocal`; stale or unknown model IDs are rejected.
  The visible catalogue's top-to-bottom order is the fixed routing priority.
  Administrators may optionally allow a user to choose raw external delivery
  after detection. A new policy row enables the guard and disallows raw
  delivery; an existing row keeps guard off until configured.

Each user can choose `ask`, strict-local routing, masked external delivery or,
when permitted, raw external delivery as the default action. The server
rechecks the administrator policy on every turn, so a previously saved raw
preference becomes ineffective as soon as the allowance is removed.

Policy is applied **before a message write, credit charge or completion call**.
Detected values are not returned in the decision response or stored in audit
metadata. See the context-assembly section of `architecture.md` for the scanned
sources, model metadata contract and surface limits.

---

## Per-user settings

| Screen | Contents |
| --- | --- |
| `/settings` | Profile, password |
| `/settings/preferences` | Default model, interface behaviour, privacy default action |
| `/settings/personalization` | About-me and response-style text prepended to every chat |
| `/settings/keys` | API key issue and revocation |
| `/settings/access` | Sign-in history and active sessions |

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
