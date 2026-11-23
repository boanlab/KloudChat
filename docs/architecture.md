# Architecture

What the system does, and why the load-bearing decisions are the way they are.
Everything here is in the code; most of it is covered by the tests under
`apps/api/tests/` and the specs under `apps/web/e2e/`.
Korean text in 「」 brackets is a literal the code reads or writes — a user's
words it matches, a label it emits — followed by an English gloss.

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

**Image edits are the same call with the picture attached.** `POST /sessions/{id}/images` with
`sourceArtifactId` (an image artifact) or `sourceFileId` (an attachment or file-box picture)
sends the stored picture, scaled to at most 2048 px, as an `image_url` content part beside the
instruction (`imagegen.generate(source=…)`), without `image_config` so the picture keeps its
shape.

- **Which models edit.** Only catalogue models with `editsImages` draw an edit: the gateway's
  `model_info.kchat_edits_images` when declared, else `imagegen.EDITING_MODELS`. A model that only
  edits (`drawsImages` false: `kchat_draws_images`, else `imagegen.EDIT_ONLY_MODELS`) has no
  `image` in its catalogue `kinds`, so no picker or default asks it for a new picture. The
  self-hosted Qwen-Image-Edit shim takes the picture from the last user message's `image_url`
  part (a data URL), refuses a request without one, and calls vLLM-Omni `/v1/images/edits`.
- **Egress.** The source picture counts as egress like an attachment. In a strict-local
  conversation (a Super Agent `super_strict` one too), for a picture a strict-local model drew,
  or for an uploaded picture (or one made from it) under `internal_data_strict_local`, the edit
  runs on a strict-local model that edits (`_media_model` with the `image_edit` pseudo-kind),
  whatever model the person picked; with none the call is refused with `image_edit_strict_local`.
  Outside strict-local, a picked model that only draws gets `image_model_cannot_edit`.
- **Result.** A new image artifact whose `data.editOf` names the source (left untouched),
  `data.lineage` the line's first picture and `data.editVersion` its number. It is charged as a
  picture, in the ledger as `image.edit`. No model behind the gateway's chat endpoint takes a
  mask, so there is no region edit.

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
   │           │         │                      ├── /tools/index     retrieval index (§8)
   │           │         │                      └── /tools/ocr       text from scanned pages
   │           ├── Postgres
   │           ├── file store (local disk)
   │           ├── media worker (stock ffmpeg, internal network only)
   │           └── MCP connectors (stdio · http)
   └── /api/files/…?t=… ← media is fetched by the element itself (§3)
