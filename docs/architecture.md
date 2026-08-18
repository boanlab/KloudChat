# Architecture

What the system does, and why the load-bearing decisions are the way they are.
Everything here is in the code and most of it is covered by the specs under
`apps/web/e2e/`.

---

## 1. What this is

A generative AI workspace with **its own authentication, credits and
workspace**, sitting in front of LiteLLM. The browser talks only to the KloudChat
API, and the LiteLLM master key never leaves this container.

Six things get produced:

| Surface | Output | Execution |
|---|---|---|
| Chat | Conversation, plus artifacts a tool produced (code, HTML, charts) | Synchronous SSE |
| Report | Document artifact + docx, PDF, HWPX, Markdown | Synchronous SSE, per section |
| Slides | Deck artifact + pptx, PDF, Markdown | Synchronous SSE, per slide |
| Image | Picture artifact | Synchronous (one image = one completion call) |
| Audio | Speech or music artifact | Synchronous, collected from a stream |
| Video | Video artifact | **Asynchronous job** — the only one that outlives its request |

Video is a job because it is the only kind that cannot finish inside its
request. Queueing the others would add a polling loop for someone already
waiting.

---

## 2. System composition

```
browser ──── KloudChat API ──┬── backend gateway ───┬── /litellm   LiteLLM ── vLLM (local GPU)
  coding agent ── /llm ──┘                      │                     └ OpenRouter (commercial, media)
   │           │         │                      ├── /tools/search    web search
   │           │         │                      ├── /tools/fetch     document fetch
   │           │         │                      ├── /tools/exec      code execution
   │           │         │                      ├── /tools/research  deep research (MCP)
   │           │         │                      ├── /tools/stt       speech-to-text
   │           │         │                      └── /tools/index     retrieval index (§8)
   │           ├── Postgres
   │           ├── file store (local disk)
   │           └── MCP connectors (stdio · http)
   └── /api/files/…?t=… ← media is fetched by the element itself (§6)
```

**The browser does not know the backend exists.** The master key is held only
by the API container, which issues a virtual key per user and calls upstream
with it. Credits are enforced by KloudChat, with the LiteLLM budget ceiling as a
backstop.

The backend address is stored through the admin screen (Settings → System →
Integrations). One address derives all six feature endpoints by appending
paths; hosting one feature elsewhere means overriding that one field. A feature
left blank drops out of the tool list and everything else keeps working.

---

## 3. Authentication

argon2id, a short-lived access token (JWT, held in memory) and a rotating
refresh cookie (httpOnly). Signup enters the `pending` state and is activated
by an administrator, and **the LiteLLM user is created at that moment** —
creating it at signup would accumulate proxy users for accounts that are never
approved.

Password reset stores only a hash, is single-use, and expires after 30 minutes.
The request always returns 204, so it cannot be used to confirm that an account
exists. The link is built from **the administrator-configured address, not the
request's `Host` header**.

### The one place the access token is not a header

`GET /files/{id}/content` also accepts `?t=<token>`. `<img>`, `<audio>` and
`<video>` cannot attach an `Authorization` header, and the token lives in
memory rather than in a cookie — without this exception, **none of the images,
audio or video this instance produces could be displayed inside it.**

The cost is that the token appears in access logs. That is why the exception is
attached to this one route rather than to the default dependency
(`core/deps.py:current_viewer`).

---

## 4. Credits

1 credit = $0.00001 (`credits_per_usd = 100_000`). **The ledger
(`CreditLedger`) is the truth**; `messages.usage.credits` is for display.

Each modality sells in a different unit, and the wrong unit makes a displayed
price a fraction of the real one.

| Modality | Unit | Catalogue field |
|---|---|---|
| Chat, report, slides | 1k tokens | `creditCost` / `inputCreditCost` |
| Image | per image | `creditPerImage` (measured tokens per model family × token price) |
| Audio | per call | `creditPerCall` (`output_cost_per_request`) |
| Video | **second × (resolution, audio)** | `creditPerSecond` |

Video rates exist in one place, `services/videogen.py:_RATES`, and the
catalogue re-exports them verbatim, so a quote and a charge cannot diverge.

**A model whose price is unknown is dropped from the list.** Upstream returning
0 means "price unknown", not "free"; taken at face value it bills real money
against a zero counter. Dropped models appear in the admin screen with the
reason.

---

## 5. Data model

Sixteen migrations under `alembic/versions/`. The principal tables:

- `users` · `refresh_tokens` (family-based rotation) · `password_resets` ·
  `api_keys` · `audit_events`
