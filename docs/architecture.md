# Architecture

What the system does, and why the load-bearing decisions are the way they are.
Everything here is in the code and most of it is covered by the specs under
`apps/web/e2e/`.

---

## 1. What this is

A generative AI workspace with **its own authentication, credits and
workspace**, sitting in front of LiteLLM. The browser talks only to the kchat
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
browser ──── kchat API ──┬── backend gateway ───┬── /litellm   LiteLLM ── vLLM (local GPU)
  coding agent ── /llm ──┘                      │                     └ OpenRouter (commercial, media)
   │           │         │                      ├── /tools/search    web search
   │           │         │                      ├── /tools/fetch     document fetch
   │           │         │                      ├── /tools/exec      code execution
   │           │         │                      ├── /tools/research  deep research (MCP)
   │           │         │                      └── /tools/stt       speech-to-text
   │           ├── Postgres (pgvector)
   │           ├── file store (local disk)
   │           └── MCP connectors (stdio)
   └── /api/files/…?t=… ← media is fetched by the element itself (§6)
```

**The browser does not know the backend exists.** The master key is held only
by the API container, which issues a virtual key per user and calls upstream
with it. Credits are enforced by kchat, with the LiteLLM budget ceiling as a
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

Twelve migrations under `alembic/versions/`. The principal tables:

- `users` · `refresh_tokens` (family-based rotation) · `password_resets` ·
  `api_keys` · `audit_events`
- `sessions` · `messages` — conversation. `messages.usage` is JSONB
- `credit_ledger` — the ledger
- `projects` · `stored_files` · `artifacts` · `artifact_versions` · `skills` ·
  `memories` · `agents` · `connectors`
- `shares` — read-only links. The token is the permission, and revocation is a
  flag rather than a delete
- `jobs` — video only. Without `provider_job_id`, a restart orphans a
  half-generated clip
- `system_settings` — LiteLLM address and master key, SMTP. Database values
  override environment variables

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

Slides use three layouts — `title`, `bullets`, `quote`. The frontend type
permits six; the others have no renderer (`image`), render identically
(`two-column`), or would draw invented numbers (`chart` is five hard-coded
bars).

### Sharing

In the `shares` table **the token is the permission**: 43 random characters,
revocable, and the only thing the public route consults.

Two scopes. `workspace` requires sign-in from any member of the instance;
`link` opens to anyone holding the URL, which is the case where the recipient
has no account here.

The response contains **only the shared thing** — no owner name, no project, no
neighbouring artifacts, no walkable ids. A revoked token and an unknown token
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
number comes from kchat's ledger and the second from the proxy; they are
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

What goes into one turn: the system prompt (per surface) → agent instructions →
project instructions → project knowledge files → skills → relevant memories →
attachments → conversation history.

Policy (`governance`) is applied **before anything reaches the model**. Blocks
happen before the write, and PII masking happens before the write — masking
after sending leaves the original in the database.

---

## 8. Frontend structure

```
apps/web/src/
  pages/        one per route, plus seven settings tabs
  components/   chat · report · slides · chart · artifacts · media · layout · ui
  store/        useStore.ts — a single zustand store
  lib/          api.ts (the backend seam) · kinds.ts · i18n.ts · reportMarkdown.ts
```

**The store has an epoch guard.** Every action that writes to the workspace
calls `touchWorkspace()`, which invalidates any `loadWorkspace()` response
still in flight — otherwise a late response overwrites the local write.

---

## 9. Verification

```bash
bash scripts/smoke-test.sh           # 64 auth, approval, rotation and suspension checks (non-destructive)
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

## 10. Known limitations

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