```

**The browser does not know the backend exists.** The master key is held only
by the API container, which issues a virtual key per user and calls upstream
with it. Credits are enforced by KloudChat, with the LiteLLM budget ceiling as a
backstop.

The browser reaches the API only through the web container, whose nginx
serves the bundle and proxies `/api` and `/llm` to the `api` service (one or
more replicas, no published port). The backend address is stored through the
admin screen (System › Features › Integrations). One address derives the LiteLLM
address and the seven tool endpoints by appending paths; hosting one feature
elsewhere means overriding that one field. A feature left blank drops out of the tool
list and everything else keeps working.

---

## 3. Authentication

argon2id, a short-lived access token (JWT, held in memory) and a rotating
refresh cookie (httpOnly). Signup enters the `pending` state (or becomes active
directly under an open signup policy; the first account is the administrator) and
**the LiteLLM user is created when the account is activated** — creating it at
signup would accumulate proxy users for accounts that are never approved.

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

### What is recorded about access

`audit_events` carries a sign-in, a failed sign-in, a password change and a key
issue, each with the client's address and browser. Two screens read it: an
account's own **Access log** (`GET /auth/me/access`, the account's rows only) and
the administrator's audit trail (`GET /admin/audit`, everything, a page at a time
with action, account, period and text filters, sorted on the server).
`GET /admin/audit/export.csv` and `GET /admin/users/export.csv` stream the same
filtered, ordered rows as CSV (`services/csv_export.py`: UTF-8 BOM, service-zone
times, formula-looking cells kept as text, at most 50,000 rows); each export is
itself recorded (`audit.export`, `user.export`) with the filters it used. A change
event keeps `metadata.changes = {field: [old, new]}` from
`services/audit.py:diff`, secrets masked and long values clipped; `audit.ACTIONS`
lists every code, and the screen labels each one.

The address is only as good as the proxy in front. `core/deps.py:client_ip`
reads the first hop of `X-Forwarded-For`, and the web container resolves that
hop from trusted proxies only — see
[Behind a reverse proxy](configuration.md#behind-a-reverse-proxy).

A region is shown beside the address when, and only when, a MaxMind GeoLite2
file is mounted (`services/geoip.py`). The lookup is offline by design:
resolving addresses through a third-party service would send every visitor's
address off the instance. With no file the screens show the address alone,
and private ranges read **Private network** (`내부망`) rather than a guess.

---

## 4. Credits

1 credit = $0.00001 (`credits_per_usd = 100_000`). **The ledger
(`CreditLedger`) is the truth**; `messages.usage.credits` is for display.
Free work that still has a quota goes on the same ledger at zero credits with
a `units` figure: seconds transcribed, chunks embedded, tokens of an API key
call, and web searches (`search.web`, one row per turn with the number of
queries the turn sent to SearXNG, counted from `ToolContext.tool_calls` in chat
and from the research step on the document surfaces). The usage screens read
searches from there.

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

**Cached prompt tokens are recorded, not discounted.** Providers report the part of a
prompt they served from their prefix cache under different names
(`prompt_tokens_details.cached_tokens`, DeepSeek's `prompt_cache_hit_tokens`,
`cache_read_input_tokens`); `services/usage_fields` reads all of them, and every model call
that counts tokens carries the figure as `cachedInputTokens` beside `inputTokens`, of which
it is a part. A turn's total lands on `messages.usage.cachedInputTokens` (absent when the
provider reported none). Credits still charge every input token at `inputCreditCost`: the
gateway's model entries carry no cache-read price, and through OpenRouter the discount
depends on which upstream provider served the call.

**A model whose price is unknown is dropped from the list.** Upstream returning
0 means "price unknown", not "free"; taken at face value it bills real money
against a zero counter. Dropped models appear in the admin screen with the
reason.

---

## 5. Data model

Migrations live under `alembic/versions/`. The principal tables:

- `users` · `refresh_tokens` (family-based rotation) · `password_resets` ·
  `email_verifications` · `api_keys` · `audit_events`
- `sessions` · `messages` — conversation. `messages.usage` is JSONB
- `credit_ledger` — the ledger
- `projects` · `files` · `file_pages` (OCR text per page) · `media_parts` (a video's
  transcribed pieces and read frames) · `file_uploads` (uploads arriving in pieces) ·
  `index_deletions` · `artifacts` · `artifact_versions` · `skills` · `memories` · `agents` ·
  `connectors` (a person's own server, shared in the store, or a reference to one through `origin_id`) · `connector_tools` · `connector_credentials`
- `design_systems` — themes: the look a project's or agent's output wears.
  `projects.design_system_id` is nullable and `ON DELETE SET NULL`: removing a theme
  costs the projects their look, not the projects
- `user_templates` — templates people make in the editor (§6 Templates)
- `user_pipelines` · `pipeline_runs` — composed step lists and bundles, and the
  recorded runs of every writing pass (§6 Pipelines)
- `publications` · `publication_versions` · `publication_visitors` ·
  `publication_counters` · `publication_slots` · `publication_questions` ·
  `publication_keys` — published agents (§6 Publishing agents)
- `source_selections` · `source_guides` · `project_notes` · `studio_items` — a
  project's knowledge shelf
- `push_subscriptions` · `push_sent` — web push
- `issue_reports` · `issue_replies` — Feedback & issues reports and the administrators' replies
- `metric_samples` — the performance monitor's series
- `templates` — present in the schema but not read or written by the application
- `sessions.render_template_id` — the template a session writes into.
  A plain string, not a foreign key: the catalogue ships inside the API image,
  and an id it does not know degrades to "no template"
- `projects.render_templates` — surface → template id, the format work started
  in this project begins in. A JSONB map rather than a column per surface,
  because the reader holds a session kind and only two surfaces have a
  rendering track. Seeded onto the session at creation, never re-read: the
  composer's own pick is a decision about that one conversation
- `shares` · `share_views` — read-only links, and who opened each one. The
  token is the permission and revocation is a flag rather than a delete. A
  visit names the reader when they were signed in and records their address
  and browser when they were not; repeat opens inside an hour are one visit
- `messages.artifact_ids` — what a turn produced, for the turns whose answer is
  a thing rather than a sentence. The image and audio/video surfaces store the
  prompt as a user message and an assistant message with an empty body and
  these ids; the transcript renders the picture or the player where the answer
  goes. A chat turn that writes a file records it here too
- `messages.superseded` — answers a rerun of the same question replaced, oldest
  first (`{id, content, model, usage, steps, artifactIds, routing, rating,
  createdAt}`); the conversation continues from the row that carries them
- `sessions.summary` — `{through, text, turns, pinned}`: the running memory standing
  in for the turns too old to send once a conversation outgrows the model's window
  (§7) — a summary, the pinned facts and rules (`[{label, value}]`, oldest first), and
  the last message it covers, which is also where the kept part of the conversation
  starts
- `sessions.skill_ids` — the skills switched on for the conversation (§6 Chat)
- `messages.failure` — how a turn ended when it did not end in an answer.
  `no_answer` sits on the question, `interrupted` on a partial reply, and
  `stopped` on either when **Stop** was pressed
- `jobs` — video only. Without `provider_job_id`, a restart orphans a
  half-generated clip
- `system_settings` — LiteLLM address and master key, backend and tool
  addresses, model roles, enabled surfaces, OCR, branding, SMTP, signup policy.
  Database values override environment variables
- `governance` — the one instance-wide policy row: privacy guard and masking,
  internal-material routing, Super Agent, outline model, intent filter,
  idle sign-out, retention

`files` carries the scope it belongs to: `project_id` for project knowledge,
`session_id` for a one-off attachment, `agent_id` for an agent's searchable
shelf. `source_url` marks text read from a page rather than uploaded, and
`indexed_at` whether the vector index covers it.

A conversation and its artifacts are one record: deleting a conversation,
singly or in bulk, deletes the artifacts it produced with their versions and
shares (`sessions._delete_artifacts_of`).

**Where an artifact is listed** (`artifacts.section`, `services/artifact_section.py`, kept by
the model's insert and update listeners):

- The 아티팩트 page (`GET /artifacts/gallery`: `section`, a title search `q`, `sort`
  `updated`/`title`/`section`/`version` with `dir`, a keyset `cursor`, per-section `counts`)
  holds what is viewed or run directly: `html` pages, `code` files, `chart`s (a chart artifact,
  or a code-drawn chart picture) and `diagram`s (a drawn figure with its source).
- Reports and decks of every format are `document`s, listed in 파일함 › 작업 문서.
- Generated pictures, sound and video are `media`, listed in 파일함 › 만든 파일 by their files.
- `snippet` artifacts (a code or HTML body without the `kind` key that every `create_artifact`
  file and designed page has) stay visible in their conversation's panel and are listed
  nowhere else — not in `GET /artifacts`, its counts, the gallery or search.
- `/artifacts?open={id}` on a document opens its editor (or 작업 문서), on a picture, sound or
  video its file in 만든 파일.

An answer's inline code block is part of the answer: only a file the model makes with
`create_artifact` becomes a code artifact. The exception is a program or page the person asked
to be made that the model wrote without calling the tool; it is saved for the model after the
turn (`services/code_file_save.py`) when the turn made no artifact of its own, the request asks
to make something and carries no code of the person's own, and the text is file-shaped (a full
HTML document, or code with an entry point, tests or several top-level definitions, at least 10
lines, or 12 when the request itself says code, script, program, file, class or app). The source
is the answer's largest such block, else the largest such program a successful `execute_code`
run used this turn. A snippet shown to explain something stays in the chat, and the answer text
is never rewritten.

---

## 6. Execution paths

### Chat

`POST /sessions/{id}/messages` → SSE, emitting `delta`, `step`, `artifact`,
`usage`, `title` and `done`. One answer at a time: a second message while this
process is still writing one (and nobody pressed **Stop**) is refused with 409
`session_busy`; with several replicas the shared transcript stands in for the
other processes' signals (a fresh question with no reply and no failure mark).
Two sends that both pass that check meet at the turn's registration, where a fresh send
(not a regenerate, not a message after **Stop**) that finds a live turn bows out with
`session_busy` and removes its own question row.

`retry_of` on the latest question reruns it; when that question already had an
answer, the old answer is kept on the new row as `messages.superseded` (the
screen pages ‹ k/n › through them) and its row is deleted only in the
transaction that stores the new answer, so a rerun that dies leaves the answer
it was going to replace.

**A present-state question with no retrieved evidence is answered, not
withheld.** When the words ask for something that changes with time (an
officeholder, a price, a version) and no read tool returned material this turn,
the model answers from what it knows under `current_evidence.instruction` —
dated, never phrased as what holds today — and the answer ends with a caveat
(`current_evidence.caveat`) unless it already hedges itself. Earlier answers in
the conversation stay in the envelope; the instruction says they are not
evidence. A question the person's own files answer (a price in an attached
list) is read from the file and is not treated as a current-fact lookup unless
the words themselves name the present (Korean cues such as 「지금」 "now",
「현재」 "currently", 「올해」 "this year").

**Skills stay on for the conversation.** The skills picked for a turn are
remembered on `sessions.skill_ids` and applied to every later turn until the
person changes the set (the picker, or `PATCH /sessions/{id}` with `skillIds`);
`[]` switches them off. A standing skill that has since been deleted, disabled
or barred by the agent drops out quietly and the stored set is pruned
(`_standing_skill_ids`); only a skill picked for this turn is checked strictly.

**The model can reach for a skill itself.** The installed, enabled chat skills
that are not switched on are offered as one tool, `use_skill`, whose description
lists each by its `when_to_use`. When a request matches one, the model calls the
tool once and receives the skill's instructions as the tool result — the one
tool result the rules say to follow, since the person installed it — and
answers under them. The step shows as 「스킬 적용」 ("applying a skill"). Up to
twelve skills are offered per turn, and an agent's skill allowlist bounds the
shelf as it bounds a hand pick.

Tools are attached only when the model supports function calling — giving them
to a model that does not yields either a 400 from upstream or an invented call.
Built-in tools (`services/tools/`): `web_search` (SearXNG), `fetch_url`
(Crawl4AI), `weather` (Nominatim geocoding plus the Open-Meteo forecast, keyless),
`execute_code` (sandboxed; files it names are mounted), `run_skill_script`,
`run_code`, `calculate` (bounded exact arithmetic, no code execution;
`tools/arithmetic.py`), `create_artifact`, `create_chart`, `share_note`,
`use_skill`, and `search_knowledge` over an agent's own documents and the files
uploaded into the conversation (§8). Tools from installed MCP connectors are
added to these.

**Script skills.** A skill may ship as a package, `app/skills/<key>/SKILL.md` (YAML
front matter for the catalogue fields, markdown body for the instructions) plus fixed
`scripts/*.py`; `services/skill_packages.py` loads them into the catalogue and finds a
row's scripts by its `catalog_key`. Two sandbox tools work on the conversation's files
(`tools/sandbox_tools.py`, `services/sandbox.py`): `run_skill_script` mounts the package
read-only beside the files and runs one script with JSON arguments (through bash, so
LibreOffice can recalculate formulas), and `run_code` hands a Korean task plus a
code-computed schema of each file (`services/file_schema.py`) to the code model
(`CODE_MODEL`, default `local/qwen3.8-27b`), runs the result, sends the error back once,
then gives `CODE_FALLBACK_MODEL` one try. What a run writes under `outputs/` is stored
as the user's files in the chat and listed on the step as `outputs` (download chips).
One sandbox session serves a turn, kept on `ToolContext.sandbox`. The sandbox sits in
the self-hosted stack with networking off, so with `SANDBOX_IN_BOUNDARY` (on) both tools
are offered to strict-local turns, and `run_code` then writes on the `strict-local/`
twin of the code model.

**A person's skill opts in the same way.** What the server runs for a skill —
`stage`/`entry`, `post_check`, `post_judge`, `copy_check`/`copy_kinds`, `tables`,
`own_stage`, `uses` — is stored on its row (`skills.server_fields`) and read through
`services/skill_specs.py` for shipped and personal skills alike; each field may name
shipped scripts only (allowlists in that module), set in the skill form or an uploaded
SKILL.md. A person's own Python scripts (`skills.script_files`, kept from `scripts/*.py` in
an upload and scanned) run only through `run_skill_script` in the sandbox, mounted
read-only under `skills/own-<id>/`; the server never imports them. Sharing a skill with
scripts waits for an administrator (`/admin/skills`, **Skill review**). A person's check skill
(`stage`) is attached through the **My check skills** bundle as `skill:<id>`.

**Missing-information questions** are data (`services/gap_questions.py`): a template lists the
questions its format needs (`[[questions]]` in `template.toml`), an agent adds its own
(`agents.gap_questions`, the agent form; a shipped agent's come from its file). Each names
a detector — a built-in one from `gap-check/scripts/gaps.py`, keywords, a bounded pattern,
or the judge model, which must quote the answer from the material. Detectors read the
request, the material and the instructions set once (account, agent, project:
`WorkspaceContext.standing`), so an agent whose instructions say 「시설과장님께 올리는
주간보고」 ("weekly report to the facilities manager") is not asked whom it reports to; the
report writer also takes the head fields (「작성자는 「…」로 적는다」, "the author is written as …")
and a section order (「금주 실적, 이슈, 차주 계획 순서」, `report.instructed_sections`) from
those instructions. A template's document format (`doc_format`) and genre rule (`genre.md`)
are carried by the template too.

**The web-search toggle has three positions.** `context.search_plan` turns the
toggle and the user's words into two facts: whether the web tools (`web_search`,
`weather`) are offered this turn, and which tool the first hop must call.

- **On** offers them and forces a search every turn.
- **Off** offers nothing unless the words explicitly ask for research.
- **Auto**, the default, offers them and forces the first hop only when the words ask for
  research, for something that changes with time (news, prices, versions, schedules, a
  year), or for the weather; otherwise the model decides under a lighter rule
  (`_WEB_SEARCH_AUTO`).

On a chat turn the route also forces the first search, for every model, when
`services/exact_facts.search_reason` finds a fact that must be exact (a phone number or
hotline, a law, tax rule, threshold or benefit, a price, rate or limit, an event date,
availability, an organization, a statistic) or legal, tax, financial or medical advice; a
short follow-up is read with the earlier user turns, and the model then writes the query
from the conversation. Writing, translation, code and formulas, role-play, drafting,
concept explanations and turns on the person's own material are left alone
(`exact_facts.not_a_lookup`), and an explicit opt-out or Off stands. On Auto, a draft,
edit, translation or code fix made from the person's own facts (`exact_facts.own_work`) is
not offered `web_search` at all, so the model cannot wander into unrelated pages; On still
searches. A model-written query keeps the request's named things
(`query_terms.keep_entities`). Weather questions force the `weather` tool rather than a
search, since a search engine returns encyclopaedia pages for 「분당 날씨」 ("Bundang
weather").

**Advice answers are traced by default.** `exact_facts.advice_domain` puts the
`grounding-check` skill on a legal, tax, financial or medical chat turn, as an agent that
turned it on would, and the writer gets `freshness.ADVICE_INSTRUCTION`. With
`"advice": true` the trace checks money and rates, phone numbers, doses and legal
consequences against the turn's search results and the person's words (not earlier
answers), marks each unsupported one 「(확인 필요)」 ("needs checking") where it stands rather
than blanking it, and leaves agency names and the guidance's durations alone; more than
three marks are listed once under the answer. The step reads 「근거 확인(법률): …」. A
strict-local route offers none of this, whatever the toggle says.

**A search runs two lanes and reads dates.** `web_search` asks SearXNG's general lane
and, when the query carries a time-sensitive word (a year, 최신 "latest", 가격 "price", a
named release), the news lane too, with the news hits first. Front pages and a host's third
hit are benched (used only to pad a thin list), and a hit's `publishedDate` reaches the
model as 「게시일」 ("published on") so the newest wins; a listed source with no 게시일
carries the day it was read (「· 확인 2026-10-15」, "checked …").

A turn makes at most `MAX_WEB_SEARCHES` (3) searches that found something, `MAX_SEARCH_CALLS`
(6) calls in all, and `MAX_FETCHES` (6) page reads (`services/agent.py`); past a cap the model is
told to answer from what it has. A turned-on skill that sets `process: {deep_search: true}`
(a person's own research skill, say) doubles the search caps and adds one search for each thing the
request lists or hands over to check. On such a turn an answer written before every
listed name (「잔디, 플로우, 네이버웍스를」) appears in some query, or before every memo
line handed over has two of its words in one query, is held once and the model is asked
to search the rest (`query_terms.unsearched`). The skills' last check (`source-check/post`)
also takes out a citation of a page about another company: a company directory's profile of
another firm, or, in a comparison table, sources that name another column's company or
another product of the same name.

**Sources are cited by number, never typed.** `run_turn` renumbers the `[n]`
entries of every search result so the numbers run across the whole turn and
gives a fetched page a number of its own; the prompt asks the model to cite
`[3]` at the end of a sentence and not to copy URLs. After the answer,
`_link_citations` turns each `[n]` into a link to that source and a numbered
「출처」 ("Sources") list is appended; numbers no result carries stay as text. A model
that types a URL anyway is checked against the tool results. A stream that repeats
one letter or digit forty times in a row (a decoder stuck on a long article id) is
closed there: the run is retracted, and a URL it broke is completed when exactly one
tool result starts the same way, dropped otherwise.

**A fetch reaches the public internet only.** Every page read on a reader's
behalf — the `fetch_url` tool, search-result bodies, research picks, project
and agent knowledge from a URL, a design read from a page — passes
`services/netguard.py` first: http(s) only, and every address the host
resolves to must be public (no loopback, private, link-local, CGNAT or
metadata addresses; no dotless service names). The Crawl4AI shim applies the
same rule again, including to the address it lands on after redirects, since
the tool gateway reaches it without authentication.

**Tool count is what sizes the MCP servers offered by default.** Every active tool ships
its full schema on every turn, and model tool-choice degrades well before
twenty of them. A server is shared or provided by default once it has been started against
a real credential and had its tool count checked.

Storing the turn's `create_artifact` files and automatic memory run **after** the turn, in their own
transaction: sharing the turn's would hold it open for an extra query and a
model call, and a failure in either would roll the answer back.

**The turn outlives the connection.** The work runs in its own task and the SSE
response only relays what it emits, so closing the tab cancels the relay and
not the turn — the answer is still stored, charged and titled. Stopping is therefore a
separate act rather than a dropped socket: `POST /sessions/{id}/stop` sets a flag the
token loop checks, and the turn settles where it is with the partial answer kept and the
row marked `stopped` (a dropped connection marks it `interrupted`). The proxy reports
usage on its final chunk, which a stopped stream never reaches, so a stopped turn's usage
is estimated from what went up and what came down and stored with `estimated: true`.

### Report and slides

Both are two-pass: one outline call returns the title and the table of
contents, then one call per section or slide, each carrying what the previous
ones wrote. Six listed sections means six are coming, which is what makes the
progress indicator honest. A failed section is marked and the rest continues.
Both receive the workspace blocks the chat surface gets — project
instructions, memories, skills, and any file attached to the turn — so a
request naming an uploaded form is written against that form.

**The plan is approved before anything is written.** The outline turn ends
with a `proposal` event: the plan (title, parts, visual style, proposed
pictures) is stored on the session and shown as a card, and writing starts
when the person approves it, possibly edited. When the material is too thin to
write from, the turn asks instead (`needs`, `services/grounding.py`) and writes
nothing.

**The plan is checked first.** `services/outline.py` holds the rule both tracks use — no
run of three identical layouts, and at least three of the offered layouts (or as many as
exist). A plan that fails it is asked for once more, naming the layouts it skipped; the
second answer is kept only if it is less flat. Nothing is coerced: a layout assigned to a
heading it does not suit would be a worse slide than a repeated one. `deck.sane_outline`
also refuses an outline that is really the planner's notes to itself (「Slides: 5 to 12.
(6 is fine)」, repeated section names, an English plan for a Korean request), and the
outline is asked for once more.

**Planning and judging follow the chosen model.** The model that writes a document also plans it (one call
per document however many blocks follow) and judges it; with Super Agent, its class table picks the
model for each. A writer that is not contained (an external model) gets a self-hosted judge only where
`_local_planner` names one, and a judge may never be less contained than the writer.

Reports search the web before writing and cite what they found; with no search
backend the shelf is empty and the citation rule drops out of the prompts. A
request about Korean scholarship also looks for KCI, DBpia and RISS papers
through the same search backend (`services/korean_papers.py`; there is no
separate KCI API or key) and reads each landing page's bibliographic meta tags,
so a reference carries its authors, year, venue and DOI.

**Slide layouts** are listed in `services/deck.py` (`title`, `agenda`, `section`,
`bullets`, `quote`, `two-column`, `table`, `metrics`, `chart`, …), each implemented in
all three renderers (preview, `.pptx`, `.pdf`). A table body row whose value columns (two
or more) all hold the same text of at least `deck_type.SHARED_MIN` characters is drawn as
one cell across them (`deck_type.shared_value` / `typeScale.sharedValue`; a merged cell in
`.pptx`); the data keeps every cell. The outline also picks the deck's accent from a fixed
palette — unless the project wears a theme, in which case the palette rule is dropped from
the prompt and the accent arrives from the project. A slide count stated in the request is
honoured up to 50 (「N장」, "a 6-slide deck"); a larger number counts only when said of the
deck itself (「슬라이드 60장」, 「총 60장」), so a quantity in the material (「컬러 월 300장」) is
never a size. A stated range is a floor and a ceiling: an outline outside it is asked
back once, and one still over the top loses its dividers and agenda first
(`deck.fit_count`), never content slides; a plan still over or under goes to the honest error.

### Revising a finished report or deck

A revision is planned by a model, then carried out by the narrowest edit that does what
was asked. The rules below are code in `services/revise.py` and `services/deck.py`, so a
model's retyping cannot move what the person did not name.

**Which part is the target.**

- When the words name exactly one existing part (「다음 단계 장을 연표로」, "next-steps slide as
  a timeline"), that part is the target whatever the planner guessed (`revise.named_target`).
  A shorter name inside a longer part's name counts as a mention of the longer one; a name
  whose every word sits in one part's name is that part; a part the deck truly lacks is made
  before the closing slide. Structural asks (add, remove, merge, reorder, a new count) are left
  to the planner.
- A stated count against a different current count (「전체를 6장으로 줄여 줘」) is a restructure
  whatever the planner read it as (`revise.count_change`). The original request's counts are
  stripped (`revise.without_counts`) and the planner's note loses totals it states
  (`revise.note_without_totals`) so the note's count is the only one.
- When every ask of a report edit names its part (by number, name, or wording only some parts
  state), only those parts are rewritten, each told its own asks (`revise.section_asks`).
- 「위험 장을 하나 추가해 줘」 ("add a risk slide") is an `insert`: a blank part is slotted in and
  only it is written (`place_insert` for 「요청 사항 장 앞에」 "before the requests slide";
  `into_existing_part` when the ask is aimed into an existing part). Other parts stay byte for byte.
- A question or numbered edit typed while a question card is open is not the card's answer
  (`sessions._sets_card_aside`).

**Done by code, not by the model.**

- A term swap (「문서 전체에서 「A」를 「B」로 통일해 줘」) is an exact string replacement across
  every text field (`revise.term_swap`, `replace_term`).
- An ask for another shape and nothing else (「4장을 번호 목록으로 바꿔줘」, "make slide 3 a
  table", `deck.form_only`) is done from the slide's own content (`deck.reshaped`), so no item
  is rewritten or invented; a shape that does not come leaves the slide as it was and says so.
  Other layout asks (`deck.requested_layout`: 연표 timeline, 단계 steps, 차트 chart, 큰 숫자
  big numbers, 표 table) are written with that layout's prompt.
- Joining or splitting one slide's items (「2장 항목을 하나로 합쳐줘」, 「6장 단계를 두 개로
  나눠줘」) is that slide's edit (`deck.joined_items`), never a new deck; a slide with nothing
  to join keeps its version and the reply says so.
- A requested summary is written as the first part's 「핵심」 callout (`revise.summary_block`);
  a quoted rename sets the heading as well as the words (`revise.requested_title`); a speaker-
  notes ask for every slide is a whole-deck pass (`revise.notes_everywhere`) and a notes-only
  rewrite changes nothing but notes (`revise.notes_only`).
- A product the model wrote (「470만 원 × 36개월 = 169,200만 원」) is recomputed and a wrong
  result replaced wherever repeated (`report.fix_products`; plain products with the same scale
  on both sides only).
- A section that came back as a one-key JSON object is unwrapped (`report.unwrap_json_prose`);
  one whose table is a frame of 「(미정)」 ("TBD") cells while the material carries numbers is
  asked again with the data (`placeholder_heavy`, `_FILL_PROMPT`); a cell the data cannot
  fill is 「—」, never an invented value.

**A restructure keeps what it did not change.** When the skeleton is re-planned (「6장으로
줄여」), the source artifact's id rides in the pending proposal (`carryFrom`) and, after the
rewrite, every new part whose name survived and which the instruction never named gets its
words back from the old part (`revise.carry_parts`): same position, new id and accent, old
content. "Named" is judged against the restructure instruction (`carryNote`), not the merged
request. A restructure asks no questions and shows no outline card (the open document is the
material). An outline still over the count after its size retry is fitted by code: dividers
and agenda go, then adjacent content slides merge into one 「A · B」 slide
(`deck.merge_to_count`). A table slide that repeats an earlier slide's rows is written as
points (`deck.repeats_table`).

**A shortened part is checked against what it had.** A section cut to a sentence count
(「2절을 두 문장으로 줄여줘」) is checked for that count and for the section's figures and
names (`revise.shortening_faults`), sent back to the writer once with the reason, and
what still misses is said in the reply. Related rules:

- The writer sees the section's own current text; a line to the person about the rewrite is
  dropped (`revise.without_preamble`); a result that reads as another section or points at a
  table or figure it lacks is sent back once, then kept as it was (`revise.drift_faults`).
- Only prose sentences are counted (not tables, captions or list rows), and a sentence
  restating the ask is dropped first (`revise.without_ask_echo`). A count still off after the
  retry is met by code at clause joints (`revise.fit_count`); a retry that misses a count the
  first version met does not replace it (`revise.better_shortening`).
- Figures are compared as numbers with their unit or counter (`deck.figures_in`: 「3분기」 not
  「3분」; money as won via `deck.amounts_in`, 「38.5억」 = 「3,850,000,000원」; a number written
  another way still counts as shown, `deck.figure_kept`). A figure or date neither the section
  nor the material states is a fault (`revise.added_facts`, `revise.without_added_dates`).
- Facts a cut dropped are named and sent back once: figures, names, days named without a date,
  things said to be done (`revise.lost_facts`), and the caveat on a figure or name
  (「확정 아님」, 「추정」, 「(확인 필요)」, `revise._with_caveats`), which is restored where the
  figure survives without it. After a cut to fewer slides, the figures, charts and tables shown
  nowhere any more are said (`deck.lost_across`, `deck.lost_blocks`), the deck's ask
  (승인, 결재, approval) is kept (`revise.keep_ask`), summary order is kept
  (`revise.keep_order`), marks on figures carry over (`revise.carry_marks`), and inline pictures
  and a mermaid figure the writer was not shown are put back (`revise.keep_pictures`,
  `revise.keep_figure`, unless the person asked for it to go).
- A figure the request states that the deck left out goes beside the metric or item it belongs
  to, and one given in parentheses is restated with the term it qualifies
  (`deck.restate_missing_facts`).

**A figure plan on request.** When the request itself asked for a structure or flow diagram
(구조도, 흐름도) and the figure planner proposed nothing, it is asked once more
(`diagrams.asks_for_diagrams`); an empty plan on a request that never mentioned diagrams
stands.
### Editing a web report: one Markdown document

The web report is Markdown only. Precise formatting (fonts, colours, sizes, line
spacing, merged cells) belongs to reports written as HWP or Word files
(`services/report_office.py`, edited in those editors). The web report is one
Markdown document that both the person and the agent's chat edit, so an edit by
one never loses formatting the other cannot see.

Storage is `sections[]`, every one `format: "markdown"`. The editor
(`components/report/DocumentEditor.tsx`, schema `reportSchema.ts`) is one
Tiptap/ProseMirror document over the whole report: each section is a `##` heading
(a level-2 section a `###`) that carries the section id as a node attribute, then
its body, whose own headings sit one level below (`lib/reportDoc.ts` shifts them on
the way in and out). The schema holds only what Markdown can write back: headings,
bold, italic, strikethrough, inline code, links, bullet/numbered/task lists, quotes,
GFM tables (a header row, no merges), pictures, code blocks, the drawn fences
(mermaid, chart, kpi, steps, cards, callout; shown drawn, edited as text), rules,
page breaks (`<!-- pagebreak -->`), and `[n]` citations and `[빈칸]` blanks as plain
text. Typing a new `##` heading starts a section; selecting, deleting and merging
across a section boundary works because it is one document. The edit view does not
guess where pages break: its only page marks are the person's own breaks, drawn as
a labelled 「쪽 나누기」 line. Exact pages belong to the page view (Paged.js) and the
exported files.

On save the document is serialised (`blocksToMarkdown`, minimal escaping so `[1]`
and `[담당자]` stay as typed) and split at its section headings (`docToSections`).
A section keeps its id, and with it its diagrams and other fields, wherever it
moves; a heading split or pasted twice gets a new id (`SectionIds` keeps them
unique); a deleted heading's body joins the section above. A section whose text the
editor did not change keeps its stored Markdown byte for byte; one whose text
changed loses its fact-check (its verdicts point at sentences that are gone; 검토
runs it again), and the server drops it too when a PATCH changes a section's text.
「Markdown 원문」 shows the same document as Markdown text (`# 제목` first); reading it
back matches sections by heading, then by place. One undo history covers the whole
document; find and replace runs in document order and a replace-all is one undo step.

The server accepts Markdown sections only: `PATCH`, create and restore convert an
HTML section (an imported `.hwpx`, a template's block) through
`design_templates.sanitise` and `richtext.to_markdown` (`markdown_section`),
dropping its styling and the rows a merge covered. The exporters read Markdown
(bold, italic and strikethrough as runs; quotes, rules and task boxes as text).

The panel uses the shared document-panel chrome (`components/document/DocumentChrome.tsx`,
also the office panels'): title · vN · 「저장 안 됨」 · 저장 (Ctrl+S) · 내려받기 · 공유 ·
더보기 · expand · close. Hand edits stay in the browser until 저장, which is
conditional on the version the editor started from (a lost race offers 「내 편집
내려받기」 / 「최신 판 불러오기」). The report registers with `lib/officeGuard.ts`, so a
chat turn, closing, another artifact or a reload asks 저장 / 버리기 / 취소 first. The
editor is locked while the agent works and reloads on its version (a version that
arrives under unsaved edits waits behind a notice). A phone reads only. The ribbon is
the Word editor's: 홈 · 삽입 · 레이아웃 · 검토 · 보기 (`ArtifactRibbon look="word"`).

### Templates

A template is a document or deck structure the model writes into; a theme (below) is
its look. New conversations, agents and projects take no template: structure comes
from the request (§ Structural patterns) and the look from a form file or design
brief (§ Form files, § Design briefs). Templates remain for artifacts and conversations that
already hold one, which keep rendering and exporting in it. A person's own templates
can be read, deleted and turned into agents (`POST /user-templates/{id}/to-agent`);
creating or changing one answers 410 (`templates_retired`).

Shipped templates live in the API image under `app/design_templates/<id>/`: a
`template.toml`, a `seed.html` and a `sample.html`, plus the `instructions.md` and
`checklist.md` a writing turn reads, and for most a `design.css`. The catalogue is a
short list of `deck-*` and `doc-*` entries, written for this product's constraints:
no script and no parent-relative assets, because artifacts render in a `sandbox=""`
iframe. Templates apply to the report and slides surfaces only. A person's own
template (`user_templates`, named `ut-<id>` in sessions and agents) holds the same
fields as a shipped one and is drawn in the shared document seed (`_document`) with
the theme's tokens, with no HTML or CSS of its own.

A session with `render_template_id` set produces an `html` artifact through
`services/page.py` instead of a JSON deck or Markdown sections. The choice is stored
on the session, so a follow-up turn keeps the shape. The pass structure matches the
other tracks: one outline call, then one call per block. An outline that will not
parse is salvaged with a regex before the turn is abandoned; what cannot be salvaged
is logged with what the model said.

**The model never writes layout.** It is given the block's layout name and returns
the inside of that block; `design_templates.assemble` puts it inside the markup the
seed styles, and `sanitise` first reduces it to a fixed tag vocabulary. It parses the
block as a browser does (nh3, an HTML5 parser) and writes it back from the tree, so
only listed tags and, per tag, listed attributes survive. Script elements, event
handlers, remote `src`/`href` and `h1`/`h2` are removed (the first three because the
file is downloaded and opened outside the sandbox, the last because the wrapper
already wrote that heading).

**Figures a document draws for itself.** After the draft, one planning call
(`services/diagrams.py`) names up to three parts where a structure, flow, comparison
or concept figure says more than the words; a report writer can also place one with
a `[[그림: …]]` mark. `services/diagram.py` writes each as a mermaid flowchart in the
house style and `diagram.draw_native` draws it: `flow_layout` lays the flowchart out,
`diagram_shapes` draws native python-pptx shapes on a one-slide deck
(`diagram_render`), and the print sidecar renders that slide to PNG through
LibreOffice (`POST /pptx/png`). A figure with a fixed shape skips the flowchart
layout: a short first call picks the shape, and for a line of steps, a cycle, a house
(goal, pillars, base) or a two-sided comparison a second call fills that kind's slots
as JSON (`figure_kinds`), drawn to a set layout with labels measured in Pretendard
(`figure_text`). Any other shape (messages between parties, three or more options,
two axes, a structure) is written as mermaid, and slots that do not make their kind
fall back to it. The stored source stays a flowchart of the same figure, with the
slots on a last `%% kloudchat-figure:` comment line, so the browser view, the fact
check and editing read a flowchart while the renderer and deck export draw the slots.
The slide goes through `.odp` with Asian–Latin autospacing off, and
`apps/print/fonts.conf` maps the faces an export names (맑은 고딕, 바탕, Segoe UI,
Georgia) to installed ones (Pretendard, 나눔명조, Selawik, Gelasio). The panel, the
PDF and the report show that picture, and the deck's `.pptx` carries the same shapes,
editable.

A source the shapes cannot read (not a flowchart, more than
`diagram_shapes.MAX_NODES` nodes) goes back to the writer once, told why; still
unreadable, or with no sidecar answering, the figure is left out (a report drops the
mark and its 〔그림〕 reference, a deck slide keeps its words). Documents get no
browser-drawn figure in its place; only chat answers draw mermaid in the browser, and
a document that already carries a mermaid fence still opens and exports. The same
path serves a figure made by hand: the picture picker in a report or deck and the
image surface's **Structure diagram** · **Flow diagram** · **Concept diagram** chips
call `POST /sessions/{id}/diagrams`, which stores the PNG as an image artifact with
its source, or answers 422 (`diagram_not_drawable` / `diagram_unrendered`). On a
slide, such a figure keeps its source as `slide.diagram` and is redrawn in the
slide's accent; a picture from the image model placed there replaces it. The only
cost is the writer's tokens.

Image-model pictures are proposed on the outline card only for illustration or real
objects, never for structure or numbers, and a deck's structure slide is moved off
layouts the figure planner cannot draw on (`deck.structure_as_drawable`). When an
image model inside the data boundary is open to the account (`dataBoundary:
self_hosted`, such as `strict-local/z-image-turbo`), the outline card proposes no
pictures. Instead, when the request asks for pictures (「삽화를 곁들여」, 「사진도 넣어」;
`illustrations.wanted`), the document plans up to two on its written parts after its
diagrams (`services/illustrations.py`; none is a fine answer) and draws them with
that model: a deck on bullet slides with no figure, a report appended to the part. A
request that does not ask, or declines (「그림 없이」), gets none, and
`illustrations.draw` refuses any model outside the boundary. Statement and quote
slides never carry a picture in the exports.

**Pictures.** The writing model cannot make or reference one: `sanitise` drops every
`src` not already inside the file. A Markdown section follows the same rule: a `data:`
picture the writer typed that does not open as an image is dropped with its 「(그림 N)」
mention (`pictures.drop_broken`), and the web view shows a cut-off one as its caption.
A person can put a picture made on the image surface into a block:
`POST /artifacts/{id}/blocks/image` inlines the stored blob as a `data:` URI and
re-renders the document. Raster only (SVG can carry script), at most 3 MB before
encoding, snapshotted like a rewrite, so the artifact stays one file that prints,
downloads and shares with the picture in it. The JSON deck track has the same path
(`POST /artifacts/{id}/slides/image`), storing the `data:` URI in the slide row.
`services/pictures.py` owns the one rule underneath: raster only, embedded only,
never fetched.

**Exports.** `page_export` reads the `<figure>` back out of the markup (bytes and
caption) and hands it to each renderer. A slide takes the right half with the words
narrowed to fit, or the middle when nothing else is on the slide; a document places
it after the section's prose, 120 mm wide at most and never taller than the page,
with its caption on the same page. Sizes come from the bytes (Pillow). A picture that
cannot be read (truncated bytes, an unsupported format) costs only its own
illustration: every renderer logs it and finishes the file.

In `.hwpx`, a picture is three things that must agree: the bytes in
`BinData/imageN.png` (stored, not deflated), an `<opf:item id="imageN" …
isEmbeded="1"/>` in `Contents/content.hpf` (OWPML spells it with one `d`; Hancom
drops the picture without it), and `<hc:img binaryItemIDRef="imageN">` inside an
`<hp:pic>` in the section. Nothing is declared in `header.xml`. Every document also
carries an `<hp:secPr>` with A4 and its margins in the first paragraph's run:
without page geometry, an object sized in absolute units is never drawn. The
structure follows genuine Hancom output (hwpxlib's test corpus).

**Typesetting.** A template's look is two files. `seed.html` (shared, named by
`seed_from`) is correctness: the tokens, the whole closed vocabulary `assemble` can
emit, the `@page` rule, the print rules, the shadow-root `:host`. `design.css` is
identity (the minutes template `doc-minutes` packs a compact grey head, the
high-contrast deck `deck-signal` presents white on black) and is spliced in as a
second `<style>` last in `<head>`, overriding by order. `stylesheet()` concatenates
every style block, which carries it into the document editor's shadow root.
`_document/seed.html` is the plain document's own design and `_deck/seed.html` is
editorial's. `test_a_template_has_a_face.py` checks that every `var(--x)` a seed uses
is declared, since an undeclared custom property fails silently.

Stored documents and conversations may name an id the catalogue no longer offers.
`design_templates.RETIRED` lists document ids drawn in `_document` alone with its
`template.docx` (`design_templates.rendering`), so they keep opening, taking pictures
and exporting; `RETIRED_KEPT` lists ids drawn in their own design but not choosable;
`RETIRED_MEDIA` lists image, audio and video ids, read as naming no template. The
page view of a report with no template asks for the shared seed by the id `_document`.

Two constraints shape every seed:

- **No script.** Artifacts render in a `sandbox=""` iframe, so a deck navigates by
  CSS scroll-snap and charts are inline SVG.
- **Print is the export.** Every seed carries `@media print` and `@page` rules that
  put one slide or section on one page. `apps/print` is a headless Chromium in its
  own container that takes the finished HTML and returns a PDF (`services/printing.py`),
  so the `.pdf` is the `.html` printed. With no printer configured (`PRINT_BASE_URL`,
  `settings.print_base_url`, empty), the structural renderer below still produces a
  file. The container has no database, no secrets, no published port and a compose
  network with `internal: true`, and inside the browser every request is aborted, so
  an `<img src="http://…">` in model-authored markup is never fetched: one wall is a
  line of code, the other a fact about the network.

The `.html` file is the faithful copy and is deliberately not opened in a tab from
the app, because a `blob:` URL inherits this origin.

The other formats come from `services/page_export.py`, which reads the markup back
with the standard library's `HTMLParser`. That works because `assemble` wrote it from
a closed vocabulary: each `<section>` becomes a slide or a section, keeping its
heading, lines, which column each line was in, and table rows. A deck goes through
`deck_export` to `.pptx` and `.pdf`; a document through `report_export` to `.docx`,
`.pdf` and `.hwpx`. The conversion buys editability, not fidelity: the `.pdf` carries
the design because a browser draws it, and the `.docx`/`.pptx` carry structure
because Word and PowerPoint have no CSS to draw into. The deck opens in PowerPoint as
real slides in order, with the theme's accent and face, laid out by the product's own
deck renderer rather than the template's stylesheet. `deck_export` has a `table`
layout and an explicit `columns` field; a JSON deck, which has neither, halves its
own list. A template marked `dark` presents on a dark ground and its `.pptx` swaps
the two neutrals to stay readable; its `.pdf` stays light.

`POST /artifacts/{id}/blocks/rewrite` rewrites one block: the artifact keeps its
blocks whole, markup included, with `content` as what they render to, and the rewrite
assembles the document again from the same seed instead of splicing markup into a
finished file. It is charged and snapshotted like a section rewrite. The block is
chosen from a list, since the preview frame is opaque to the app, and an artifact
whose blocks carry no markup is refused.

`GET /design-templates` lists the catalogue with Korean and English names,
descriptions and example prompts. `GET /design-templates/{id}/preview` renders the
seed around its own sample and is **unauthenticated**, like the branding logo: the
body is a constant shipped in the image and an iframe `src` cannot carry an
Authorization header. It takes the four tokens as query parameters (`accent`, `ink`,
`muted`, `font`), so a picker inside a project shows that project's theme and the
theme editor shows its current form. The values go through `design.normalise_tokens`,
the call the renderers make, so a bad one falls back per field and only a validated
colour and one of two known faces reaches the document.

### Checking what was written

Three surfaces carry the same rules in their prompts (do not invent figures, do not
pad, keep emoji out of headings): `craft`, the per-surface system prompts and the
starter skills. Two stages read the answer back.

**Verify and repair (reports).** After the sections are written,
`verify.verify_and_repair` runs a list of checks (`services/checks.py`). Each check
only finds: the sentence, why it is wrong, and how to tell whether a rewrite still
is. Code checks cost nothing: numeric issues, one metric stated with two values,
references to a source's chapters the reader does not have (「해당 자료」), sentences
that give a settled figure another value (`key_figures.changes` lets a stated change
such as 「80만원 요청 → 50만원으로 줄이자고 합의」 stand), and scaled-metric slips.
Model checks run once, before the repair: a judge on the figure sentences (only when
a separate outline model exists, so the writer does not judge itself), claims
checked against the web when search is on, and assessment items worked through by the
judge (`services/fact_check.py`). One `repair` call rewrites the found sentences; a
rewrite is kept only when the check that raised it no longer fires. Then the code
checks run again. What remains, plus model findings no rule can confirm, is listed in
the turn's quality check step (`quality`) for the reader, never passed off as done.

Around it: `key_figures` settles the figures a document set fixes (sale price,
variable cost, break-even, targets, budget) once from the material, tells the writer,
and mends table rows and slides that drift from them. `calc` has the code sandbox
compute derived values before the prose is written, only when the request's data
holds figures (the document's own size and setting, such as 「12장」, are not data).
`report.fix_ledger_magnitudes` repairs a power-of-ten slip but never touches an amount
the request itself writes. `units` corrects a money table's unit label.
`quality_gate.report_findings` / `deck_findings` read the finished document for form
defects (a broken block, a table with no caption, a figure nobody mentions, a number
stated two ways). Decks get the code-side checks (settled figures, scaled numbers,
missing sequence members) but not the model verify stage.

**Lint.** `services/lint.py` runs on every report, deck and HTML artifact with no
model call. Findings are stored on the artifact, so a document that was fine when
made does not start reporting problems because the rules tightened. `P0` means the
document is wrong: a placeholder nobody replaced, a block never written, an
unsourceable figure, the writer's plan or instructions written in place of the text.
`P1` means it reads badly: filler adjectives, an emoji leading a line, a repeated
line, a slide too crowded to read from the back of a room. The numbers rule is narrow
on purpose (only round marketing figures and the multiplier-with-a-verb form), since a
check that fires on ordinary writing is one people learn to ignore. Nothing is
corrected automatically: the panel shows a count and a list, and **Rewrite just this
section** (or the HTML artifact's per-block rewrite) acts on it.

Visual rules are not checked, because the model cannot break them: the seed owns every
colour and face, and `sanitise` drops `style`, remote addresses and tags outside the
vocabulary. `class` survives (a block needs `lead` and `cols`), so an invented class
renders as nothing. What is checked is what the model chooses: the words.

A template can tighten the shape rules. The general bounds (seven items a slide,
forty-five characters a line) are what a screen can carry; a template whose own
instructions promise less says so in a `[limits]` table (the lecture deck asks for
four items of twenty-five characters) and the checker reads those instead.

**A follow-up edit does not commit a new P0.** A report section rewritten by a chat
follow-up first loses the writer's plan by code (`internal_words.strip_plan_preface`,
then the plan sentences). A P0 the rewrite still brings in, one the section did not
have and the person's own words do not set off (`revise.new_faults`), is sent back to
the writer once with the finding as the reason. If the second version fails too, the
section stays as it was and the reply says which part was kept (when all are kept, no
new version is stored). Markdown bold labels are not counted there.

### Mechanisms by model group

The checks and devices after writing help a weak writer and can harm a strong one: a
check that overrides the writer pays only when the writer is weaker than the check.
Each mechanism therefore decides its behaviour by the **model group** of the model
that actually wrote (`model_tier.group`, `Request.group`):

- **self-hosted**: `local/…` and `strict-local/…` (27B, flash). The evaluation switch
  `KCHAT_LIGHT_WRITERS` (comma-separated model names treated as strong) changes their
  prompt, not their group.
- **strong external**: `model_tier.STRONG_MODELS` (`model_tier.strong`), which also
  picks the light prompt (`prompt_tier`). Names with a small suffix (`-flash`,
  `-nano`, `-mini`, `-lite`, `:free`) never count as strong.
- **small external**: every other external model.

Two rules hold for every group. **Marking is apart from deleting**: no judge model
deletes or rewrites a sentence unless code has confirmed that one of its figures or
names is in none of the evidence (`post_checks.code_reading`); a statement that cites
pages read this turn (`[[n]](url)`) is read against those pages only
(`post_checks.cited_pages`). A finding code cannot check is marked or shown, never
acted on. **Code fixes only what it computes**: a value is replaced by code only when
code knows the right one (a settled figure, an official hotline, a recomputed date).

The small-external column mostly follows the self-hosted one. When a fix changes what
a group gets, change its row here and test both a self-hosted and a strong external
writer. "Same" means the same as the column to its left.

| Mechanism (code) | Self-hosted | Small external | Strong external |
|---|---|---|---|
| Writing rules (`prompt_tier`, `context`) | full rule bundle | full rule bundle | light: one anti-fabrication line |
| Judge findings of skill post-checks (`post_checks.run`) | the judge is the planner; a finding whose figures and names are all in the evidence is dropped; one with none is marked 「(확인 필요)」 where it stands; one with a figure or name in none of the evidence (or a script's own finding) goes to one rewrite | as self-hosted | the writer judges its own answer; dropped findings as for every group; the rest go back to it once as notes to weigh; nothing it kept is taken out |
| Copy check claims (`copy_check.run`) | as post-checks; numbers, offers and lengths fixed by code | as self-hosted | notes to the writer; what it kept is marked |
| Value marks (`value_guard`, `grounding-check/scripts/trace.py`) | an untraced value is marked, never cut. A multiplier (「94x」) is backed only by a given one or the ratio of two given figures of one measure. Not marked: the person's values in another notation or unit, parts and ends of a period they give, what an attached data table works out (`table_pivots`), values under an assumptions or examples heading, values set as a condition, fractions, the system date | as self-hosted | as self-hosted; its own revision stands (`settle(writer=True)`) |
| Where marks stand in a chat answer (`copy-marks`, `check_stages._advice_note`) | inline; an advice answer with more than three marks gets one 「확인 필요: …」 line | always one 「확인 필요: …」 line at the end | inline |
| Report quality follow-through (`report_stages.Verify`, `Quality`) | a web-contradicted sentence is marked; a judged figure code finds at odds with a settled one, and a number a judge tied to another item whose value is in none of the evidence (`bound_token`), are repaired once by the planner and marked if they stand; the step shows what a judge found, never its reasoning | as self-hosted | the same three go back to the writer once; what stands is marked |
| Table and figure shape after the last read (`report_stages.mend_shapes`, `table_mend`) | broken pipe tables rebuilt under one header, uncaptioned tables captioned, a table or figure the text never names pointed at, a header with no rows marked; read again once and kept only when no finding is added; no model call | same | same |
| Report length from the pages asked (`report.section_targets`, `report_stages.Sections`, `_lengthened`) | a request that sets A4 pages gives each section its share in the draft and section prompts; a document still under 90% of it has sections under 80% of their share added to once, kept only when every existing line stands (`report.only_added`) | same | same |
| Computed table over a writer's table (`calc.replace_tables`) | a table of the computed columns only is replaced; one with more columns keeps them and only its cells under the computed columns take a differing computed value | same | same |
| One item given another's attribute (`item_claims`, `checks.item_attributes`) | a comparison cell or clause whose words the material says only of other listed items goes back to the repair stage once; a cell still standing says 「자료 없음」 | same | same |
| Owner and deadline blanks (`checks.owner_cells`) | a 「[담당자]」 blank is named by the department or role the work belongs to, never a person; a 「[기한]」 blank is placed on the material's schedule, never a made-up date; minutes keep 「미정」 owners | same | same |
| Deck quiz count and broken notes (`deck.requested_contents`, `deck.finished_notes`) | a quiz slide with fewer questions than requested is rewritten once; a broken note is cut back to its last whole sentence or emptied and written again (`fill_missing_notes`); content-slide notes under three sentences are asked for more once, four slides at most | same | same |
| Deck comparison table without values (`deck._write_slides`, `table_values`) | a slide planned as a table that came out as names only is rewritten once; kept only with value rows | same | same |
| Recommendation override (`report_checks.settle_verdict`) | on | on | off: the finding is reported |
| Sweeping-claim narrowing (`superlatives`) | on | on | off |
| Crisis lines (`hotlines.fix`) | a wrong number where a crisis line is meant becomes the official one (109, 1577-0199, 1366, 1388); numbers the person or a source gives stay | same | same |
| Work notes and check reports (`internal_words.strip_work_notes`) | stripped, including untagged English plan paragraphs and check-report sentences | same | same |
| Mixed script in a chat answer (`hangul.repair_mixed_script`) | on (Cyrillic and kana inside Korean words; runs of other scripts the material does not use taken out) | on | on |
| Chinese or another script in a Korean report section (`report_stages._korean_again`, `hangul.chinese_slips`, `hangul.foreign_slips`) | each prose paragraph with ideographs or a foreign-script run the request, files and pages lack goes back to the writer once as that paragraph alone; the answer stands only with no slip left, else the Hanja are read in Hangul (`hangul.read_back`) and the run is taken out (`hangul.drop_foreign_runs`) | same | same |
| Odd Korean words in a report section or deck draft (`word_fix`) | the judge model (its `strict-local/` twin on a strict-local turn) lists `{from, to}` pairs for wrong words, misspellings and English or Chinese fused into Korean; code swaps a pair only where it stands verbatim outside code, links and figure markers, and refuses one touching a digit, citation, Markdown mark, acronym, name, negation or a word of the request or material; any failure keeps the text | off | off |
| Calculation time limit (`calc.DEADLINE`, `Calc.run`) | 240 s; a finished run is handed over unchecked; after any run short of a checked one, code gives 「(추정)」 to each worked amount that is neither the material's nor code's (`report_stages._figures_marked`, `units.label_estimates`) | same | same |
| Written sums and chains (`units.fix_written_sums`, `fix_table_totals`, `fix_written_arithmetic`) | recomputed from the answer's own figures: 「A + B = C」, a sum after its listed amounts, a table's total row over plain rows of one unit, a wrong step of a chain and the steps after it; marked 「(확인 필요)」 not rewritten: a result a tool printed, a total over subtotals, a sum off by 1000× or more | same | same |
| Figures code finds off but cannot settle (`units.mark_slipped_formulas`, `mark_scaled_restatements`, `mark_profit_contradictions`) | marked where they stand, never rewritten | same | same |
| Company-assumption labels (`units.label_assumptions`) | a report table row labelled 「가정(사내)」 whose figures the person never gave reads 「가정(본 검토 설정)」 | same | same |
| Weekdays beside dates (`weekdays.fix_weekdays`; chat `answer-weekdays`, report `save_stages.last_pass`, composed `compose._last_pass`) | set to the calendar after every stage that may rewrite; the year the task names, else the one nearest today, else as written; a weekday the person gave that the calendar contradicts is kept and flagged 「(화, 확인 필요: 달력상 수요일)」 | same | same |
| Days a chat question counts from today (`date_rules.today_block`, `dates.from_today`) | 「다음 주 월요일」, 「내일」, 「3일 후」 and similar in a line with no date of its own are counted by code (a week runs Monday to Sunday) and given to the writer as 「오늘 기준 날짜(코드 계산)」 with the days between | same | same |
| Guidance echoes (`guide_echo.scrub`) | an example name or full date from a skill's or template's text becomes 「[이름]」 / 「[날짜]」, and a sentence repeating the guidance's own rule wording is dropped, each only when the person's input, sources, tool output and today do not give it; quoted examples, tables and fenced formats count as meant to be copied | same | same |
| Code-printed values (`agent.code_checked`) | a row's own printed values, and any value of five or more digits printed in any run, are kept; a mismatch is marked, never rewritten | as self-hosted | as self-hosted |
| Citation links (`agent._link_citations`) | a number whose page shares no word with the claim is dropped | same | same |
| Repair replies (`verify.repair`) | only a sentence string (or `{sentence: …}`) is written into the text; a prose reply that only restates the section repairs nothing (`verify.restates`); a reply that restates another sentence of the section with its blank filled (`verify.filled_twin`) fills that sentence and leaves the asked one unrepaired | same | same |
| Limits, approvals and rules the material only asks about (`stated_policy`, `checks.stated_rules`, `report_stages.mark_policy`; revisions `revise_stages._policy_marked`) | a sentence or row stating a limit (「허용 한도」), an approval given (「결재 완료」), an approval precondition, a rule (「규정상」) or an in-team review that the person's material never states goes to one repair; what stands is marked 「(확인 필요)」; a sentence asking about it or setting it as a condition is left alone, and so is everything when the material is not Korean | as self-hosted | as self-hosted (the writer repairs its own sentence) |
| Weekdays the material never gives an item (`stated_days`, `checks.stated_dates`, `report_stages.mark_policy`) | a plan-table row under a weekday cell, or a clause with 「X요일」, whose item shares no word with the material's stretch on that weekday (or sits in the wrong week) goes to one repair (drop the day, 「미정」); what stands is marked 「(확인 필요)」 | same | same |
| English dates in a memo (`weekdays.english_days`; `report_checks.meeting_base`, `_day_known`) | 「Oct 19」, 「4 Nov」, 「(Mon)」 read as 10/19, 11/4, (월) wherever the minutes' meeting day, deadlines and weekdays are read from the memo; a yearless day takes the one year the memo's weekday-marked days agree on | same | same |
| What minutes say about the meeting that the memo never says (`stated_meeting`, `report_checks.minutes_unsourced`, `settle_unsourced`) | in a 회의록, claims the memo does not support (a meeting length or end time, a part of the day, an open question, a guess, a next-meeting agenda, a promise to tell more later, the writer's advice, a mail line when none was asked for, a note on what was kept out, a task for a memo person or an action row with no basis, an agreement the memo states none of) go to one repair; what stands is dropped or marked 「(확인 필요)」 by kind, an 오전·오후 before the memo's bare clock is dropped by code, and a decision told with an agreement verb takes a plain verb (「합의했습니다」 → 「정했습니다」). The checks read the memo alone, never the skill's or agent's instructions | as self-hosted | all marked 「(확인 필요)」 after the writer repairs its own sentence; only mail lines are taken out |
| The meeting as the memo gives it (`meeting_memo`; `fill_head` in `title-block`, the rest in `minutes-reground`) | in a 회의록, the head (name, day, clock, place, attendees, next meeting) is filled from the memo's own head stretches, and empty fields take that value or a bracket (「[장소]」); a value called missing that the memo gives is filled; a value a memo decision changed takes the new one (`decided_values`); a task tied to the next meeting or a week fills a blank 기한 cell; a duplicated table row or sentence goes; a count before a table or list takes the number of items it holds; a weekday flag stands at its first mention only (`weekdays.flag_once`, also run on every report). The memo is the request's text before its 「덧붙인 조건」 block plus the block's answer lines, never its rule lines | as self-hosted | as self-hosted |
| The language a new document is written in (`output_language.asked`, `asked_condition`; in `send_message`) | with no output language set on the agent, a new report, deck or page is written in the language the person asked in, added to the request as a condition (the first and last sentence of what they typed that is no memo item; the shorter when they differ), so a Korean request over an English memo is Korean; revisions keep their document's language; the finish line is in the written document's language | same | same |
| An English document's head (`doc_formats.in_english`, `EN_LABELS`, `head_key`; in `title-block`) | when the body reads English (`output_language.reads_english`), a head whose fields all have English labels (minutes, agenda) is set in English, the block marked `language: en`, with blanks named in English (「[Location]」; `placeholders.is_blank`, the web's `isBlank`); steps that find a head field by name read it through `head_key`; a Korean body keeps the Korean head | same | same |
| An English document's captions and references (`captions`; `report.caption_figures_and_tables`, `table_mend.cite`, `meeting_memo.drop`) | when the body reads English, tables are captioned 「**Table 1. Title**」 and figures 「*Figure 1. Title*」, an unreferenced block is cited 「(Table 1)」 or led into with 「See Table 1 for Title.」, a dangling 「(see 〔그림〕)」 goes, and Korean references code wrote earlier are rewritten in English on renumbering; every reader of captions takes both languages' words | same | same |
| Minutes told as the meeting was (`meeting_tense.held`, `report_checks.minutes_open_due`, in `minutes-reground`) | in a 회의록, a sentence ending in one of the meeting's own acts outside the next-meeting and follow-up parts, tables and forward-looking sentences is put in the past by code; prose calling a deadline open while its one action row gives a date takes that date | as self-hosted | as self-hosted |
| Empty columns in minutes (`report_checks.minutes_blank_columns`) | a table column no memo word names and no cell fills goes; 할 일·담당·기한·비고·출처 columns stay | same | same |
| Report reference list (`citations.cite_order`, `same_works`, `credit_attachments`) | a citation number no listed source has is dropped; one work found twice is listed once and its numbers merged; a sentence citing a web page for figures or technical terms only the attached file holds cites the file, and a page cited alone for most of the file's own terms stands in for it; a sentence naming a work by its own name or giving a decimal figure only one other page has cites that page; markers like [7, 11], [7–9], [7; 11], and those in the app's prose blocks, are renumbered with the list | same | same |
| Deck values (`check_stages.DeckGrounding`, `ground_slides`) | runs on the person's material, a request writing four or more figures with a measure, or a request or agent instruction forbidding unsupported figures; tables and charts are traced row by row; up to four slides holding untraced values go back to the writer once, kept only with fewer; what stays untraced is marked; a chart category holding one is left out (under two points left: the values as bullets, an untraced one marked, never blanked); a figure given only in brackets after another named item and read under a different one is handed back and marked if it stays (`misbound_values`); a slide title or subtitle never holds a mark (`titles_unmarked`) | as self-hosted | the hand-back as self-hosted; then a chart category is marked, not cut, and the title stands |
| Fractional counts (`report._without_fractional_counts`; `report_stages._unbacked_marked`, `revise_stages._figures_marked`) | a count of whole things with a decimal part (「잔여 14.8건」; not 「3.5개월」) that the material or sources do not state is marked 「(확인 필요)」 even when worked from given figures; averages and rates stand | same | same |
| Percentages (`value_guard.percent_basis`, `check_stages.mark_unbacked_percents`, `check_stages.edited_percents`, `report._without_invented_percents`) | a percentage (or %p) stands only when the material, sources or code results give it, or it is a share, gap, ratio or change of given figures of one measure, a share of a data table's group sums (`table_pivots`), or the result of a formula the document itself writes from given terms (`worked_results`, `_sentence_share`); 「약」 allows a place off, a total or baseline row 100%; else marked 「(확인 필요)」 where it stands, in deck grounding, in a report's cleanup, and on a deck or report edit for a percentage the document did not show before | same | same (a chart category marked, not cut) |
| Deck revisions (`revise.slide_clause`, `deck.shown_layout`, `deck.unmet_asks`) | a slide or section named by number (「4장」, 「slide 4」) is the target whatever the planner picked (`revise.numbered_target`) and, with no structure change asked, is edited without the planner; words that ask no new structure or whole rewrite edit every part in place when they carry an edit verb (`revise.asks_restructure`) and otherwise change nothing and ask what to change; a question or explicit no-change wording changes nothing; a picture asked away is removed by code; a layout change drops the old shape's fields; the reply names an asked layout or removal the new slide does not show; figures the slide showed and its rewrite dropped (`deck.lost_figures`) are asked for once more, then named in the reply; an ask typed in English gets an English reply | same | same |
| Report revisions' numbered removals (`revise.drop_numbered`, `revise.drop_added`) | 「표 2」 or 「그림 1」 asked away is cut by code with the prose citing it, and with the rest asked of the parts it names the planner is not asked (`revise.settled_by_code`); the one-copy-per-table tidy (`revised_tables.drop_superseded`) never removes one of two look-alike tables the document already had; a table or figure the rewrite adds beyond what the part kept is dropped unless the ask wants one | same | same |
| Deck table width (`deck._clean_rows`) | a wide, short table is transposed; else the five-column cap | same | same |
| Skill frames and genre rules (`asked_form`, `skill_turn`, `report._genre_rule`, `skills/*/SKILL.md`) | the request's own parts, order, counts, columns and tone are the structure; a skill's frame, default count and extra tables fill only what the request leaves open | same | same |
| Automatic web search (`report.given_material_suffices`, `routers/sessions.py`) | off on an 「자동」 turn whose given material does the job and that asks for no research or current facts; forced searches (current facts, contacts, advice) and the empty-result retry stay | same | same |
| Answer plan (`answer_plan`) | a mail or notice written for the person carries only the facts they gave; a short answer gets no plan | same | same |
| Duration must-haves (`report_checks.must_status`, `duration_limit`, `option_duration`) | a lead time or period is judged in days from the material's own words; the limit is the one written with its unit or a deadline counted from a written contract day, else the must is not judged and said so; a range across the limit is open; an extension the option sells meets a minimum, said with its cost | same | same; the block is data to weigh (`_must_block(data=True)`) |
| A 「있음」 the material never writes (`report_checks._mentions`) | a yes/no must for an option whose lines say nothing of it is open (조건 미확인), not 충족 | same | same |
| Cost readings settled from the price lines (`report_checks.price_lines_reading`, `_compare_cost`) | when the two readings differ, code reads the plainly written price lines (unit price × count, per month/year/once, discounts, VAT basis) as a third reading; a reading agreeing with it takes its scenarios; a line it cannot read leaves the vote as it was | same | same |
| System prompt order (`context.system_prompt(cache_order=…)`, `model_tier.cache_ordered`) | date and session blocks before the tool rules and contracts, unless `self_hosted_prompt_cache_order` is on (default off) | fixed blocks first, then the search toggle, the date, the session's blocks and one line keeping the contracts above them | as small external, on the light prompt |
| Large connectors by name (`tools/deferred`, `connector_tools_eager_max`) | every schema sent, unless `self_hosted_defer_connector_tools` is on (default off) | tools of a connector over the limit (default 6) sent by name; `load_tools` brings their schemas | as small external |

### Pipelines

The report writer, the deck and page writers, a chat answer's finish, a revision and
the hand-over of files are pipelines: an ordered list of named **stages** that read
and write one typed **work bundle**. A fix to one step lives in one stage with its own
tests, every stage of every turn is recorded (run or not), and which stages a document
runs is data, not an if-chain.

**Where things are.** `app/pipeline/` holds the machinery: `bundle.py` (the work
bundle), `stage.py` (the contract), `runner.py`, `registry.py`, `compose.py` (composed
step lists), and the stages by flow: `report_stages.py`, `check_stages.py`,
`chat_stages.py`, `revise_stages.py`, `export_stages.py`, `save_stages.py` (the `io`
stages that load and store a document), `make_stages.py` (deck, page),
`route_stages.py`. `runs.py` keeps a run across requests, `marks.py` the spans a
document's reader should look at, `labels.py` the stages' Korean names.
`app/pipelines/*.toml` declares four pipelines: `report` (every document), `chat` (a
chat answer's finish), `deck` and `page`.

**A pipeline file** names the agents it serves, its flows and a `[summary]` of plain
lines for the drawn chain:

| flow | what it lists | who runs it |
|---|---|---|
| `pre` | stages before the first draft (`check:gaps`, `gap-card`) | `sessions._gaps` |
| `plan` | the planning pass, ending with the offered outline | `report.write` without an approved plan |
| `write` | the writing pass on the approved outline | `report.write` |
| `prepass`, `checks`, `judged` | the process-settings groups a format names (pre-pass calculations, code checks, judged checks) | `report.write` |
| `finish` | a chat answer after the agent loop | `sessions._run_turn` |
| `revise` | a follow-up applied to the document on screen, `revise-load` … `revise-save` | `sessions._revise_document` |
| `make` | a one-pass writer: deck, page | `sessions._make_pass` |

What a document does besides writing is not in the file. The template's and the
agent's **process settings** (`services/process_settings.py`; `[process]` in a shipped
`template.toml`, `user_templates.process`, `agents.process_settings`) name the pre-pass
calculations, the optional format-repair stages (each listed in `report.toml` and
skipped unless named), the code and judged checks, structure rules (sections, tables,
summary lines, bullet (개조식) style, page limit), written review rules and options
such as `search`, `fixed_outline` and `draws`. Every name is from a closed list the
code ships, so a person who sets the same values as a shipped template gets the same
run. `tests/test_pipelines.py` holds every shipped format to its expected pre-pass
steps, check families, stages and options. Runs stored under a pipeline name without a
file of its own still open (`registry.LEGACY`).

**The bundle** (`Bundle`) has narrow, typed parts: `request` (what the turn was asked,
never written), `spec` (format and genre), `material` (each `Part` with its origin:
attachment, pasted, project, research, computed, settled, conflicts, prepass, note),
`answers`, `computed` (computed values, settled figures and conflicts, the pre-pass),
`plan`, `document` (sections, head block; a chat answer as streamed and as finished),
`findings` (codes found before and after the repair, what the verify stage left, the
grounding outcome, the quality record), `files`, `steps` (the records) and `usage`.

**The stage contract** (`stage.py`): a stage has a `name`, a `kind` (`compute`,
`model`, `check`, `io`), the dotted bundle fields it `reads` and `writes`,
`skip(bundle)` (the reason it does not apply, or "") and `run(bundle, ctx)`, an async
generator that writes its fields and yields the wire events (`step`, `section`,
`titleBlock` …) the surface shows. It ends the pipeline on purpose by raising
`Stop(reason)` (an outline offered, a question card, a revision with nothing to do)
after yielding its own closing events.

**The runner** records one `StepRecord` per stage: `done`, `skipped` with the stage's
reason, `stopped`, `failed` (the exception goes on to the caller) or `cancelled` (the
turn was closed under it); the stages after a stop are recorded as skipped. The
records go to the log (`pipeline minutes.write: research:done, calc:skipped(…), …`),
onto the bundle, and to `report.write(trace=[…])`. `run(start="verify")` resumes from
a stage on a bundle the earlier ones filled. `StepRecord.wire()` is the record as a
surface `step` (category `pipeline`, the whole record under `stage`: Korean label,
summary, evidence, times, attempt). `KLOUDCHAT_PIPELINE_STEPS` sends them, on unless
set to `0`. `KLOUDCHAT_PIPELINE_STRICT=1` refuses a stage that changed a field it did
not declare; the test suite sets both (`tests/conftest.py`).

**Runs are kept.** A writing pass (report `write`, `revise`, a deck's or page's `make`
on an approved outline) runs with a `runs.Recorder`: the bundle encoded before the
first stage and, after each stage, the fields it changed. The run is a `PipelineRun`
row (kept apart from `messages`, which the transcript reads whole) with a compact copy
of its records on the message it settled (`routing.run`). `runs.restore` gives the
bundle as it stood before any recorded stage; `POST /sessions/{id}/runs/{run}/rerun
{fromStage, answers}` runs that stage and those after it again (the earlier records
are carried), and the flow's own save stage stores the result as a new version of the
artifact, billed like a turn. A value that is a function (the verify stage's checks)
is not encoded; the stage that built it registers a rebuild (`runs.REBUILT`).
`tests/test_pipeline_replay.py` reruns every recorded report and revision from each of
its stages and requires the same document, with no model call from the stages before
the restart.

**The gap card is a stage.** `pre` ends with `gap-card`, which raises `Wait`: the run
is kept as `waiting`, the card's questions (fact, label, why, placeholder) ride on the
pending turn with its run id, and the answer (or **Go with what arrived**, which leaves
each fact its bracket blank) closes it; the writing pass that follows names it as its
parent.

**Persistence and routing are stages.** `report-save`, `revise-load`, `revise-save`,
`deck-save` and `page-save` are `io` stages that reach the database and the bill
through the router module, so tests that replace `sessions.SessionLocal` reach them.
`send_message` decides where a turn goes with the `route` stage (page, file hand-over,
revision, report, deck, chat); its record opens the stream. A stored report carries
`data.marks` (placeholders, values the grounding check blanked with what stood there,
what the verify stage left), found again in the current body by
`GET /artifacts/{id}/marks`. `GET /pipelines`, `/pipelines/{name}` and
`/agents/{id}/pipeline` read the shipped definitions.

**Check skills are stages of the `check` kind.** A check skill's SKILL.md names its
stage: `pre` checks run as `check:gaps` (one question card before the first draft);
`post` checks run as `check:grounding` before the verify stage and
`check:grounding-again` after its rewrites (report sections), and as
`check:answer-grounding` on a chat answer. The verify stage and `consistency` are
`check` stages too: find, repair once, find again.

**Adding a stage.** Write a class on `stage.Base` in the flow's stage module (or a new
one imported by `registry._load_stages`): name, kind, `reads`/`writes`, `skip`, `run`.
Call helpers through their module (`report._complete`, `report.fix_products`) so a test
that replaces one reaches the stage. Register it, list it in the pipelines that run it,
and add its own test. A format-specific behaviour is a stage listed only in that
format's file, or an `[options]` switch, not a format comparison inside a stage.

**Replay tests.** `tests/test_pipeline_replay.py` replays recorded turns (one
acceptance run per format, the chat turns for data analysis and career documents, and
two revisions) and requires the same events, sections and stored rows as when they
were recorded. `tests/replay_harness.py` answers every `/chat/completions` POST from
`tests/fixtures/replay/<case>/calls.json` by the call's key (model, messages,
max_tokens) and fails on a prompt it has no reply for; any other HTTP call fails as
offline; the clock and uuids are fixed. `tests/replay_build.py` re-records a case from
an acceptance run against a scripted model; rebuild only when a change in behaviour is
meant, and say so.

**Composed pipelines** (`app/pipeline/compose.py`, `app/routers/user_pipelines.py`).
The shipped files stay locked. An agent's process › **Set it up yourself (advanced)**
edits a `user_pipelines` row: a list of at most twelve coarse **steps** `{id, kind,
ref, settings}`:

| kind | ref / settings | needs | leaves |
|---|---|---|---|
| `agent` | an agent the person may run (own or organisation's), `instruction` | none | a document (report agent) or an answer (chat agent) |
| `skill` | a calculation run by code (`date-calc`, `quote-calc`: the pre-pass steps; `quote-calc` is the server's own module `services/quote_engine.py`, no longer a catalogue skill) | none | material |
| `check` | `grounding-check` (document or answer), `copy-check` (answer) | a product | findings, repaired once |
| `review` | `model` (one the person may use), `repair`, `focus` | a product | findings in the grounding check's shape |
| `human` | `gaps` (the question card) · `outline` (approval; just before a document agent) | a later agent | answers · an approved plan |
| `export` | `formats` | a document | files (after the save) |
| `condition` | `name`, `mode` `rule` (`rule: {step, value, op, target}`, branches `yes`/`no`) or `ai` (`question`, 2–4 `branches: [{key, label}]`) | an earlier step's value (rule) | one branch taken |

Step ids are stable (`s1`, `s2` … when none is given). `user_pipelines.edges`
(`[{from, to, branch?}]` by step id; `null` means the steps run in their stored order,
and edges that only repeat that order are read as `null`) makes the steps a graph from
the first step; only a condition step may have more than one line out, one per branch.
A **rule** reads a value the step it names left behind (`compose.CONDITION_VALUES`, also
`condition_values` in `GET /user-pipelines/catalogue`: the request's attachments and
text; an agent's result kind, sections, characters and text; a calculation's result; a
check's found/left; a review's found/fixed/left; the card's blanks and answers; the
outline's sections) and compares it (`gt`, `eq`, `empty`, `contains`). An **AI**
condition asks the judge model (`Request.judge`; the writer when a strict-local run's
judge is not local) one small prompt and keeps its reason; its tokens join the run's
usage. A rule whose value is missing (that step was on another branch or left none)
takes `no`, an AI reply naming no branch the last branch, and the record says so.

`compose.validate` holds the order to these contracts before every save and on
`POST /user-pipelines/validate` (the editor's live messages), one Korean line per
problem (「1번 검사(근거 대조): 앞 단계에서 만든 문서나 답이 있어야 합니다.」), plus
warnings (a card before an agent with no facts to ask is skipped). The **recursion
guard** refuses an agent step whose agent is bound to this pipeline, directly or
through the pipelines its agents are bound to. The last agent decides the surface:
`report` for a document, `chat` for an answer; an agent's own pipeline must end on the
agent's own surface. With edges the graph is checked first (`cycle`,
`unreachable_step`, `branch_dead_end`, `condition_incomplete`, `parallel_next` for two
lines out of a non-condition step), then the order rules along every path from the
first step; all paths must end on the same surface (`branch_surface`) and no condition
may sit between a card and its agent (`card_before_branch`). Every problem carries
`step` (1-based number) and, for a step, `stepId`. A run refuses a pipeline with
problems (`pipeline_invalid`).

**Running.** A conversation runs a user pipeline when `sessions.pipeline_id` names one
or its agent has one (`agents.pipeline_id`, set by `PUT /agents/{id}/process`).
`send_message` then skips the agent's own gap card and, for a new request on the report
or chat route, hands the turn to `user_pipelines.turn_response`; a follow-up on a
finished document still revises it with the document's own `revise` flow.
`compose.compile_steps` makes one stage per step (named `3:check`, titled by the step)
and a save stage before the first export; the existing `Runner` and `runs.Recorder` run
them as flow `compose` of pipeline `user:<id>`, so every step is recorded, kept and
rerunnable (`POST …/runs/{id}/rerun` routes a `compose` run to `user_pipelines.rerun`,
on the steps as they were when it ran). With edges the stages come in graph order
(exports last, so the save still precedes them); a condition stage writes its choice to
`wire.branches` and the steps a later rule reads note their values on `wire.values`,
both kept in the snapshots, and every step whose incoming lines all come from steps not
reached or branches not taken is recorded as skipped (`compose.OTHER_BRANCH`); the
condition's record keeps `evidence.branch = {key, label, reason, missing}`.
`GET /user-pipelines/{id}/runs` (the pipeline's owner, their own runs, newest 30) and
`GET /user-pipelines/runs/{run_id}` read the stored records per step: `{id, number,
status, pipeline_id, request_text (the request's opening words), attachments,
session_id, parent_id, from_stage, started_at, ended_at, steps: [{step_id, kind, title,
status (waiting · running · done · failed · skipped), started_at, ended_at,
input_summary, output_summary, note, branch_taken, branch_label?}]}`, with summaries
built from the records' labels only, never the text a step read.

**Canvas.** The web edits an agent's custom order as a graph at
`/customize/agents/{id}/chain` (`pages/customize/ChainPage.tsx`, `@xyflow/react`): tabs
편집 (the canvas), 실행 기록 (the two run reads above, read once per run and again on
새로 고침) and 목록으로 보기 (the list editor, read-only once there are branches).
`lib/pipelineGraph.ts` turns canvas lines into `stages` + `edges`: the step the start
node leads to is stored first, the rest in reading order (branches in their order), and
`edges` is `null` when the lines only repeat that order; a step (or a condition branch)
keeps one outgoing line, and a line that would close a loop is refused. Before a
확인·내보내기 item is added, the palette asks `POST /user-pipelines/validate` about the
graph with it after the selected step and shows the server's problem on that step. It
saves with `PUT /agents/{id}/process` and marks problems by `stepId` (else number).
Dropping a skill opens `/customize/agents/new?from=skill:<id>` with `return`, and the
agent saved there comes back as a step (`?added=<id>`); unsaved canvas state waits in
`sessionStorage`. Its e2e answers with `apps/web/e2e/fixtures/chain-canvas.json`, which
`tests/test_the_chain_canvas_fixture_is_the_apis.py` keeps equal to the API's
catalogue, problem sentences and run reads.

An **agent step** runs that agent's own flow as a sub-run: a document agent its
format's `plan` (or the outline a `human:outline` step got approved) then `write`, with
no save of its own; a chat agent one answer on its own prompt (tools stay with the
conversation surface). What the previous agent produced is handed to the next as
material (`Part(origin="handoff")`). A **review step** asks the chosen model for
findings (quote, why, fix) over the product and the material, places them, applies the
fixes once when `repair` is on, leaves the rest as `check-finding` marks, and is billed
at its own model's price (`pipeline.review`). A **human step** raises `Wait`: what the
run spent is billed, the card (`needs`) or the outline (`proposal`) is the session's
pending turn with `pending.compose = {runId, stage}`, and the next message resumes the
run from that step with the answers or the approved outline.

**An agent's process** (`app/services/bases.py`, `app/services/bundles.py`,
`PUT /agents/{id}/process`, or `process` on `POST /agents`: a new agent and its process
are saved in one transaction, so a refused process leaves no agent). The agent form
offers **How it works** (**Answer directly** with no base; **Start from a base
process**, a **base** being an offered catalogue agent, `agents.base_key`; or **Set it
up yourself (advanced)**, the step editor above, not offered for slides) and **Extra
work**, one checkbox per **bundle**.

- A **base** is any offered agent file (`starter.offered_agents`; drafts for
  administrators only). With none offered the form hides the choice (an agent already on
  a withdrawn base keeps it). Its plain steps are its pipeline's `[summary]` lines (at
  most five); `sessions._agent_catalog_key` resolves `catalog_key`, then `base_key`,
  then the original's, so a person's agent with a base runs that base's question card,
  format, template and pipeline. Picking a base sets `surface`, `doc_format` and (when
  the agent has none that fits) `render_template_id`.
- A **bundle** is data: `app/bundles/{prepare,review,finish,publish-check}.toml` ship
  **Prepare before writing** · **Review after writing** · **Finish** · the
  pre-publication check; a person's own is a `user_pipelines` row with `kind =
  "bundle"` in the same item shape (`{id, kind, ref, settings, label, default}`;
  `GET/POST/PUT/DELETE /bundles`, `POST /bundles/{id}/share`). Where an item runs is
  decided by its kind alone (`bundles.target`): a check skill with a stage is an
  `Agent.check_skills` entry, run inside the agent's own flow (find → repair → find
  again); `option` is `Agent.check_options` (`tool_numbers`, `titles`, read by
  `check_skills.options_for`); `flow` items (format checks, repair) are what the report
  flow always does; `skill` items are the report flow's own pre-pass but steps before
  the agent on chat; `human:outline`, `review`, `export`, other agents and `copy-check`
  are steps around the agent. Agent files name their bundles (`bundles = [...]`) and the
  seeder compiles them with the same `bundles.compile` the form's save uses, so a person
  who picks a base and ticks the same bundles gets the same agent
  (`tests/test_agent_process.py` holds this for every base).
- `agents.bundles` keeps the ticks; `check_skills`, `check_options` and, only when an
  item is a step, the agent's **own pipeline** (`user_pipelines.agent_id`) are compiled
  from them. Its `@self` agent step (`compose.SELF`) runs the agent's own shipped flow
  with the turn's web search and connectors (none under strict-local); the recursion
  guard does not apply to it. With no step needed there is no pipeline and the shipped
  flow runs as is.
- **Review on share.** A shipped bundle never needs review. A person's bundle that
  exports, reviews or runs another agent, and a set-it-up-yourself pipeline with any of
  those, reach others only after an administrator approves them (**Process review** on
  the admin users page); until then others run the agent on its base
  (`user_pipelines.runs_for_others`). Installing a copy carries `base_key`, `bundles`,
  `check_skills`, `check_options` and, when it runs for others, a clone of the
  pipeline; deleting an agent deletes its own pipeline.
- `GET /user-pipelines` lists a person's unbound pipelines (no `agent_id`, not bundles),
  which a set-it-up-yourself pipeline can start from.

**Save as agent** (`GET /sessions/{id}/agent-draft`, `services/agent_draft.py`): the
result card's and the panel's ⋯ open `/customize/agents/new?from=session:<id>`
prefilled; nothing is saved until **Save**. "What this conversation settled" is
distilled from the person's own messages only, by the model the conversation ran on:
its strict-local model (no fallbacks, nothing masked away) when any turn ran
strict-local, otherwise masked by `governance.mask`. A sealed agent's prompt is never
copied. The base is the conversation's agent's, else inferred from its template; the
conversation's files go onto the new agent's shelf as copies
(`POST /agents/{id}/knowledge/from-files`, the caller's own files only).

**Sharing.** Private by default. Sharing an agent whose set-it-up-yourself pipeline
needs review sets that pipeline's `review` to `pending` when every agent step is one
others can run (a catalogue agent or one shared with the organisation); an
administrator approves (`visibility` org) or rejects with a reason
(`/admin/user-pipelines/{id}/approve|reject`, the queue on the admin users page). A
saved edit takes an approved pipeline back to private. A copy of the agent remaps
catalogue agents to the taker's own copies (`settings.catalogKey`).
### Asking for a review

`POST /artifacts/{id}/critique` is one reading of a finished document by somebody who did not
write it. It answers with a score out of ten and up to six things to fix, in **the same shape
the linter produces**, so the panel shows one list. The linter is free and certain; the review
costs a call and is an opinion.

The score is a reading, not a gate. A review annotates the artifact (`data.critique`) rather
than editing it: no version snapshot and no version bump, as with the fact-check. It is one
reviewer and one pass, asked for explicitly, and charged like any other call (422
`insufficient_credits` when the balance is short). Reports, decks and HTML artifacts can be
reviewed.

The rubric is the `checklist.md` of the template an HTML artifact was written into, or a
default rubric (`critique._DEFAULT_RUBRIC`) otherwise. Reviewing rules are kept apart from
writing rules on purpose: folded into the brief, a rubric becomes a checklist the model writes
*to* rather than one it can be measured by.

### Design briefs

The look of a project's or agent's documents is its 디자인 지침, free text of at most 2000
characters (`style_spec.MAX_BRIEF`). `design_brief.of(agent, project)` turns the texts into one
design the writers read (the agent's first). The split is the whole design:

**`tokens`** (`accent`, `ink`, `muted`, `font`, plus `visualStyle`, `footer`, `logo`) is what the
*renderers* draw with. `style_spec.parse` reads a colour, a typeface or a look from the text and
`design.normalise_tokens` puts every value in a valid shape, so a bad colour becomes the default
rather than something saved unnoticed. The values are what `.pptx`, `.pdf`, `.hwpx` and the browser
preview can all express, so the preview does not lie about the file.

**The text** is what the *model* reads, whole, as guidance. Colours do not help a model writing
report prose (a hex code in a system prompt is spend with no effect); for images the colour is the
instruction, so it travels as an English phrase (`design.image_clause`) beside the style chip, and a
line 「그림 스타일: …」 in the brief sets the picture style. `design.CRAFT` holds brand-agnostic rules
(`restraint`, `typography`) filtered per surface: a rule about heading depth reaches a report and not
a chat turn.

The tokens are **copied onto the artifact** (`data.design`) when it is made, and the exporters read
them from there, so a deck made last month does not repaint when the project's brief changes. With
no brief the default palette is used, down to the greys in the PDF; the export tests pin that output.

Themes (`design_systems` rows) are not read by any writer. `POST /designs`, `PATCH`/`DELETE
/designs/{id}` and `POST /designs/extract` answer 410 (`themes_retired`).

### The artifact gallery

The 아티팩트 page reads `GET /artifacts/gallery`: **a page of cards**, not complete bodies. It
lists HTML pages, code files, charts and diagrams (`section`, one or a comma list; the sections
are in `artifact_section.GALLERY`); reports and decks are listed under 파일함 › 작업 문서
(`GET /artifacts/working`, one row per document with its version count) and pictures, sound and
video under 만든 파일. `q` matches titles in the database (titles only, since bodies are what
the endpoint exists not to read), `sort` is one of `updated`, `title`, `section`, `version` with
`dir`, and `counts` gives each section over the same search, so the filter row is about the whole
workspace and not the loaded page.

**Keyset pages.** The default order is `updated_at`, which moves when somebody edits, and an
offset would skip or repeat rows under the person scrolling. The cursor encodes the sort and the
last row (ties broken by id) and belongs to one sort; the web client also pages by `offset`.
`GET /artifacts` (`kind` as a comma list, `project_id`, `q`, `before_at`/`before_id`) is the
same kind of card list for other callers, and `GET /artifacts/counts` its per-kind counts.

**A card.** `ArtifactOut.card` cuts the body down to what a thumbnail shows: an HTML or code
artifact travels without its markup, a deck as its first four slides (`id`, `title`, `layout`)
plus `partCount`, a report as its first four sections with the text cut to 400 characters, an
HWP/Word/PowerPoint artifact as the top 400 characters of its text. Media artifacts are already
card-sized and travel whole. Every trimmed row is marked `partial`; anything that renders or edits
a whole document fetches it by id first.

### HWP documents

An `hwp` artifact is a file, not JSON: each version's HWPX sits in a blob
(`storage_key`, on `ArtifactVersion` rows too), and `data` holds only the
format, size and body text for search. `routers/hwp.py` creates one (blank, or
from an uploaded `.hwp`/`.hwpx`), serves a version's bytes, and takes the
editor's bytes as the next version. Every writer names the version it read:
`services/office_doc.save` moves the row with one conditional UPDATE
(`version = expected`), so a hand save and an agent edit cannot both land on
the same version — the loser gets 409 (the editor) or a "read again" (a tool).

The editor is rhwp-studio (MIT), framed from `/rhwp/` on this origin; the
document never goes to the upstream demo site. `apps/web/rhwp/build-studio.sh`
builds it in the web image's own stage from a pinned source commit and the
published `@rhwp/core` wasm (checked against its npm integrity), with the AI
panel, the hwpctrl plugin, CDN web fonts and the service worker left out.
nginx serves `/rhwp/` under `rhwp-security-headers.conf`: the app's CSP would
block its WebAssembly, so it gets its own, which adds `'wasm-unsafe-eval'` and
otherwise allows nothing outside this origin. Its page and unhashed files
(`index.html`, `rhwp-version.json`, fonts) are `Cache-Control: no-cache`, revalidated on
each load, so an image update reaches open tabs; only `/rhwp/assets/`, content-hashed,
is cached for a year as immutable. The panel mounts the iframe once
per document (`HwpPanel`, keyed by id); width and full-screen changes restyle
its container and never move it, since a moved iframe reloads.

The agent works on the saved file, never the browser's copy. In a chat that
has an HWP document (or a request that names one), `hwp_read`, `hwp_edit` and
`hwp_write` join the turn's tools (strict-local too: they make no call out),
with a context block telling the model to work in the document and leave
unknown facts as bracket blanks. When the document already has text, the server
runs `hwp_read` itself before the model's first hop (a preset call), so even a
small model starts from the saved file and its numbers; a request that only puts
the person's own values into it ("…로 채워 주세요") is own work and forces no web
search. `services/hwpx_edit.py` reads the section XML
into a numbered outline (`p3`, `t1 r0: a | b`, the blanks left) and applies
edit ops all-or-nothing against the version the model read (a blank can be
filled everywhere or `at` one paragraph or cell, and a particle after it is made
to agree: 「[연수명]을」 → 「교원연수를」); a replaced
paragraph keeps its `paraPrIDRef`, its first text run's `charPrIDRef`, and any
controls, and loses its stored line layout so the viewer lays it out again.
`hwp_write` builds a new document through `report_export.to_hwpx` with a
`doc_formats` head (공문 by default). Each tool step carries the artifact id,
so the open editor loads the new version mid-turn; before a turn is sent, the
panel asks to save or drop unsaved hand edits (`lib/officeGuard.ts`).

### Word documents, and what they share with HWP

A `docx` artifact is the same kind of thing as an `hwp` one, and the parts that
do not depend on the file format are written once. The office layer is:
`services/office_doc.py` (versioned blobs, the conditional-UPDATE save, the
`doc_formats` head of a new document), `services/office_edit.py` (bracket
blanks, the particle that agrees with a filled value, op arguments, the numbered
outline), `routers/office_docs.py` (create, fetch a version, save; mounted by
`routers/hwp.py` and `routers/docx.py`), and `services/tools/office.py` (the
read, edit and write tools for one kind; `tools/hwp.py` and `tools/docx.py`
each make a set; `tools/pptx.py` too). `services/hwp_doc.py`, `services/docx_doc.py`
and `services/pptx_doc.py` only say what their files are and how a new one is written
(`report_export.to_hwpx`, `report_export.to_docx`, `deck_export.to_pptx`). In a chat, `routers/sessions.py` looks up each kind's
document (`_OFFICE`): its tools join the turn when the chat has one or the
request names it (「한글 문서로」, 「Word로」, 「docx 파일」, not 「키워드로」), and
the newest document with text is read by the server before the first hop.

`services/docx_edit.py` reads a `.docx` with python-docx and lxml. Ids are body
paragraphs `p1…` and body tables `t1…` in reading order (paragraphs inside a
content control count as body); headings, the title and list paragraphs are
marked in the outline, a table with merged cells names each cell by its grid
column (`c2~3: …`, the lower half of a vertical merge said as such), and header
and footer text follows the body. Word splits a line across runs freely, so a
blank is matched on the paragraph's text and spliced back into the runs: the
value goes into the run the blank starts in, keeping its `rPr`, and the rest of
the span is cut from the runs after it. A replaced paragraph keeps its `pPr`
(style, numbering); only what changed is rewritten — the text the old and new
lines share at the start and the end stays in its own runs (a bold 「수신」 label
stays bold), and the changed middle takes the run it begins in — and a line
with nothing in common becomes one run in its first text run's `rPr`. Runs
holding fields, pictures or note marks stay, and a written marker such as
「1.<tab>」 keeps its tab. A new paragraph copies its neighbour's `pPr` without section or page-break
settings; a new table row copies the row above without its heading flag; rows of
a table with vertical merges are not added or removed. Ops resolve against the
version read before anything changes, so a bad op changes nothing. Zip limits
are checked before python-docx unpacks a file. A legacy `.doc` is refused.

The editor is open-docx-editor (`open-docx-editor`, MIT, pinned to 0.7.6), plain
JavaScript on ProseMirror with Word's ribbon (File, Home, Insert, Layout,
References, Review, View); `DocxPanel` mounts it with `mountDocxEditor` and is
loaded with `React.lazy`, so the editor and its stylesheet are a chunk of their
own fetched when a Word document opens. Its PDF exporter is not used: it embeds
only fonts the document carries or the computer lends through Local Font Access,
so Korean text came out as 「?」; File > Export and Print open the print dialog
instead, and since the app tree is not printed (`index.css`), `DocxPanel` copies
the pages the editor laid out for print into `body > [data-print-docx]` on
`beforeprint` and drops them on `afterprint`. It runs under the app's CSP as it
is (no eval, no WebAssembly, no outside requests; its inline styles are allowed
by `style-src 'unsafe-inline'`). File > New and Open are off (one artifact per
panel), File > Create a Copy downloads, and Ctrl+S in the editor is the
header's 저장. `open-docx-editor/fonts.css` gives the metric stand-ins for
Calibri, Cambria, Arial, Times New Roman and Courier New (latin and latin-ext
woff2, fetched by `unicode-range` only when used).

**The editor patch.** open-docx-editor ships with a patch of our own,
`apps/web/patches/open-docx-editor+0.7.6.patch` (the package's
MIT notice stays in `node_modules/open-docx-editor/LICENSE`). `npm ci` applies it
through patch-package (the `postinstall` script, `--error-on-fail`), so it is in
place for local development, the web image (`Dockerfile` copies `patches/` before
`npm ci`) and CI; `src/components/docx/odxKorean.test.ts` fails when the patched
source lacks its `kloudchat-odx-patch` marker. The app imports the package's
`src/` (its `dist/` bundle is not used and is not patched). A new
version needs the patch re-made: edit `node_modules/open-docx-editor`, then
`npx patch-package open-docx-editor`. What it changes:

- **East Asian fonts.** A run's `w:eastAsia` font (or `w:eastAsiaTheme`, the
  theme's `a:ea` or, when that is empty, its `a:font` for the script of the
  document's East Asian language: `w:themeFontLang`, else the defaults'
  `w:lang`) is read in runs, styles and document defaults. It is drawn first,
  under the alias `odx-ea:<name>`, a FontFace limited to Hangul, kana and Han
  (`EA_RANGE`), so Hangul takes the East Asian font and Latin text skips it to
  the run's ascii font, as in Word. The CSS is two custom properties,
  `--odx-fea` (East Asian list) and `--odx-fa` (the run's own), so each level
  of styles can set either. The alias is the installed font (`local()`) unless
  the app adds faces for it; `odx-ea-sans` / `odx-ea-serif` follow it.
- **Home > Font** merges the choice into the run's `w:rFonts`
  (`mergeRFonts`): a Korean font sets ascii, hAnsi and eastAsia with
  `w:hint="eastAsia"`, as Korean Word does; a Latin font sets ascii, hAnsi and
  cs and keeps the East Asian font; an `rFonts` kept raw (a theme font, an
  East-Asian-only one) is folded in, theme references dropped where a font is
  now named (they outrank it in Word and LibreOffice). A run is written with
  one `w:rFonts`. The font list starts with 맑은 고딕, 나눔고딕, 나눔명조, 바탕,
  돋움, 굴림, 궁서, 함초롬바탕, 함초롬돋움 and Pretendard; at Hangul the box shows
  the East Asian font.
- **Korean documents** (Hangul text in a document whose East Asian language
  is ko-KR, or text that is mostly Hangul; `DocxSession.korean`) get the class
  `odx-ko`: lines break between words (`word-break: keep-all`, a word longer
  than the line still breaks), `w:wordWrap="0"` breaks anywhere, and Hangul
  beside Latin letters or digits gets Word's extra space (`text-autospace`,
  turned off by `w:autoSpaceDE` / `w:autoSpaceDN`). A line is as tall as the
  taller of the run's font and its East Asian font. The editor measures the
  laid-out text, so pages and print follow. Other documents lay out as before.
- **Line heights** of Korean fonts: 맑은 고딕 1.3301 em (its hhea ascent and
  descent, 2229 + 495 of 2048, the value published with Word layout
  measurements), Nanum Gothic, Nanum Myeongjo and Nanum Barun Gothic 1.15 and
  Noto Sans KR 1.448 (read from the font files). 굴림, 돋움, 바탕 and 궁서 are
  measured in the browser (the installed font or its stand-in): we have no
  published values for them.
- **Whole-word find**, Format Painter's word and a comment's word use Unicode
  letters (`\p{L}`), so 어절 count as words.
- **Words of the UI.** `mountDocxEditor(el, { locale, labels })`: the ribbon,
  menus, find bar, dialogs, margin comments and screen-reader messages go
  through `t()` (`editor/i18n.js`, built-in Korean in `editor/locales/ko.js`,
  in Korean Word's terms). `DocxPanel` passes the app's language when the
  editor mounts.

`components/docx/koreanFonts.ts` gives Korean font names their stand-ins: serif
names (바탕, 궁서, 함초롬바탕, 나눔명조, 본명조…) the app's Nanum Myeongjo, sans
names (맑은 고딕, 굴림, 돋움, 함초롬돋움, 나눔고딕…) Nanum Gothic
(`@fontsource/nanum-gothic`, OFL, imported by `DocxPanel`). Each name gets
copies of the stand-in's `@font-face` rules with the installed font first
(`local()` by its Korean and English names), so a computer with 바탕 or 맑은 고딕
uses the real font, and an `odx-ea:<name>` copy limited to Hangul, kana and
Han. A name's faces are added the first time the editor draws it
(`setFontProvider`); each name has some 180 faces and a document uses a few.
The HWP and Word panels are `components/office/OfficePanel.tsx` (`useOfficeDocument` holds the
load, save, 409, reload-on-new-version and ask-before-send logic) around their
editor, in the chrome every document panel shares (`components/document/DocumentChrome.tsx`); on a phone the Word editor is read only, without its ribbon.

### Reports written as HWP or Word files

A report asked for as an HWP or Word file (「한글 문서로 2027 사업계획 보고서 써 줘」,
"write the quarterly report as a Word document") is written by the report pipeline —
outline, research, `[n]` citations and their sources, checks, figures — and stored as that
file, not as the HTML report. `services/report_office.py` reads the format from the person's
own words (`asked_file`: the request before pasted material and before the 「덧붙인 조건」
the server appends; the first file named wins; 「한글로」 counts as HWP only when the
sentence says nothing about language, so 「영어 말고 한글로」 stays the language).

Routing. In a chat, `lib/documentRequest.ts` `officeReportRequest` sends such a sentence to
the report screen when it orders a long-form document (보고서, 기획서, 제안서, 계획서,
백서, 매뉴얼…, "report", "proposal") and is not a short one (공문, 기안문, 품의서, 메모, a
letter, 「한 장짜리」); those, an edit, a question about documents, and any sentence in a
chat that already has an office document open stay with the editor beside the chat and
its `hwp_write`/`docx_write`. On the report screen any request naming a file is written as
that file.

Picking the file. The formats belong to the surfaces. With 보고서 chosen, a 형식 chip in the prompt box (beside where the
picture options sit for 이미지) offers 웹 보고서 (the default), 한글 and Word; with 슬라이드,
웹 슬라이드 and PowerPoint (`Composer` `OutputOptions`). The chip shows the file the typed
request names (`Composer` reports `reportFileKind`, or
`NAMES_PPTX` for a deck) until one is picked, and a pick wins: the report request carries
it as `outputFormat` (`SendMessage.output_format`: `web`, `hwp`, `docx`), and
`sessions._report_output` prefers it to the request's words. The decided file is kept on the
waiting card (`pending.output`), so the approval that writes the outline writes that file
and the browser opens no HTML draft for it. PowerPoint on the slides screen is the PowerPoint
tool path, not the deck pipeline: the request goes to a chat that opens with a blank
deck in the editor (`createDocument`), where the `pptx_*` tools write it. With a file picked, a
「빈 문서로 시작」 chip beside it opens it blank in its editor beside a chat without a prompt.

Nothing new is stored on the session row: `_send_message` passes the format
(`office`) into `_run_report` and the pass's `Request.turn`, and `report-save`
(`ReportSave` → `sessions._office_target`, `_store_office_report`) builds the file and
creates the `hwp`/`docx` artifact through `office_doc.create` (a rerun of the run writes the
same file's next version with `office_doc.save`). A conversation that already has an HTML
report keeps it; 「한글 파일로 줘」 there is still the file hand-over. While the pass runs no
HTML draft opens (`streamReport` checks the waiting request with `reportFileKind`); the
file opens in `HwpPanel` / `DocxPanel` when stored.

The file. `report_office.write` gives the exporters the sections as `GET
/artifacts/{id}/export` would (`richtext.normalise`, references appended by
`with_references`, the design tokens, the `doc_formats` title block, a 서식's Word
template), after drawing what the browser draws for the HTML report: each `chart` fence
as a one-slide python-pptx chart rendered by the printer (`/pptx/png`) and each `mermaid`
figure through `diagram_render.render_png`, stored under the section's `diagrams` key that
`report_export._diagram_picture` reads. OWPML has no chart element, so the HWPX places a chart as a picture; the Word file does the same here (`charts_as_pictures`)
because the web editor draws no DrawingML chart, and its 목차 is the headings as text
(`static_toc`) since no field runs there. Without the printer a chart keeps its numbers'
table and a mermaid figure its 「[다이어그램]」 line. Both exporters keep a line's
`**bold**` as a bold run, write link lines left-aligned (justified, an unbreakable URL
spread the words before it) and table cells left-aligned; in the HWPX a card's or a
note box's title is bold and its lines are not, and a step table's head row is body
size.

Follow-ups. `sessions._office_report` finds a report conversation whose document is an
`hwp` or `docx` artifact; `_send_message` then runs the turn as a chat turn (`kind`, passed
to `assemble` too): the office tools join, the server reads the file first
(`_document_preset`), and the model edits it with `hwp_edit` / `docx_edit` against the
version it read. Hand saves in the editor and the agent's edits share the version history,
as for any office document. 「표 추가」 is the `insert_table` op of both editors
(`{after, rows}`, the first row the head): the table is drawn as the exporters draw one
(`report_export._hwpx_table` / `_docx_table`), so it matches the report's own tables; an HWPX
made elsewhere whose header lacks the house border, paragraph and character shapes those
tables point to gets a refusal that names `insert_table_row` instead of a broken file, and
a Word file without the table style gets plain bordered cells. 「3장」 is counted on the
headings the outline marks: the Word outline names heading styles, and the HWPX outline
(`hwpx_edit._heading_marks`) marks short lines set larger than the body text — the one
largest line as `(title)`, the next sizes as `(heading 1)`, `(heading 2)`, a cover label
above the title left out — so a report's chapters can be counted in either file; figure
captions are set in the small note shape, and the Word file's listed 목차 head is bold body
text, so neither counts as a chapter. The server also counts for the model: when the ask
names a chapter by number, `sessions._named_parts` adds where it stands (「3장: p13~p18
「핵심 가정」」, by `revise.numbered_parts` over the `(heading 1)` lines) to the turn's
context, as a report revision resolves 「3장」 in code. The web sends those turns
through the chat stream
(`useStore.send`, `officeReport`). A report written as a file is not re-planned or revised
by the report pipeline afterwards; a new report is a new conversation.

### PowerPoint documents

A `pptx` artifact is a third office kind on the same layer (`office_doc`,
`office_docs` routes, `tools/office.py`), for a deck that has to stay a
PowerPoint file: an uploaded `.pptx`, a company template, or a request that
names PowerPoint (「PowerPoint로」, 「pptx 파일」, 「파워포인트」, 「PPT 파일로」).
A bare 「PPT 만들어 줘」 or 「발표 자료」 is any deck and still goes to the
slides surface (the `deck` kind, whose own export is `.pptx` too): the web
composer keeps a request on the chat only when `NAMES_PPTX`
(`lib/documentRequest.ts`) matches, mirroring the server's `_ASKS_PPTX`.

`services/pptx_edit.py` reads and edits the file with python-pptx (MIT) and
lxml. Ids are slides `s1…` and, per slide, the shapes worth naming in drawing
order (`s2.1`, `s2.2`; a group's shapes where the group stands): text, tables,
charts, pictures and SmartArt (the last three read only). Placeholders are
marked (제목, 부제목, 본문, 날짜, 바닥글, 쪽 번호), notes are `s2 메모`. Hidden
shapes are not listed: KloudChat's own exported decks carry a cover title twice,
in a visible box and in a 1 pt hidden title placeholder for the outline view, and
python-pptx would take the hidden one for the title; the visible box is named
the title instead, and an edit of it carries the hidden twin along. A shape's
text is one line per paragraph; a rewrite diffs the lines (`difflib`), so lines
that stay keep their paragraphs, a changed line keeps the text it shares at the
start and the end in its own runs (a bold 「담당:」 label stays bold) and takes the
format of the run its change starts in, a new line copies its neighbour's `pPr`
(level, bullets) and run format, and a written bullet run (「• 」 in the accent
colour, as the house exporter writes bullets) stays in its own run. Blanks are
filled across runs with the agreeing particle (`office_edit`). Slide ops: an
added slide takes its layout when that layout has title and body places (a real
template), else it is a copy of the slide it is shaped like with its title and
main text replaced, its other own text cleared (page numbers and text repeated on
other slides stay) and tables and charts dropped; a duplicated slide copies its
shapes, background, transition and notes, relates the same images and copies
each chart part (with its workbook) so the two charts stay apart; delete and
move edit `sldIdLst`. All ops resolve against the version read, so a bad op
changes nothing. `pptx_write` writes a new deck in the house look through
`deck_export.to_pptx` (cover, a slide per entry as bullets or a table, an
optional closing line) with bracket blanks for what was not given; the context
note tells the model to fill an upload or template with `pptx_edit` instead,
whose `add_slide` keeps its look. A blank deck (`pptx_edit.blank_deck`) is
python-pptx's default template stretched from 4:3 to 16:9 (masters and layouts
too) with 맑은 고딕 as the theme's East Asian font. A legacy `.ppt` is refused.

Written decks (`deck_export.to_pptx`, for `pptx_write` and the web slides' download)
use real bullets: `a:buChar` in the accent (`a:buClr`) with a hanging indent
(`marL` and a negative `indent`, `pptx_edit.set_bullet`), the item's own run in
the body colour; a line break inside an item is `a:br`, never a raw newline in a
run (the editor would split it into a paragraph that borrows the bullet's
style); the corner page number is a `slidenum` field, renumbered on every save.
Text a pattern layout cannot fit is written whole and shrunk (`a:normAutofit`);
only the PDF draws an ellipsis. Older decks are healed as they are read (`pptx_edit.healed`: on load for the agent's
edits and when the editor fetches the file): a lone marker run with `buNone` becomes a real
bullet, newlines in runs become breaks, a static corner number becomes the field.
`pptx_doc.deck` takes design tokens and an optional slides template like the web slides' export
(`pptx_doc.project_look` finds them from the conversation's agent or project).

The editor is pptx-react-viewer (crv, Apache-2.0, pinned to 4.74.0, with
`ooxml-core` and `ooxml-ui` pinned as it resolves them), used through its
building blocks (`useViewerBuildingBlocks`, `SlideCanvas`, `SlideThumbnail`)
under the app's own Google-Slides-like chrome in `components/pptx/`: one toolbar
row (undo, zoom, text box, shape, image, table, font, size, B/I/U, colour,
alignment and lists folded into ⋮ when narrow), a thumbnail rail with a slide
menu and drag to reorder, the speaker notes, and a right-click menu on the canvas
(cut, copy, paste, duplicate, layer order, delete). Keys outside the inline text
editor (undo, redo, copy, cut, paste, duplicate, delete, B/I/U) count only after
a press inside the editor, so copying text in the chat is left alone. Inserted
elements are made by the chrome in one undo step each, moved off the spot of an
element already there; a new table's header row is 「항목 1…」 in Korean.
`PptxPanel` is loaded with `React.lazy`: crv and its dependencies are a chunk of
several MB fetched when a deck opens, never part of the main bundle.

The `kchat-crv-patches` Vite plugin changes crv at build time, node_modules
untouched: `useViewerBuildingBlocks()` hands out neither the notes writer its own
viewer uses nor a way to clear its unsaved flag after a save, so both are added
to its result (`updateNotes`, `markClean`); and crv's stylesheet sets its theme
variables (`--font-sans`, `--color-accent`, …) on `:root`, where they would
replace the app's own once a deck opens, so they are set on the editor's own
elements instead. A new crv version that moves either spot fails the build
(`tests/vite-config.test.mjs`). `mtx-decompressor` (MPL-2.0; unpacks fonts
embedded as MTX/EOT in old decks) is aliased to a stub that returns nothing, so
no MPL code ships; such a font falls back to the deck's other fonts. crv's
inline editor shows text that was typed and then formatted twice (「수정됨
수정됨」): when its DOM text differs from the model's at a format change, the
chrome mounts the canvas anew in the same render and puts the selection back; a
format pressed while a Korean syllable is being composed waits for the
composition to end and applies to the composed syllables; a caret formats the
word it stands in. It runs under the app's CSP as it is (no eval, no WebAssembly,
no outside requests: Google Fonts and linked content are off). Korean labels are
the app's i18n; crv's own few canvas words get Korean through
`PptxI18nProvider`. On a phone the deck is read only, its thumbnails a row under
the slide.

### Form files (양식)

A form file is an Office file whose look and slots a document is written into, as opposed to
material that is read. It decides the look of PowerPoint, Word and HWPX output.

- **Profile on upload.** `services/form_profile.py` reads an Office upload once by code and
  stores `files.form_profile`: a PowerPoint's slide size, theme fonts and colours, and every
  layout with its placeholders (classified as `title`, `section`, `content`, `two`,
  `comparison`, `title_only`, `picture` or `blank`); a Word file's heading styles, header and
  footer, fields and section skeleton; an HWPX file's label/value cells. A score and a verdict
  (`form`, `material`, `unsure`) come with it. `.potx`/`.dotx` are forms by kind. A binary
  `.hwp` is never a form: the person is told to save it as HWPX.
- **Role per turn.** `services/form_files.py` decides each attachment's role: the chip's pick
  (`attachmentRoles`), else the verdict moved by the request's words. Forms are taken out of
  the turn's material; the conversation keeps an attached form (`sessions.form_files`) and
  never reads it as material in later turns or in `search_knowledge`. Precedence: attached
  form, the agent's saved form, the project's (`projects.form_files`, saved as `files.role =
  form` copies through `routers/forms.py`, kept out of the file box). `savedFormsOff` takes a
  saved one off for a conversation. The question's attachment list records the forms followed.
- **Writing on a PowerPoint form.** `services/pptx_form.py` draws the deck with the house
  renderer in its bare mode (`deck_export.render_on`: no ornaments, no foot, the form's theme
  accent and ink as tokens) into the form's own package, then puts each slide on the form
  layout its kind maps to (a layout named for its use, `closing`, `agenda`, `numbers` or
  `table` in the profile's `role`, is taken before the generic class, and a slide is kept inside
  the slide edges, `pptx_form.clamp`). Titles, subtitles and plain bullets go into the layout's
  placeholders; tables, charts, patterns and figures are moved and scaled into the layout's body
  area, with the accent and ink rewritten as theme colours (`schemeClr`) and typefaces left to
  the theme. The form's sample slides go and its slide size stays. Used by the PowerPoint tools
  (`pptx_write`, the blank deck a slides conversation starts) and by a web deck's `.pptx`
  download.
- **Writing on a Word form.** `services/docx_form.py` sets the form's body aside and writes the
  report into the emptied form with `report_export.to_docx(form_mode=True)` (the form's styles,
  page and type; a house style the form lacks is added on the form's look, and the house
  headings take the form's heading styles). The form's body comes back: the cover before the
  first chapter is kept with its label cells filled from the report head, and under each form
  chapter (heading 1) the report's chapter of the same name replaces the guidance: the form's
  blank tables are filled in place (head row, 합계 row and the unit of each column kept) and its
  sub-headings (1.1, 1.2) stay over the content of their name; an empty cover value cell becomes
  a 「[ ]」 blank and the cover's guidance goes. A chapter
  the form lacks is added in the form's heading style, its typed number running on. The
  planner takes the form's chapters as the fixed 목차 (`Request.form_outline`), and the writer
  is told the form is not a source (`form_files.writer_note`). `report_office.file_bytes` uses it
  for a report stored as a Word file.
- **Filling a 한글 form.** HWPX cannot be regenerated without losing its tables, so the form
  itself becomes the conversation's HWP document (`sessions._hwpx_form_surface`, when a report is
  asked as an HWP file and the conversation follows a 한글 form; `hwpx_form.start` marks it with
  the form's value cells). The turn runs as the office chat, told to fill it in place with the
  HWP tools and never to replace it (`hwpx_form.rule`). The form's own lines (labels, headings,
  fixed sentences) are kept in the mark (`hwpx_form.snapshot`) and held by code
  (`hwpx_form.Guard`, applied inside `hwpx_edit.apply`): a write goes after a label or heading
  and never over it, a hairline spacer row is never written, `fill_fields` writes several
  labelled values at once, what was left out or put back is returned to the writer, and the
  fixed lines are checked again after the edits. `hwp_doc.describe` counts the slots that now
  hold a value after each save (`hwpx_form.progress`). A turn that saved nothing says so and
  keeps the form's card (`hwpx_form.nothing_saved`); a request to fill an old `.hwp` is answered
  with the way to HWPX without a model call (`form_files.hwp_refusal`).
- **The mark.** `services/form_marks.py` writes what was followed (form, source, layouts used,
  stand-ins) into the file's custom properties (an HWPX: the entry `KloudChat/form.json`); `pptx_doc.describe` puts it on the artifact,
  and the answer's card shows 「양식을 따름」 from it.

### Structural patterns

Structure comes from the request, not from a picked template (`services/patterns.py`).

- **Reports.** The pattern is the purpose format the request names (`doc_formats.detect` on the
  instruction, as `report_stages.bundle_for` decides it; a picked template or an agent's bound
  format still wins). With it come the format's pre-writing questions (`gap_questions.resolve`
  by `doc_format`), process settings (`process_settings.governing_template`), genre rule, and
  its check skills (`render_template_checks` falls back to `patterns.check_skills`). `patterns` is where the turn asks
  for these settings by format.
- **Decks.** The pattern is the deck's purpose, one of 13 (`app/patterns/deck/*.toml`): 심사 발표,
  연구·논문 발표, 수업 과제 발표, 투자 IR, 실적·성과 보고, 프로젝트 결과 보고, 제품·서비스 소개, 제안 발표,
  정책·제도 안내, 교육·온보딩, 강의 자료, 케이스 발표, 회의·브리핑 자료. Each file holds the words that
  name it (Korean and English, English words bounded by `(?<![A-Za-z])`), its rules, its usual
  flow (parts, slide counts, the layouts that fit each), its gap questions, its checks and its
  process (`search = false`: no web search unless the request asks). `patterns.deck_purposes`
  reads the lead sentence, then the whole instruction (pasted material aside); when two match,
  the lower `order` wins (narrower purposes first) and the turn's timeline step
  (`patterns.step`, 「구조: … 패턴」) and the deck's `data.pattern` name both. With no template
  picked, the purpose's note reaches the deck planner (and the office chat writing a PowerPoint
  deck); its questions join the pre-writing card. A form file still decides the look.

### Web slides editor

The web slides (`DeckPanel`, a deck artifact's JSON) work the way the PowerPoint
editor does, so the two read as one family. The header is the document panels'
(`OfficePanel`): title, version, 저장 안 됨, 저장 (and Ctrl+S), 내려받기, 공유, 더보기
(버전 기록, 검토, 발표, 이 장 다시 만들기, 이 장 팩트체크, reuse), width, close; it
is the shared `DocumentChrome` header. Under it is the
chrome the two editors share (`components/pptx/chrome.tsx`, no editor library
with it): one toolbar row (`DeckToolbar`) in the PowerPoint editor's order and
icons (실행 취소, 다시 실행, 확대/축소 | 레이아웃, 이미지, 표, 차트 | 글꼴, 크기 | 굵게,
기울임꼴, 밑줄, 텍스트 색상, 서식 지우기 | 모양, then 검토 and 발표), where the layout,
table and chart stand in the place of the text box and shape (a web slide places
its content by layout) and 모양 holds the deck's look, accent colours, this
slide's accent and several slides at once; the numbered thumbnail rail with the
slide menu (새 슬라이드, 슬라이드 복제, 위로 옮기기, 아래로 옮기기, 슬라이드 삭제), drag to
reorder and 「+ 새 슬라이드」; the speaker notes under the slide. A selected table,
chart, metric set or picture opens its data editor between the slide and the
notes. Keys: Ctrl+S, Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z), Ctrl+D (duplicate the
slide), Delete (the slide in the rail, a selected picture), arrows and
Page/Home/End to step; text boxes keep the browser's own keys.

Hand edits stay in the browser until 저장 (`useDeckDocument`): the deck (slides
and design) is one working copy, so moving between slides never drops an edit,
and undo covers the whole deck (100 steps; typing in one field within a moment
is one step) and starts afresh when another version comes in or one is
restored. 저장 writes the whole deck once, conditional on the version the edits
started from; a lost race shows the conflict notice with 「내 편집 내려받기」 (the
edited deck as text) and 「최신 판 불러오기」. The deck registers with
`lib/officeGuard.ts` like an office document, so closing, opening another
artifact, navigating, reloading and an agent turn in the deck's conversation ask
저장 / 버리기 / 취소 first; steps that change the saved deck on the server (rewrite
a slide, fact check, fix a finding, put in a picture) ask the same. While the
agent works the editor is locked; its version shows when nothing is unsaved.
Review notes are notes on the deck and save at once. A layout change converts
the content into the new layout's fields without cutting it (`relayout.ts`);
what the new layout cannot show (a chart on a text layout, text the conversion
left out) is kept on the slide as `held`, said under the slide with
「다음 장으로 옮기기」, and taken back by a later layout change. On a phone the
deck reads only, with the PowerPoint editor's note and layout (thumbnails in a
row under the slide).
### Office files in the file box

The file box (`FilesPage`, `components/files/FilePreview.tsx`) previews an
`.hwp`/`.hwpx`, `.docx` or `.pptx` as its editor draws it, read only, rather than
as extracted text. Each preview is its own lazy chunk, fetched only when a file
of that kind is opened: `DocxPreview` mounts open-docx-editor with
`readOnly` (pages, no ribbon), `PptxPreview` mounts `SlidesEditor` with
`canEdit` off (canvas and thumbnails, no toolbar), and `HwpPreview` runs
rhwp-studio in an unseen iframe and asks it for each page as SVG
(`getPageSvg`), laying out a sheet for every page and drawing each as it is scrolled
to (`components/hwp/lazyPages.ts`). Those pages are put into the app's own DOM,
so the app's Korean fonts apply; `safePage` (`officePreview.ts`) first drops
scripts, `foreignObject`, animation, event handlers and any link that is not to
the drawing's own parts or a `data:image/` picture. If an editor cannot draw a
file, the preview falls back to its extracted text with a short note. Legacy
`.doc`/`.ppt` are text previews.

「편집」 never writes the file: it starts a chat and creates an office artifact
from the file (`POST /artifacts/{hwp|docx|pptx}` with `fileId`), titled from the
file name, and opens it in the editor beside the chat, where versions, saves
and the agent's tools work as for any office document. The artifact remembers
its file in `data.sourceFileId` (`office_doc.with_source`); saves carry it to
every version and a restore keeps it.
`GET /files/{id}/documents` lists the documents opened from a file, newest
first, and the preview offers the newest whose chat still exists
(「이어서 편집」) before 「새 사본으로 편집」 makes another. On a phone the
preview, like the editors, only reads. `POST /files/{id}/open-as-document` answers 410
and names `POST /artifacts/hwp` with `fileId`.

### Chat edits of the document open beside the chat

All five document formats (웹 보고서, 한글, Word, 웹 슬라이드, PowerPoint) take chat edits
the same way.

**Which document.** Every turn carries the document open in the panel,
`SendMessage.openArtifact` `{id, version}` (`useStore.openTarget`; a local draft is not
sent). The server trusts it only when it is the person's and this conversation's
(`_opened_document`). A report or slides conversation then makes it its document
(`session.artifact_id`), so a revision lands on what the person sees. The office tools
pick by `office_doc.target`: the document this turn already read or wrote, else the open
one, else the conversation's only one of the kind; with several and none open the read
tool says so and the model asks which (`*_read` takes `document`, a title). The
server's pre-read and its chapter count (「3장」) read the same document.

**Versions.** Every writer moves a document only from the version it read. JSON
documents (web report, web deck) go through `artifact_versions.advance`, the
conditional UPDATE the hand save (`workspace._next_version`) also uses. A chat revision
pins the version `revise-load` read (`turn.base`); at `revise-save`
(`save_stages.store_revision`), if a hand save landed meanwhile, the parts the revision
changed are put onto the newer version when nobody touched them there
(`artifact_versions.merge`, by part id, title and other keys as wholes); otherwise the
revision is kept apart as a draft document 「… (수정안)」 beside the person's version and
the reply says so. A whole rewrite (re-plan, regeneration, a report written again as
HWP or Word) pins the version its turn started from (`_document_base`) and keeps its
result as a draft on a conflict too. Office files move the same way in
`office_doc.save`. 「되돌려 줘」 is a version restore, never a rewrite: `revise.asks_undo`
routes it to `_undo_document` (web) and the `*_restore` tools bring an earlier office
version back as the newest (conditional, history kept).

**Office tools.** `*_read` (outline with ids), `*_edit` (ops, all or none, against the
ids read), `*_write` (a new document beside the others; `replace: true` rewrites the
open one only when asked and only after a read; a blank open document is written into),
`*_restore`. The edit ops are text (`replace_text` keeps the runs around a change in
both HWP and Word, `fill_blank` across runs, `set_cell` line by line keeping a cell's
other paragraphs and nested tables, `whole: true` to empty it), structure
(`insert_paragraph`, `delete_paragraph`, `move_paragraphs` for a chapter with its body,
tables and their rows and columns, `merge_cells`, `split_cell`, `insert_table`), format
(`set_style`, `format_text` on a span, `align`), pictures (`insert_picture`,
`replace_picture` from an image the person attached or a tool made in the
conversation, found by `tools/office._pictures`) and `page_setup` (orientation,
margins, header, footer, page numbers) — `docx_ops.py`, `hwpx_ops.py`. In HWPX a shape is
never changed in place, since paragraphs share its id: the changed copy, and every house
shape a new table or picture brings, is defined in the file's own header under a fresh
id or an equal definition there is reused (`hwpx_edit._define`, `_import_shapes`).
PowerPoint adds `insert_table` (a real `a:tbl`, in place of a body box), table columns,
pictures and `change_layout` (`pptx_ops.py`); its outline counts slides in order
(「5장」 is `s5`, not a printed page number). The tool descriptions say what cannot be
done (charts, text boxes, footnotes, a table of contents). After the turn
`office_claims.note` holds the reply to what the tools saved: a claimed change with no
saved version, or a claimed failure with one, gets a sentence saying what is so.

**Web report and web deck edits** work like the office files. A report or slides
conversation with a finished document runs an edit turn as its chat
(`_web_edit_target`) with `report_read`/`report_edit` or `deck_read`/`deck_edit`
(`tools/web_doc.py`, ops in `web_doc_edit.py`): the outline numbers sections and slides in
order, and the ops change only what they name. A report takes span replacement, blank
filling, one section rewritten, sections renamed, added, deleted or moved, Markdown table
cells, rows and columns, the title, the head fields and the theme. A deck takes the same plus a
slide's title, subtitle, body or speaker notes alone (a notes request changes only
`slides[i].notes`), its list items, a real table, slide duplication and its design
(`design.visualStyle`, `accent`, `font`, the slides surface's own fields). The tool saves
from the version it read, as a hand save does. The writers keep what is not an edit in
place: a new document, a new structure or a whole rewrite asked in so many words, an undo,
a file hand-over, an answer to a waiting card. A shortening (「절반으로」, 「줄여」) that would
drop a part's table or list whole, or cut far more than the share asked, is refused in both
paths (`revise.shortening_loss`). Counts asked are held: 「5장 정도」 aims at 5, and a reply
says when the slides or sections written differ from the count asked
(`deck.count_note`, `save_stages.section_count_note`).

**Web report and web deck revisions by the writers** run the `revise` flow on the loaded
parts. By hand, with no model: a part moved (「위험 요인 장을 3번 장 뒤로」, `revise.part_move`;
numbers name parts as titles do), two swapped, a theme colour (`revise.theme_accent`
→ `design.accent`), the head fields 작성일·작성 부서·작성자 (`revise.title_block_fields`
→ `titleBlock`). A question about the document or the conversation is answered from
them (`revise_stages.answer_about`), not refused. A part the edit was not asked to
touch that a rewrite left without figures it stated is put back as it was
(`save_stages.keep_untouched`), unless the new figures are the ones the person typed.

**What the person set holds.** Standing rules from the conversation
(`context.standing_rules`: 「앞으로 …」 directives over every user turn, and the running
memory's pinned items) reach every part rewritten (`revise_stages.standing_note`) and
every office `*_read`. In a revision's material, attachments spend the budget newest
first, and a file sent again under the same name replaces the older one.

**Look.** HWP and Word documents written by the tools, blank ones made in a project and
reports written as HWP take the project's theme and the conversation's (else the
project's default) report template (`office_look`): Word builds on the template's
`template.docx`; HWPX takes what it can carry from it (page margins, body face, heading
colour), the theme's colour first. Reports written as Word number their lists with real
numbering definitions, put the 목차 in a TOC field whose result is the headings listed
(Word refreshes it on open; an editor that runs no field shows the list), and keep each
chart a native, editable chart with its picture in an `mc:AlternateContent` Choice that
only the in-app editor takes. HWPX numbers its lists with `hh:numbering`; it has no chart
element, so its charts stay pictures.

### Unsaved office edits, phones, sharing

Hand edits in an office editor live only in the browser until 저장, so nothing
that would drop them does so unasked. `lib/officeGuard.ts` holds every open
document's guard (its unsaved state, a fresh read of it, the question, a plain
save). Closing the panel or opening another artifact (`openArtifact`), a new
conversation from the chat (`newSession`), another page (`UnsavedLinkGuard`,
a `useBlocker` on a data router whose one catch-all route renders `<Routes>`; it covers links, router moves and browser Back, and after the office
question asks 머물기 / 버리고 옮기기 for any other unsaved form, so one move asks
each once), the new-version bar's reload and the automatic reload after
a deploy's missing chunk (`vite:preloadError` in `main.tsx`) all ask first:
저장, 버리기 or 취소, and the move waits for the answer. A tab close or reload
gets the browser's own prompt (`useMarkUnsaved`, which reads the state as the
page goes, so an answer just given is not asked again). The idle sign-out asks
nobody: it saves the open documents as a version first (bounded to 15 s), then
signs out. Before an agent turn the same question is asked, and "discard" sends
only once the saved file is loaded back.

A save clears the unsaved mark only for what it exported. The panel counts the
edits the editor reports (`noteEdit`: Word's `onChange`, every change to the
deck in PowerPoint), and HWP has its own count (`getDocumentState().changeSeq`);
if the count moved while the save ran, the new version is saved but the mark
stays. HWP's unsaved state is polled, so a send or a leave asks the studio
anew, and a key or click inside the studio's frame (same origin) asks soon
after, counts as activity for the idle sign-out (`noteActivity`), and Ctrl+S
there is the header's 저장.

At phone width every office editor reads only; HWP shows the studio's pages as
SVG (as the file-box preview does) over the studio, which stays mounted but
`inert`. A window narrowed over unsaved edits keeps the editor and 저장 until
they are saved or dropped. A studio that does not start within 60 s, or a
first load that failed, is offered again (다시 시도).

Office panels share like the other artifacts (「이 아티팩트만 공유」). The shared
page draws the document with the same read-only views as the file box
(`components/files/OfficeView.tsx`), from `GET /shared/{token}/document` — the
current version's file, under the same rules as the share's media route, not
counted as a visit — with a download of that file; if it cannot be drawn, its
extracted text. The shared body leaves out `sourceFileId` (an id of the
owner's). The artifacts page draws an office document the same way, filters by
한글/Word/PowerPoint, and opens it in the editor beside its chat; a document
with no chat joins a new one (`POST /artifacts/{id}/{kind}/session`, refused
with 409 for a document whose chat exists). An attachment's 「Word로 열기」 (and
the HWP and PowerPoint ones) reopens the copy made from it in the same chat
(`GET /files/{id}/documents`) and offers 「새 사본으로 열기」 for another. The
version summary of a hand save is in the UI language.

### Sharing

In the `shares` table **the token is the permission**: 43 random characters,
revocable, and the only thing the public route consults.

Two scopes. `workspace` asks whether the reader is signed in and never asks
who they are — there is no team, group or invite anywhere in this product, so
it means *anyone with an account on this instance* and the picker says so.
`link` opens to anyone holding the URL, which is the case where the recipient
has no account here.

Every open is recorded in `share_views`. A signed-in reader is named — name and
email copied rather than joined, so the row still reads after the account is
renamed or deleted. An anonymous one is recorded by address and browser, which
is all the server ever learns about them. Repeat opens inside an hour are one
visit, so a reader refreshing a long report does not bury everyone else.

A shared session carries the artifact it produced alongside its messages —
resolved through the session's own `artifact_id`, so nothing else in the
owner's workspace becomes reachable. The response contains **only the shared
thing** — no owner name, no project, no neighbouring artifacts, no walkable
ids. A revoked token and an unknown token
give the same 404, because distinguishing them discloses somebody else's
account.

The router sits **above** the auth gate, or a share link would land on the
sign-in screen.

### Publishing agents

An agent can be published as a chatbot (**Published chatbots**: `routers/publications.py`,
`services/publishing.py`, the owner's screen at `/customize/publications/:id`, the
members' list at `/bots`). A bot opens to the members of this instance as soon as it is
published. Opening it to people without an account — by link (`link`) or listed on the
public page `/b` (`external`) — waits for an administrator (`/admin/publications`), who
approves the audience and the version together; on a bot open outside, each new version
waits for the same review while the approved one keeps answering.

**A bot answers as its owner.** A visitor's conversation is a `sessions` row owned by the
publisher and marked with the publication, and the ordinary turn runs it, so credits,
policy, masking and retention are the owner's. The turn is constrained on the session
rather than on the caller: the model is a strict-local one fixed with the version (refused
when none exists); the agent is the version's hidden copy (`Agent.snapshot_of`) with its
knowledge text; the tools are **Search knowledge** and **Calculate** only, and none of the owner's memories,
personalisation, projects, connectors, skills or other files reach the turn. Limits apply
per visitor, per address, per bot and per day, plus the owner's daily credit cap and a
concurrency cap per bot and across the instance, with a short queue before 429. A visitor
without an account is a signed browser cookie and nothing more.

**What the owner sees.** Counts, frequent topics, unanswered questions (personal details
masked) and 👍👎 — not conversations, except one a visitor sent with **Send to its
maker** or, with **Conversation access** on, those started after the visitor's consent, both masked
when read. An administrator may read a conversation for an abuse review, with a recorded
reason.

**Channels.** A bot open outside reaches further through channels the owner asks for and an
administrator opens with the audience (`Publication.channels`): an embed on the owner's sites
(`/embed/b/<slug>`; `GET /embed/frame-policy` gives nginx the `frame-ancestors` list for
that bot, every other page keeps `'self'`), an OpenAI-compatible API (`/bots/v1/models`,
`/bots/v1/chat/completions`, the model is the bot's address) and an MCP server
(`/bots/mcp/{slug}`, streamable HTTP, one tool `ask`), both called with a key the owner
issued for that bot (`/publications/{id}/keys`; `routers/bot_api.py`). Earlier `messages`
an API caller sends are the caller's word, quoted inside the question, never replayed as
the bot's own turns; `X-KloudChat-Conversation` continues a conversation the key started.
An answer goes out only once the turn settled normally, so a failure is an error, never a
partial answer. Closing the outside audience closes every channel with it.

### The workspace store

Agents and skills live in one account — the oldest administrator's, resolved by
`starter.catalog_owner_id` and not stored — shared to the workspace, and taken from the store tabs of **Customize** (`/customize`) a copy at a time.
`POST /agents/{id}/install` and `POST /skills/{id}/install` do the copying, and
both are idempotent: pressing **Import** twice returns the row you already have
rather than a second one with the same name.

The shipped catalogue is `starter._SKILLS` plus `starter._AGENTS`, the
agents read from `app/agents/<key>.toml` (prompt, guide, starters, kinds,
bindings, an explicit tool list — `[]` denies every tool — skill keys and the bundles
they run). No agent is offered: every shipped agent file is withdrawn or retired (below,
"Skills instead of shipped agents"), and the work they described ships as skills.

**Listed, hidden, retired.** `listed: false` (a SKILL.md, a `template.toml`, a
`starter._DESIGNS` entry) or a shared MCP server's `listed` keeps a shipped part out
of the lists people pick from — the skill hub and store, the template and theme pickers, the
MCP server store — while it still runs by name, in bundles, in the editors that
name it, and in the conversations and documents that already use it. Unlike `draft`, it does
not gate `run_skill_script`. The wire carries `listed`; administrators see hidden rows behind
**Show hidden**. Unlisted: the skills `copy-check`, `generic-language-killer`,
`answer-hold` and `source-check`; the deck templates `deck-signal`,
`deck-proposal`, `deck-editorial`, `deck-lecture`, `deck-defense` and `deck-case`; the theme
「발표용 청록」.
**Finish**'s export item names `by_format`: a document format listed there exports HWPX when
ticked without a format, anything else its first `formats` (`bundles.export_default`).
An agent file with `retired = true` (`career-docs`, `client-proposal`) is never seeded afresh;
its rows and every copy get `agents.retired`, are listed nowhere, refuse install and new
conversations (410 `agent_retired`), and keep their conversations and documents. Their
templates are in `design_templates.RETIRED_KEPT` and their formats in `doc_formats.RETIRED`.
A prompt's `[채팅에서]` / `[보고서에서]` / `[슬라이드에서]` lines ("in chat", "in reports",
"in slides") open parts read on that surface only (`workspace_context.surface_prompt`);
`[보고서·슬라이드에서]` names two. A skill body uses the same lines (`surface_body`), so one
skill can tell a chat answer to run its script and a report writer to copy the server's
pre-pass block.
`seed_catalog` syncs the catalogue on the owner's sign-in and deletes the
owner's rows whose `catalog_key` is not shipped at all (a key still in a file with
`retired = true` is kept, hidden); copies people installed are theirs and stay. Sharing is
set on the first run only, so a skill the administrator made private stays private. `former_names` in an agent file renames untouched copies on sync.

Shipped skills are ordinary skills of that account (`source = personal`; copies are
`workspace` — there is no built-in source): the owner
can edit or unshare them in **Customize** like any skill of their own (a deleted one is seeded
again on the next sync), and the store
shows them under the owner's name. `skills.package_hash` is the owner row's content as last
synced; while the row still hashes to it the sync rewrites it from its package, and copies
whose content equals the owner row's previous content follow along; an edited owner row
(hash differs, or `""` for an adopted per-account seed) or an edited copy is left alone.
A copy without `origin_id` is linked to the owner row by key. Check skills run by key
from the packages (`skill_specs.resolve`), so an agent's or template's checks work for
someone who added nothing; a template's text skills come from the owner's shared row only
(`sessions._template_skills`), never from someone's shared copy.

**Sharing means copyable, not usable in place.** A skill is only ever resolved
out of its owner's account (`workspace_context._resolve_skills`), so a shared
row is inert until copied. This is why the store is its own endpoint and not a
wider `GET /skills`: mixed into the composer's list, a shared skill would be a
picker entry that fails at the moment it is used.

Installing an agent installs the shared skills it names and rewrites its
allow-list against the copies; rows in the author's account resolve to nothing
here. An allow-list emptied by the copy becomes `null` (inherit) rather than
`[]`, which is a hard deny that would refuse every skill the new owner ever
switched on. Knowledge shelves never travel: those are the author's documents,
and copying their agent is not a grant over their files.

`official` is computed per request from the publisher's current role rather
than stamped on the row.

`_copy_of` matches an account's existing copies by catalogue key (for agents also by
slug), so seeded copies count as installed.

### Skills instead of shipped agents

There are no built-in agents. Each piece of work a shipped agent file describes is an admin-owned
shared skill (`app/skills/<key>/`, seeded and followed like every package), which a person
switches on in a conversation or builds into an agent of their own. Five skill fields carry
what an agent would — the same ones a person sets in the skill form (**Server checks**,
서버가 돌리는 점검) or an uploaded SKILL.md, stored on `skills.server_fields` and validated in
`skill_specs`:

| field | what it brings to a turn the skill is on | an agent's equivalent |
|---|---|---|
| `template` | the template a report/deck writes into when none is picked (the composer shows it as soon as the skill is picked; otherwise the server sets it on the session) | `render_template_id` (with its `doc_format`, questions, `[process]`, checks) |
| `checks` | check skills attached to the turn (`gap-check`, `grounding-check`, `generic-language-killer`, …) | `check_skills` (compiled from bundles) |
| `check_options` | trace strictness (`tool_numbers`, `titles`) | `check_options` |
| `questions` | missing-information questions added to the template's | `gap_questions` |
| `process` | process settings (`search`, `draws`, …) under the agent's own | `process_settings` |

`services/skill_turn.py` merges what the turn's skills bring — the picked or standing skills,
then the agent's always-on ones: the first skill's template and settings win, checks and
questions add up, and the agent's own fields sit on top. Skills stay on for a report or deck
conversation as they do for chat (`sessions._STANDING_KINDS`), so the plan's approval, which
carries no picks, writes with them; a document asked for in chat takes the skills that are on
along to the new report or deck (those that need no tool, the rest named in a notice). The
composer lists the official (administrator-shared)
skills a person has not added under **Official skills**; picking one adds the copy and
switches it on.

**An agent is built from skills.** `agents.active_skill_ids` (**Skills kept on**, at most
six) are on in every turn of the agent's conversations, apart from the three
a person picks per turn; `skill_ids` stays the allow-list of what the composer may add. They
are resolved where the agent runs (`workspace_context.agent_skill_rows`): the agent owner's
rows, the person's own, or shared ones — a skill the owner built into a shared agent travels
with it the way its prompt does, while a hand-picked skill is still only ever the person's own
copy. Installing such an agent copies the shared skills it is built from. A published bot never
runs skills; its version freezes the agent's prompt with each built-from skill's instructions
appended and adds the skills' `post` checks (`publishing.built_from`). A composed pipeline's
agent step gets the same prompt.

**Language, web search and memory are the agent's own settings** (form **Advanced**). They
are plain agent fields, not catalogue cases, so a person builds the same thing a shipped
agent would:

- `agents.output_language` (`auto`, `ko`, `en`, `ja`, `zh`, or a language name typed in,
  cleaned to one line by `output_language.normalize`). `workspace_context.assemble` adds it as
  the last trusted block (`agent.language`, `output_language.rule`) on chat, report and slides
  alike, and `context.build_messages` / `build_document_messages` then skip their own
  request-language rule. A turn that names a language itself (「영어로」, "in Korean",
  `output_language.asks_language`) drops the block for that turn; a standing request for
  English earlier in the chat still wins. Report and slides turns also carry it as a request
  condition (「Write the whole document in …」), which their stage prompts read with the
  request. A published version copies the field, so the bot answers in it.
- `agents.web_search_default` (`auto`, `on`, `off`). The composer opens a conversation with the
  agent on this setting and the person changes it per turn. `SendMessage.web_search` is
  optional: a turn that sends none (API, MCP) takes the agent's default, and without an agent
  stays off. With `off`, current-fact forcing (`freshness`, `exact_facts`) does not
  start a search, since it only forces one when search is on; asking for a search in the words
  still searches.
- `agents.use_memory` (default true). When false, `assemble` reads no memory block for the
  agent's turns, the turn writes no automatic memory (`_enrich_memory` is not asked), and
  `share_note`, which writes a memory row, is not offered.

**Tool skills on documents.** A report or deck writer runs no tools. A skill whose work the
server does around the writing is accepted there (`skill_specs.runs_on_server`: a check skill
with a `stage`, or `process` with `prepass`, `checks`, `stages` or `code_calc`, like
`calc-check` and `date-calc`); its body reaches the writer without the tool
steps, and its pre-pass, checks and code calculation join the turn through `skill_turn`. A
hand-picked tool skill without such a process is refused (`skill_tools_unavailable`), and the
composer greys it out with the same rule (`lib/serverSkills.ts`).

| withdrawn agent | skill (`app/skills/`) | template | checks | own questions / settings | to rebuild the agent |
|---|---|---|---|---|---|
| minutes `minutes` | meeting minutes `minutes-writing` (report) | `doc-minutes` | gap-check, grounding-check, generic-language-killer | — (the template's: meeting date, attendees, due dates; date pre-pass from the meeting date) | keep on `minutes-writing` · theme document serif · screen Report |
| compare and decide `comparison-report` | compare-and-decide report `option-comparison` (report) | `doc-comparison` | same | — (the template's: prices, must-haves; cost and score pre-pass) | keep on `option-comparison` |
| bullet-style briefing `gaejo-report` | bullet-style briefing `gaejo-briefing` (report) | `doc-gaejo-report` | same | — (the template's: addressee, reporter; fixed outline, one page) | keep on `gaejo-briefing` · theme document serif |
| official letter and draft `document` (draft) | official document draft `document-drafting` (report) | none (format from the words) | same | `process`: no search, pictures only when asked | keep on `document-drafting` |
| extract and tally `data-analysis` | extract and tally `data-tabulation` (chat, needs `run_code`) | — | gap-check, grounding-check; `tool_numbers` | deadline basis, period split, denominator | keep on `data-tabulation` · tools file work, chart, calculation |
| regulations and procedures `regulation-guide` | — (withdrawn; its own prompt) | — | grounding-check | — | keep on `date-calc` · knowledge (the regulation files) · department and contact · the publish-check bundle (hold ungrounded answers, mask PII; the hold reads the shelf's hits and the files attached to the conversation) |
| presentation `slides` (draft) | presentation deck `deck-writing` (slides) | — (deck flow) | — | — | keep on `deck-writing` · screen Slides |

The skill's body is the agent file's prompt; the agent file's bundles compile to the checks
above (the prepare bundle's date and amount pre-passes and the review bundle's format checks
and repair run inside the report flow for every template that sets them). What a skill does
not carry is the look — the theme stays a choice of the person or the agent — and the agent file's
0.3 temperature (a turn without an agent writes at 0.4).

Two skills tell the model when and how to use the default connectors: YouTube transcript and
summary `youtube-summary` (needs `mcp__youtube__transcript`, which heads the transcript with the
video's title, channel, length and upload date; summarises from the transcript
only and marks doubtful words) and deep research `deep-research` (needs
`mcp__deep-research__quick_research`; the lightest research tool that answers, said to take
minutes). The time connector joins `date-calc`: with no date in the material, today comes from
`mcp__time__get_current_time`. Connector tool panels show a Korean line per shipped tool
(`tools/blurbs.py`), not the server's model-facing docstring, and a connector asks for
credentials only for env values the person enters — `{{...}}` identity placeholders and
`${VAR}` values the server fills (YouTube's `WHISPER_URL`) are not theirs. An administrator's
shared skill reads **By: Official** in the hub, as their agents do; a copy reads **Original:
Official**, and **Edited by me · Original: …** once changed.

Four more work skills have no agent file behind them: translation `translation` (chat,
report), press release `press-release` (chat, report; grounding-check and
generic-language-killer, no search), Excel file `xlsx-build` and chart `chart-make`. Excel
file (chat, needs `run_skill_script`) builds a workbook with `scripts/build.py` from `sheets` given in the
args or from a chat file (a pasted table, an attachment, or a CSV `run_code` saved): computed
columns as `{column}` formulas per row, a `SUM` total row where a formula column repeats its
formula (a rate stays total ÷ total), number formats, and an optional native chart. LibreOffice
in the sandbox caches the formula values, which the summary returns for the answer to quote;
the file is a step output, shown under the answer as a download. Chart `chart-make` (chat,
needs `create_chart`) tells the model when and how to draw a chart artifact. Translation and
press release are instructions only; a press release written as a report gets the `press`
format's head when the request names it. The meeting agenda
template `doc-agenda` binds the `agenda` format (the minutes' head) with its own genre rule,
missing-information questions (meeting date, attendees) and date pre-pass.

**Withdrawn.** An agent file with `withdrawn = true` stays in the catalogue: the sync keeps
its owner row and untouched copies follow it, `bases.get` still resolves it for agents built
on it as a base, and existing conversations and published bots keep running. It is left out
of `GET /agents`, the base picker (`bases.bases()` without `withdrawn=True`) and install (410
`agent_retired`). A member's own copy (which carries `origin_id`) stays listed.

### Coding agents

`/llm/*` forwards requests to LiteLLM unchanged, authenticating **by key, not
by session**: the incoming `Authorization` header goes straight upstream and
LiteLLM decides whether it is valid. An issued key *is* a virtual key, so spend
and the model allow-list follow it. The budget attaches to the account, so
extra keys split one allowance rather than multiplying it.

The master key never travels this path, and a call without credentials gets a
401 from upstream. Responses are relayed chunk by chunk, so streaming passes
through intact.

**Super Agent as a model.** `POST /llm/v1/chat/completions` with `model`
`kloudchat/super-agent` or `kloudchat/super-agent-strict-local` is answered by the app
(`services/super_api.py`, one chat turn per request; reports and slides are not offered).
The key's owner comes from the gateway's `/key/info` (the owner in the key's metadata, kept
60 seconds under the key's hash), so no stored secret is decrypted and an unknown key gets
the gateway's own 401. The whole `messages` list is routed by the same table, classifier,
picks, efforts and `model_health` skips as a turn in the app, as a chat turn whatever it
says about documents; `tools` limit the pick to models that call functions; personal data
moves the ordinary variant to the strict-local column, or masks it where none serves; the
strict-local variant never leaves (`503 strict_local_unavailable`). The request then goes
on under the caller's own key with the chosen model and its reasoning fields, once (one
retry on another model if the gateway answers 5xx before any byte), so budget, allow-list
and credits are the gateway's and nothing is charged twice. The answer keeps the requested
`model` and adds `kloudchat.route` (`model`, `row`, `effort`, `strictLocal`, `fallback`);
a stream carries it once on the usage chunk (or the finishing one) and every response has
the `X-KloudChat-Route` header. `GET /llm/v1/models` lists the two ids (`owned_by:
kloudchat`) for accounts that can use them. The switch is `system_settings`
`api_super_agent_enabled` (default on; 설정 › 시스템 › 기능); off, both ids answer
`404 model_not_found`.

Calls made with a member's key reach the proxy without passing through a
turn, so `api_key_usage.watch` reads them back: every five minutes it pulls each
issued key's rows (revoked keys included) from LiteLLM's `/spend/logs/v2`,
filtered by the key's hash, and writes one ledger row per successful request —
reason `api.key`, surface `api`, the model, prompt plus completion tokens as
`units`, and credits at this instance's rates (`charge_for_tokens`, so free
models record the request at zero). The request id goes in
`credit_ledger.external_ref` under a unique index, and each
pass re-reads from 30 minutes before the newest such row, so a request is never
written twice; the first pass starts at the month's cycle start. Credits land
in `credits_used` only for calls in the current cycle. One replica runs a pass
at a time (`pg_try_advisory_xact_lock`). The usage screens, the admin user list
and the balance therefore include API key calls; `/usage` also still lists each
key's lifetime spend as the proxy reports it.

### Administrator controls

**Enabled surfaces** turn screens off in the UI *and* make the server refuse
sessions of that kind — hiding alone leaves the feature on for anyone who types
the URL. Chat cannot be disabled.

**Accounts** can be corrected from the users screen: name and address
(`PATCH /admin/users/{id}`, an address in use is refused) and a password reset
(`POST /admin/users/{id}/password`) that hashes the new password and deletes
every refresh token the account holds, so a person who lost their password gets
back in and any session that was still open is ended. Both are audited. The
model-restriction picker reads `GET /admin/models`, the whole catalogue, rather
than the administrator's own (possibly restricted) list. Reissuing a LiteLLM key
asks first, since the old key is revoked at once.

**Model roles** are product choices stored in `system_settings` (`models.*`,
`thinking.*`; `services/settings_store.py`) and edited under System › Model: each surface's
default model, the title·memory·summary model, the `run_code` model and its fallback, and
whether self-hosted and external models think. The outline model and the Auto classifier
sit in the `governance` row (System › Routing). The `.env` values of the same names only seed
a fresh install; deployment facts — addresses, secrets, which served models think unless
told not to — stay in `.env` (see [configuration.md](configuration.md)). The defaults use
the two self-hosted models: `local/qwen3.8-flash` writes chat answers, `local/qwen3.8-27b`
writes reports, decks and `run_code` programs; Flash also plans outlines, judges and checks,
classifies for Auto, writes titles, memories and summaries, and (as
`strict-local/qwen3.8-flash`) reads scanned pages. See
[Which model does what](configuration.md#which-model-does-what).

**Branding** logos are served without authentication, because the sign-in
screen renders one before anybody is authenticated. The filename carries a
content hash, so replacing the logo changes its URL. SVG is rejected: it can
carry script, and that file loads for every visitor.

**Feedback & issues** (`routers/issues.py`, `/issues`, `/admin/issues`): a person reports a fault
or asks for something, optionally attaching a read-only copy of a conversation
(`services/issue_snapshot.py`; a strict-local conversation needs an explicit
confirmation); administrators work the queue with status, assignee and replies, and
every open of an attached conversation is an audit event. `GET /issues/page` pages a
person's own reports (`cursor`, `limit`, `unread=true` for those the badge counts) with
`total` and the `unread` count; `/issues` stays the newest 200.

**Performance monitoring** (`/admin/performance`, `services/perf/`) samples each replica,
the host, the GPU nodes and LiteLLM's request log once a minute into `metric_samples`;
its settings are in [configuration.md](configuration.md#performance-monitoring).
The screen is a one-screen dashboard (verdicts, key figures, sortable GPU node and model
server tables) over the same overview; trends and tables sit on detail screens
(`/admin/performance/host`, `/models`, `/gpu?node=`, `/vllm?target=`, `apps/web/src/pages/perf/`).

### Reading scanned pages (OCR)

An upload whose pages carry no text layer is stored at once with what could be read
without OCR (`files.ocr_state = "queued"`), and a background loop on every API replica
reads its pages (`services/ocr_jobs.py`). The claim is a row lock taken with
`SKIP LOCKED`, and every finished page is committed (`file_pages`) with a fresh claim
time, so a replica that stops mid-file leaves a stale claim the next one resumes after
the last committed page.

Two readers, both inside the deployment (`services/ocr.py`): the engine (ocr-shim behind
`/tools/ocr`, CPU), which returns every line with a score and a box, and a vision model
through LiteLLM that must be strict-local and declare vision — checked against an
uncached catalogue right before each page is sent, or the page is not sent. The reading
mode (`both` by default, `engine`, `vision`), the vision model (**Auto** takes
`OCR_VISION_MODEL`, `strict-local/qwen3.8-flash`), on/off and a page limit are set under
System › Features › Text from scans (OCR). With both, the engine reads every page and a page it scored
low or that holds a ruled table is read again by the vision model, whose text is kept;
when the two readings differ in words or in any number the page is marked for review.
Pages read are recorded on the ledger as units.
### Understanding videos and recordings

People upload lecture recordings and screen recordings to a project or a conversation and
ask about them. An answer has to use what was **said** and what was **shown** (slide text,
code, diagrams), say **when**, and let the reader jump there. Everything runs on the server
after the upload, so the tab can close, and every model it calls is inside the deployment
whatever the chat model is.

**Upload in pieces** (`routers/media.py`). Audio and video go up through `POST /uploads`,
`PUT /uploads/{id}?offset=` (one piece of `UPLOAD_PIECE_MB`, 8 MB, per request) and
`POST /uploads/{id}/finish`, to their own ceiling `MAX_VIDEO_UPLOAD_MB` (500 MB). Each piece is
appended to a partial file under the storage root, so no request body is ever larger than a
piece: the API replicas never hold a video in memory, and nginx's body limit stays at
`MAX_UPLOAD_MB`. A piece already received (a retry after a dropped answer) is acknowledged,
one ahead of what arrived is refused with the offset to resume from, and the browser
(`mediaApi.upload`) retries a piece four times and then asks where the server stands. The
browser reads the video's length first and asks `GET /media/estimate`: a video over
`VIDEO_MAX_MINUTES` is refused before a byte is sent, and one over ten minutes says how long
reading it will take (and its credits, for a priced vision model). `finish` checks the bytes
are audio/video, moves the partial file into place and queues the job. Pieced uploads left
for a day are removed by the job loop.

**The media worker** (`apps/media`, the `media` service). ffmpeg is LGPL/GPL, so it is
never linked or bundled into the app: it runs as Debian's unmodified package in a container
of its own, behind a standard-library HTTP API — `/probe` (ffprobe), `/audio` (a 16 kHz mono
FLAC piece), `/scenes` (the times the picture changes, below) and `/frame` (one JPEG).

*Finding changes.* The worker compares edge maps — ffmpeg's `edgedetect` at 320×180, averaged
to 80×45 — because text and slide shapes are crisp edges while a speaker's motion is mostly
soft ones: a frame is a change when more than 4 % of the cells moved past a step against the
last kept frame, and the frame kept is the first after the change settles (a transition, a
build). How the frames are read depends on the file: a picture at least 900 px tall whose
keyframes come at least every 6 s (`MEDIA_KEYFRAME_*`) is read at its keyframes only, one
seek each, `MEDIA_SCAN_PARALLEL` side by side; anything else is decoded at 1 fps in that many
segments side by side. A stretch over 90 s without a change still gets a frame.

The worker reads uploads through a read-only mount, so a request names a storage key and
never carries the video; it holds no secrets, joins only the internal `media` network, runs
ffmpeg at low priority with `MEDIA_JOBS` runs at once, and compose caps it at `KCHAT_MEDIA_CPUS`
and `KCHAT_MEDIA_MEMORY`.

**The job** (`services/video_jobs.py`). Claimed like OCR jobs — `SKIP LOCKED`, a claim token,
a ten-minute lease another replica takes over — and taken through:

1. *probe* — duration and streams; a file with neither, or longer than `VIDEO_MAX_MINUTES`
   (240), fails at once with the reason;
2. *frames* — the worker's candidate times (twice the cap), each frame fetched once; one that
   is the same picture as the last kept frame is dropped (`video_read.same_picture`: a 64-bit
   difference hash within 6 bits *and* at most 1.5 % of a 640×360 edge map moved — the hash
   alone calls slides of one template with different text the same); the rest thinned evenly to
   `VIDEO_FRAMES_PER_MINUTE` × minutes (at most `VIDEO_MAX_FRAMES`); each kept frame and a
   320 px thumbnail are stored beside the video (`<key>.frames/`, removed with it);
3. *reading* — speech and pictures side by side. Speech: pieces of 580 s overlapping by 5 s
   to the internal Whisper (`transcribe.segments_of`, `verbose_json` with segment timings; the
   first piece's language holds for the rest). Pictures: each frame to the strict-local vision
   model the OCR settings choose (`video_read`; checked per deployment before the first frame), `VIDEO_VISION_PARALLEL` at a time, for its kind, its
   text verbatim and a one-line description; a picture shown again (back to an earlier
   slide) is read once. Without a vision model the speech still counts; without audio the
   pictures do;
4. *writing* — the pieces merged on one timeline (each piece owns its stretch to the middle of
   its overlaps, so a word at a seam appears once), scenes cut at the kept frames holding the
   speech said while they were on screen, a long talk over one picture split every 75 s.

Every finished piece and frame is a `media_parts` row with its own short session, so a job
taken over after a restart resumes where it stopped, and a failure is retried up to three attempts
(a file that cannot be read fails at once). Speech is recorded on the ledger as seconds
(`media.transcribe`), pictures as tokens (`media.vision`) — a priced vision model is charged
at its full rate and is checked against the owner's credits before the
first frame.

**The time-coded document** becomes the file's text, so search, project context, the index
and per-file digests read it like any document:

```
# 강의.mp4 — 영상 00:21:11 (말·화면을 시간 순서로 옮김)

[00:12:30]
(화면) 도표 — 연도별 환자 수 | 글자: 2010 2015 2020 …
(말) 보시다시피 환자가 크게 늘었습니다 …
```

Marks are always `[HH:MM:SS]`. `files.media` keeps the structured form for the player:
segments `{start, end, said, shown, frame}`, the kept frames, timings and notes.

**Answers point at a moment.** `knowledge.search` gives a video passage the time of the mark
it sits under (the best-matching one when it spans several) and the label
「강의.mp4 · 00:12:30」; the shelf gives a finished video the link `/files?open=<id>`, so the
tool prints 「영상: /files?open=<id>&t=750」 under the passage and `agent._number_sources`
keeps it with the source: the citation becomes `[[1]](/files?open=<id>&t=750)` and the source
list links the label. A video given whole in the context carries a note under its heading
(`workspace_context.VIDEO_NOTE`) asking for the mark after a claim; the web links such marks
(`lib/media.linkTimestamps`) to the video named just before them, else the one the answer
cites, else the conversation's only video. A link into a video renders as a chip with the
moment's thumbnail (`TimeChip`); a click opens the player at that time.

**The player** (`MediaPlayer`, also the file box preview and `/files?open=<id>&t=<s>`) asks
`GET /files/{id}/media` for the state, the segments and signed URLs — `stream` (the original,
served from disk with HTTP Range, so seeking never downloads the whole file) and `frames`
(thumbnails). The signature is an HMAC over the file, its owner and an expiry six hours out:
an element cannot send an Authorization header, and an access token in a URL would land in logs. Below the player, the outline lists each stretch with its picture,
what was on screen and what was said; a click plays from there. While the job runs, the file
chip and the player show the step and progress, and a failed video offers 「다시 읽기」
(finished pieces are kept). A conversation asking about a video still being read is told so,
and told not to guess (`ContextFile.state = "watching"`).

### Transcription and fact-checking

Both are contained within this instance.

**Transcription** records in the browser and sends the audio to the internal
Whisper, and nowhere else: there is no outside-model fallback, so without a
Whisper address dictation and audio files are unavailable, and a Whisper failure
is reported rather than retried elsewhere.
`webkitSpeechRecognition` needs no backend but streams the microphone to a third
party, so it is not used. Recordings are not stored, and the transcript fills
the composer rather than being sent.

**Fact-checking** (`POST /artifacts/{id}/slides/factcheck` and
`sections/factcheck`) extracts only checkable claims from a slide or a report
section and verifies them through SearXNG. One rule makes it safe: **a confident verdict (`supported` or
`unsupported`) must carry a source URL, and with nothing to point at it drops to
`uncertain`.** A badge with no evidence stops the reader looking where they
should. Opinions are not extracted.

### Images and audio

**Both are stored as ordinary turns.** The prompt is a user message; the reply
is an assistant message with an empty body carrying `artifact_ids`, and the
transcript renders the picture or the player where the answer goes. Nothing is
written *about* the result — prose invented for a picture would be the model
quoted saying something it never said. A request that produced nothing marks
the prompt `no_answer` and leaves no reply.

**One picture per request.** The composer sends one; the style picks the path.
The **Picture** group (자동, 사진, 일러스트, 인포그래픽, 수채화) goes to the
picture model: a chat model in the same boundary first rewrites the request as
one structured English prompt (`imagegen.plan`), then a single upstream call
draws it. The **Exact figure** group draws without a picture model: 구조도,
흐름도 and 개념도 through `POST /sessions/{id}/diagrams` (§6 Templates,
"Figures a document draws for itself"), 차트 as a chart from code
(`chart_code`). 없음 (**Prompt as written**) sends the words unplanned.
`imagegen.STYLE_CHOICES` and the web's `STYLE_GROUPS` mirror each other.
Image generation never goes through `assemble`; it resolves the project's
theme on its own (`workspace_context.design_for`).

**Picture models come from the gateway.** External ones are OpenRouter models
LiteLLM lists with `mode: image_generation`; self-hosted ones are served by the
gateway's image backend and listed only as `strict-local/<name>`, with no
external twin. Image-only models are asked for `modalities: ["image"]`, models
that also write text (Gemini, GPT Image, self-hosted) for `["image", "text"]`
(`imagegen.modalities_for`). A model that honours `image_config.aspect_ratio`
offers 16:9, 9:16, 4:3 and 1:1; any other is offered square only
(`imagegen.aspects_for`). A picture is billed at the `usage.cost` the gateway
reports, else from its output tokens (`charge_for_image`); the quote uses a
per-family tokens-per-picture figure (`models._IMAGE_TOKENS`).

**Pictures follow the conversation's boundary.** Pictures, charts, figure
proposals and diagrams use the chat turn's egress rules without its dialog
(`sessions._media_boundary`): in a strict-local conversation, or one carrying
internal material while that policy is on, only strict-local models are used —
for a picture, the person's **strict-local picture model** preference, else the
first strict-local picture model — and with none the call is refused
(`409 internal_data_requires_strict_local`), never sent out. Outside it, a
prompt with personal data is masked, as for reports and decks. A picture with
no model named takes the administrator's default; a named model outside the
account's allowlist is refused (`model_not_allowed`). Pictures inside reports
and decks follow the same rule (`_document_image_model`). Keeping a call inside
or refusing it is audited as `privacy.*`, like a chat turn.

Audio calls OpenRouter through LiteLLM via `chat/completions`. It **requires
`stream: true`**, and streaming yields only `pcm16`, which a 44-byte RIFF header
turns into a WAV. Without `stream_options.include_usage` the stream carries no
usage block and the turn bills as one credit.

### Video

OpenRouter serves video from a separate endpoint (`/api/v1/videos`), so these
models do not appear in `/model/info` and are **declared in
`services/adapters.py:ADAPTER_MODELS`**. Only models `videogen.submit` can
actually call belong there.

A clip lands in its conversation the same way, written by the worker as it
delivers rather than by the tab that asked — so a reload or another machine
sees it. Until then the job card stands in the answer's place.

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
instructions → project instructions → theme → memories → skills → files
attached to this turn → files attached earlier in the conversation → project
knowledge → tool rules → conversation history.

A file uploaded into a chat stays readable for the rest of that chat. This
turn's own attachments take the file budget first and go whole where they fit;
earlier ones take what is left, excerpted around the question once they no
longer fit, and are always named in the file report so the model does not deny
having received them. Project knowledge shares that same budget: it takes what
the attachments left, never less than a quarter of it, read around the question
(`knowledge_budget_after`), so a project's files and a conversation's uploads
fill one context rather than two. Reports and decks author one document and do
not carry earlier uploads. In chat, this turn's attachment is placed as its own exchange
right before the question (`context.build_messages(turn_context=…)`), not in
the reference block that opens the transcript, so the model reads the file next
to the sentence about it; earlier files, knowledge and memories stay in that
opening block, which is a stable prefix across turns.

**The conversation is fitted to the model's window** (`context.fit_history`),
once the model is final. Every model has one (`context.effective_window`): the
window its catalogue row states (LiteLLM metadata or a `MODEL_OVERRIDES`
`context_window`), else `DEFAULT_CONTEXT_WINDOW` (128k tokens) — most hosted
models state none, so none is ever sent an unbounded conversation. The estimate below is calibrated on the self-hosted Qwen tokenizer; another
model may count the same Korean text up to 1.4× higher (GLM), so outside the
self-hosted group the window is read 1.5× smaller in the estimate's units. The
attached-text budget follows the same window (`file_budget`). The fixed
parts of the request — system turn, reference block, this turn's attachment and
question, tool schemas — are counted with a deliberately high token estimate
(`context.estimate_tokens`, about 1.2× the gateway's count on Qwen), an answer
reserve is held back, and the earlier turns that do not fit the rest are dropped
oldest first, at a question boundary.

**The cut moves in chunks.** A new cut keeps 60 % of the budget, and it stays where
it is — at the message after the last one the cached memory covers — for as long as
the turns since still fit; only then does it move forward again. So the memory is
rewritten once every several turns rather than on every turn, and the request's
prefix (system turn with the memory, the kept turns) stays the same between cuts,
which a provider's prompt cache can reuse.

**The dropped turns become a running memory** in a trusted 「이전 대화 요약」 ("summary of the
earlier conversation") block written by the enrichment model (`services/conversation_summary`):
the previous memory plus only the newly dropped turns (in batches of about 24k
characters when a lot goes at once) yield the next one. It has two parts. The
**pinned facts and rules** — what the user stated about themselves and their task
(names, codes, budgets, dates, places, counts), data they gave for something built
in the conversation, and rules they set — are written as `label: value` items and
*merged*, never rewritten: the summariser returns only new or changed items, a changed
value replaces its label, a withdrawn rule leaves, and everything else stays however
many cuts later (`merge_pinned`, oldest first, bounded). The **summary** carries the
rest — what was done, decided and left open — in the conversation's language
(`context.conversation_language`: a standing language rule, else the language of the
user's own words). Both are cached on `sessions.summary` by the id of the last message
they cover. A memory that could not be written is said so, never invented (the pinned
items already kept still go); the turn's timeline shows how many messages were
summarised; the cost settles as `chat.summary`. Material pasted into a request the cut
dropped (2,000 characters or more, the newest three) is carried like an earlier upload
(`context.carried_pastes`): excerpted around the current question with its end kept, in
the reference block, within a quarter of the history budget — so a question about a long
paste one turn later still reads the paste, not only its summary. A prompt the provider
still refuses for length is reported as `context_length_exceeded` with a sentence that
says what to do.

**The composer shows how much of the window the next turn takes.** A gauge beside the
model picker (`components/chat/ContextGauge.tsx`, chat only) asks
`POST /sessions/context-usage` (`routers/context_usage.py`) — with no conversation yet,
for the project's or agent's base cost — when the conversation, the model or the
attachments change. The route assembles the next turn with the turn's own pieces — the
same workspace context, tool schemas, `build_messages` envelope, `fit_history` cut,
pasted-material carry and cached memory — counted with `estimate_tokens`, and calls no
model and writes nothing. It answers the window (stated, and the one fitted to after the
foreign-tokenizer factor), the answer reserve, the estimated request split into base
system prompt, agent/project instructions, summary and memory, files and project
knowledge, kept history, the draft and tool schemas; whether older turns already run on
the summary and how much history still fits before the next cut; and the provider's own
input count for the last answer (every tool round added up). Wording-driven extras the
turn adds (a date rule, a calculation brief, a forced search's instructions) are left
out, so the figure is an estimate, within a few percent of what is sent. The draft is
counted in the browser as it is typed (`lib/contextGauge.ts`, the same estimator), so
typing does not call the server. The ring turns amber at 80 % of the summary threshold
(`window − reserve`) or once the conversation runs on a summary, and red where this turn
would cut older messages. Under Auto the gauge measures against the model that answered
last, else the conversation's default, and says so.

The design block sits after the project's own instructions and before the
skills: the look is a property of the project, and a skill switched on for this
turn is the more specific instruction, so it comes later and wins.

Later blocks weigh more with a small model, so the order is load-bearing: the
material a turn was given sits closest to the question, and the standing
instructions that shape every turn sit furthest from it.

Policy (`governance`) is applied **before anything reaches the model**. For
chat, the privacy guard scans this exact assembled prompt,
the current input and conversation history before it writes a message, issues a
LiteLLM key, charges credits or starts any completion. Chat also materializes
the exact OpenAI tool-schema array first and includes it in the same scan and
decision-token hash; every model hop reuses that detached snapshot. A masked
external retry drops a tool whose definition is sensitive instead of rewriting
function or property names and breaking its runtime mapping. The deterministic
detector covers high-confidence identifiers and secrets; it does not guess
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
the authoritative policy row cannot be read, every chat and
report/slides send path fails closed with `503 governance_unavailable` before
model discovery, transcript writes, billing, virtual-key issuance or upstream
calls. This also blocks a strict-local request: without the row the server does
not know whether organisation-wide masking, intent filtering or blocked categories apply.

**A fallback answer is recorded.** LiteLLM marks a response its fallback served with
`x-litellm-attempted-fallbacks` (> 0) and names the answering group in
`x-litellm-model-group`; the body's `model` is then left as the fallback's own. The chat
stream reader keeps those headers per hop (`agent._stream_once`), and `run_turn` reports a
`fallback_route` event (`fallback_watch.served_by`: the group header when it names another
group, else the body's model). The turn stores `routing.fallback` (asked and answering model,
the answering model's catalogue boundary, `external` unless it is `self_hosted`), the answer
shows 「외부 대체로 답함」 (or 「대체 모델로 답함」 for a self-hosted fallback), and the
answer's transaction writes a `model.fallback` audit event (`warn` when external) for the
administrator. Report and slides writers call the proxy through their own clients and are
not covered.

Strict-local turns disable web search, URL fetching and remote connector tools.
Their tool registry is built from in-process runners only, rather than building
remote connectors and filtering them afterwards. Tool output on an external
turn is inspected before every follow-up completion and masked before it enters
that prompt. While either the guard or organisation-wide masking is active, every persisted
model-generated textual field (answer, timeline details,
artifact payloads and routing metadata) is deterministically masked with the
`answer` scope described next, even when the inbound envelope was clean. Title
generation and automatic memory receive only the masked turn text.

**A strict-local turn asks before it searches** (`services/search_consent.py`). A strict-local turn whose web search is on (or 자동) shows what would leave and asks, so the
person knows when a question was answered from their own material alone. Nothing but short queries ever leaves; the material stays inside.

- *Chat.* The turn is offered a local tool, `request_web_search(queries)` (1–5 short keyword
  queries, no sentences or figures from the material). A question the server would search
  anyway (`search_plan`, the current-fact rules) forces that tool instead of `web_search`. In
  「매번 묻기」 the tool ends the turn: the answer row is the card (`routing.searchConsent`,
  `status: pending`, the proposed queries) and the transcript keeps it until it is answered,
  across reloads. The browser answers by rerunning the question (`retry_of`) with
  `searchConsent: {messageId, decision, queries?, remember?}`. The server checks the card is
  still pending in the stored transcript (`409 search_consent_stale` otherwise — another tab
  or replica answered it; the turn claim keeps two answers from running at once), settles the
  decision against today's policy and vets the queries again (the browser's edit is never
  trusted), deletes the card row, and runs `request_web_search` as the turn's preset call: the
  approved queries go to the ordinary `web_search` one by one, and their numbered results are
  read by the same local model, which cites them as `[n]`. The model's own arguments cannot
  widen what was approved, and a second call is refused.
- *Report and slides.* Research runs before the outline and cannot pause mid-pipeline, so the
  local planner proposes the queries up front (`research.plan`) and the card is stored before
  the pipeline starts (`_search_card`, SSE `search_consent`). Every strict-local document
  turn sets `research.ALLOWED`: research sends only the approved queries, once, plans none of
  its own and follows no product pages; with nothing approved it sends nothing, so an
  approved outline's later turns cannot search again.
- *Vetting* (`search_consent.vet`): queries are cut to 100 characters, at most five; personal
  identifiers are masked with the egress detectors; a query carrying a secret, a 40-character
  run of the turn's internal material, or an administrator-blocked category is withheld and
  the card says why. Edited queries pass the same check.
- *Saying it.* Declined, withheld, turned off, or simply not asked although the question
  plainly reaches outside the material (`search_consent.needs_outside`, the server's search
  rules): the answer ends with 「외부 검색 없이 자료만으로 답했습니다.」 (a server note,
  stripped before the answer is sent back as history).
- *Settings.* `governance.strict_local_web_search`: `ask` (default), `allow` (queries only,
  no card) or `off`. A project's `strict_local_web_search` may only tighten it (the PATCH is
  refused otherwise; at run time the stricter of the two applies). 「이 대화에서는 계속 허용」
  on the card stores `remember` on the answer; later turns of that conversation then send
  the model's vetted queries without a card (as `allow` does), unless policy is `off`. The
  composer says which applies to a strict-local model (「웹 검색: 보내기 전에 묻기
  (strict-local)」) and sends the person's search setting, so the server can offer the ask.
- *Record.* The answer's `routing.searchConsent` keeps `status` (`pending`, `allowed`,
  `auto`, `declined`, `blocked`, `unavailable`, `none`), the queries sent, the withheld ones
  and why, `decidedBy`, `decidedAt` and `remember`; the same goes to the audit log as
  `search.consent_*` with the paused card's id. The answer's badge reads from that record —
  「strict-local로 보호됨 · 외부 검색 2회(허용)」, 「… · 외부 검색 안 함」, 「… · 외부 검색 확인
  대기」 — and every strict-local action (`internal_strict_local` included) reads as
  strict-local; a request-side 「외부 전송」 label appears only for `send_raw_external`.

**Masking is scoped by where the text is going.** The detector tells a
person's details (a mobile number, a personal mailbox, a routable address, an
ID or card number) from an organisation's (a switchboard or 15xx service
number, a role mailbox such as press@ or minwon@, a private-range, loopback or
documentation address, a well-known resolver) and from secrets (API keys,
tokens, private keys, and the ID and card numbers again). `governance.SCOPES`
says what each place masks: `egress` — the user's own words and files on
their way to an external model — takes out personal details and secrets;
`tool` — pages fetched from the public web — takes out secrets only, because
a company's contact number is the answer, not a leak; `answer` — the reply,
steps and routing persisted at rest — takes out secrets plus exactly the
values egress removed from the user's words this turn (`protected_values`),
so nothing the user gave comes back from storage while a number the model
found on the web stays readable. Organisation details are never masked and
never count as findings, so a page that names a switchboard does not trigger
the privacy dialog.

The selectable guard covers chat. Reports, slides, media
generation and the `/llm` compatibility API always mask what may leave and
present no decision flow.

**Nothing is masked inside the boundary.** Masking exists for what leaves, so a
turn whose models are all strict-local reads the person's words, files, history
and material as given, under organisation-wide masking too: `_resolve_privacy`
settles a strict-local envelope as `strict_local` (or `internal_strict_local`)
with no outbound mask, the stored question and summary stay as typed (the next
turn's history reads them), and `protected_values` is empty, so the answer at rest
masks secrets only and never turns an address the person gave into 「[이메일]」.
Reports and decks count as inside when `_document_stays_inside` says so: a
strict-local model the person picked outside Super Agent, a `super_strict`
conversation, or internal material the admin keeps strict-local. A plain Super
Agent document's writer is the table's pick, so it is masked as before. The
`kloudchat/super-agent*` API masks only when its pick is not strict-local.
What still leaves a strict-local turn — web search queries (`search_consent.vet`),
media calls outside the boundary — is masked on its way out.

**An answer shows what left, masked.** When a chat, report or deck turn goes to an
outside model with personal data masked (chat `mask_external`, or organisation-wide
masking on a document that may leave), the answer's `routing.egress` keeps what was
sent: `masked` (category, placeholder, count) and `parts` — the masked request, and the
attachments, material, instructions and earlier conversation as sent, each cut to 20,000
characters (60,000 in all); a part other than the request and attachments is kept only
where the mask changed it. Only masked text is stored, never an original value, and only
on the answer row (the question's routing drops it). It reaches the browser with the
conversation's messages, which only their owner can read, and the answer shows
「외부로 보낸 내용 보기 · 이메일 2 · 전화번호 1 가림」, opening the parts with each
placeholder marked. A strict-local turn sent nothing out and has no record; a document
whose writer turns out strict-local drops it.

### Prompt caching

Every agent hop resends the system turn, the tool schemas and the conversation, and a
provider (DeepSeek, GLM and others behind OpenRouter) or the vLLM prefix cache reuses the
longest prompt prefix it has seen. `context.system_prompt(cache_order=True)` therefore puts
what is the same for every turn of the deployment first: the surface, accuracy, language and
writing rules, the tool rules, the chat task contract and the grounding instruction; then the
web-search toggle, today's date, and the session's own blocks (agent, project, theme,
memories, skills, the turn's language rule). Nothing that changes per day, person or session
sits before the date. A closing line says those contracts still apply over the blocks after the date. The answer policy `freshness.with_answer_policy` appends per turn stays at the end.
Tool schemas follow the system turn in the chat templates, so a change anywhere in the system
turn ends the reusable prefix before them; within a turn every hop after the first reuses the
whole prefix. Which models get this order is described under "Mechanisms by model group".

The tool schemas are the other fixed cost of every hop. Built-in descriptions carry the rules
a model needs before it calls a tool; what it needs only after a call comes back in the
result (`run_skill_script` names its scripts, and a wrong name returns what each does). A
connector with more enabled tools than `connector_tools_eager_max` goes out by name
(`tools/deferred`): its tools are listed with one line each in the description of a
`load_tools` tool, the model loads the ones it needs, and their full definitions come back
as that tool's result and are offered from the next hop. Smaller connectors, built-ins and
the tools a turn must call at once (a forced or preset tool, a skill's source tool) go out
whole, a direct call to a deferred tool still runs, and the loader's text comes from the
same tool-definition snapshot the privacy scan read. A `load_tools` result is not material:
it gets no source number and adds no number sources.

### Super Agent

Super Agent is two picker entries, `routing_mode` `super` and `super_strict`, that choose a
model, a reasoning effort and a tool budget for every turn (`services/super_agent.py`). `super`
may use any model the account is allowed, under the usual privacy rules; `super_strict` only
self-hosted strict-local models. The older Auto routing (`adaptive_routing`) still serves
sessions set to it through the API.

**Always there, and the default.** There is no switch: the picker lists both entries first on
chat, report and slides, each with its credit range, or as unavailable while a conversation is
on an entry whose table has no model the account may use. A new conversation the caller sends
without a `routing_mode` starts as `services/start_mode.py` decides: a model fixed by the caller
or the conversation's agent keeps it `manual`; the person's own pick for the surface
(`preferences.modelByKind`, a model id or the entry `super` / `super_strict`) comes next;
otherwise the surface's start from 설정 › 시스템 › 모델 (`system_settings` `routing.chat`,
`routing.report`, `routing.slides`; unset is `super`), with `super` moved to `super_strict` in a
project where internal material stays strict-local, and to `manual` when neither entry has a
usable model (a `super_strict` start never widens to `super`). The web client applies the same
rule before the first send (`lib/superAgent.ts` `startingMode`, from the catalogue's
`defaultRoutingByKind` and `superAgent.projectStrict`), and a chat handed off to a report or
deck starts the same way. Existing conversations keep their stored mode.

**Task class first, by rules.** Each turn is sorted into `simple`, `chat`, `transform`
(translate, summarise, proofread), `calc`, `code`, `visual`, `current` (facts that change;
search switched on), `long` (large pasted material or conversation), `reasoning` or `document`
(report and deck sessions). Rules read only the ask, not material pasted with it
(`split_ask`): a meeting memo that mentions 「코드 프리즈」 is not a coding task. `code` covers
code words (`_CODE`) and requests to build something that runs (`super_agent.builds`: an app, a
web page, a game, an HTML file, a program in a named language, a chart; 「계산기 웹앱 만들어줘」,
「파이썬 정렬 알고리즘 짜줘」), but not a plan or list about one (「앱 기획서 작성해 줘」). `visual` is
a `code` request that draws data (`super_agent.draws_data`: 대시보드, 차트, 그래프, 시각화,
dashboard, chart, graph, plot, visualisation; a graph of nodes and edges, a flowchart are not),
such as 「월별 매출 표로 대시보드 만들어줘」 or 「차트로 그려 줘」; a question about a chart that asks
for nothing to be built (「이 차트가 뭘 뜻하는지 설명해줘」) stays where it was. The classifier knows
`code` only; its `code` for a request that draws data becomes `visual` too
(`super_agent.visual_class`). Calculation and
current-fact cues reuse `calculation_policy` and `freshness`. Only a turn the rules cannot
place (short or unmarked) goes to the classifier: a zero-cost strict-local model
(the configured classifier model, else the table's strict `simple` model) that receives the
request and the previous question, each cut short (about 300 tokens), and answers one JSON class. A failure or a confidence
under 0.6 keeps the rule's answer. The classifier is never called when the turn holds
personal data.

**The table.** `DEFAULT_RULES` maps every class to a model for `super`, a strict-local model
for `super_strict`, and an effort (`default`, `none`, `low`, `high`) with an optional
`strictEffort` for the strict-local column (absent: `effort` applies there too). The admin edits
it in 설정 › 시스템 › 라우팅 and only changes are stored (`governance.super_agent_rules`). Effort
becomes the request fields of the routed writer only (`agent.TURN_REASONING`): OpenRouter
`reasoning.effort` for external models (the lowest offered level when a model cannot turn
reasoning off), the vLLM template switch for self-hosted ones (`enable_thinking` for Qwen,
`thinking` for DeepSeek). For Qwen the switch carries a `thinking_token_budget` (500 at `low`,
2000 at `high` and deeper; `SELF_HOSTED_THINKING_BUDGET_LOW/HIGH`, 0 sends none) so a thinking
call cannot run away. A table model that is not served, not allowed, too small for the
conversation, without tools a class needs or without vision for pictures falls back along a
short list, then to the surface default. Self-hosted Qwen3.8 Flash is the default wherever
thinking is not needed; hard turns go to an external model with reasoning on.

| Class | `super` | `super_strict` | Effort (`super` / strict) |
|---|---|---|---|
| simple, chat, transform | Qwen3.8 Flash | strict Flash | none |
| calc, long, reasoning, current | DeepSeek V4.1 Flash | strict Flash (no search for current) | high |
| code | Qwen3.8 Flash | strict Flash | none |
| visual | DeepSeek V4.1 Flash | strict Flash | high / none |
| document | Qwen3.8 Flash | strict Flash | none |
| material (a row, not a class) | empty: the class's own row | empty: the class's own row | high |
| complex_document (a row) | Qwen3.8 Flash | strict Flash | high / high |
| document_plan (a row) | GLM-5.3 Flash (`z-ai/glm-5.3-flash`) | strict Flash | low / low |
| document_hard, document_repair (rows) | GLM-5.3 (`z-ai/glm-5.3`) | strict Flash | default / low |
| escalation (a row) | DeepSeek V4.1 Flash | strict Flash | high / high |

`visual` is the one artifact kind Flash writes clearly worse without thinking, hence its own row.
The rows marked "a row" are not request classes; the sections below say when a turn moves to
them. Material too long for Flash's window reaches a larger model through the size check
(`usable_for`, the last-resort pick by window).

**Escalation, de-escalation, stickiness.** 「다시」, 「더 자세히」, 「틀렸어」 and the like, or an
empty or failed previous answer, move the turn to the `escalation` row; that row's model also
rewrites, inside the same turn, an answer the class's model left empty or cut (the rescue
writer, with its own effort). A rescuer gets `_FALLBACK_PATIENCE` (240 s) to show text; if it
fails or stays silent, the turn's own model still writes (`_RESCUE_PATIENCE`, 300 s, sits
above it). A thanks or 「ok」 after a hard turn comes down to `simple`. A short follow-up with no
markers of its own keeps the previous turn's class and model (one voice per thread, and the
provider's prefix cache stays warm), and every revision of a report or deck keeps the model that
wrote its first version (`_first_document_model`). The pick is the turn's: `sessions.model` is
not overwritten.

**Internal material.** A chat turn works over internal material when a file that is not a
picture is attached to it, or when `search_knowledge` has something to search in the
conversation (`_material_present`, the same set as `_knowledge_shelf`): files with text
attached earlier, the project's knowledge sources still switched on, the agent's own files.
Pasted text alone is not material here (it is the `long` class's business). Such a turn moves
to the `material` row (`route.row`), in a column where that row names a model, except a thanks
(`simple`), a document and a retry (`escalation`), which keep their rows; the class stays what
the request is. By default neither column names a model, so nothing moves (an override row's
empty column keeps the class's row; the admin may fill or empty either column). 빠르게 still
sends the turn to the `chat` row, 깊게 to `escalation`.

**Complex documents (the `complex_document` row).** Documents are routed by what they ask of
the writer, not only by the surface. The row is an override row (`OVERRIDE_ROWS`), editable per
column and effort in 설정 › 시스템 › 라우팅 (「복잡한 문서」). By default it is the same Flash
as the `document` row but with thinking on (effort `high`, a 2000-token budget). An empty column
moves nothing there. A document turn (a report or deck session, including its HWPX, Word and
PowerPoint files, which run as chat turns) is complex when any of these holds, read from signals
the app already computes (`sessions._document_signals`, `super_agent.complexity`; every
threshold is a constant in `super_agent.py`):

| Reason (`complexReason`) | Signal | Threshold |
|---|---|---|
| `form` | a form file is followed: attached, or the conversation's, agent's or project's saved one (`form_files`), of kind pptx, docx or hwpx | any |
| `coverage` | an each-item request (`coverage.asks_each`) over source files, or simply many source files (attached, project and agent files counted once) | 2 files with the ask (the `Coverage` stage's floor); 6 files without |
| `tools` | a deck asked for a chart over an attached data table (native chart from data), or for a paper's own figures with a PDF at hand (`_ASKS_FIGURES`, `pdf_figures`) | either |
| `long_source` | characters of text in those source files | `LONG_CONTEXT_BYTES // 3` (20,000) |

A simple document stays on the `document` row (Flash, thinking off). For the office-file
conversations the report and deck screens always send the model they show, so the stored mode
decides (`super_turn` ignores `payload.model` there; a model picked by hand already made the
conversation `manual`), and the turn is classified as a document turn (`Turn.kind` is the
conversation's), so only the `document` and `complex_document` rows apply, never the hard-class
rows. The route note carries `row: complex_document`, `complexReason` and `complexSignals`; the
answer's processing row reads 「이번 답: Qwen3.8 Flash · 복잡한 문서 · 양식을 따름」. A model picked
by hand is never moved. Revisions keep the first version's writer and row
(`_first_document_route`): a complex document stays complex whatever its revision brings, and a
simple document is not moved by a later form. The reasoning chip steers the row's effort only:
빠르게 turns thinking off but keeps the row's model (a form must not silently go to another
writer), 깊게 raises it to the highest level. When the row's model cannot write the turn (failed
lately in `model_health`, not served, window too small) the pick goes to the `document` row's
model, not the table's fallback list, and the note says `skipped`.

*Same-turn fallback.* The fallback writer is the `document` row's model in the turn's column
(`super_agent.complex_fallback`, inside the boundary), thinking as that row says. With the
built-in table both rows are Flash, so there is none (the same model again would not help); an
admin who puts another model in the complex row gets the `document` row's model behind it. A
chat-path turn (office files) takes it through `agent.run_turn(fallback_model=…)` (the machinery
above: refused, dropped stream, 240 s silent; the 「깊이 생각하는 중」 row shows while it thinks).
A report or deck pipeline makes whole-answer calls (`report._complete`, `deck._complete`,
`page._complete`) that go through `complex_turn.run_call`: a call that fails or takes longer than
`complex_document_call_sec` (480 s) is asked again on the fallback, the failed model is marked in
`model_health`, and the turn's later calls go straight to the fallback. The pipeline turn shows
「깊이 생각하는 중」 from its start until its first output.

*Slots.* A slow self-hosted model that chat shares must not have all its sequences taken by
long document tool loops, so with `COMPLEX_DOCUMENT_SLOTS` set at most that many complex turns
on a self-hosted model run at once across all API replicas. The default is 0 (no cap); set it
when the row names a slow model. A slot is a Postgres session-level advisory lock
(`complex_turn.LOCK_BASE + i`) on a connection of its own, so a replica that dies releases its
slots with its connection; without Postgres (tests) the cap is per process. The stream
(`_survive_disconnect` → `_with_slot`) takes a slot before the turn starts, shows 「대기 중 · n번째」
(n counts the waiting turns of this replica) while it waits, and gives the slot back however the
turn ends. After `COMPLEX_DOCUMENT_WAIT_SEC` (120 s) without a slot the fallback writes the turn
(`route.fallbackReason: queue`).

**Staged documents (the `document_plan`, `document_hard` and `document_repair` rows).** A small
self-hosted model alone writes weak reports and decks (garbled terms, unwritten sections, wrong
slide counts), so in `super` and `super_strict` a report or deck is written in stages and no one
model writes the whole document. The `document` row's model stays the writer of the ordinary
sections and slides; three override rows (`OVERRIDE_ROWS`, editable per column and effort like
the others) name the upper model of a stage:

| Stage | What it does | Where |
|---|---|---|
| `plan` (`document_plan`) | the report outline, the deck storyline and its slide-count retries, the cover title | `Outline` stage; `deck.write` `ask` |
| `hard` (`document_hard`) | the sections or slides the objective signals below mark as hard, one call each, started together | `Draft` and `Sections` stages; `deck._write_slides` |
| `repair` (`document_repair`) | only what an existing check sends back: an error text, tool call, cut-off or empty section, placeholder or table gaps, a language slip, a format amendment, the verify stage's findings, a retold, empty or unusable slide, a deck check's rewrite (a language slip, long lines, template blanks, a missing summary) | `report_stages` `_staged` and `_repair_complete`; `deck.rewrite_slide(stage="repair")` |

Defaults: the `super` column is `z-ai/glm-5.3` for `hard` and `repair` (the model's default
effort) and `z-ai/glm-5.3-flash` for `plan`. Neither model can switch reasoning off on
OpenRouter (400 "Reasoning is mandatory"), and GLM-5.3 Flash with no reasoning field can spend
tens of thousands of thinking tokens on an outline, so the plan row sends `low` explicitly
(`reasoning: {effort: "low"}`, never `enabled: false`). The `-flash` suffix puts it in the
small-external group of "Mechanisms by model group". The strict column is
`strict-local/qwen3.8-flash` with `strictEffort` `low` for all three (a 500-token thinking
budget; the strict writer is Flash too, so these stages run only because they ask it to think).
To use another upper model change only a row, for example `moonshotai/kimi-k3.1-pro` in the three
`super` cells; nothing else needs a code change. An empty column switches the stage off: the
writer does it. So does a stage whose model is the writer's own, unless the stage's effort asks
it to think (`low` or `high`): then it is a real stage on the same model, called with its own
reasoning fields (`stage_pick`, `doc_stages.staged`).

*What is hard* (`doc_stages`, no model judgement, no phrase lists of its own). A report's
section is hard by its role, read from the headings with the words `report._section_role`
already uses: the summary that opens the document (`summary`), the conclusion that closes it
(`conclusion`), a section that analyses or compares (`analysis`: 비교, 분석, 대안, 옵션, 검토,
평가), and a section with a table, results or trend role (`figures`) when the material holds
figures to trace (at least six distinct numbers or a table). A deck's slide is hard when it is a
data layout (chart, metrics, big number, table) over such material (`data`), or when its title
is a summary or decision (`summary`); structural slides take no model call. At most half of the
sections (`DOCUMENT_HARD_SHARE`) and 30 % of the content slides (`DOCUMENT_DECK_HARD_SHARE`) go
up, strongest reason first, so the writer always keeps a share. The one-call draft of the report
or deck leaves the hard parts out (and says they are written separately); hard sections that
need no earlier text (all but the conclusion) are started together.

*Fallbacks.* A stage whose row names no model, a model that is not served, not allowed, failed
lately (`model_health`), or is the writer itself, stays with the writer
(`super_agent.stage_pick`): a document turn never fails for a missing upper model. A call to an
upper model that fails or answers nothing is asked again on the writer, and the rest of the
turn's calls of that model go straight to the writer (`doc_stages.staged`); the calls still go
through `_complete` and its retries (`complex_turn.run_call`, `model_retry`, `thinking`). Each
stage call is sent its row's reasoning level (the chip steers it: `fast` none, `deep` highest)
for that call only.

*Privacy.* The picks are made in the turn's column. A turn that went to the strict column (the
strict entry, personal data, internal material) takes all three stage models from the strict
column, which accepts strict-local models only (the admin table refuses any other), and the
router drops any non-strict stage model again before the writer runs. Nothing new leaves in a
strict turn. In `super` the upper models are external picks like the hard chat classes: the
user's model allowlist applies, and the gateway applies its OpenRouter policy (ZDR, quantization
for open-weight models) as for every external pick.

*Observability.* The route note carries `stages` (the model of each stage). Each stage's tokens
are kept apart in the usage (`usage.stageUsage`, never in the writer's own counts), billed at
that model's own price, and the stored message's `routing.runInfo` lists each stage's model,
calls, fallbacks and tokens with what sent work up (`triggers`) and `selfHostedShare` (the share
of the turn's tokens, input and output, that stayed on the house's servers) and
`selfHostedOutputShare`. 「이번 답」 details show them. Revisions of an existing document are not
staged.

**Failure inside a strict turn.** A strict-local model other than Flash (an admin's pick; the
built-in table names none) may be served by fewer nodes, and the gateway refuses strict aliases
outright (503 `strict_local_unavailable`) while vLLM has requests waiting. A strict Super Agent
turn whose writer is not Qwen3.8 Flash therefore carries a same-turn fallback
(`_super_fallback`, `agent.run_turn(fallback_model=…)`): when the writer's call is refused,
cannot connect, breaks mid-stream, or shows neither answer text nor a finished call within
`_FALLBACK_PATIENCE` (240 s), the shown part is taken back and Flash continues the same
conversation, tool results included, at the writer's effort; nothing is answered outside
strict-local. The switch is a `writer_fallback` event: the answer's route note gains
`fallbackModel`/`fallbackReason`, the answer row records Flash, and the tokens the failed
writer spent bill on its own ledger line. The failed model is marked in `model_health` (60 s
after a busy refusal, 300 s otherwise, per process) and left out of Super Agent picks, rescuers
and the 깊게 route meanwhile, so the next turns go to Flash at once (`route.skipped` says so).
Within a tool loop a self-hosted DeepSeek writer (an admin's pick) gets its own reasoning back
on each tool call (`reasoning_content`, `agent._passes_reasoning`; no other model is sent it),
so it does not re-derive figures at every step. Its first visible text can take a minute or
more: the step list shows a running 「깊이 생각하는 중」 row naming the writer, the wait and the
fallback until the first text or tool call, next to the turn's elapsed clock.

**Boundaries.** `super_strict` picks only `self_hosted` strict-local models; with none usable
the turn is refused (503 `strict_local_unavailable`), never answered outside, and titles,
summaries and checks follow the strict writer. In `super`, personal data in the conversation
or internal material (attachments, a project, `internal_data_strict_local`) picks from the
strict-local column instead of asking, and the privacy resolution still runs on the pick as
for a manual choice. The user's `allowed_models` bound every pick.

**What the person sees.** The answer's processing row reads 「이번 답: Qwen3.8 Flash · 간단한
질문」 plus the reason when there is one (same model as before, retried stronger, personal data
kept inside); its title names rules or classifier, the effort, tools and the classifier's
tokens and time. The picker shows each entry with a typical answer's credits (10k tokens in,
1k out) across the table. The route is stored as `routing.superAgent` and audited as
`routing.super`.
### The reasoning chip

The composer's **추론** chip (자동 / 빠르게 / 깊게; `auto`, `fast`, `deep`) is a per-conversation
setting (`sessions.reasoning_mode`, NULL for `auto`) sent with every chat, report and slides
turn (`SendMessage.reasoning`) and stored in the question's `turnOptions`, so a retry or
regeneration runs under the choice it was asked with. A sent value also becomes the
conversation's; a choice made before a conversation exists goes with its creation.

- **Super Agent** (`super_agent.steer`): `auto` follows the routing table. `fast` sends a class
  whose row reasons (`low`/`high`) to the `chat` row and every class to effort `none`, so no
  turn escalates to a reasoning route; the class's other needs (tools, vision, a long window)
  still narrow the pick. `deep` sends every chat class to the `escalation` row's model (strict
  column under `super_strict`, personal data or internal material) at effort `high`, skipping
  the classifier and stickiness; when that model cannot answer the turn (not served, not
  allowed) the class's own pick answers at effort `high`. A report or deck keeps its writer
  (the `document` row's model, or `complex_document`'s for a complex one; revisions reuse the
  first writer, `_first_document_model`) under every choice; `deep` raises only its effort to
  the highest level the model takes (`highest`), `fast` sets `none`.
- **A model picked by hand** (`super_agent.manual_effort`): `auto` sends nothing (the
  deployment's thinking switches), `fast` sends effort `none` (the lowest level where reasoning
  is mandatory), `deep` the highest level the model offers (the template switch on for a
  self-hosted model). A model is steerable when it has effort levels or a self-hosted thinking
  switch (`reasoning.controllable`, `reasoningControl` in the catalogue); on any other model the
  chip is shown disabled with the reason, and a value sent anyway is recorded with
  `unsupported: true`. The same mark goes on a Super Agent turn whose writer cannot be steered.
- **Document writers** read the same fields (`thinking.TURN_REASONING`, set in the request for
  report and deck turns) through `thinking.writer_switch`/`writer_room`; without them they keep
  thinking off.

The question and answer carry `routing.reasoning` = `{mode, effort}`, where `effort` is what
the writer was actually sent (`super_agent.applied_effort`: a level, `on`, `none` or
`default`); the answer's usage line reads it as 「깊게 · high」.

### Auto routing

Auto is a session mode, not a model alias. `sessions.model` always stores the last real model
the person selected; `routing_mode` is `manual`, `auto` (cost: a low-complexity turn may use an
economy model) or `auto_quality` (a high-complexity turn may use an administrator-ordered
upgrade model that does not widen the data boundary and keeps its tools). (`super` and
`super_strict` are the Super Agent modes above.) No synthetic `auto` id ever reaches LiteLLM,
and manual model selection stays authoritative.

Privacy inspection runs before classification. If the assembled envelope has a finding, Auto
makes no key issuance or model call and the privacy decision owns the turn. A clean, ordinary
chat turn may be classified by a live, zero-cost strict-local model using the caller's virtual
key, redacted LiteLLM logging and `disable_fallbacks`. The classifier receives the complete
answer-visible messages and the quality model's tool schemas; a payload over 80,000
characters is not truncated and the chosen model is kept. Only a high-confidence JSON verdict
moves a turn (low on the cost lane, high on the quality lane); every timeout, malformed
response or uncertain state keeps the chosen model. Report and deck turns are classified on
the request text; a turn moved to strict-local for its internal material stays there.

Economy candidates are administrator-ordered and revalidated against the live catalogue,
caller allowlist, context window, data boundary and both token prices. `unknown` and
`privacyOnly` models are excluded, and none may have a wider boundary than the chosen model.
The economy answer call receives no tools and disables fallback, so an unbounded tool result
cannot invalidate the checked context fit and a failed economy route cannot cause a second,
hidden premium charge. Message routing stores the quality, selected and actual models plus
enum-only decision metadata; classifier prompts and free-form model reasoning are never
persisted.

### Chat answer shaping

**Standing rules are repeated next to the question.** An earlier user message that sets a rule
for every later answer (「앞으로 … 세 문장 이내로」, "from now on … within three sentences"; "keep
this whole conversation in English") is replayed verbatim as a short exchange right before the
latest question (`standing_directives`, chat only). A rule is read as a *scope* and an
*aspect*: a scope that reaches past this answer (「앞으로」, 「항상」, 「이 대화 내내」, "from now
on", "the whole conversation") makes a rule on its own; a weaker one (「계속」, 「유지」, "keep",
"stick to") only when it names what it sets (language, tone such as 「존댓말」, format such as
「표로」, length), so 「계속 진행해 줘」 ("keep going") stays a task and 「이번만」 ("just this
once") is never standing. A short message is replayed whole, a long one only its rule
sentences, and pasted material never sets a rule. A later rule replaces an earlier one on the
same aspect, up to five are kept, and a withdrawal such as 「원래대로」 ("back to normal")
clears them. Rules are read off the **whole** conversation, not only the turns that still fit
the window. A standing English rule also sets the answer language for a Korean question: the
system turn says so, the replay adds it as a plain line, and the envelope's own
acknowledgements for the assistant are written in English too. A skill fetched through
`use_skill` is told that such rules take precedence over its own instructions.

**The auto toggle's forced search asks about the world.** `needs_web_search` stands down when
the person is telling rather than asking (their team, memo, rule, code), when the question is
about the conversation (「아까」 "earlier", 「그 규칙」 "that rule"), when it is arithmetic on
supplied numbers or a rule of thumb (「몇 장이 적당해?」 "how many slides is right?"), and when
a sentence has a time word but no world cue (news, price, version, law, office). The search
tool stays offered; only the forced first hop is withheld. The auto instruction likewise names
what never needs a search and ties searching to answers that depend on events after training
or today's values; `create_chart` is for charts the user asked for, not for trend questions.

**A rough figure still says where to check.** A question about the usual level of a changing
value (「왕복 요금은 보통 얼마나 해?」, "what does a round trip usually cost?") forces no search,
but the figure comes from training data, so the turn runs with `caveat_request`
(`freshness.ballpark_current_value`): the answer streams live and closes with the same light
caveat an unverified current-fact answer gets, unless the conversation's files ground it.

**Length follows the question.** The chat contract asks for two to four sentences of prose on
a casual question or quick fact, with structure only when the user asks for detail or the
material has parallel parts.

**One answer, once.** A small model sometimes restates its whole answer after the prompt's
silent self-check. When a tool-less hop ends with one block written twice in a row, the agent
keeps the first copy and takes the second back with a `retract` event (`_repeated_tail`), so
neither the stream nor the stored message shows it twice.

### Other boundaries

- A `stdio` connector is a command the API server runs, so only an
  administrator adds or shares one (`403 stdio_requires_admin`). A reference to
  an administrator's shared stdio server runs the shared command, with the
  receiver's own credentials only for the keys the sharer listed (`required_env`).
  The check runs again before every spawn (`catalog.check_runnable`); a reference
  whose sharing ended (`origin_gone`) is refused instead of run. The child
  inherits a minimal environment (`PATH`, `HOME`, locale, `TZ`), never the API's
  own secrets (`mcp._INHERITED_ENV`), and a `${VAR}` in a connector's env reads
  only those names from the API's environment.
- A member's own `http`/`sse` connector, and a reference to a member's shared one,
  reaches the public internet only, by the same rule as page fetches (`netguard`):
  an endpoint on loopback, link-local (metadata `169.254.169.254` included),
  private (RFC 1918, ULA) or CGNAT addresses, a dotless or reserved-suffix
  service name, or a `${TOOLS_*_URL}` placeholder is refused when saved (`422`)
  and again before every call (`catalog.check_runnable`). The call itself dials
  through `netguard.PublicTransport`, which resolves the host, checks every
  address and connects to the checked one (Host header and TLS name unchanged),
  so a name that turns internal after saving, or a redirect to an internal host,
  is not dialed; environment proxies are not used for these calls. An
  administrator's own connectors, and references to them, may name internal hosts.
- A cached conversation memory — the summary and its pinned items — is part of the
  turn's privacy scan (with the history) and is masked under the policy in force when
  it is reused, not only the one in force when it was written; the summariser's prompt
  and reply pass the same masker, and the memory is stored masked when the turn's
  policy masks at rest.
- Report sections reach storage sanitised on every path — create, update and
  restore alike (`_clean_report_data`).
- A document edit — fact-check, review, a section, slide or block rewrite, a
  theme read from a file — picks its model inside the boundary of the
  conversation that made the document (`workspace._edit_model`): internal
  material or a strict-local conversation keeps it on strict-local models (a
  `local/x` conversation model becomes `strict-local/x`), refused with
  `internal_data_requires_strict_local` when there is none. Personal data in
  the document also keeps it strict-local rather than masked, because the mask
  would be written back into the document. A web fact-check inside the
  boundary is refused (`factcheck_needs_web`). The account's allowlist holds.
- A video job passes the media gate pictures use (`sessions._media_gate`): the
  intent filter the chat turn applies, the account's model allowlist (a named
  model is refused, never swapped), then the conversation's boundary — inside
  it the job is refused (`409 internal_data_requires_strict_local`) unless a
  strict-local video model exists, outside it personal data in the prompt is
  masked before it leaves.
- An index delete that fails (the index down, a timeout) is kept in
  `index_deletions` — the document and collection, or a whole collection — and
  retried every ten minutes by a background loop (`services/index_cleanup.py`)
  until it lands, given up with a warning after 30 days. An entry indexed again
  since is left alone. A vector hit counts only while a shelf document of that
  name still holds its passage, so an entry a deleted (or since edited) file left
  behind never becomes evidence, though a living file shares its name.
- Credit settlement is an atomic SQL increment; two turns of one account
  settling at once both land.
- A finished video is claimed once across replicas by flipping its stage with
  a conditional update; a cancel that landed first wins, and nothing is stored
  or charged after it.
- A chat turn is claimed on the session row (`running_turn`, `running_since`)
  in the same transaction as its question, released when the
  answer settles or stop is pressed, renewed every minute while it runs, and
  ignored once four minutes stale. Two replicas serialize on the row; the
  second sees the claim and answers 409. Restarts free claims without waiting
  for that bound (see "Restarts and turn claims").
- A save, picture insert, rewrite or restore of a report, deck or page moves
  the version in one conditional `UPDATE … WHERE id = … AND version = expected`
  (`workspace._next_version`), the way office documents save
  (`office_doc.save`): of two writers that read the same version — two
  replicas, or an edit that lands while a rewrite waits on its model — exactly
  one matches the row and the other gets 409 with nothing written. A restore
  carries the version on screen (`expectedVersion`, sent by the version
  history of every panel); without it, the version it read. Annotations that
  add no version (fact-check, review, stored diagram rasters) are not checked.
- **Restarts and turn claims.** On SIGTERM uvicorn stops accepting and waits
  up to 120 s for open responses (`--timeout-graceful-shutdown 120`); the
  lifespan then gives turns still running — those whose reader left included —
  the rest of `TURN_DRAIN_SEC` (default 120 s, counted from the signal), stops what is
  left through the stop signal with the label *interrupted* (a partial answer
  is kept, as on **Stop**), cancels anything still there after ten seconds,
  and frees every claim this process holds, marking a question left without an
  answer `failure = interrupted` (`sessions.drain_turns`). The container's
  `stop_grace_period` (150 s) sits above that. A claim token names its
  replica and boot (`<host>:<boot>:<random>`); the host name survives a restart
  of the same container, so a replica that crashed frees what its earlier boot
  held as soon as it starts again (`free_claims_of_earlier_boots`), never
  another replica's claims. A replica that dies and is replaced rather than
  restarted is covered by the four-minute stale bound. The SSE response sends
  a `: keep-alive` comment after 15 s of silence (`_heartbeat`), from its own
  timer, so a tool call that runs for minutes keeps the proxies' idle timers
  reset. The browser follows a dropped answer by polling the transcript (10
  minutes for an answer, 30 for a document); a question marked interrupted, or
  a poll that runs out, shows 「중단됨」 with **다시 보내기** under the
  question. Deploys replace replicas one at a time
  (`scripts/rolling-restart-api.sh`, see `docs/deployment.md`).
- The first signup is decided under a transaction-scoped advisory lock, so two
  simultaneous first signups cannot both become the administrator.
- Office and HWPX archives — text extraction, HWPX import, and the HWPX,
  Word and PowerPoint editors — are opened only through
  `files.open_capped_zip`: declared sizes must pass a limit (64 MB unpacked,
  4,000 entries, 32 MB a member), a member over 1 MB may not inflate more than
  200 times its packed size, and only stored or deflated members are accepted
  (bzip2 and LZMA inflate a chunk without an output bound). `zipfile` stops
  inflating a member at its declared size, so a member lying about it fails its
  CRC instead of growing; `CappedZipFile.read` still counts what it actually
  unpacks. Parsing runs in a worker thread so one upload cannot stall the
  event loop.
- The release workflow publishes an image only after the CI workflow passed on
  the same commit (`verify` calls `ci.yml`).

## 8. Agent knowledge and retrieval

An agent can carry documents of its own: files uploaded to it, and pages read
once from a URL and stored as text. They are `files` rows with `agent_id` set,
so extraction, blob storage and token counting are the same as anywhere else.

They are **searched, not injected**: an agent's shelf is reached through the
`search_knowledge` tool, so retrieval happens when the model asks for it.

Project knowledge is indexed as well: a project has its own collection
(`projects.index_key`, minted on the first indexed document) that holds its
files, its URL snapshots and the uploads made in its conversations, so one
`search_knowledge` call covers all of them. A conversation inside a project
searches the project's collection and, for uploads indexed before the project
had one, its own (comma-separated collections); one outside it searches its
own. Each file records the collection its write went to
(`files.index_collection`): deleting the file forgets that one; moving the
conversation to another project forgets the old entry and indexes the file
into the new home's collection (`reindex_session_files`); deleting the
conversation, singly or in bulk, forgets its uploads' entries while the rows
stay in the file box (`forget_session_files`), unless the person ticked 「대화의 파일도 함께
삭제」 (`?files=true`, bulk `files: true`): then the files attached or made in it go too,
a project's or an agent's excepted (`delete_session_files`). Each file records how it came
(`files.origin`: `upload`, or `generated` for a code run's output, an image, a chart, a
figure, audio, video, an export) and, when it only copies an artifact listed elsewhere (a
document's export, the picture of a chart or a diagram), which one (`files.copy_of`). The file
box (`/files`) lists 「올린 파일」 and 「만든 파일」 (`?tab=made`) apart, copies left out, a page
at a time (`GET /files/page?box=true`: agents' knowledge left out; `origin`, a name search `q`,
`sort` `created`/`name`/`size` with `dir`, a keyset `cursor` tied to the sort; `total`,
per-origin `counts` and `working` in the reply), and a composer's 「파일함에서 고르기」
attaches one again by id. A third tab, 「작업 문서」 (`/files?tab=working`), lists the documents
worked on as a whole (HWP, Word and PowerPoint artifacts, web reports and web slides), one row
per artifact (`GET /artifacts/working`: `kind` one kind or a comma list, a title search `q`,
`sort` `updated`/`title`/`kind`/`version` with `dir`, a keyset `cursor`; `total`, per-kind
`counts`, each row's conversation and project names; someone else's or a deleted conversation's
left out). The rows are the artifacts themselves, not copies: they open in the editor they
have in chat (`/s/{session}?artifact={id}`; an office document with no conversation joins a new
chat, a web document with none opens over the file box, `/files?tab=working&doc={id}`),
download in their own format (the office file, Markdown, `.pptx`), and rename and delete
through `PATCH`/`DELETE /artifacts/{id}`. Deleting an office document, or the conversation
that holds it (singly or in bulk), also removes the file it keeps per version, after the
commit (`office_doc.kept_files`, `drop_files`); deleting an account with its files takes them
with its directory, and one deleted with `purgeFiles=false` leaves them to the storage sweep.
Uploaded office files stay in 올린 파일 as files; a copy opened from one for editing is the
working document. Every file-box tab sorts on the server by its column headings
(`components/files/ListSort.tsx`: `SortHeader` cells, the sorted one carrying `aria-sort`),
the order kept in the address (`?sort=…&dir=…`); a phone picks column and direction instead
(`PhoneSort`). Deleting
the project drops the collection; `POST /projects/{id}/knowledge/reindex`
backfills files uploaded before the index existed. The lexical shelf the tool
also reads includes the project's files, so a passage the budget could not
carry whole is still reachable by lookup.

Project knowledge is also injected: it shares the turn's file budget and goes whole
while it fits (§7). Once it does not, the turn still sees **every file**:

- **Per-file digests.** Each project file gets a short digest (`files.digest`: unit label
  such as 「3강」, title, a 2–4 sentence summary, its outline and key terms, plus the text
  length it was made from). `services/digest_jobs.py` makes them in the background, claimed
  like OCR jobs (`digest_state` queued → running → done/failed, three attempts, a stale claim
  taken over). A file is queued when its text arrives (upload, URL snapshot, finished OCR),
  lazily when a turn finds it without a current digest, or by the administrator's backfill
  (`POST /admin/knowledge/digests`, `?project_id=`, `?force=true`). The digest is written
  only by a strict-local model the catalogue lists as such (`KNOWLEDGE_DIGEST_MODEL`,
  default `strict-local/qwen3.8-flash`; an outside model configured there is never used), so a
  file's text never leaves the deployment whatever model the conversation uses. The file
  text is sent as delimited data the prompt tells the model never to follow. The call is
  billed as a side call (`knowledge.digest`): a priced model at its full rate, a self-hosted
  one as a zero-credit ledger row with the tokens. Until a digest exists (or when no
  strict-local model is served) a code digest stands in: the label from the file name, the
  first line as title, headings and frequent terms (`file_digest.code_digest`).
- **The 「자료 목록」.** The knowledge block opens with every file, one entry each, within
  45% of the knowledge budget: full entries, then one-line entries, then name and label
  only, and past that as many names as fit with the rest counted.
- **Passages after the list.** A focused question gets the best eight passages, lexical and
  — outside strict-local turns, as with `search_knowledge` — merged with the index's hits.
  A request about each item (`coverage.asks_each`: 「각 강」, 「모든 자료」, 「주차별」,
  「21개 강의」, "each lecture") gets one passage from every file instead (its best match, or
  its opening when the ask names no topic), each within an even share of the room, and the
  file report adds a rule to cover every listed file and name any it cannot.
- **No silent drop.** For such a request, the report pipeline's `coverage` stage and the
  chat finish stage `answer-coverage` look for each file in the finished text (its unit
  label, title, file name or citation number) and end the document or answer with
  「※ 자료 N건 중 M건은 이 답에서 다루지 못했습니다: …」 when some are missing.

A file that reached the model only as its list entry is a `ContextFile` with `listed`; it
becomes a numbered source of a document only when the request asks about each item.

Files uploaded into a conversation sit on the same shelf. They are injected
(§7) *and* searchable: the tool is what finds the passage the injected excerpt
missed, with a query the model wrote from the whole conversation rather than
the last short message. Each conversation gets an index collection of its own
(`sessions.index_key`, minted on the first indexed upload, dropped with the
conversation); in an agent's conversation the vector half searches the agent's
collection and the uploads are covered lexically.

Three tiers, chosen by size and by what is available:

| Shelf | Behaviour |
|---|---|
| under 12,000 characters | returned whole, unranked — nothing to miss |
| larger | vector hits merged with lexical hits |
| no index configured, or it is down | lexical alone |

`services/knowledge.py` is the lexical half: term containment plus character
bigrams, which is what carries Korean, where whitespace tokens include
particles. It matches words, so it cannot find 접근 통제 (Korean for "access control") from "access control".

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

### Knowledge shelf (지식 서가)

The project's sources — its `files` rows: uploads, web snapshots, pasted text, notes made
into sources, video/recording transcripts — get three things on top of §7's injection and
the `search_knowledge` shelf. User-facing names live in `apps/web/src/lib/shelfNames.ts`
(server titles in `services/shelf_names.py`), so the working name can change in one place.

**Per-conversation selection.** `source_selections` keeps, per conversation, the source ids
switched *off* (no row = all on; a source added later starts on). `source_selection.keep`
drops them in both places a turn reads project material: `workspace_context._project_knowledge`
(injection) and `sessions._knowledge_shelf` (the lexical shelf, and through `_living_hits`
the vector hits too, which are kept only for documents still on the shelf).

**Guides and citations.** `source_guides` (one row per source, plus `subject = "project"`)
holds a summary, key topics and suggested questions, written in the background by the
strict-local guide model (`shelf_models`) the first time someone opens one and kept
until the material's signature (ids and text lengths) changes; a per-file digest
(`files.digest`, when present) fills the summary meanwhile. A chat answer that cites project
material ends with a source list of names: the project files a turn was given are numbered
as material sources before any search (`agent._with_material_numbers`, from the context's
`knowledge` fates, after the numbers earlier answers used), a system note tells the writer
which number is which file, and search passages (`agent._number_sources`) number on after
them; material citations keep their plain `[n]` and the server lists them by name. A
citation of a project file the citing sentence is not backed by (`claim_support`) is taken
out before the answer is linked. The web links its
inline `[n]` to the source viewer, which asks `GET /projects/{id}/shelf/locate` for the
passage closest to the citing sentence (`shelf_sources.locate`: lexical chunk score, then
the best sentence), its page from `[페이지 n]` marks and, for a transcript, its
`[HH:MM:SS]` time code; a video plays from there in the file box (`/files?open=<id>&t=<s>`).

**Studio and notes.** `studio_items` are one-click outputs grounded in the sources the person
picks: documents (study guide, FAQ, briefing, timeline, glossary, mind map), quiz and
flashcard sets; the studio writes text only and makes no audio. `studio.start` writes a `running` row
and returns; the work runs on its own DB session (`shelf_jobs.spawn`; a row older than 20
minutes in `running` reads as failed). The sources are numbered `[n] name` within the
writer's window (`shelf_sources.bundle`: small ones whole, long ones read in windows spread
to their end), the model answers one JSON object per kind (`studio_kinds`), and code then
holds the output to its sources: citations of numbers outside the list are removed, a
document with no citation at all is asked for once more, a glossary keeps only terms
written in the sources, and quiz items need a valid key, distinct choices (shuffled so the
key's place carries no pattern) and an explanation. `shelf_checks` then reads every
sentence, question and card: its script (Japanese kana, Chinese outside a gloss, other
scripts, a Hangul word glued to Latin letters, an English word the sources never use, a
source term misspelt by one letter), its grounding (`claim_support`: the numbers of a cited
sentence must be in its cited sources and at least 35% of its content words, matched with
up to two letters of particle or ending), and its fluency (the writer and the guide model
each give a verdict per sentence; a verdict naming the sources' own wording is ignored).
What fails goes back to the writer once, in one call, with its problem and its cited
passages; what still fails is mended in code (a script slip) or dropped (an unbacked or
garbled sentence, or an item left without its question or answer).
Guide questions are kept only when the writer and the guide model both find every part of
the answer in the question's best passages. Documents are saved as a report artifact
(or an HWP/Word file through `report_office`) in a conversation of their own, so they open,
edit and export in the document panel; quiz sheets take the same path. `project_notes` is
the 메모함: notes pinned from an answer or a selection or written by hand, saved explicitly
with a version check (`expected` → `note_conflict`), and made into a source (a `.md` file,
indexed like an upload; later edits update it).

**Models.** Which model writes each output kind, and with what reasoning effort, is one table
(`services/shelf_models.py`, overridable as JSON in `KNOWLEDGE_MODELS`): a strict-local pick
and a pick for where material may leave (the Super Agent's escalation model, reasoning on).
Documents are written without thinking, quizzes and flashcards with it; a thinking call that
runs out of room is answered again without thinking. At most `KNOWLEDGE_CONCURRENCY` calls
(default 2) run at once per replica, since the self-hosted models serve conversations on the
same node.

**Boundaries and billing.** Project sources are internal material: while
`governance.internal_data_strict_local` holds, guides and studio outputs use a strict-local
model only (`shelf_calls.route`; asking for another is refused with
`internal_data_requires_strict_local`); otherwise an outside pick sees the text masked for
personal data. Every call is a side call billed
at the full rate with no cache discount, or a zero-credit token row on a self-hosted model;
`POST /projects/{id}/studio/estimate` shows tokens and credits before anything runs.
Audio and video reach the shelf only as input, turned into text (transcripts, frame reading,
OCR); nothing is spoken back.

---

## 9. Frontend structure

```
apps/web/src/
  pages/        one per route; settings/ holds the account tabs and the admin
                System screen's sections, customize/ the agent, skill, template
                and publication editors
  components/   one folder per area: chat · report · slides · chart · artifacts ·
                office · files · shelf · projects · media · agents · chain ·
                customize · publishing · issues · memory · share · auth ·
                layout · ui · …
  store/        useStore.ts — a single zustand store
  lib/          api.ts (the backend seam) · kinds.ts · i18n.ts · mermaid.ts ·
                shelfNames.ts · …
```

**The store has an epoch guard.** Every action that writes to the workspace
calls `touchWorkspace()`, which invalidates any `loadWorkspace()` response
still in flight — otherwise a late response overwrites the local write.

---

## 10. Verification

```bash
cd apps/api && pytest -q             # unit and pipeline tests, no services
bash scripts/smoke-test.sh           # auth, approval, rotation and suspension checks (non-destructive)
cd apps/web && npm run test:e2e:ci   # fixture suite, API stubbed (CI)
cd apps/web && npx playwright test   # full E2E against a running stack — pinned to workers: 1
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
  four claims per slide or section.
- **The first-signup bootstrap test** needs an empty database and is skipped by
  default.
- **Speech-to-text needs the internal Whisper backend.** Without it the
  composer's microphone is not rendered and audio files are not read. The `youtube` connector handles
  only videos that have captions.
- **Runtime settings are cached per replica** for 15 seconds, so with several
  API replicas a change reaches the others within that window. See
  [deployment.md](deployment.md#scaling-notes).
- **A region needs a mounted GeoLite2 file.** There is no network lookup, by
  design, so without one every screen shows an address and no place.