- `sessions` · `messages` — conversation. `messages.usage` is JSONB
- `credit_ledger` — the ledger
- `projects` · `files` · `artifacts` · `artifact_versions` · `skills` ·
  `memories` · `agents` · `connectors`
- `templates` — starting points a user added, optionally carrying a form file
  whose text is attached when the template is picked
- `shares` — read-only links. The token is the permission, and revocation is a
  flag rather than a delete
- `jobs` — video only. Without `provider_job_id`, a restart orphans a
  half-generated clip
- `system_settings` — LiteLLM address and master key, SMTP. Database values
  override environment variables

`files` carries the scope it belongs to: `project_id` for project knowledge,
`session_id` for a one-off attachment, `agent_id` for an agent's searchable
shelf. `source_url` marks text read from a page rather than uploaded, and
`indexed_at` whether the vector index covers it.

Artifacts outlive conversations: clearing history detaches them
(`session_id = NULL`) rather than deleting them.

---

## 6. Execution paths

### Chat

`POST /sessions/{id}/messages` → SSE, emitting `delta`, `step`, `artifact`,
`usage`, `title` and `done`.

Tools are attached only when the model supports function calling — giving them
to a model that does not yields either a 400 from upstream or an invented call.
Built-in tools: `web_search` (SearXNG), `fetch_url` (Crawl4AI), `execute_code`
(sandboxed), `create_artifact`, `create_chart`. Tools from installed MCP
connectors are added to these.

**Tool count is what sizes the connector catalogue.** Every active tool ships
its full schema on every turn, and model tool-choice degrades well before
twenty of them. A server enters the catalogue once it has been started against
a real credential and had its tool count checked.

Artifact extraction and automatic memory run **after** the turn, in their own
transaction: sharing the turn's would hold it open for an extra query and a
model call, and a failure in either would roll the answer back.

### Report and slides

Both are two-pass: one outline call returns the title and the table of
contents, then one call per section or slide, each carrying what the previous
ones wrote. Six listed sections means six are coming, which is what makes the
progress indicator honest.

A failed section is marked and the rest continues.

Both receive the workspace blocks the chat surface gets — project
instructions, memories, skills, and any file attached to the turn — so a
request naming an uploaded form is written against that form.

Reports search the web before writing and cite what they found; with no search
backend the shelf is empty and the citation rule drops out of the prompts.

Slides use four layouts — `title`, `bullets`, `quote`, `two-column` — each
implemented in all three renderers (preview, `.pptx`, `.pdf`). The frontend type
permits six; `image` has no producer and `chart` would draw invented numbers.
The outline also picks the deck's accent from a fixed palette, and honours a
slide count stated in the request up to 50.

### Sharing

In the `shares` table **the token is the permission**: 43 random characters,
revocable, and the only thing the public route consults.

Two scopes. `workspace` requires sign-in from any member of the instance;
`link` opens to anyone holding the URL, which is the case where the recipient
has no account here.

A shared session carries the artifact it produced alongside its messages —
resolved through the session's own `artifact_id`, so nothing else in the
owner's workspace becomes reachable. The response contains **only the shared
thing** — no owner name, no project, no neighbouring artifacts, no walkable
ids. A revoked token and an unknown token
give the same 404, because distinguishing them discloses somebody else's
account.

The router sits **above** the auth gate, or a share link would land on the
sign-in screen.

### Coding agents

`/llm/*` forwards requests to LiteLLM unchanged, authenticating **by key, not
by session**: the incoming `Authorization` header goes straight upstream and
LiteLLM decides whether it is valid. An issued key *is* a virtual key, so spend
and the model allow-list follow it. The budget attaches to the account, so
extra keys split one allowance rather than multiplying it.

The master key never travels this path, and a call without credentials gets a
401 from upstream. Responses are relayed chunk by chunk, so streaming passes
through intact.

`/usage` shows conversation usage and API key usage **side by side**. The first
number comes from KloudChat's ledger and the second from the proxy; they are
aggregated at different moments, so adding them makes neither correct.

### Administrator controls

**Enabled surfaces** turn screens off in the UI *and* make the server refuse
sessions of that kind — hiding alone leaves the feature on for anyone who types
the URL. Chat cannot be disabled.

**Branding** logos are served without authentication, because the sign-in
screen renders one before anybody is authenticated. The filename carries a
content hash, so replacing the logo changes its URL. SVG is rejected: it can
carry script, and that file loads for every visitor.

