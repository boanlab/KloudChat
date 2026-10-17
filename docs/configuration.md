# Configuration

KloudChat is configured in two places, and the split matters:

| | Where | When it applies |
| --- | --- | --- |
| **Bootstrap** | `.env`, read by `docker compose` | At container start |
| **Runtime** | Admin screens, stored in `system_settings` | Immediately, no restart |

The rule for what goes where: **what the deployment needs lives in `.env`;
everything else is decided in the admin screen.** Addresses, ports, secrets,
the database, replicas, cookies and storage paths are deployment. Which model
does which job, whether models think, and policy such as a new account's
credits are product choices, stored in the database. See
[Deployment settings and product settings](#deployment-settings-and-product-settings)
for every setting's side.

Resolution order is **database → environment → code default**. The environment
is a working bootstrap so that a fresh deployment runs before anyone opens the
admin screen; anything an administrator saves afterwards takes precedence and
keeps taking precedence. Editing `.env` will not undo a value set in the UI.
For the product choices the environment is only the first-install seed: a
migration writes it into the database once, so from then on the `.env` line
does nothing and can be removed.

Most runtime values are cached in-process — they are read on every model call —
and the cache is invalidated on write. With more than one API replica, those
display and feature settings reach the others within the cache TTL. Two values
that can authorize external data movement deliberately bypass that rule:
governance is read fresh for every egress decision, and model-boundary metadata
is refreshed from LiteLLM before strict-local eligibility is trusted. If the
authoritative governance read fails, the chat and report/slides
send path return `503 governance_unavailable` before reading the
model catalogue, creating a message, issuing a key, charging credit or calling
an upstream model. A cached policy remains useful for display, but never
authorizes raw, masked or strict-local egress.

---

## Deployment settings and product settings

| Side | Settings | Why there |
| --- | --- | --- |
| Deployment | `JWT_SECRET`, `SECRET_KEY`, `LITELLM_MASTER_KEY`, `TOOLS_GATEWAY_KEY`, `CODE_INTERPRETER_API_KEY`, `SCRAPER_API_KEY`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`, `BOOTSTRAP_ADMIN_NAME` | Secrets and keys. Never in a screen a browser can read; the master key can be replaced in **System › Proxy** but is never shown. |
| Deployment | `BACKEND_BASE_URL`, `LITELLM_BASE_URL`, `PRINT_BASE_URL`, `SEARXNG_URL`, `SCRAPER_URL`, `CODE_INTERPRETER_URL`, `DEEP_RESEARCH_URL`, `INDEX_URL`, `OCR_URL`, `WHISPER_URL`, `PERF_METRICS_URL`, `GUIDE_BASE_URL`, `APP_BASE_URL` | Addresses of services this stack talks to. The gateway, LiteLLM, tool and mail-link addresses can also be overridden in **System** (they move without a redeploy); the environment is their boot value. |
| Deployment | `DATABASE_URL`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `API_REPLICAS`, `ENV`, `API_PREFIX` | Database and processes. |
| Deployment | `CORS_ORIGINS`, `COOKIE_SECURE`, `COOKIE_DOMAIN`, `REFRESH_COOKIE_NAME`, `KCHAT_TRUSTED_PROXIES`, `KCHAT_WEB_PORT`, `KCHAT_API_URL` | Network, proxy and cookie security: they depend on how the host is exposed. |
| Deployment | `FILE_STORAGE_DIR`, `MAX_UPLOAD_MB`, `STORAGE_RECLAIM_AT`, `GEOIP_DATABASE` | Storage paths and what the volume can hold (the upload limit must agree with the proxy's body limit). |
| Deployment | `JWT_ALGORITHM`, `ACCESS_TOKEN_TTL_MIN`, `REFRESH_TOKEN_TTL_DAYS`, `REFRESH_GRACE_SEC`, `LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_MIN`, `ARGON2_TIME_COST`, `ARGON2_MEMORY_COST`, `ARGON2_PARALLELISM` | Sign-in mechanics, tuned to the host's CPU and the threat model, not a product choice. |
| Deployment | `CHAT_TIMEOUT_SEC`, `CHAT_STALL_SEC`, `TOOL_TIMEOUT_SEC`, `MAX_TOOL_HOPS`, `TITLE_TIMEOUT_SEC`, `AGENT_DRAFT_TIMEOUT_SEC`, `LITELLM_TIMEOUT_SEC`, `LITELLM_PROBE_TIMEOUT_SEC`, `AUTO_ROUTING_CLASSIFIER_TIMEOUT_SEC`, `FILE_CONTEXT_CHARS`, `WEB_SEARCH_RESULTS`, `WEB_SEARCH_SCRAPE` | Timeouts and per-turn load, set by how fast the GPU nodes and the search backend are. |
| Deployment | `SANDBOX_IN_BOUNDARY`, `SELF_HOSTED_THINKING_MODELS`, `OCR_VISION_MODEL`, `SKILL_DRAFTS`, `TIMEZONE` | Facts about the infrastructure: whether the sandbox is inside the boundary, which served models think unless told not to, which served vision model reads pages best when the administrator picked **Auto**, whether this is an evaluation instance, the install's time zone. |
| Deployment | `CREDITS_PER_USD`, `LITELLM_BUDGET_HEADROOM` | Accounting constants (shown read-only under **System › Proxy**). |
| Deployment | `PERF_*` | Performance monitoring: mounts, targets and thresholds of this host. |
| Product, seeded once | `DEFAULT_CHAT_MODEL`, `DEFAULT_REPORT_MODEL`, `DEFAULT_SLIDES_MODEL`, `DEFAULT_IMAGE_MODEL`, `DEFAULT_AUDIO_MODEL`, `DEFAULT_VIDEO_MODEL`, `TITLE_MODEL`, `CODE_MODEL`, `CODE_FALLBACK_MODEL`, `SELF_HOSTED_CHAT_THINKING`, `EXTERNAL_CHAT_THINKING` | **System › Model** (`models.*`, `thinking.*` in `system_settings`). The environment seeds a fresh install once. |
| Product, seeded once | `DEFAULT_MONTHLY_CREDITS` | **System › Signup** (`credits.default_monthly`). Seeded once the same way. |
| Product, environment as fallback | `ADAPTIVE_ROUTING_DEFAULT`, `ADAPTIVE_CLASSIFIER_MODEL_DEFAULT`, `ADAPTIVE_ECONOMY_MODELS_DEFAULT`, `ADAPTIVE_QUALITY_DEFAULT`, `ADAPTIVE_QUALITY_MODELS_DEFAULT`, `OUTLINE_MODEL_DEFAULT`, `INTERNAL_DATA_STRICT_LOCAL_DEFAULT` | **System › Routing** and **Security · audit** (`governance` row). The environment only fills the row when it is first created. |
| Product, environment as fallback | `BRAND_NAME`, `SIGNUP_MODE`, `SMTP_HOST` … `SMTP_FROM` | **System › Branding / Signup / Mail**. A stored value wins; with none stored the environment applies. |
| Product, admin screen only | enabled surfaces, OCR on/off, reading mode, vision model, page limit, contact address, signup domains and verification, logo, governance policies | No environment variable. |

---

## Environment variables

These are consumed by `docker-compose.yml` and passed to the API container.
The variable names in `.env` are prefixed `KCHAT_`; the names the application
itself reads are in the second column.

### Required

| `.env` | Container | Notes |
| --- | --- | --- |
| `KCHAT_JWT_SECRET` | `JWT_SECRET` | Access token signing key. Generate with `openssl rand -hex 32`. Compose refuses to start without it. Rotating it invalidates every issued access token — the intended response to a suspected compromise. |
| `KCHAT_SECRET_KEY` | `SECRET_KEY` | Seals stored secrets: users' LiteLLM keys, issued API keys, connector credentials, secret system settings. Generate the same way. Empty falls back to a key derived from `JWT_SECRET` (logged as a warning at startup), so rotating the signing key would also make every stored secret unreadable. Once set, the API re-seals existing rows on its next start; rows sealed under the derived key still open until then. |

### Backend integration

| `.env` | Container | Default | Notes |
| --- | --- | --- | --- |
| `BACKEND_BASE_URL` | `BACKEND_BASE_URL` | — | The KloudChat-LLM gateway. Setting this alone derives the LiteLLM address and the seven tool endpoints by appending paths. `/tools/index` is optional: without it, agent knowledge is searched lexically. `/tools/ocr` is optional: without it, scanned PDF pages are not read (document photos still are, by a strict-local vision model, when one is chosen). |
| `KCHAT_TOOLS_GATEWAY_KEY` | `TOOLS_GATEWAY_KEY` | — | Shared secret the gateway requires on every `/tools/*` request, sent as `X-Tools-Key`. Must equal `TOOLS_GATEWAY_KEY` in KloudChat-LLM's `.env`. Generate with `openssl rand -hex 32`. Attached only to requests under a configured tool address (see below); blank sends nothing and a gateway that requires it answers 401. |
| `LITELLM_BASE_URL` | `LITELLM_BASE_URL` | derived | Only when LiteLLM is not behind the gateway. |
| `LITELLM_MASTER_KEY` | `LITELLM_MASTER_KEY` | — | Never leaves the API process. Not returned by any route, including admin routes. |
| `KCHAT_PRINT_BASE_URL` | `PRINT_BASE_URL` | `http://kloudchat-print:8200` | The printer — a headless browser that turns a finished document into a PDF that looks like the screen. Compose runs one, on a network with no route out. Blank turns it off: exports still produce a PDF, drawn by the structural renderer, carrying the words without the template's own layout. |

The gateway derives these paths:

```
<BACKEND_BASE_URL>/litellm          models
<BACKEND_BASE_URL>/tools/search     web search
<BACKEND_BASE_URL>/tools/fetch      document fetch
<BACKEND_BASE_URL>/tools/exec       code execution
<BACKEND_BASE_URL>/tools/research   deep research (MCP)
<BACKEND_BASE_URL>/tools/stt        speech-to-text
<BACKEND_BASE_URL>/tools/index      retrieval index for agent knowledge and chat uploads
<BACKEND_BASE_URL>/tools/ocr        text from scanned pages (ocr-shim); `OCR_URL` without the gateway
```

Scanned pages are read in the background (`services/ocr_jobs.py`): the engine
first, and pages it is unsure of or that hold a ruled table again by a vision
model. That model is chosen in the admin screen (**System › Features › Text from scans (OCR)**) and must be strict-local and
declare vision; right before each page is sent, every LiteLLM deployment under its name is checked to be strict-local,
and the page is not sent otherwise.

A feature with no address drops out of the tool list. It does not break
anything else.

#### Tools gateway key

KloudChat-LLM's gateway refuses `/tools/*` with `401 {"error":"unauthorized"}`
unless the request carries `X-Tools-Key: <TOOLS_GATEWAY_KEY>`. (`/litellm/*`
and `/v1/*` are authenticated by LiteLLM itself; `/health` stays open.)

The API attaches the key in one place, `services/tools_gateway.py`: every
client that talks to a tool backend (web search, document fetch, code
execution, the deep-research MCP connector, speech-to-text, the retrieval
index, the OCR engine, and the admin screen's connection test) is built by
`tools_gateway.client()`, whose request hook adds the header only when the
request URL lies under one of the seven tool addresses in effect (same scheme,
host, port and path prefix) and strips it otherwise — so it never reaches a
fetched page, a user's own MCP server or a redirect target. The MCP client is
narrower still: it sends the key only to the deep-research address. The `youtube`
catalogue connector, a stdio process that calls speech-to-text itself, gets the
key as `TOOLS_GATEWAY_KEY` only while its `WHISPER_URL` is the configured
speech-to-text address. The admin screen's connection test reports a 401 from
the gateway as a key mismatch.

Where it is set:

| Repository | File | Variable |
| --- | --- | --- |
| KloudChat | `.env` | `KCHAT_TOOLS_GATEWAY_KEY` (compose passes it to the API as `TOOLS_GATEWAY_KEY`) |
| KloudChat-LLM | `.env` | `TOOLS_GATEWAY_KEY` (compose passes it to the gateway) |

Rotation (brief 401s on tool calls between steps 3 and 4 are expected; run
it when nobody is mid-turn):

1. `openssl rand -hex 32` → the new key.
2. Write it to both `.env` files.
3. Recreate the API so it sends the new key: `docker compose up -d api`.
4. Recreate only the gateway in KloudChat-LLM:
   `docker compose up -d --no-deps --force-recreate gateway`.
5. Check: **System › Features › Integrations** → test each tool, or
   `curl -s -o /dev/null -w '%{http_code}' -H "X-Tools-Key: $KEY" <BACKEND_BASE_URL>/tools/search/healthz`
   (200) and the same without the header (401).

### Accounts and policy

| `.env` | Container | Default | Notes |
| --- | --- | --- | --- |
| `KCHAT_ADMIN_EMAIL` | `BOOTSTRAP_ADMIN_EMAIL` | — | Applied only when the database has no accounts at all. Blank means the first signup becomes administrator. |
| `KCHAT_ADMIN_PASSWORD` | `BOOTSTRAP_ADMIN_PASSWORD` | — | Set both to create an administrator on first boot. Never commit a value — this file is published. |
| `KCHAT_SIGNUP_MODE` | `SIGNUP_MODE` | `approval` | `open` (active immediately), `approval` (admin approves), `closed` (signup disabled). |
| `KCHAT_DEFAULT_MONTHLY_CREDITS` | `DEFAULT_MONTHLY_CREDITS` | `500000` | First-install seed of **System › Signup › Monthly credits for new accounts**, which then decides. Assigned at approval unless the administrator overrides it. 1 credit = $0.00001, so 500,000 ≈ $5/month. |
| `KCHAT_DEFAULT_CHAT_MODEL` | `DEFAULT_CHAT_MODEL` | `local/qwen3.8-flash` | First-install seed of **System › Model › Default chat model** (as are the four rows below). Falls back to the surface's cheapest model when absent from the catalogue. |
| `KCHAT_DEFAULT_REPORT_MODEL` / `KCHAT_DEFAULT_SLIDES_MODEL` | `DEFAULT_REPORT_MODEL` / `DEFAULT_SLIDES_MODEL` | `local/qwen3.8-27b` | Per-surface defaults for Report and Slides; documents keep the larger model while chat uses the faster one. Empty falls back to the chat default. |
| `KCHAT_DEFAULT_IMAGE_MODEL` | `DEFAULT_IMAGE_MODEL` | `google/gemini-2.5-flash-image` | Default picture model, for the image surface and for pictures in reports and decks. The picture models are whatever the gateway's catalogue lists; one that does not honour an aspect ratio is offered square only (`imagegen.aspects_for`). Absent from the catalogue → another usable picture model. A strict-local conversation never uses it: see [Governance](#governance-security--audit-admingovernance). |
| `KCHAT_DEFAULT_AUDIO_MODEL` / `KCHAT_DEFAULT_VIDEO_MODEL` | `DEFAULT_AUDIO_MODEL` / `DEFAULT_VIDEO_MODEL` | `openai/gpt-audio-mini` / `google/veo-3.1-lite` | The Audio / Video surface keeps one default per modality. Absent from the catalogue → cheapest model of that modality. |

### Auto routing

Auto has two lanes, each with its own switch under **System › Routing**. A
fresh governance row turns both on (`ADAPTIVE_ROUTING_DEFAULT`,
`ADAPTIVE_QUALITY_DEFAULT`), with `local/qwen3.8-flash` as the economy model
and `deepseek/deepseek-v4.1-flash`, `z-ai/glm-5.3-flash` as upgrade models;
a lane with no usable model is not offered:

- **Auto · Save credits** (cost) sends a turn classified as low-complexity to one of
  one to three ordered economy models: cheaper than the chosen model on known
  prices, with a stated context window, no `privacyOnly` flag, and a boundary
  no wider than the chosen model's.
- **Auto · Favour quality** (quality) sends a turn classified as high-complexity to one
  of one to three ordered upgrade models. An upgrade never widens the data
  boundary of the chosen model and keeps the turn's tools.

Both need one live classifier model declared by LiteLLM as `self_hosted` and
`strictLocal`, with zero input and output credit cost.

A model's boundary comes from the gateway's catalogue metadata
(`kchat_data_boundary`): `external`; `hybrid`, a `local/` model that runs on
the organisation's GPUs first but may fall back to an external twin;
`self_hosted`, a `local/` model with no such twin; and `self_hosted` with
`kchat_strict_local`, a `strict-local/` model that never leaves. Only
strict-local is inside the data boundary. `unknown` models are never economy
candidates; a `hybrid` one qualifies only under a `hybrid` or `external` chosen
model, an `external` one only under an `external` chosen model. A `local/`
alias's advertised zero cost is not evidence that the turn stays inside.
The classifier may be `privacyOnly`; answer models may not, which keeps privacy
capacity reserved for protected traffic.

Auto is an explicit, per-conversation choice. The selected real model remains
the reference model and a new conversation starts in manual mode. In chat, a
turn with attachments, forced web search, selected skills, a starting
template, an agent or a project keeps the chosen model, as does a turn that
requires calculation on the cost lane. A classifier outage or uncertain
verdict keeps the chosen model. Classification reads the complete
answer-visible message envelope and tool definitions; if that payload exceeds
80,000 characters, Auto keeps the chosen model instead of truncating context.
A cost-routed answer runs without tools and with LiteLLM fallback disabled, so
a later tool result cannot overflow the smaller context window and an error is
shown instead of silently retrying the premium model.

### Transport

| `.env` | Container | Default | Notes |
| --- | --- | --- | --- |
| `KCHAT_COOKIE_SECURE` | `COOKIE_SECURE` | `false` | **Set to `true` behind TLS.** The refresh cookie is httpOnly; over plain HTTP it is readable on the wire. Also switches `SameSite` from `Lax` to `None`. |
| `KCHAT_CORS_ORIGINS` | `CORS_ORIGINS` | `["http://localhost:5173"]` | JSON array of exact origins. Credentialed requests make a wildcard impossible. |
| `KCHAT_WEB_PORT` | — | `5173` | Host port for the web container. |
| `KCHAT_API_URL` | `KCHAT_API_URL` | `http://api:8100` | nginx upstream, resolved at run time so the API can move hosts without an image rebuild. `api` is the compose service name, not a fixed container: nginx re-resolves it on every request (`resolver 127.0.0.11`), so it reaches every `api` replica. |
| `KCHAT_API_REPLICAS` | `API_REPLICAS` | `1` | `api` instances. Migrations run once in the one-shot `migrate` container first, so replicas booting together never race `alembic upgrade head`. The `api` service publishes no host port — reach the API through the web container (`curl localhost:5173/api/health`) or one instance with `docker compose exec api sh`. The stop button's cancellation signal is per-process (`_STOPPING` in `app/routers/sessions.py`); above one replica each process also broadcasts and listens for it over Postgres `NOTIFY` (`services/stop_signal.py`), so a stop request reaches whichever instance is actually streaming. At `1` the broadcast is skipped and no listener connection is held open. |

### Database

| `.env` | Container | Default |
| --- | --- | --- |
| `KCHAT_DB_USER` | part of `DATABASE_URL` | `kchat` |
| `KCHAT_DB_PASSWORD` | part of `DATABASE_URL` | `kchat` |
| `KCHAT_DB_POOL_SIZE` | `DB_POOL_SIZE` | `10` |
| `KCHAT_DB_MAX_OVERFLOW` | `DB_MAX_OVERFLOW` | `20` |

Postgres is published on host port **5433** because 5432 is commonly already
taken by a vector database on the same host. Change both credentials before
exposing the port beyond the compose network.

Each `api` replica runs one uvicorn process, so `DB_POOL_SIZE +
DB_MAX_OVERFLOW` is that replica's database budget. Postgres's own
`max_connections` (100 by default) is the ceiling across every replica
sharing the database: replicas × (pool + overflow), plus one listener
connection per replica above one, must stay under it.

### Advanced

Not exposed in `.env.example`; set them directly on the `api` service in
`docker-compose.yml` when you need to. Defaults live in
[`apps/api/app/core/config.py`](../apps/api/app/core/config.py).

Some of them compose already passes through from `.env`: `ENV` as
`KCHAT_ENV`, `TIMEZONE` as `KCHAT_TIMEZONE`, `GEOIP_DATABASE` as
`KCHAT_GEOIP_DATABASE`, `SELF_HOSTED_THINKING_MODELS` as
`KCHAT_SELF_HOSTED_THINKING_MODELS`, and the first-install seeds (`TITLE_MODEL`
and the two thinking switches, in `x-first-install-seeds`, which both `migrate`
and `api` receive). The others take effect only when added to the `api`
service's `environment:` — or, for `CODE_MODEL` and `CODE_FALLBACK_MODEL`, to
`x-first-install-seeds` before the first install.

| Variable | Default | Notes |
| --- | --- | --- |
| `ENV` | `dev` | `prod` disables `/docs`. Set through `KCHAT_ENV`. |
| `ACCESS_TOKEN_TTL_MIN` | `15` | |
| `REFRESH_TOKEN_TTL_DAYS` | `30` | |
| `LOGIN_MAX_FAILURES` | `5` | Failed sign-ins in a row, on one address, before it is locked. Counted from the audit log, so no extra table; a success ends the run. Unknown addresses are locked the same way so the response does not say which exist. |
| `LOGIN_LOCKOUT_MIN` | `15` | How long a locked address refuses sign-in, measured from the last failure. Attempts while locked are logged as `locked` and neither verify the password nor extend the lock. |
| `REFRESH_GRACE_SEC` | `15` | Window in which a just-rotated refresh token may be replayed without being treated as theft. Two tabs restoring a session at once send the same cookie; without the leeway, the loser is logged out of everything. |
| `CHAT_TIMEOUT_SEC` | `900` | A tool-using turn on a large local model runs for minutes. |
| `CHAT_STALL_SEC` | `180` | The longest gap allowed between two chunks of a streamed model reply; a stream that stops sending is given up here instead of at `CHAT_TIMEOUT_SEC`. Time to the first token counts, so keep it above a long prompt's prefill. |
| `TOOL_TIMEOUT_SEC` | `300` | Per-tool ceiling. `MAX_TOOL_HOPS` is what bounds the turn. |
| `MAX_TOOL_HOPS` | `8` | Model↔tool round trips per turn; past eight a model is usually in a retry loop. The last hop runs without tools so the turn still ends in an answer. |
| `MAX_UPLOAD_MB` | `200` | Exists so one upload cannot fill the disk. |
| `STORAGE_RECLAIM_AT` | `0.8` | Disk fill (used ÷ total) past which the files of deleted accounts are removed, oldest first, until the volume is back under it. Checked every 30 minutes and from the admin usage screen's **Sweep orphaned files** button. `0` disables the sweep. Living accounts are never touched. |
| `FILE_CONTEXT_CHARS` | `24000` | Floor for the characters of attached text carried per turn before excerpting. A model that reports its context window gets 35 % of it (about 2.5 characters a token, capped at 150,000), so a sixteen-page paper reaches a 128k-token model whole. |
| `CREDITS_PER_USD` | `100000` | The single exchange rate. Adjust this when provider prices move, rather than re-cutting everyone's allowance. |
| `LITELLM_BUDGET_HEADROOM` | `0.2` | How far above the KloudChat allowance the proxy-side budget sits. A backstop that sits exactly on the limit fires first, blocking someone with a number no screen shows them. |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_COST` / `ARGON2_PARALLELISM` | `3` / `65536` / `4` | `memory_cost` is in KiB. |
| `TITLE_MODEL` | `local/qwen3.8-flash` | First-install seed of **System › Model › Titles, memories and summaries model**. Names conversations, extracts memories and writes the running summary of a long conversation. A strict-local turn uses its own model instead. Empty falls back to the session's own model — correct, but wasteful on an expensive one. Set through `KCHAT_TITLE_MODEL`. |
| `CODE_MODEL` / `CODE_FALLBACK_MODEL` | `local/qwen3.8-27b` / `local/qwen3.8-flash` | First-install seeds of **System › Model › Code-writing model** and its fallback. Write the Python behind `run_code`: the first gets a try and a repair, the second one more try. The fallback also settles two cost readings of a comparison that disagree. A strict-local turn uses the `strict-local/` twin of a `local/` id and drops any other. |
| `OUTLINE_MODEL_DEFAULT` / `ADAPTIVE_CLASSIFIER_MODEL_DEFAULT` | `local/qwen3.8-flash` / `strict-local/qwen3.8-flash` | The outline model and the Auto classifier of a fresh governance row; once an administrator saves **System › Routing**, the saved values win. |
| `WEB_SEARCH_RESULTS` / `WEB_SEARCH_SCRAPE` | `5` / `3` | Each scrape is a page fetch; this trades answer quality against turn latency. |
| `APP_BASE_URL` | — | Origin used to build password reset links. Never taken from the request `Host`, which is attacker-controlled. |
| `TIMEZONE` | `Asia/Seoul` | IANA name. Used only for the date given to the model on every turn — every timestamp in the database stays UTC. Set through `KCHAT_TIMEZONE`. |
| `GEOIP_DATABASE` | — | Path to a MaxMind GeoLite2 City `.mmdb`. Empty disables region lookup; see [Where an address is](#where-an-address-is). Set through `KCHAT_GEOIP_DATABASE`. |
| `TITLE_TIMEOUT_SEC` | `20` | Naming a conversation and extracting memories. Both are best effort, so this is short: a title that takes longer than the turn did is not worth waiting for. |
| `LITELLM_TIMEOUT_SEC` / `LITELLM_PROBE_TIMEOUT_SEC` | `20` / `4` | The master-key client — provisioning a user, issuing a key, listing the catalogue — and the probe behind the admin connection test. **Not the model call**, which is `CHAT_TIMEOUT_SEC` above; raising this one will not help a generation that is being cut off. |
| `AUTO_ROUTING_CLASSIFIER_TIMEOUT_SEC` | `8` | Auto's classification call. On timeout the quality model answers. |
| `FILE_STORAGE_DIR` | `/srv/data/files` | Where uploads and generated media are written. Mount it, or a container rebuild loses every picture. |
| `SELF_HOSTED_CHAT_THINKING` | `false` | First-install seed of **System › Model**'s thinking switch. Whether self-hosted chat models may think before answering. Off sends `enable_thinking: false` to the models named below. Set through `KCHAT_SELF_HOSTED_CHAT_THINKING`. |
| `SELF_HOSTED_THINKING_MODELS` | `qwen3.8-flash` | Comma-separated name fragments of self-hosted models that think unless told not to. Set through `KCHAT_SELF_HOSTED_THINKING_MODELS`. |
| `EXTERNAL_CHAT_THINKING` | `false` | First-install seed, as above. The same for external chat models; off sends OpenRouter's `reasoning` switch. Set through `KCHAT_EXTERNAL_CHAT_THINKING`. |
| `INTERNAL_DATA_STRICT_LOCAL_DEFAULT` | `true` | Default of the governance switch that keeps turns carrying internal material (attachments, project files, memories) on strict-local models. The admin screen's value wins once saved. |
| `API_REPLICAS` / `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `1` / `10` / `20` | Set through `KCHAT_API_REPLICAS`, `KCHAT_DB_POOL_SIZE`, `KCHAT_DB_MAX_OVERFLOW` (above). |
| `SMTP_HOST` … `SMTP_FROM` | — | Seed values for **System › Mail**; the admin screen wins. |

---

### Performance monitoring

The admin **Performance** page (`/admin/performance`) reads `metric_samples`,
written once a minute: every `api` replica writes its own open streams, running
turns and request durations, and one replica (holding a Postgres advisory
lock) also reads the host, the GPU nodes and LiteLLM's request log, folds
minute rows into 10-minute rows after `PERF_RAW_HOURS` and deletes rows older
than `PERF_RETENTION_DAYS`. Set these on the `api` service like the variables
above.

| Variable | Default | Notes |
| --- | --- | --- |
| `PERF_MONITORING` | `true` | Off: nothing is collected. |
| `PERF_HOST_PROC`, `PERF_HOST_CGROUP` | `/host/proc`, `/host/cgroup` | The host's `/proc` and `/sys/fs/cgroup`, mounted read-only in `docker-compose.yml`. Without them the page has no host or container figures. Containers are named from their processes (command line, process names, bind-mount sources) and from `PERF_DNS_NAMES`; there is no docker socket. |
| `PERF_DISKS` | `data=/srv/data/files,docker=/` | `label=path` pairs whose filesystems are reported. |
| `PERF_TARGETS` | `vllm-27b,vllm-flashnext,vllm-bgem3,vllm-rerank,vllm-whisper,gpu-1,gpu-2` | Read from `<backend address>/metrics/<name>` with `X-Tools-Key` (KloudChat-LLM [tools.md](https://github.com/boanlab/kloudchat-llm/blob/main/docs/tools.md)). `PERF_METRICS_URL` overrides the base. |
| `PERF_MODEL_LOGS` | `true` | Per-model latency and errors from LiteLLM `/spend/logs/v2` (master key; no prompts or responses are read). |
| `PERF_VLLM_WAITING_MAX`, `PERF_KV_CACHE_MAX`, `PERF_TTFT_P95_MAX_SEC` | `2`, `90`, `10` | GPU saturation, KV cache and first-token verdicts, each sustained for `PERF_SUSTAIN_MIN` (10). |
| `PERF_API_CPU_MAX` | `80` | Percent of one core per api replica, sustained for `PERF_API_CPU_SUSTAIN_MIN` (15): the "consider adding an api replica" verdict. |
| `PERF_MEM_MAX`, `PERF_DISK_WARN`, `PERF_DISK_CRITICAL`, `PERF_ERROR_RATE_MAX`, `PERF_GPU_TEMP_MAX` | `90`, `85`, `95`, `5`, `85` | |
| `PERF_GPU_NODE_MEM_MAX` | `99` | Memory warning for a GPU node. vLLM reserves unified memory up front, so a node sits near 97% normally; kept apart from the host's `PERF_MEM_MAX`. |

`GET /api/admin/perf/metrics` (an admin's bearer token) returns the latest
value of every series in the Prometheus text format, for Grafana.

## Behind a reverse proxy

The web container serves the app and proxies `/api/`. When something else
answers the internet in front of it — the usual arrangement — two things have
to line up or every audit row, share visit and access log line records the proxy
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
account's own access log, and the admin audit trail.

The lookup is **offline only**. Resolving addresses through a third-party
service would send every visitor's address off the instance, so KloudChat reads
a MaxMind DB file from disk or says nothing:

1. Download `GeoLite2-City.mmdb` (free, needs a MaxMind account).
2. Mount it into the API container.
3. Set `GEOIP_DATABASE` to its path.

With no file configured, those screens show the address alone. Private ranges
never reach the database and read **Private network** (`내부망`).

---

## Runtime settings (System)

The **System** (시스템) screen (`/admin/system`, in the account menu) is stored in
`system_settings`, editable by administrators, and applied without a restart.
Secrets in this table are encrypted at rest with `SECRET_KEY` (or, when that is
unset, a key derived from `JWT_SECRET`).

### Proxy

- **LiteLLM address** — empty uses the server's address (`LITELLM_BASE_URL`,
  else derived from the backend address).
- **Master key** — never displayed once saved; leaving the field blank keeps
  the current key. The tool endpoints use `TOOLS_GATEWAY_KEY` from `.env`
  instead.
- Connection test, model list refresh, and the models hidden because their
  price is unknown.

### Features

- **Features** (enabled surfaces) — see [Enabled surfaces](#enabled-surfaces-features).
- **Integrations** (feature integrations) — one **server address** (the backend
  gateway) derives the LiteLLM address and the seven tool endpoints. Each
  feature has its own address field and connection test; a filled field
  overrides the derived one if you host that feature elsewhere.
- **Text from scans (OCR)** — on/off, reading mode (engine, vision model or both),
  the strict-local vision model (**Auto** picks `OCR_VISION_MODEL`), and the most
  pages read from one document.

### Mail

Host, port, security (`starttls` / `ssl` / `none`), username, password,
envelope sender. Named modes rather than a boolean because the two encrypted
modes use different ports and a different handshake, and picking the wrong one
produces a timeout with no clue which.

With no SMTP host, outbound mail is disabled and password reset is hidden.
Telling people to contact an administrator beats a reset link that never
arrives. The only mail this system sends is what a person triggered: a password
reset they asked for, or the confirmation of an address they signed up with.

### Signup

- **Mode** — `open` / `approval` / `closed`, overriding `SIGNUP_MODE` when set.
- **Allowed domains** — a comma-separated list of mail domains that may
  register; empty allows any. Subdomains are not implied.
- **New accounts' monthly credits** — granted at signup (open mode) or
  approval; the approval dialog can still set a different number per account.
- **Email verification** — when on, a new account is `pending` until the link
  in the confirmation mail is clicked (24 hours, single use); then `open`
  activates it and `approval` hands it to an administrator. Approving an
  account counts as confirming its address. Needs SMTP: without a mail server
  the switch is inert and signups go through unverified, and the screen says
  so.

### Model

Which model does which job when the person has not chosen one: the default of
each surface (chat, report, slides, image, audio, video), the model for
titles, memories and the running summary, the model that writes `run_code`
programs and its fallback, and whether self-hosted and external models think
before answering. Chat and image must be chosen (`422 model_chat_required`,
`422 model_image_required`); report and slides left empty follow the chat
default; audio and video left empty take the cheapest model of their kind. The
side-work models read the conversation, so only self-hosted chat models can be
chosen (`422 model_title_invalid` and the like otherwise); a strict-local turn
uses the `strict-local/` twin. A model the gateway stopped serving stays
selectable as it is, marked unavailable, and is not swapped silently.

### Routing

The Auto routing classifier, economy and upgrade models — see
[Auto routing](#auto-routing) — and the outline model, which plans
a document before the surface's own model writes it.

#### Which model does what

With the two self-hosted models, the defaults split the work by what each does
better on Korean institutional requests:

| Work | Model | Set by |
| --- | --- | --- |
| Chat answers | `local/qwen3.8-flash` | the person's choice; else **System › Model › Default chat model** |
| Report and deck prose | `local/qwen3.8-27b` | the person's choice; else **System › Model › Default report / slides model** |
| Writing an empty or announced-only chat answer once more | the other self-hosted model (27B for Flash, Flash for 27B) | follows the chat, report and slides roles |
| Outline, research plan, figure proposals | `local/qwen3.8-flash` | **System › Routing › Model that plans** (`outline_model_id`) |
| Judging: key figures, cost and score readings, checks, repairs, title block | the outline model, else the writer | follows the outline model |
| Calculation programs for a document (`calc`) | the outline model, else the writer | follows the outline model |
| Missing-information questions judged against the material | the outline model's `strict-local/` twin, else the first strict-local model | follows the outline model |
| Auto classifier | `strict-local/qwen3.8-flash` | **System › Routing › Complexity classifier** |
| Titles, memories, running summary | `local/qwen3.8-flash` | **System › Model › Titles, memories and summaries model** |
| `run_code` file programs | `local/qwen3.8-27b`, then Flash | **System › Model › Code-writing model**, its fallback |
| Re-reading scanned pages | `strict-local/qwen3.8-flash` | **System › Features › Text from scans (OCR)** |

Why this split:

- **Writing goes to the 27B.** Flash adds details nobody gave (a booth size, a
  promise to pay costs in an official letter) and lets Chinese characters and
  English words slip into Korean; the 27B does neither.
- **Reading and judging go to Flash.** It copies totals out of the material,
  writes calculation programs and classifies in a valid shape more reliably,
  and keeps more key facts in a long conversation's summary.
- **Code that reads files stays on the 27B.** Flash's pandas code can return
  wrong counts without failing.
- **Thinking stays off.** It makes Flash's readings exact but takes minutes a
  call instead of seconds.
- **Load is split by node.** Prose, the bulk of the tokens, runs on the 27B's
  node, which serves more sequences at once; Flash's node takes the many short
  readings.

### Connector catalogue

The MCP connectors users can install from **Customize › Connectors**: name,
address or stdio command, whether the entry is listed, and whether it is
**Built in** (`by_default`). An installed connector keeps its entry's slug.
Users can also register their own MCP server by address.

A **Built in** entry is installed for every active user: everyone at once when an
administrator saves it with the switch on, a new user when the account becomes
active (sign-up, e-mail verification, approval), and anyone still missing it
when the API starts (a background task, one replica at a time under an advisory
lock). Users can switch the connector or its tools off but cannot remove it
(`409 connector_default`). Tools are copied from a connected install synced
since the entry last changed, otherwise listed by the server once per process
(60 s limit); a server that does not answer leaves the copy in error with
**Reconnect**. An entry that asks each user for credentials (`required_env`)
cannot be built in.

Shipped entries, all built in and attributed to the oldest administrator:

| Slug | Runs | Needs |
|---|---|---|
| `time` | `uvx --with 'mcp<2' mcp-server-time --local-timezone=Asia/Seoul` | PyPI reachable from the API container on first run (uv cache under `/srv/data/uv-cache`) |
| `youtube` | `uv run --script /srv/mcp/youtube.py` | `./mcp` mounted at `/srv/mcp`; speech-to-text address for videos without captions |
| `deep-research` | `${TOOLS_RESEARCH_URL}/mcp` | the deep-research address (`BACKEND_BASE_URL` + `/tools/research`, kloudchat-llm's `deep-research` service); runs within `TOOL_TIMEOUT_SEC` |

### Enabled surfaces (Features)

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

### Governance (Security · audit, `/admin/governance`)

Prohibited-intent categories, message-body retention, an idle sign-out timer
(enforced by the browser; `0` disables it) and three privacy policies are
available:

- **Internal material stays strict-local** — on by
  default (`INTERNAL_DATA_STRICT_LOCAL_DEFAULT`). A chat, report or deck turn
  that carries the organisation's own material — an attachment, a project's
  files or instructions, a memory — runs on the strict-local twin of the chosen
  model (`local/x` → `strict-local/x`), else the first strict-local safe model,
  else it is refused with `internal_data_requires_strict_local`. This is decided
  before any Auto lane, and a strict-local writer is never moved outward.
  Pictures, charts, figures and document edits (fact-check, review, section,
  slide or block rewrite, a theme read from a file) follow the boundary of the
  conversation they belong to: inside it they use strict-local models only — a
  picture goes to the person's **strict-local picture model** preference, else
  the first strict-local picture model — and are refused with the same code
  when there is none. A document edit carrying personal data also stays
  strict-local rather than being masked, since the mask would be written back
  into the document; a web fact-check of such a document is refused
  (`factcheck_needs_web`).

- **PII masking** is the organisation-wide upper bound. When enabled,
  matching content is always masked and raw external delivery cannot be
  enabled.
- **External data guard** protects chat. Administrators
  choose a set of models whose live catalogue metadata explicitly says
  `self_hosted` and `strictLocal`; stale or unknown model IDs are rejected.
  The visible catalogue's top-to-bottom order is the fixed routing priority.
  Administrators may optionally allow a user to choose raw external delivery
  after detection. A fresh install starts with the guard on and raw delivery
  disallowed.

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
| `/settings/preferences` | Default model, interface behaviour, privacy default action, the strict-local picture model (shown when two or more exist) |
| `/settings/personalization` | About-me and response-style text prepended to every chat |
| `/settings/memory` | Memories, global or per project |
| `/settings/themes` | Themes — your own looks (colour, type, voice) beside the shipped ones |
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