### Transcription and fact-checking

Both are contained within this instance.

**Transcription** records in the browser and sends the audio to Whisper.
`webkitSpeechRecognition` needs no backend but streams the microphone to a third
party, so it is not used. Recordings are not stored, and the transcript fills
the composer rather than being sent.

**Fact-checking** extracts only checkable claims from a deck and verifies them
through SearXNG. One rule makes it safe: **a confident verdict (`supported` or
`unsupported`) must carry a source URL, and with nothing to point at it drops to
`uncertain`.** A badge with no evidence stops the reader looking where they
should. Opinions are not extracted.

### Images and audio

Both call OpenRouter through LiteLLM via `chat/completions`. Audio **requires
`stream: true`**, and streaming yields only `pcm16`, which a 44-byte RIFF header
turns into a WAV. Without `stream_options.include_usage` the stream carries no
usage block and the turn bills as one credit.

### Video

OpenRouter serves video from a separate endpoint (`/api/v1/videos`), so these
models do not appear in `/model/info` and are **declared in
`services/adapters.py:ADAPTER_MODELS`**. Only models `videogen.submit` can
actually call belong there.

**Submission uses the user's virtual key**, so the charge lands on that person.
**Polling and download use the instance master key**, because LiteLLM
classifies job routes as administrative and answers a virtual key with 401. A
401 or 403 while polling ends the job as failed; read as "still running", it
would sit at 1% forever.

The accepted request fields are `duration`, `resolution`, `generate_audio` and
`aspect_ratio`. **`duration_seconds` is silently ignored**, producing a
default-length clip at twice the quoted price.

---

## 7. Context assembly

What goes into one turn, in order: the system prompt (per surface) → agent
instructions → project instructions → memories → skills → files attached to
this turn → project knowledge → tool rules → conversation history.

Later blocks weigh more with a small model, so the order is load-bearing: the
material a turn was given sits closest to the question, and the standing
instructions that shape every turn sit furthest from it.

Policy (`governance`) is applied **before anything reaches the model**. For
chat and model comparison, the privacy guard scans this exact assembled prompt,
the current input and conversation history before it writes a message, issues a
LiteLLM key, charges credits or starts any completion. Chat also materializes
the exact OpenAI tool-schema array first and includes it in the same scan and
decision-token hash; every model hop reuses that detached snapshot. A masked
external retry drops a tool whose definition is sensitive instead of rewriting
function or property names and breaking its runtime mapping. The deterministic
v1 detector covers high-confidence identifiers and secrets; it does not guess
at names or postal addresses.

An external, hybrid or unknown-boundary model with findings requires one of
four outcomes: route to an explicitly declared strict-local model, mask the
envelope, send the raw envelope when the administrator allows it, or cancel and
edit. The five-minute decision token is bound to the user, session, requested
models and a hash of the complete envelope, so editing context cannot replay an
old decision. Only value-free category, count and source aggregates are sent to
the browser or audit log.

`model_info.kchat_data_boundary`, `kchat_strict_local` and
`kchat_privacy_only` are the authority for a model's boundary. IDs such as
`local/*`, providers and API-base addresses are not evidence: a local-looking
alias may have an external fallback. Missing or invalid metadata is `unknown`
and can never be selected as a privacy-safe route. Egress decisions bypass the
30-second display catalogue cache and refresh LiteLLM metadata on every send;
if that refresh fails, no cached strict alias is trusted.

Governance authorization likewise bypasses its process-local display cache. If
the authoritative policy row cannot be read, every chat, comparison and legacy
report/slides send path fails closed with `503 governance_unavailable` before
model discovery, transcript writes, billing, virtual-key issuance or upstream
calls. This also blocks a strict-local request: without the row the server does
not know whether legacy masking, intent filtering or blocked categories apply.

Strict-local turns disable web search, URL fetching and remote connector tools.
Their tool registry is built from in-process runners only, rather than building
remote connectors and filtering them afterwards. Tool output on an external
turn is inspected before every follow-up completion and masked before it enters
that prompt. While either guard or legacy masking is active, every persisted
model-generated textual field (answer, comparison variants, timeline details,
artifact payloads and routing metadata) is deterministically masked even when
the inbound envelope was clean. Title generation and automatic memory receive
only the masked turn text.

The selectable guard is intentionally limited to chat and model comparison in
this release. Reports, slides, media generation and the `/llm` compatibility
API keep their existing always-mask behaviour where it was already supported;
they do not present the new decision flow.

### Auto cost routing

Auto is a session mode, not a model alias. `sessions.model` always stores the
last real model the person selected as the quality ceiling; the separate
`routing_mode` field says whether an eligible turn may use an economy model.
This prevents a synthetic `auto` id from reaching LiteLLM and keeps manual
model selection authoritative.

Privacy inspection runs before classification. If the assembled envelope has
a finding, Auto performs no key issuance or model call and the existing privacy
decision owns the turn. A clean, ordinary chat turn may be classified by a
live, zero-cost strict-local model using the caller's virtual key, redacted
LiteLLM logging and `disable_fallbacks`. Only a high-confidence low-complexity
JSON verdict can select an economy model; every timeout, malformed response or
uncertain state keeps the quality ceiling.

Economy candidates are administrator-ordered and revalidated against the live
catalogue, caller allowlist, context window, data boundary and both token
prices. `hybrid`, `unknown` and `privacyOnly` models are excluded. The chosen
answer call also disables fallback, so a failed economy route cannot cause a
second, hidden premium charge. Message routing stores the quality, selected and
actual models plus enum-only decision metadata; classifier prompts and free-form
model reasoning are never persisted.

---

## 8. Agent knowledge and retrieval

An agent can carry documents of its own: files uploaded to it, and pages read
once from a URL and stored as text. They are `files` rows with `agent_id` set,
so extraction, blob storage and token counting are the same as anywhere else.

They are **searched, not injected**. Project knowledge goes into every turn
whole inside a character budget; past that budget the block degrades to a list
of filenames. An agent's shelf is reached through a `search_knowledge` tool
instead, so retrieval happens when the model asks for it.

Three tiers, chosen by size and by what is available:

| Shelf | Behaviour |
|---|---|
| under 12,000 characters | returned whole, unranked — nothing to miss |
| larger | vector hits merged with lexical hits |
| no index configured, or it is down | lexical alone |

`services/knowledge.py` is the lexical half: term containment plus character
bigrams, which is what carries Korean, where whitespace tokens include
particles. It matches words, so it cannot find 접근 통제 from "access control".

`services/index_client.py` is the other half, calling `/tools/index` in
KloudChat-LLM — pgvector beside the embedding model. Nothing there raises: a
failed write is a boolean and a failed search is no passages, because the
lexical path still answers. The index is derived and rebuildable from
`files.text`; `indexed_at` records what it covers, and
`POST /agents/{id}/knowledge/reindex` fills the gaps (`?force=true` re-sends
everything, which an embedding-model change needs).

**A collection name is the authorisation.** `agents.index_key` holds 32 bytes of
urlsafe randomness, minted on first use and never derived from `agent_id`,
which travels in URLs and API responses. Deleting an agent drops its collection.

---

## 9. Frontend structure

```
apps/web/src/
  pages/        one per route, plus settings/ — three user tabs and the
                admin system tab with its sections
  components/   chat · report · slides · chart · artifacts · media · layout · ui
  store/        useStore.ts — a single zustand store
  lib/          api.ts (the backend seam) · kinds.ts · i18n.ts · reportMarkdown.ts
```

**The store has an epoch guard.** Every action that writes to the workspace
calls `touchWorkspace()`, which invalidates any `loadWorkspace()` response
still in flight — otherwise a late response overwrites the local write.

---

## 10. Verification

```bash
bash scripts/smoke-test.sh           # auth, approval, rotation and suspension checks (non-destructive)
cd apps/web && npx playwright test   # E2E — pinned to workers: 1
```

**Do not override `--workers`.** Every spec uses the same account and several
pick "the most recent X". Running in parallel produces failures that have
nothing to do with the app.

Some specs **spend real money**: roughly 4,400 credits for an image, 1,000 for
audio, 12,000 for a video. Each creates exactly one.

One principle above the rest: **watch it fail before you fix it.** A spec that
passes identically before and after a change is guarding nothing.

---

## 11. Known limitations

- **The video playback spec generates a new clip on every run** (12,000
  credits). Exclude it with `--grep-invert` when iterating.
- **Fact-checking** costs one search and one model call per claim, capped at
  four claims per slide.
- **The first-signup bootstrap test** needs an empty database and is skipped by
  default.
- **Speech-to-text requires a Whisper backend.** Without one the composer's
  microphone is not rendered, and the `youtube` connector handles only videos
  that have captions.
- **A single API instance is assumed.** Migrations run on container start and
  runtime settings are cached in-process; see
  [deployment.md](deployment.md#scaling-notes).
