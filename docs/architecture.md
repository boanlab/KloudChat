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
   │           └── MCP connectors (stdio · http)
   └── /api/files/…?t=… ← media is fetched by the element itself (§6)
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
elsewhere means overriding that one field. A feature left blank drops out of the tool list and everything
else keeps working.

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

### What is recorded about access

`audit_events` carries a sign-in, a failed sign-in, a password change and a key
issue, each with the client's address and browser. Two screens read it: an
account's own **Access log** (`GET /auth/me/access`, the account's rows only) and
the administrator's audit trail (`GET /admin/audit`, everything, a page at a time
with action, account, period and text filters). A change event keeps
`metadata.changes = {field: [old, new]}` from `services/audit.py:diff`, secrets masked
and long values clipped; `audit.ACTIONS` lists every code, and the screen labels each one.

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
call (§ Coding agents), and web searches
(`search.web`, one row per turn with the number of queries the turn sent to
SearXNG, counted from `ToolContext.tool_calls` in chat and from the research
step on the document surfaces). The usage screens read searches from there.

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

Migrations live under `alembic/versions/`. The principal tables:

- `users` · `refresh_tokens` (family-based rotation) · `password_resets` ·
  `api_keys` · `audit_events`
- `sessions` · `messages` — conversation. `messages.usage` is JSONB
- `credit_ledger` — the ledger
- `projects` · `files` · `file_pages` (OCR text per page) · `artifacts` ·
  `artifact_versions` · `skills` · `memories` · `agents` · `connectors` ·
  `connector_tools` · `connector_credentials` · `connector_catalog`
- `design_systems` — themes: the look a project's or agent's output wears.
  `projects.design_system_id` is nullable and null by default, and
  `ON DELETE SET NULL`: removing a theme costs the projects their look, not the
  projects
- `user_templates` — templates people make in the editor (§6 Templates)
- `user_pipelines` · `pipeline_runs` — composed step lists and bundles, and the
  recorded runs of every writing pass (§6 Pipelines)
- `publications` · `publication_versions` · `publication_visitors` ·
  `publication_counters` · `publication_slots` · `publication_questions` ·
  `publication_keys` — published agents (§6 Publishing agents)
- `issue_reports` · `issue_replies` — Feedback & issues reports and the administrators' replies
- `metric_samples` — the performance monitor's series
- `templates` — not read or written by the application; the table and its rows
  remain in the schema
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
  createdAt}`); the conversation continues from the row that carries them.
- `sessions.summary` — `{through, text, turns}`: the summary standing in for the
  turns too old to send once a conversation outgrows the model's window (§7).
- `sessions.skill_ids` — the skills switched on for the conversation (§6 Chat).
- `messages.failure` — how a turn ended when it did not end in an answer.
  `no_answer` sits on the question, `interrupted` on a partial reply, and
  `stopped` on either when **Stop** was pressed — same shape, the reader's choice
- `jobs` — video only. Without `provider_job_id`, a restart orphans a
  half-generated clip
- `system_settings` — LiteLLM address and master key, backend and tool
  addresses, model roles, enabled surfaces, OCR, branding, SMTP, signup policy.
  Database values override environment variables
- `governance` — the one instance-wide policy row: privacy guard and masking,
  internal-material routing, Auto routing lanes, outline model, intent filter,
  idle sign-out, retention

`files` carries the scope it belongs to: `project_id` for project knowledge,
`session_id` for a one-off attachment, `agent_id` for an agent's searchable
shelf. `source_url` marks text read from a page rather than uploaded, and
`indexed_at` whether the vector index covers it.

A conversation and its artifacts are one record: deleting a conversation,
singly or in bulk, deletes the artifacts it produced with their versions and
shares (`sessions._delete_artifacts_of`).

---

## 6. Execution paths

### Chat

`POST /sessions/{id}/messages` → SSE, emitting `delta`, `step`, `artifact`,
`usage`, `title` and `done`. One answer at a time: a second message while this
process is still writing one (and nobody pressed **Stop**) is refused with 409
`session_busy`; with several replicas the shared transcript stands in for the
other processes' signals (a fresh question with no reply and no failure mark).
`retry_of` on the latest question reruns it; when that question already had an
answer the old answer is kept on the new row as `messages.superseded` (the
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
`[]` switches them off. A pick for one turn therefore shapes the whole
conversation, as a skill one reaches for should. A standing skill that has since
been deleted, disabled or barred by the agent drops out quietly and the stored
set is pruned (`_standing_skill_ids`); only a skill picked for this turn is
checked strictly.

**The model can reach for a skill itself.** The installed, enabled chat skills
that are not switched on are offered as one tool, `use_skill`, whose description
lists each by its `when_to_use`. When a request matches one, the model calls the
tool once and receives the skill's instructions as the tool result — the one
tool result the rules say to follow, since the person installed it — and
answers under them. The step shows as "applying a skill" (스킬 적용). Up to twelve skills are
offered per turn so the description stays a paragraph, and an agent's skill
allowlist bounds the shelf as it bounds a hand pick.

Tools are attached only when the model supports function calling — giving them
to a model that does not yields either a 400 from upstream or an invented call.
Built-in tools (`services/tools/builtin.py`): `web_search` (SearXNG),
`fetch_url` (Crawl4AI), `weather` (Nominatim geocoding plus the Open-Meteo
forecast, keyless), `execute_code` (sandboxed; files it names are mounted), `run_skill_script`, `run_code`, `calculate` (bounded exact
arithmetic, no code execution; `tools/arithmetic.py`), `create_artifact`,
`create_chart`, `share_note`,
`use_skill`, and `search_knowledge` over an agent's own documents and the files
uploaded into the conversation (§8). Tools from
installed MCP connectors are added to these.

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
One sandbox session serves a turn, kept on `ToolContext.sandbox`.

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

**Missing-information questions** are data (`services/gap_questions.py`): a template lists the questions its
format needs (`[[questions]]` in `template.toml`), an agent adds its own
(`agents.gap_questions`, the agent form; a shipped agent's come from its file). Each names
a detector — a built-in one from `gap-check/scripts/gaps.py`, keywords, a bounded pattern,
or the judge model, which must quote the answer from the material. A template's document format
(`doc_format`) and genre rule (`genre.md`) are carried by the template too. The sandbox sits in
the self-hosted stack with networking off, so with `SANDBOX_IN_BOUNDARY` (on) both tools
are offered to strict-local turns, and `run_code` then writes on the `strict-local/`
twin of the code model.

**The web-search toggle has three positions.** `context.search_plan` turns the
toggle and the user's words into two facts: whether the web tools (`web_search`,
`weather`) are offered this turn, and which tool the first hop must call. **On**
offers them and forces a search every turn; **Off** offers nothing unless the
words explicitly ask for research; **Auto**, the default, offers them and forces
the first hop only when the words ask for research, for something that changes
with time (news, prices, versions, schedules, a year), or for the weather —
otherwise the model decides under a lighter rule (`_WEB_SEARCH_AUTO`) that says
what to look up and what to answer outright. On a chat turn the route also forces the
first search, for every model, when `services/exact_facts.search_reason` finds a fact
that must be exact (a phone number or hotline, a law, tax rule, threshold or benefit, a
price, rate or limit, an event date, availability, an organization, a statistic) or
legal, tax, financial or medical advice; a short follow-up is read with the earlier user
turns, and the model then writes the query from the conversation. Writing, translation,
code and formulas, role-play, drafting, concept explanations and turns on the person's
own material are left alone (`exact_facts.not_a_lookup`), and an explicit opt-out or Off
stands. A model-written query keeps the request's named things
(`query_terms.keep_entities`).

**Advice answers are traced by default.** `exact_facts.advice_domain` puts the
`grounding-check` skill on a legal, tax, financial or medical chat turn, as an agent that
turned it on would, and the writer gets `freshness.ADVICE_INSTRUCTION`. With
`"advice": true` the trace checks money and rates, phone numbers, doses and legal
consequences against the turn's search results and the person's words (not earlier
answers), marks each unsupported one 「(확인 필요)」 where it stands rather than blanking
it, and leaves agency names and the guidance's durations alone; more than three marks
are listed once under the answer. The step reads 「근거 확인(법률): …」. A strict-local route offers none of
this, whatever the toggle says. Weather questions force the `weather` tool
rather than a search, since a search engine returns encyclopaedia pages for
「분당 날씨」 ("Bundang weather").

**A search runs two lanes and reads dates.** `web_search` asks SearXNG's
general lane and, when the query carries a time-sensitive word (a year, 최신 "latest",
가격 "price", a named release), the news lane too, with the news hits first; the
general lane answers 「최신 모델」 ("latest model") with home pages and encyclopaedias. Front
pages and a host's third hit are benched (used only to pad a thin list), and a
hit's `publishedDate` reaches the model as 「게시일」 ("published on") so the newest wins. A turn
makes at most three searches and six page reads (six searches when a turned-on skill
sets `process: {deep_search: true}`, as 기업 조사 and 경쟁사 분석 do, plus one for each
thing the request lists or hands over to check); past either cap the model is told to answer
from what it has. On a deep-search turn an answer written before every name the request
lists (「잔디, 플로우, 네이버웍스를」) appears in some query, or before every memo line it
hands over (「- 공공 클라우드 사업도 한다고 함」) has two of its words in one query, is held
once and the model is asked to search the rest (`query_terms.unsearched`). A listed source
with no 게시일 carries the day it was read (「· 확인 2026-10-15」). The skills' last check
(`source-check/post`) also takes out a citation of a page about another company: a company
directory's profile of another firm in a 「○○ 한눈에」 one-pager, or, in a comparison
table, sources that name another column's company and not the cell's own, or a page off the
column's own sites about another product of the same name (「GitHub Copilot」 in a 365
Copilot column).

**Sources are cited by number, never typed.** `run_turn` renumbers the `[n]`
entries of every search result so the numbers run across the whole turn and
gives a fetched page a number of its own; the prompt asks the model to cite
`[3]` at the end of a sentence and not to copy URLs. After the answer,
`_link_citations` turns each `[n]` into a link to that source and a numbered
「출처」 ("Sources") list is appended; numbers no result carries stay as text. A model that
types a URL anyway is checked against the tool results. A stream
that repeats one letter or digit forty times in a row (a decoder stuck on a
long article id) is closed there: the run is retracted, and a URL it broke is
completed when exactly one tool result starts the same way, dropped otherwise.

**A fetch reaches the public internet only.** Every page read on a reader's
behalf — the `fetch_url` tool, search-result bodies, research picks, project
and agent knowledge from a URL, a design read from a page — passes
`services/netguard.py` first: http(s) only, and every address the host
resolves to must be public (no loopback, private, link-local, CGNAT or
metadata addresses; no dotless service names). The Crawl4AI shim applies the
same rule again, including to the address it lands on after redirects, since
the tool gateway reaches it without authentication.

**Tool count is what sizes the connector catalogue.** Every active tool ships
its full schema on every turn, and model tool-choice degrades well before
twenty of them. A server enters the catalogue once it has been started against
a real credential and had its tool count checked.

Artifact extraction and automatic memory run **after** the turn, in their own
transaction: sharing the turn's would hold it open for an extra query and a
model call, and a failure in either would roll the answer back.

**The turn outlives the connection.** The work runs in its own task and the SSE
response only relays what it emits, so closing the tab cancels the relay and
not the turn — the answer is still stored, charged and titled. Which makes
stopping a separate act rather than a dropped socket: `POST
/sessions/{id}/stop` sets a flag the token loop checks, and the turn settles
where it is with the partial answer kept and the row marked `stopped` (a
dropped connection marks it `interrupted`). The proxy reports usage on its
final chunk, which a stopped stream never reaches, so a stopped turn's usage
is estimated from what went up and what came down and stored with
`estimated: true` — the tokens were spent either way.

### Report and slides

Both are two-pass: one outline call returns the title and the table of
contents, then one call per section or slide, each carrying what the previous
ones wrote. Six listed sections means six are coming, which is what makes the
progress indicator honest.

**The plan is approved before anything is written.** The outline turn ends
with a `proposal` event: the plan (title, parts, visual style, proposed
pictures) is stored on the session and shown as a card, and writing starts
when the person approves it, possibly edited. When the material is too thin to
write from, the turn asks instead (`needs`, `services/grounding.py`) and writes
nothing.

A failed section is marked and the rest continues.

**The plan is checked before anything is written.** `services/outline.py`
holds the rule both tracks use — no run of three identical layouts, and at
least three of the offered layouts (or as many as exist). A plan that fails it
is asked for once more, naming the layouts it skipped; the second answer is
kept only if it is less flat, so the worst case is one extra outline call.
Nothing is coerced: a layout assigned to a heading it does not suit would be a
worse slide than a repeated one.

Both receive the workspace blocks the chat surface gets — project
instructions, memories, skills, and any file attached to the turn — so a
request naming an uploaded form is written against that form.

Reports search the web before writing and cite what they found; with no search
backend the shelf is empty and the citation rule drops out of the prompts. A
request about Korean scholarship also looks for KCI, DBpia and RISS papers
through the same search backend (`services/korean_papers.py`; there is no
separate KCI API or key) and reads each landing page's bibliographic meta tags,
so a reference carries its authors, year, venue and DOI.

Slide layouts are listed in `services/deck.py` (`title`, `agenda`, `section`,
`bullets`, `quote`, `two-column`, `table`, `metrics`, `chart`, …), each
implemented in all three renderers (preview, `.pptx`, `.pdf`). The outline
also picks the deck's accent from a fixed palette — unless the project wears a
theme, in which case the palette rule is dropped from the prompt
entirely and the accent arrives from the project. A slide count stated in the
request is honoured up to 50.

**The outline can run on a different model.** `governance.outline_model_id`
names one; empty — the default everywhere — plans with the surface's own model.
It applies to all three writing tracks and only to the planning call, which is
one call per document however many blocks follow it.

Its tokens are billed at its own price: the turn reports planning and writing
apart, so the ledger says where the money went.

The outline carries the same request and the same context the body does, so it
is subject to everything the body is: the id is checked when it is saved, and
again per turn against this account's allowlist, this surface, and the boundary
the turn was decided on. A turn privacy routed to a strict-local model gets no
planner at all — that route exists so the text does not leave — and a planner
may never be less contained than the writer, or a field on the admin screen
would quietly widen the egress of every document. Anything that fails falls
back to the writing model rather than failing the turn.

**Revising one slide the way the person said.** A slide rewrite reads the
layout the words ask for — 「연표로」 (as a timeline), 「단계로」 (as steps), 「차트로」 (as a chart),
「큰 숫자로」 (as big numbers), 「표로」 (as a table) (`deck.requested_layout`;
「연표」 "timeline" is not a 「표」 "table") — and writes with that layout's
prompt; a chart or metrics answer replaces the slide's chart or metrics, not
just its notes, and a title the person asked to change (「제목을 …로 바꿔 줘」, "change the title to …") is
taken, quoted text verbatim. 「위험 장을 하나 추가해 줘」 ("add a risk slide") is an `insert`: the
revision planner names the new part and its place, the router slots a blank
part in and rewrites only it, so the other slides stay byte for byte. A restructure (「6장으로 줄여」, "cut it to six slides") is told to hit the count
exactly, merge only what was named, and keep every other part's title and
content.

**A restructure keeps what it did not change.** When the skeleton is re-planned
(「6장으로 줄여」), the source artifact's id rides in the pending proposal
(`carryFrom`) and, after the approved rewrite, every new part whose name
survived and which the instruction never named gets its words back from the
old part (`revise.carry_parts`): same position, new id and accent, old content.
A part the instruction names — 「지표와 진척은 한 장으로」 ("metrics and progress on one slide") — is written fresh;
"named" is judged against the restructure instruction (`carryNote`), never
against the merged request, which names every part of the original. A quoted
rename (「위험 절 제목을 「위험과 대응」으로」, "retitle the risk section
'Risks and responses'") sets the section's heading as well as
its words (`revise.requested_title`). An insert aimed *into* an existing part
(「비용 절에 표를 넣어 줘」, "put a table in the cost section") is that part's edit (`into_existing_part`), and one
that names its neighbour (「요청 사항 장 앞에」, "before the requests slide") lands exactly there
(`place_insert`). The deck writer is told that every figure the person wrote
must appear once.

**A term swap is done by hand.** 「문서 전체에서 「A」를 「B」로 통일해 줘」 ("use B for A throughout") is a string
replacement across every text field (`revise.term_swap`, `replace_term`): exact,
instant, and nothing else moves, rather than a model retyping and possibly
rewording every section.

**The words name the part.** When a revision instruction names exactly one
existing part (「다음 단계 장을 연표로」, "next-steps slide as a timeline"), that part is the target whatever the
planner guessed — another part, or a restructure of the whole deck
(`revise.named_target`); a
shorter name inside a longer part's name counts as the longer part's mention,
and an instruction that asks for a structural change (add, remove, merge,
reorder, a new count) is left to the planner. When the request itself asked for a
structure diagram or flow diagram (구조도, 흐름도) and the figure planner proposed nothing, it is asked once more
with that fact in front of it (`diagrams.asks_for_diagrams`); an empty plan on
a request that never mentioned diagrams stands as the planner's judgement.

**An outline has to be a deck.** A salvaged outline can be the planner's notes
to itself — 「Slides: 5 to 12. (6 is fine)」, repeated section names, an English
plan for a Korean request. `deck.sane_outline` refuses it so the outline is
asked for once more. A restructure strips the original request's part counts
(`revise.without_counts`) so the note's count is the only one the planner
reads. A section rewrite that drops the section's mermaid
figure, or redraws it as ASCII boxes, gets the figure back (`revise.keep_figure`)
unless the person asked for it to go.

**A stated count is a restructure.** 「전체를 6장으로 줄여 줘」 ("cut the whole thing
to six slides") against a nine-slide
deck is re-planned whatever the revision planner read it as — a partial edit, or
a request for a new document (`revise.count_change`). A count equal to the current one, or no count,
leaves the planner's reading alone.

**One or two over the count.** Asked twice for exactly N slides and still over,
the plan loses what carries no argument before the turn fails: section dividers
first, then the agenda — a six-slide deck needs no table of contents
(`deck.fit_count`). Content slides are never dropped there; a plan still over,
or under, goes to the honest error.

**A send never supersedes a live turn.** The pre-flight 409 and the turn's
registration are far apart; two sends that both passed the check meet at the
registration, where a fresh send (not a regenerate, not a message after **Stop**)
finding a live turn bows out with `session_busy` and removes its own question
row, so the transcript stays question, answer.

**A written product is checked.** A rewrite that says 「470만 원 × 36개월 =
169,200만 원」 ("4.7M won × 36 months = 1,692M won") has written its own proof of error; `report.fix_products`
recomputes every 「A × B = C」 and replaces a wrong C, with its scale and unit,
wherever the section repeats it — the sentence and the table cell alike. Only
plain products with the same scale on both sides are touched.

**The part the words mean.** 「오버헤드 결과 장을」 ("the overhead results slide") for a slide called
「오버헤드 측정 결과」 ("overhead measurement results") is that slide: every word of the phrase sits in the one name. A part
the deck truly lacks (no name shares a word) is made, before the closing slide,
rather than guessed from another slide (`revise.named_target`).

**A section is prose, with the data in it.** A drafted section that came back as
a one-key JSON object (`{"결과의 섹션 본문": "…"}`, "section body of the results") is unwrapped to its text
(`report.unwrap_json_prose`). One whose table is a frame of 「(미정)」 ("TBD") cells while
the attached material carries numbers is asked for once more with the data in
front of it (`placeholder_heavy`, `_FILL_PROMPT`); a cell the data cannot fill
is 「—」, never an invented value.

**Notes are notes.** 「모든 장에 발표자 노트를 두 문장씩 넣어 줘」 ("two sentences of speaker
notes on every slide") is a whole-deck
pass whatever the planner picked (`revise.notes_everywhere`), and a notes-only
rewrite (`revise.notes_only`) changes nothing on the slide but its notes.

### Templates

A template is a document or deck structure the model writes into; a theme
(below) is its look. Shipped templates live inside the API image under
`app/design_templates/<id>/`: a `template.toml`, a `seed.html` and a
`sample.html` always, plus the `instructions.md` and `checklist.md` a writing
turn reads. The catalogue is a short list of `deck-*` and `doc-*` entries,
written against this product's constraints: no script and no parent-relative
assets, because artifacts render in a `sandbox=""` iframe. Templates apply to
the report and slides surfaces only; the image and audio/video surfaces take a
plain prompt.

A person's own template (`user_templates`, `/user-templates`, the editor at
`/customize/templates`) holds every field a shipped one sets — what to bring,
instructions and checklist, genre rule, document format, missing-information questions and
process settings — and is drawn in the shared document seed (`_document`) with
the theme's tokens: no HTML or CSS of its own. A form file it was made from is
kept as reference text only. Sessions and agents name it `ut-<id>`.

Picking one **replaces the surface's built-in track**. A slides session with
`render_template_id` set produces an `html` artifact through `services/page.py`
instead of a JSON deck, and a report session produces one instead of markdown
sections. The choice is stored on the session, so a follow-up turn keeps the
shape without resending it.

The pass structure is the same as the other two tracks — one outline call, then
one call per block. An outline that will not parse is salvaged with a regex
before the turn is abandoned: a small model drops a quote often enough that the
difference is a whole paid-for call, and a plan legible to somebody reading the
log should be legible to the code reading it. What cannot be salvaged is
logged with what the model actually said, because a refusal and a malformed
answer are otherwise the same silence.

**The model never writes layout.** It is given the
block's layout name and returns the *inside* of that block; `design_templates.
assemble` puts it inside the markup the seed styles, and `sanitise` reduces it
to a fixed tag vocabulary first. It parses the block the way a browser does
(nh3, an HTML5 parser) and writes it back from the tree, so only listed tags
and, per tag, listed attributes survive; a regex pass would have to know every
attribute that can carry a URL or a handler, and an unclosed quote could carry
markup past it. Script elements, event handlers, remote `src`/`href`, and
`h1`/`h2` are removed with their contents: the first three because the file is
downloaded and opened outside the sandbox, the last because the wrapper already
wrote that heading and a second one prints the title twice.

**Figures a document draws for itself.** After the draft, one planning call
(`services/diagrams.py`) names up to three parts where a structure, flow,
comparison or concept figure (구조도·흐름도·비교도·개념도 in Korean requests) says more than the
words; a report writer can also place one inline with a `[[그림: …]]` mark (그림, "figure").
`services/diagram.py` writes each as a mermaid flowchart source in the house
style and `diagram.draw_native` draws it: code lays the flowchart out
(`flow_layout`), `diagram_shapes` draws it as native python-pptx shapes on a
one-slide deck (`diagram_render`), and the print sidecar renders that slide to
PNG through LibreOffice (`POST /pptx/png`). The panel, the PDF and the report
show that picture, and the deck's `.pptx` carries the same shapes, editable. A source the shapes cannot read (not a flowchart, more than
`diagram_shapes.MAX_NODES` nodes) goes back to the writer once, told why; still
unreadable, or with no sidecar answering (logged as a warning), the figure is
left out — a report drops the mark and the 〔그림〕 ("figure") reference pointing at it, a deck slide
keeps its words. Documents get no browser-drawn figure in its place; only chat
answers draw mermaid in the browser, and a document that already carries a
mermaid fence still opens and exports. The same path serves a figure made by hand: the picture picker in a
report or deck and the image surface's **Structure diagram** · **Flow diagram** · **Concept diagram** chips call
`POST /sessions/{id}/diagrams`, which stores the PNG as an image artifact with
its source, or answers 422 (`diagram_not_drawable` / `diagram_unrendered`). Placed
on a slide, such a figure keeps its source as `slide.diagram` and is drawn again
in the slide's accent. No image model and no card: the only cost is the writer's
tokens. A picture from the image model placed on that slide replaces the figure. Pictures from the
image model are proposed on the outline card only for illustration or real
objects, never for structure or numbers, and a deck's structure slide is moved
off layouts the figure planner cannot draw on (`deck.structure_as_drawable`).

**Pictures come from the other direction.** The writing model cannot make one
and cannot reference one — `sanitise` drops every `src` that is not already
inside the file. What a person can do is put a picture this workspace made on
the image surface into a block: `POST /artifacts/{id}/blocks/image` reads the
stored blob, inlines it as a `data:` URI and re-renders the document. Raster
only (`image/svg+xml` is a document that can carry script), capped at 3 MB
before encoding, and snapshotted like a rewrite. The artifact stays one file
that prints, downloads and shares with the picture in it, and a reader opening
it fetches nothing.

**And it leaves in the exports.** `page_export` reads the `<figure>` back out
of the markup — bytes and caption — and hands it to each renderer: a slide
takes the right half and the words narrow to fit, or the picture takes the
middle when there is nothing else on the slide; a document places it after the
section's prose, at 120 mm wide and never taller than the page, with the
caption kept on the same page as the picture. Sized from the bytes with
Pillow, because nothing in the artifact says how big a picture is and a figure
drawn to a guessed box is a squashed one.

**Every format carries it, `.hwpx` included.** A picture in OWPML is three
things that have to agree — the bytes in `BinData/imageN.png` (stored, not
deflated), an `<opf:item id="imageN" … isEmbeded="1"/>` in
`Contents/content.hpf` (one `d`: OWPML's own spelling, and Hancom drops the
picture without it), and `<hc:img binaryItemIDRef="imageN">` inside an
`<hp:pic>` in the section. Nothing is declared in `header.xml`:
`<hh:binDataList>` belongs to the older HML format. Every document also carries
an `<hp:secPr>` with A4 and its margins in the first paragraph's run: without
page geometry, an object sized in absolute units is read and never drawn. The
structure follows genuine Hancom output (hwpxlib's test corpus); no
independent OWPML reader is installable to check it against.

A picture that has stopped being one — truncated bytes, a format a library
refuses — costs its own illustration and nothing else: every renderer catches
it, logs it and finishes the file.

**The JSON deck track has the same path.** `POST /artifacts/{id}/slides/image`
puts one on a slide by id. Its slides are rows in a JSONB column rather than
markup, so the picture is stored as the `data:` URI itself and the renderers
accept either shape — decoded bytes from the HTML reader, or the address from
a deck. The preview draws it where the exporters do, so what the panel shows
is what the room sees. `services/pictures.py` owns the one rule underneath all
of it: raster only, embedded only, never fetched.

A template's typesetting is two files. `seed.html` — shared, named by `seed_from` —
is correctness: the tokens, the whole closed vocabulary `assemble` can emit, the
`@page` rule, the print rules, the shadow-root `:host`. `design.css` is
identity: the minutes template (`doc-minutes`) packs a compact grey head, the
high-contrast deck (`deck-signal`) presents white on black. It is spliced in as a second `<style>`
last in `<head>`, so it overrides by order rather than by specificity, and
`stylesheet()` — which concatenates every style block — carries it into the
document editor's shadow root by the same route. Two of them have no `design.css`
at all: `_document/seed.html` is the plain document's own design and `_deck/seed.html` is
editorial's, which is what makes the shared file somebody's rather than nobody's.

Stored documents and conversations may name an id the catalogue does not
offer. `design_templates.RETIRED` lists document ids drawn in `_document` alone,
with its `template.docx` (`design_templates.rendering`), so they keep opening,
taking pictures and exporting; `RETIRED_KEPT` lists ids still drawn in their
own design but chosen by nothing new; `RETIRED_MEDIA` lists image, audio and
video ids, read as naming no template. The page view of a report with no
template asks for the shared seed by the id `_document`.

`test_a_template_has_a_face.py` checks that every `var(--x)` a seed uses is
declared: an undeclared custom property is not an error anywhere, and
`background: var(--tint)` simply comes out white.

Two constraints shape every seed:

**No script.** Artifacts render in a `sandbox=""` iframe, so a deck navigates
by CSS scroll-snap rather than a keyboard runtime, and charts are drawn as
inline SVG. What a seed gives up is interaction, not design.

**Print is the export.** Every seed carries `@media print` rules and an
`@page` rule that put one slide or section on one page. `apps/print` reads
them: a headless Chromium in a container of its own that takes the finished
HTML and returns a PDF, so the `.pdf` is the `.html` printed rather than a
second drawing of it. See `services/printing.py`; where no printer is
configured (`PRINT_BASE_URL` empty), the structural renderer below still
produces a file.

That container is deliberately poor. What it opens is markup a model wrote —
sanitised and script-free, and still the least trusted input here — so it has
no database, no secrets, no published port, and a compose network with
`internal: true`, which means no route out at all. Inside the browser every
request is aborted as well, so an `<img src="http://…">` in model-authored
markup is not a request made from inside the deployment. Two walls: one a line
of code, the other a fact about the network.

The `.html` file is still the faithful copy, and is deliberately not opened in
a tab from the app: a `blob:` URL inherits this origin.

The other formats come from `services/page_export.py`, which reads the markup
back with the standard library's `HTMLParser`. That is possible because
`assemble` wrote it out of a closed vocabulary, so the structure is known
rather than guessed: each `<section>` becomes a slide or a section, keeping its
heading, its lines, which column each line was in, and its table rows. A deck
then goes through `deck_export` to `.pptx` and `.pdf`, a document through
`report_export` to `.docx`, `.pdf` and `.hwpx`.

What that conversion buys is **editability, not fidelity**, and that is the
division of labour: the `.pdf` carries the design because a browser draws it,
and the `.docx`/`.pptx` carry the structure because Word and PowerPoint have no
CSS to draw into. A design that survived the trip as a picture would open as a
document nobody can edit, which is not what somebody who brought their
organisation's template came for. The deck opens in PowerPoint as real slides in the
right order with the theme's accent and face, laid out by this
product's own deck renderer — not by the template's stylesheet. `deck_export`
carries a `table` layout, because flattening a table into bullets leaves the
reader to reassemble it, and an explicit `columns` field, because an HTML deck
knows which column each line was in. A JSON deck, which has neither, halves
its own list.

A template marked `dark` presents on a dark ground, and its `.pptx` follows —
the theme's ink is a colour chosen for paper and would be unreadable
there, so the two neutrals swap. The `.pdf` stays light, which is the same
decision that seed's own print rules already make: a projector and a printer
want opposite things.

One block can be rewritten after the fact — `POST /artifacts/{id}/blocks/rewrite`
— and that is why the artifact keeps its blocks whole, markup included, with
`content` as what they render to. The rewrite replaces one block and assembles
the document again from the same seed, rather than splicing markup into a
finished file where the seams are wherever the model last put them. It is
charged and snapshotted like the report's section rewrite, so a worse rewrite
is one click from undone.

The part is chosen from a list rather than by clicking into the preview: the
frame is `sandbox=""` and opaque to the app, which is the same property that
makes it safe. An artifact whose blocks carry no markup is refused rather than
rebuilt out of whichever pieces happened to be stored.

`GET /design-templates` lists the catalogue with both a Korean and an English
half — names, descriptions and example prompts — and the client picks by language. `GET /design-templates/{id}/preview`
renders the seed around its own sample and is **unauthenticated**, like the
branding logo: the body is a constant that ships in this image, an iframe `src`
cannot carry an Authorization header, and `current_viewer`'s `?t=` escape hatch
would put a live access token in the proxy log for a static asset.

That preview takes the four tokens as query parameters — `accent`, `ink`,
`muted`, `font` — because a card is only worth looking at if it is in the look
the file will come out in: a picker opened inside a project asks for that
project's theme, and the theme editor asks for whatever the form
currently holds, which is what makes three hex fields into something you can
see. The values go through `design.normalise_tokens` on the way in, the same
call the renderers make, so a bad one falls back per field and nothing but a
validated colour and one of two known faces reaches the document. The look
travels as itself rather than as an id, which is what keeps the route free of
anybody's rows and therefore still safe to leave open.

### Checking what was written

Three surfaces carry the same rules in their prompts — do not invent figures,
do not pad, keep emoji out of headings — stated in `craft`, in the per-surface
system prompts, and in the starter skills. Two stages read the answer back.

**Verify and repair (reports).** After the sections are written,
`verify.verify_and_repair` runs a list of checks (`services/checks.py`). Each
check only finds: the sentence, why it is wrong, and how to tell whether a
rewrite still is. Code checks cost nothing — numeric issues, one metric stated
with two values, references to a source's chapters the reader does not have
(「해당 자료」, "the material in question"), sentences that give a settled figure another value, and scaled
metric slips. Model checks run once, before the repair: a judge on the
figure sentences (only when a separate outline model exists, so the writer
does not judge itself), claims checked against the web when search is on, and
assessment items worked through by the judge (`services/fact_check.py`). One
`repair` call rewrites the found sentences; a rewrite is kept only when the
check that raised it no longer fires. Then the code checks run again. What
remains, plus model findings no rule can confirm, is listed in the turn's
quality check step (`quality`) for the reader to check — never passed off as done.

Around it: `key_figures` settles the figures a document set fixes (sale price,
variable cost, break-even, targets, budget …) once from the material, tells the writer, and
mends table rows and slides that drift from them; `calc` has the code sandbox
compute derived values before the prose is written; `units` corrects a money
table's unit label against the amounts the person gave; and
`quality_gate.report_findings` / `deck_findings` read the finished report or
deck for form defects — a broken block, a table with no caption, a figure
nobody mentions, a number stated two ways. Decks get the code-side checks
(settled figures, scaled numbers, missing sequence members) but not the
model verify stage.

**Lint.** `services/lint.py` runs on every report, deck and HTML artifact, and
it costs no model call: the check is free, and acting on it stays explicit. Findings are stored on the artifact, so a document that was fine when
it was made does not start reporting problems because the rules were tightened
afterwards.

**Visual rules are not checked**, because the model cannot break them: the
seed owns every colour and face, and `sanitise` drops `style`, every remote
address and every tag outside a fixed vocabulary before anything is stored.
`class` survives — a block needs `lead` and `cols` to reach what its own seed
styles — so an invented `card` or `columns-2` renders as nothing rather than
fighting the template. What is checked is what the model does choose — the
words.

**A template can tighten the shape rules.** The general bounds — seven items a
slide, forty-five characters a line — are what a screen can carry. A template
whose own instructions promise less says so in a `[limits]` table, and the
checker reads those instead: the lecture deck asks for four items of
twenty-five characters, and without this its own rule would be the one rule
nothing enforces.

`P0` means the document is wrong: a placeholder nobody replaced, a block that
never got written, a figure nobody could have sourced. `P1` means it reads
badly: filler adjectives, an emoji leading a line, a line repeated from another
section, a slide too crowded or too long to read from the back of a room. The
numbers rule is narrow on purpose — an ordinary "12% 증가" ("12% increase") is what a report is
*for*, so only round marketing figures and the multiplier-with-a-verb form are
flagged. A check that fires on ordinary writing is one people learn to ignore.

Nothing is corrected automatically. The panel shows a count and a list; the
report surface already has **Rewrite just this section** for acting on it, and an HTML
artifact has the same per-block rewrite.

### Mechanisms by model group

The checks and devices after writing help a weak writer and can harm a strong one: a check
that overrides the writer pays only when the writer is weaker than the check. So each
mechanism decides its behaviour by the **model group** of the model that actually wrote
(`model_tier.group`, `Request.group`):

- **self-hosted** — `local/…` and `strict-local/…` (27B, flash). The light-setup evaluation
  switch (`KCHAT_LIGHT_WRITERS`) changes their prompt, not their group.
- **strong external** — `model_tier.STRONG_MODELS` (`model_tier.strong`), which also picks the
  light prompt (`prompt_tier`).
- **small external** — every other external model (a `-flash`, `-mini`, `-lite` variant of a
  strong family included).

Two rules hold for every group. **Marking is apart from deleting**: no judge model deletes
or rewrites a sentence unless code has confirmed that one of its figures or names is in
none of the evidence (`post_checks.code_reading`); a statement that cites pages read this
turn (`[[n]](url)`) is read against those pages only (`post_checks.cited_pages`), not every
page the turn searched. A finding code cannot check is marked or shown, never acted on. **Code fixes only what it computes**: a value is replaced by code only
when code knows the right one (a settled figure, an official hotline, a recomputed date).

The small-external column mostly follows the self-hosted one. Change a row here when a fix
changes what a group gets, and test both a self-hosted and a strong external writer.

| Mechanism (code) | Self-hosted | Small external | Strong external |
|---|---|---|---|
| Writing rules (`prompt_tier`, `context`) | full rule bundle | full rule bundle | light: one anti-fabrication line |
| Judge findings of skill post-checks (`post_checks.run`) | judge is the planner; a finding whose figures and names are all in the evidence is dropped; one with none is marked 「(확인 필요)」 where it stands; one with a figure or name in none of the evidence (or a script's own finding) goes to one rewrite and may come out | as self-hosted | the writer judges its own answer; dropped findings as for every group; the rest go back to it once as notes to weigh; nothing it kept is taken out |
| Copy check claims (`copy_check.run`) | as post-checks: dropped / marked / rewritten by the same code reading; numbers, offers and lengths fixed by code | as self-hosted | notes to the writer; what it kept is marked |
| Value marks (`value_guard`, `grounding-check/scripts/trace.py`) | marked, never cut; not marked: the person's values and the days inside a month part they give (「9월 하순」), values under a heading or bold label naming assumptions or examples, the system date on a date label (「확인일」), a time slot's own length, fractions (「10분의 1」); a value marked twice reads once | as self-hosted | as self-hosted; its own revision stands (`settle(writer=True)`) |
| Where marks stand in a chat answer (`copy-marks`, `check_stages._advice_note`) | inline; an advice answer with more than three marks gets one 「확인 필요: …」 line, the writer's own marks included | always one 「확인 필요: …」 line at the end | inline, as self-hosted |
| Report quality follow-through (`report_stages.Verify`, `Quality`) | a web-contradicted sentence is marked; a judged figure code still finds at odds with a settled one, and a number a judge tied to another item whose value with its unit is in none of the evidence (`bound_token`), are repaired once by the planner and marked if they stand; the step shows what a judge found, never its reasoning | as self-hosted | web contradictions, code-confirmed judged figures and code-confirmed bound numbers go back to the writer once; what stands is marked; reasoning hidden |
| Table and figure shape after the last read (`report_stages.mend_shapes`, `table_mend`) | broken pipe tables rebuilt under one header (also while drafting, `repair_pipe_tables`), uncaptioned tables captioned, a table or figure the text never names pointed at, pipe lines no table takes written as items, a header with no rows marked; read again once and kept only when no finding is added; no model call | same | same |
| Deck comparison table without values (`deck._write_slides`, `table_values`) | a slide planned as a table that came out as names only, or a table with no value cell, is rewritten once as a table; kept only with value rows, else the slide stands and the read reports it | same | same (the writer rewrites its own slide) |
| Recommendation override (`report_checks.settle_verdict`) | on | on | off: the finding is reported |
| Sweeping-claim narrowing (`superlatives`) | on | on | off |
| Crisis lines (`hotlines.fix`) | a wrong number where a crisis line is meant becomes the official one (109, 1577-0199, 1366, 1388); numbers the person or a source gives stay | same | same |
| Work notes and check reports (`internal_words.strip_work_notes`) | stripped, also an untagged English plan paragraph, an English tool-step opener, 「N개 중 … 확인」 report sentences | same | same |
| Mixed script in a chat answer (`hangul.repair_mixed_script`) | on (Cyrillic and kana inside Korean words) | on | on |
| Calculation time limit (`calc.DEADLINE`, `Calc.run`) | 240 s; a finished run is handed over unchecked; with none, the writer marks its own figures 「(추정)」; after any run short of a checked one (timeout, one run, none), code also gives 「(추정)」 to each worked amount in the document that is neither the material's nor code's (`report_stages._figures_marked`, `units.label_estimates`) | same | same |
| Written sums and chains (`units.fix_written_sums`, `fix_table_totals`, `fix_written_arithmetic`) | recomputed from the answer's own figures: 「A + B = C」 (a bold C too), a sum after its listed amounts (「A원 + B원으로 합계 N원」), a table's total row over plain rows of one unit, a wrong step of a chain and the steps that start from it; marked 「(확인 필요)」, not rewritten: a result a tool or the calculation printed, a total over subtotals, blanks or a second total that matches no reading, a sum off by 1000× or more; rates, averages, unit prices and rows of other units are not added | same | same |
| Figures code finds off but cannot settle (`units.mark_slipped_formulas`, `mark_scaled_restatements`, `mark_profit_contradictions`) | marked where they stand, never rewritten: a product a unit off, an amount with a worked result's digits a power of ten off under the same name, a profit above what its paragraph's revenue less its named costs leaves (the arithmetic shown in the mark) | same | same |
| Company-assumption labels (`units.label_assumptions`) | a report table row labelled 「가정(사내)」 whose figures the person never gave reads 「가정(본 검토 설정)」 | same | same |
| Weekdays beside dates (`weekdays.fix_weekdays`; chat `answer-weekdays`, report `save_stages.last_pass`, composed `compose._last_pass`) | set to the calendar again after every stage that may rewrite; the year the task names, else the one nearest today (within 120 days), else left as written | same | same |
| Guidance echoes (`guide_echo.scrub`; chat `answer-echo`, report and composed last pass) | an example name from a skill's or template's text (「김현수 드림」) → 「[이름]」, an example full date → 「[날짜]」, a sentence repeating the guidance's own rule wording dropped — each only when the person's input, sources, tool output and today do not give it; quoted 「…」 examples, tables and fenced formats are meant to be copied and count as no echo; a person's own skill is read the same way | same | same |
| Code-printed values (`agent.code_checked`) | a row's own printed values are kept; a mismatch is marked, never rewritten | as self-hosted | as self-hosted |
| Citation links (`agent._link_citations`) | a number whose page shares no word with the claim is dropped | same | same |
| Repair replies (`verify.repair`) | only a sentence string (or `{sentence: …}`) is written into the text | same | same |
| Deck table width (`deck._clean_rows`) | a wide, short table is transposed; else the five-column cap | same | same |
| Skill frames and genre rules (`asked_form`, `skill_turn`, `report._genre_rule`, `skills/*/SKILL.md`) | the request's own parts, order, counts, columns and tone are the structure; a skill's frame, default count and extra tables fill only what the request leaves open | same | same |
| Automatic web search (`report.given_material_suffices`, `routers/sessions.py`) | off on an 「자동」 turn whose given material does the job and that asks for no research or current facts; forced searches (current facts, contacts, advice) and the empty-result retry stay | same | same |
| Answer plan (`answer_plan`) | a mail or notice written for the person carries only the facts they gave (no invented cause, replacement supply, priority handling or time); a short answer gets no plan | same | same |
| Duration must-haves (`report_checks.must_status`, `duration_limit`, `option_duration`) | a lead time or period (납기, 보증 기간) is judged in days from the material's own words, never on the reading's unitless number; the limit is the one written with its unit or a deadline counted from a written contract day, else the must is not judged and said so; a range across the limit is open; a longer period the option sells as an extension meets a minimum, said with its cost | same | same; the block is data to weigh (`_must_block(data=True)`) |
| A 「있음」 the material never writes (`report_checks._mentions`) | a yes/no must read as met for an option whose lines say nothing of it is open (조건 미확인), not 충족 | same | same |
| Cost readings settled from the price lines (`report_checks.price_lines_reading`, `_compare_cost`) | when the two readings differ, code reads the plainly written price lines (unit price × count, per month/year/once, 「… 시 N% 할인」, VAT basis) as the third reading instead of a thinking read; when no two agree its totals stand; a reading agreeing with it takes its scenarios; a line it cannot read (usage price, two amounts in a clause, an amount outside the options) leaves the old vote; two readings agreeing against it stand | same | same |

### Pipelines

The report writer, the deck and page writers, a chat answer's finish, a
revision and the hand-over of files are pipelines: an ordered list of named **stages** that read and write one typed **work
bundle**. A fix to one step lives in one stage with its own tests; every stage of every turn
is recorded, run or not; and which stages a document runs is data, not an if-chain.

**Where things are.** `app/pipeline/` holds the machinery: `bundle.py` (the work bundle),
`stage.py` (the contract), `runner.py`, `registry.py`, `compose.py` (composed step lists),
and the stages by flow — `report_stages.py`, `check_stages.py`, `chat_stages.py`,
`revise_stages.py`, `export_stages.py`, `save_stages.py` (the `io` stages that load and store
a document), `make_stages.py` (deck, page), `route_stages.py`; `runs.py` keeps a run across
requests, `marks.py` the spans a document's reader should look at, `labels.py` the stages'
Korean names. `app/pipelines/*.toml` declares four pipelines: `report` (every document),
`chat` (a chat answer's finish), `deck` and `page`.

**A pipeline file** names the agents it serves, its flows, and a `[summary]` of plain lines
for the drawn chain:

| flow | what it lists | who runs it |
|---|---|---|
| `pre` | stages before the first draft (`check:gaps`, `gap-card`) | `sessions._gaps` |
| `plan` | the planning pass, ending with the offered outline | `report.write` without an approved plan |
| `write` | the writing pass on the approved outline | `report.write` |
| `finish` | a chat answer after the agent loop | `sessions._run_turn` |
| `revise` | a follow-up applied to the document on screen, `revise-load` … `revise-save` | `sessions._revise_document` |
| `make` | a one-pass writer: deck, page | `sessions._make_pass` |

What a document does besides writing is not in the file. The template's and the agent's
**process settings** (`services/process_settings.py`; `[process]` in a shipped `template.toml`,
`user_templates.process`, `agents.process_settings`) name the pre-pass calculations, the optional
format-repair stages (each listed in `report.toml` and skipped unless named), the code and
judged checks, structure rules (sections, tables, summary lines, bullet (개조식) style, page limit),
written review rules and options such as `search`, `fixed_outline` and `draws`. Every name is from a
closed list the code ships, so a person who sets the same values as a shipped template gets
the same run. `tests/test_pipelines.py` holds every shipped format to its expected pre-pass
steps, check families, stages and options. Runs stored under a per-format pipeline name
that no longer has a file of its own still open (`registry.LEGACY`).

**The bundle** (`Bundle`) has narrow, typed parts: `request` (what the turn was asked,
never written), `spec` (format and genre), `material` (each `Part` with its origin:
attachment, pasted, project, research, computed, settled, conflicts, prepass, note),
`answers`, `computed` (computed values, settled figures and conflicts, the pre-pass),
`plan`, `document` (sections, head block; a chat answer as streamed and as finished),
`findings` (codes found before and after the repair, what the verify stage left, the
grounding outcome, the quality record), `files`, `steps` (the records) and `usage`.

**The stage contract** (`stage.py`): a stage has a `name`, a `kind` (`compute`, `model`,
`check`, `io`), the dotted bundle fields it `reads` and `writes`, `skip(bundle)` — the
reason it does not apply, or "" — and `run(bundle, ctx)`, an async generator that writes
its fields and yields the wire events (`step`, `section`, `titleBlock` …) the surface shows.
It ends the pipeline on purpose by raising `Stop(reason)` (an outline offered, a question
card, a revision with nothing to do), after yielding its own closing events.

**The runner** records one `StepRecord` per stage: `done`, `skipped` with the stage's
reason, `stopped`, `failed` (the exception goes on to the caller) or `cancelled` (the turn was closed under it); the stages after a stop are recorded
as skipped. The records go to the log (`pipeline minutes.write: research:done,
calc:skipped(nothing to compute…), …`), onto the bundle, and to `report.write(trace=[…])`.
`run(start="verify")` resumes from a stage on a bundle the earlier ones filled.
`StepRecord.wire()` is the record as a surface `step` (category `pipeline`, the whole record
under `stage`: Korean label, summary, evidence, times, attempt); `KLOUDCHAT_PIPELINE_STEPS`
sends them, on unless set to `0` (the test suite sets `0`; the replay test holds the stream
with records to the same events plus one record per stage). `KLOUDCHAT_PIPELINE_STRICT=1` (the test suite sets it, `tests/conftest.py`)
refuses a stage that changed a field it did not declare.

**Runs are kept.** A writing pass (report `write`, `revise`, a deck's or page's `make` on an
approved outline) runs with a `runs.Recorder`: the bundle encoded before the first stage and,
after each stage, the fields it changed. The run is a `PipelineRun` row (kept
apart from `messages`, which the transcript reads whole) and a compact copy of its records
on the message it settled (`routing.run`). `runs.restore` gives the bundle as it stood
before any recorded stage; `POST /sessions/{id}/runs/{run}/rerun {fromStage, answers}` runs
that stage and those after it again — the earlier ones are not run and their records are
carried — and the flow's own save stage stores the result as a new version of the artifact,
billed like a turn. A value that is a function (the verify stage's checks) is not encoded;
the stage that built it registers a rebuild (`runs.REBUILT`). `tests/test_pipeline_replay.py`
reruns every recorded report and revision from each of its stages and requires the same
document, with no model call from the stages before the restart.

**The gap card is a stage.** `pre` ends with `gap-card`, which raises `Wait`: the run is
kept as `waiting`, the card's questions (fact, label, why, placeholder) ride on the pending
turn with its run id, and the answer (or **Go with what arrived**, which leaves each fact its
bracket blank) closes it; the writing pass that follows names it as its parent.

**Persistence and routing are stages.** `report-save`, `revise-load`, `revise-save`,
`deck-save` and `page-save` are `io` stages that reach the database and the
bill through the router module, so tests that replace `sessions.SessionLocal` reach them.
`send_message` decides where a turn goes with the `route` stage (page, file hand-over,
revision, report, deck, chat); its record opens the stream. A stored report carries
`data.marks` (placeholders, values the grounding check blanked with what stood there, what
the verify stage left), found again in the current body by `GET /artifacts/{id}/marks`.
`GET /pipelines`, `/pipelines/{name}` and `/agents/{id}/pipeline` read the shipped definitions.

**Check skills are stages of the `check` kind.** A check skill's SKILL.md names its stage:
`pre` checks run as `check:gaps` (one question card before the first draft); `post` checks
run as `check:grounding` before the verify stage and `check:grounding-again` after its
rewrites (report sections), and as `check:answer-grounding` on a chat answer. The verify
stage and `consistency` are `check` stages too: find, repair once, find again.

**Adding a stage.** Write a class on `stage.Base` in the flow's stage module (or a new one
imported by `registry._load_stages`): name, kind, `reads`/`writes`, `skip`, `run`. Call
helpers through their module (`report._complete`, `report.fix_products`) so a test that
replaces one reaches the stage. Register it, list it in the pipelines that run it, and add
its own test. A format-specific behaviour is a stage listed only in that format's file, or
an `[options]` switch — not a format comparison inside a stage.

**Replay tests.** `tests/test_pipeline_replay.py` replays recorded turns — one
acceptance run per format (minutes, bullet-style briefing, client proposal, comparison, career history,
a long-form proposal through the parallel gate), the data-analysis and career-docs chat
turns, and two revisions (one that widens, settles again and aligns) — and requires the
same events, sections and stored rows as when they were recorded. `tests/replay_harness.py`
answers every `/chat/completions` POST from `tests/fixtures/replay/<case>/calls.json` by
the call's key (model, messages, max_tokens) and fails on a prompt it has no reply for; any
other HTTP call fails as offline; the clock and uuids are fixed. `tests/replay_build.py`
re-records a case from an acceptance run against a scripted model (the run's outline and
sections, readings of its material); rebuild only when a change in behaviour is meant, and
say so.

**Composed pipelines** (`app/pipeline/compose.py`, `app/routers/user_pipelines.py`). The
shipped files stay locked. An agent's process › **Set it up yourself (advanced)** edits a `user_pipelines` row: a
list of at most twelve coarse **steps** `{id, kind, ref, settings}` —

| kind | ref / settings | needs | leaves |
|---|---|---|---|
| `agent` | an agent the person may run (own or organisation's), `instruction` | — | a document (report agent) or an answer (chat agent) |
| `skill` | a script skill run by code (`date-calc`, `quote-calc`: the pre-pass steps) | — | material |
| `check` | `grounding-check` (document or answer), `copy-check` (answer) | a product | findings, repaired once |
| `review` | `model` (one the person may use), `repair`, `focus` | a product | findings in the grounding check's shape |
| `human` | `gaps` (the question card) · `outline` (approval; just before a document agent) | a later agent | answers · an approved plan |
| `export` | `formats` | a document | files (after the save) |

`compose.validate` holds the order to these contracts before every save and on
`POST /user-pipelines/validate` (the editor's live messages), one Korean line per problem
(「1번 검사(근거 대조): 앞 단계에서 만든 문서나 답이 있어야 합니다.」, "check 1
(grounding check) needs a document or answer from an earlier step"), plus warnings (a card
before an agent with no facts to ask is skipped). The **recursion guard** refuses an agent step
whose agent is bound to this pipeline, directly or through the pipelines its agents are bound
to. The last agent decides the surface: `report` for a document, `chat` for an answer; an
agent's own pipeline must end on the agent's own surface.

**Running.** A conversation runs a user pipeline when `sessions.pipeline_id` names one or its
agent has one (`agents.pipeline_id`, set by `PUT /agents/{id}/process`). `send_message` then skips the agent's
own gap card and, for a new request on the report or chat route, hands the turn to
`user_pipelines.turn_response`; a follow-up on a finished document still revises it with the
document's own `revise` flow. `compose.compile_steps` makes one stage per step (named
`3:check`, titled by the step) and a save stage (`report-save`, or the answer stored as the
message) before the first export; the existing `Runner` and `runs.Recorder` run them as flow
`compose` of pipeline `user:<id>`, so every step is recorded, kept and rerunnable
(`POST …/runs/{id}/rerun` routes a `compose` run to `user_pipelines.rerun`, on the steps as they
were when it ran: the definition, the agents and the review models ride on `Request.turn`).
An **agent step** runs that agent's own flow as a sub-run: a document agent its format's
`plan` (or the outline a `human:outline` step got approved) then `write`, with no save of its
own; a chat agent one answer on its own prompt (tools stay with the conversation surface). What
the previous agent produced is handed to the next as material (`Part(origin="handoff")`).
A **review step** asks the chosen model for findings (quote, why, fix) over the product and the
material, places them, applies the fixes once when `repair` is on, leaves the rest as
`check-finding` marks, and is billed at its own model's price (`pipeline.review`). A **human
step** raises `Wait`: what the run spent is billed, the card (`needs`) or the outline
(`proposal`) is the session's pending turn with `pending.compose = {runId, stage}`, and the
next message resumes the run from that step with the answers or the approved outline.

**An agent's process** (`app/services/bases.py`, `app/services/bundles.py`,
`PUT /agents/{id}/process`). The agent form offers **How it works** — **Answer directly** (no base), **Start from a
base process** (a **base**: an offered catalogue agent, `agents.base_key`) or **Set it up
yourself (advanced)** (the step editor above, not offered for slides) — and **Extra work**,
one checkbox per **bundle**.

- A **base** is any offered agent file (`starter.offered_agents`; drafts for
  administrators only). Its plain steps are its pipeline's `[summary]` lines (at most five);
  `sessions._agent_catalog_key` resolves `catalog_key`, then `base_key`, then the original's,
  so a person's agent with a base runs that base's question card, format, template and
  pipeline. Picking a base sets `surface`, `doc_format` and (when the agent has none that
  fits) `render_template_id`.
- A **bundle** is data: `app/bundles/{prepare,review,finish,publish-check}.toml` ship **Prepare
  before writing** · **Review after writing** · **Finish** · the pre-publication check; a person's own is a `user_pipelines` row with `kind = "bundle"`
  in the same item shape (`{id, kind, ref, settings, label, default}`; `GET/POST/PUT/DELETE
  /bundles`, `POST /bundles/{id}/share`). Where an item runs is decided by its kind alone
  (`bundles.target`): a check skill with a stage is an `Agent.check_skills` entry, run inside
  the agent's own flow (the report flow's find → repair → find again); `option` is
  `Agent.check_options` (`tool_numbers`, `titles`, read by `check_skills.options_for`);
  `flow` items (format checks, repair) are what the report flow always does; `skill` items
  are the report flow's own pre-pass but steps before the agent on chat; `human:outline`,
  `review`, `export`, other agents and `copy-check` are steps around the agent. Agent files
  name their bundles (`bundles = [...]`) and the seeder compiles them with the same
  `bundles.compile` the form's save uses, so a person who picks a base and ticks the same
  bundles gets the same agent (`tests/test_agent_process.py` holds this for every base).
- `agents.bundles` keeps the ticks; `check_skills`, `check_options` and, only when an item is a
  step, the agent's **own pipeline** (`user_pipelines.agent_id`) are compiled from them. Its
  `@self` agent step (`compose.SELF`) runs the agent's own shipped flow with the turn's web
  search and connectors (none under strict-local); `compose.validate`'s recursion guard does not
  apply to it. With no step needed there is no pipeline and the shipped flow runs as is.
- **Review on share.** A shipped bundle never needs review. A person's bundle that exports,
  reviews or runs another agent, and a set-it-up-yourself pipeline with any of those, reach others only after an
  administrator approves them (**Process review** on the admin users page, the same queue as
  below); until then others run the agent on its base (`user_pipelines.runs_for_others`).
  Installing a copy carries `base_key`, `bundles`, `check_skills`, `check_options` and, when
  it runs for others, a clone of the pipeline; deleting an agent deletes its own pipeline.
- `GET /user-pipelines` lists a person's unbound pipelines (no `agent_id`, not bundles), which
  a set-it-up-yourself pipeline can start from.

**Save as agent** (`GET /sessions/{id}/agent-draft`, `services/agent_draft.py`): the result
card's and the panel's ⋯ open `/customize/agents/new?from=session:<id>` prefilled; nothing is
saved until **Save**. "What this conversation settled" is distilled from the person's own messages only, by the
model the conversation ran on — its strict-local model (no fallbacks, nothing masked away) when
any turn ran strict-local, otherwise masked by `governance.mask`. A sealed agent's prompt is
never copied. The base is the conversation's agent's, else inferred from its template; the
conversation's files go onto the new agent's shelf as copies
(`POST /agents/{id}/knowledge/from-files`, the caller's own files only).

**Sharing.** Private by default. Sharing an agent whose set-it-up-yourself pipeline needs
review sets that pipeline's `review` `pending` when every agent step is one others can run (a
catalogue agent or one shared with the organisation); an administrator approves (`visibility`
org) or rejects with a reason (`/admin/user-pipelines/{id}/approve|reject`, the queue on the
admin users page). A saved edit takes an approved pipeline back to private. A copy of the agent
remaps catalogue agents to the taker's own copies (`settings.catalogKey`).

### Asking for a review

`POST /artifacts/{id}/critique` is the other half: one reading of a finished
document by somebody who did not write it. It answers with a score out of ten
and up to six things to fix, in **the same shape the linter produces**, so the
panel shows one list of things to look at rather than two. The difference
between the two is only where they came from — the linter is free and certain,
the review costs a call and is an opinion.

The score is a reading, not a gate. Nothing is blocked by it, and a review
annotates the artifact rather than editing it: no version snapshot and no
version bump, the same rule the fact-check follows.

**One reviewer, one pass.** Every call is somebody's credit and the bill is
shown before the turn, so the panel is one reviewer and the pass is asked for
explicitly.

The rubric comes from the template the document was written into
(`checklist.md`), or from a default for the built-in report and deck tracks.
Reviewing rules are kept apart from writing rules on purpose: folded into the
brief, a rubric becomes a checklist the model writes *to* rather than one it
can be measured by.

### Reading a theme out of a document

Four colours and a paragraph of house style are usually already on hand — the
official-letter template everything is filed on, an earlier report, a page on the
department site — rather than typed from scratch.

`POST /designs/extract` takes a `fileId` (its text is already extracted on
upload) or a `url` (read through `builtin.scrape`, the same scraper the
`fetch_url` tool uses) and answers with a **draft**. Nothing is stored. What
comes back is one model's reading of a document, and the person who owns that
document is the one who can say whether it read it right, so the editor opens
on it with a note naming what it was read from.

Every field is put through `design.normalise_tokens` and `design.craft_keys`
before it leaves the service: a colour in the wrong shape becomes the default
rather than a value somebody saves without noticing. The prompt says to leave
`body` and `image_style` empty when there is nothing in the document to observe
— an invented house style is worse than a blank field, because the blank one is
obviously unfinished.

Costs one call on the cheapest allowed chat model, charged like any other; an
uploaded file is internal material, so it is read on a strict-local model
(see "Other boundaries", §7).

### Themes

One look, read by every surface that produces a document. Split in two, and the
split is the whole design:

**`tokens`** — `accent`, `ink`, `muted`, `font` — is what the *renderers* draw
with. Four values, because `.pptx`, `.pdf`, `.hwpx` and the browser preview can
each express all four; a fifth that only PowerPoint could draw would make the
preview lie, which is the one property the three deck renderers guarantee.

**`body`** is what the *model* reads, and it is capped at 400 characters.
Colours are not in it for the text surfaces: a model writing report prose
cannot act on `#7a1f3d`, and a hex code in a system prompt is spend with no
effect. Image is the exception — there the colour is the instruction, so it
travels as an English phrase (`design.image_clause`) appended to the prompt
beside the style chip.

`craft` names brand-agnostic rules the theme opts into, filtered per
surface: a rule about heading depth reaches a report and not a chat turn.

The tokens are **copied onto the artifact** when it is made, and the exporters
read them from there. A deck presented last month does not repaint itself
because the project changed its theme since — the same rule the
per-slide accent already followed.

A project with no theme renders with the default palette, down to the
greys in the PDF. The export tests pin that output.

### The artifact gallery

`GET /artifacts` returns **a page of cards**, not complete artifact bodies.
The client fetches full documents only when they enter the viewport or open in
an editor.

Three parts, and each is visible in that sentence:

**A page.** Keyset, not offset: the list is ordered by `updated_at`, which
moves when somebody edits, and an offset would skip or repeat rows underneath
the person scrolling. The cursor is the last row the client already has —
`before_at` and `before_id`.

**A card.** `ArtifactOut.card` cuts the body down to what a thumbnail shows:
an HTML or code artifact travels without its markup at all, a deck as its first
four slide titles, a report as the top of its first four sections. Media
artifacts are already card-sized — a `src` and a duration — so they travel
whole. Every trimmed row is marked `partial`, and anything that renders or
edits a whole document fetches it by id first; the client keeps its fuller copy
when a listing row for the same version arrives.

**Counts and search that outlive the page.** The filter row claims to be about
the workspace, so `GET /artifacts/counts` counts it; `q` searches titles in the
database rather than filtering the page in the browser. Titles only — the
bodies are what this endpoint exists not to read.

HTML thumbnails hydrate on approach: an `IntersectionObserver` a screen ahead
fetches the document for a card about to be seen, so the picture is there when
it arrives and the ninety cards below it cost nothing.

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
`starter.catalog_owner_id`, derived rather than stored — shared to the
workspace, and taken from the store tabs of **Customize** (`/customize`) a copy at a time.
`POST /agents/{id}/install` and `POST /skills/{id}/install` do the copying, and
both are idempotent: pressing **Import** twice returns the row you already have
rather than a second one with the same name.

The shipped catalogue is `starter._SKILLS` plus `starter._AGENTS`, the
agents read from `app/agents/<key>.toml` (prompt, guide, starters, kinds,
bindings, an explicit tool list — `[]` denies every tool — skill keys and the bundles
they run). No agent is offered: every shipped agent is withdrawn (below, "Skills instead of
shipped agents"), and what they did ships as skills.

**Listed, hidden, retired.** `listed: false` (a SKILL.md, a `template.toml`, a
`starter._DESIGNS` entry) or `connector_catalog.listed` keeps a shipped part out of the lists
people pick from — the skill hub and store, the template and theme pickers, the members'
connector catalogue — while it still runs by name, in bundles, in the editors that name it, and in
the conversations and documents that already use it. Unlike `draft`, it does not gate
`run_skill_script`. The wire carries `listed`; administrators see hidden rows behind **Show
hidden**. Listed: the skills `grounding-check`, `gap-check`, `calc-check`, `date-calc`,
`quote-calc`, `xlsx-clean`, `pdf-table` and `pii-mask`, the work skills that replaced the
shipped agents and the two connector companions, and translation `translation`, press release
`press-release`, Excel file `xlsx-build` and chart `chart-make`; the offered document templates
(the meeting agenda `doc-agenda` beside the minutes) and
the briefing deck (`deck-briefing`); the themes **Basic** (기본) and the document serif
(문서용 명조); the time connector (deep research disabled, YouTube unlisted).
**Finish**'s export item names `by_format`: an official-letter-family format (`internal_draft`, `official`,
`event_plan`, `result_report`, `gaejo`) exports HWPX when ticked without a format, anything
else its first `formats` (`bundles.export_default`). An agent file with `retired = true`
(cover letter and career history `career-docs`, client proposal `client-proposal`) is never seeded afresh; its
rows and every copy get `agents.retired`, are listed nowhere, refuse install and new
conversations (410 `agent_retired`), and keep their conversations and documents. Their
templates are in `design_templates.RETIRED_KEPT` and their formats in `doc_formats.RETIRED`.
A
prompt's `[채팅에서]` / `[보고서에서]` / `[슬라이드에서]` lines ("in chat", "in reports",
"in slides") open parts read
on that surface only (`workspace_context.surface_prompt`); `[보고서·슬라이드에서]` names two.
A skill body uses the same lines (`surface_body`), so one skill can tell a chat answer to run
its script and a report writer to copy the server's pre-pass block.
`seed_catalog` syncs the catalogue on the owner's sign-in and deletes the
owner's rows whose `catalog_key` is not shipped at all (a key still in a file with
`retired = true` is kept, hidden); copies people installed are theirs and stay. Sharing is set on the first run only (told apart by a
catalogue agent, or a shipped skill that is shared or has been installed), so a
skill the administrator made private stays private. `former_names` in an agent file renames untouched copies on sync.

Shipped skills are ordinary skills of that account (`source = personal`; copies are
`workspace` — there is no built-in source): the owner
can edit or unshare them in **Customize** like any skill of their own (a deleted one is seeded
again on the next sync, as agents are), and the store
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
than stamped on the row, so an entry does not go on claiming to be official
after the administrator who published it was demoted.

`_copy_of` matches an account's existing copies by catalogue key — and, for
agents, by slug — so seeded copies count as installed.

### Skills instead of shipped agents

There are no built-in agents. Each thing a shipped agent file describes is an admin-owned
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
`calc-check`, `date-calc` and `quote-calc`); its body reaches the writer without the tool
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
| regulations and procedures `regulation-guide` | answers from regulations `regulation-answer` (chat) | — | grounding-check | — | keep on `regulation-answer` + `date-calc` · knowledge (the regulation files) · department and contact · the publish-check bundle (hold ungrounded answers, point to the department, mask PII; the hold reads the shelf's hits and the files attached to the conversation) |
| presentation `slides` (draft) | presentation deck `deck-writing` (slides) | — (deck flow) | — | — | keep on `deck-writing` · screen Slides |

The skill's body is the agent file's prompt; the agent file's bundles compile to the checks
above (the prepare bundle's date and amount pre-passes and the review bundle's format checks
and repair run inside the report flow for every template that sets them). What a skill does
not carry is the look — the theme stays a choice of the person or the agent — and the agent's
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

Four more work skills have no agent file behind them. Translation
`translation` (chat, report) and press release `press-release` (chat, report; grounding-check
and generic-language-killer, no search) are instructions only; a press release written as a
report gets the `press` format's head when the request names it. Excel file `xlsx-build` (chat,
needs `run_skill_script`) builds a workbook with `scripts/build.py` from `sheets` given in the
args or from a chat file (a pasted table, an attachment, or a CSV `run_code` saved): computed
columns as `{column}` formulas per row, a `SUM` total row where a formula column repeats its
formula (a rate stays total ÷ total), number formats, and an optional native chart. LibreOffice
in the sandbox caches the formula values, which the summary returns for the answer to quote;
the file is a step output, shown under the answer as a download. Chart `chart-make` (chat,
needs `create_chart`) tells the model when and how to draw a chart artifact. The meeting agenda
template `doc-agenda` binds the `agenda` format (the minutes' head) with its own genre rule,
missing-information questions (meeting date, attendees) and date pre-pass.

**Withdrawn, not retired.** An agent file with `withdrawn = true` stays in the catalogue: the
sync keeps its owner row and keeps untouched copies following it, `bases.get` still resolves
it for the agents built on it as a base, and existing conversations and published bots run as
before. It is left out of `GET /agents`, the base picker (`bases.bases()` without
`withdrawn=True`) and install (410 `agent_retired`). Nothing in the database changes for it;
a member's own copy (which carries `origin_id`) stays listed and theirs.

### Coding agents

`/llm/*` forwards requests to LiteLLM unchanged, authenticating **by key, not
by session**: the incoming `Authorization` header goes straight upstream and
LiteLLM decides whether it is valid. An issued key *is* a virtual key, so spend
and the model allow-list follow it. The budget attaches to the account, so
extra keys split one allowance rather than multiplying it.

The master key never travels this path, and a call without credentials gets a
401 from upstream. Responses are relayed chunk by chunk, so streaming passes
through intact.

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
every open of an attached conversation is an audit event.

**Performance monitoring** (`/admin/performance`, `services/perf/`) samples each replica,
the host, the GPU nodes and LiteLLM's request log once a minute into `metric_samples`;
its settings are in [configuration.md](configuration.md#performance-monitoring).

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
once the model is final. The fixed parts of the request — system turn, reference
block, this turn's attachment and question, tool schemas — are counted with a
deliberately high token estimate (`context.estimate_tokens`, about 1.2× the
gateway's count on Qwen), an answer reserve is held back, and the earlier turns
that do not fit the rest are dropped oldest first, at a question boundary, to
80 % of the budget so the next few turns fit without another cut. The dropped
turns go in as a trusted 「이전 대화 요약」 ("summary of the earlier conversation") block written by the enrichment model
(`services/conversation_summary`, in batches of about 14k characters when a lot
is dropped at once) and cached on `sessions.summary` by the id of the last
message it covers, so a later cut summarises only the turns since. A summary
that could not be written is said so, never invented; the turn's timeline shows
how many messages were summarised; the cost settles as `chat.summary`. A
prompt the provider still refuses for length is reported as
`context_length_exceeded` with a sentence that says what to do.

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
generation and the `/llm` compatibility API always mask and present no
decision flow.

### Auto routing

Auto is a session mode, not a model alias. `sessions.model` always stores the
last real model the person selected; the separate `routing_mode` field is
`manual`, `auto` (cost: a low-complexity turn may use an economy model) or
`auto_quality` (a high-complexity turn may use an administrator-ordered upgrade
model that does not widen the data boundary and keeps its tools).
This prevents a synthetic `auto` id from reaching LiteLLM and keeps manual
model selection authoritative.

Privacy inspection runs before classification. If the assembled envelope has
a finding, Auto performs no key issuance or model call and the existing privacy
decision owns the turn. A clean, ordinary chat turn may be classified by a
live, zero-cost strict-local model using the caller's virtual key, redacted
LiteLLM logging and `disable_fallbacks`. The classifier receives the complete
answer-visible messages and quality-model tool schema snapshot. If that payload
exceeds 80,000 characters, it is not truncated and the chosen model is kept.
Only a high-confidence JSON verdict moves a turn (low on the cost lane, high on
the quality lane); every timeout, malformed response or uncertain state keeps
the chosen model. Report and deck turns are classified on the request text;
a turn moved to strict-local for its internal material stays there.

Economy candidates are administrator-ordered and revalidated against the live
catalogue, caller allowlist, context window, data boundary and both token
prices. `unknown` and `privacyOnly` models are excluded, and none may have a
wider boundary than the chosen model. The chosen
answer call receives no tools and also disables fallback, so an unbounded tool
result cannot invalidate the checked context fit and a failed economy route
cannot cause a second, hidden premium charge. Message routing stores the
quality, selected and actual models plus enum-only decision metadata; classifier
prompts and free-form model reasoning are never persisted.

### Chat answer shaping

**Standing rules are repeated next to the question.** An earlier user message
that sets a rule for every later answer (「앞으로 … 세 문장 이내로」, "from now on … within three sentences";
"from now on answer in English") is replayed verbatim as a short exchange right before the
latest question (`standing_directives`, chat only, the three most recent, a
withdrawal such as 「원래대로」 ("back to normal") clears them). The rule still sits in the
transcript; the replay is where a small model attends. A skill fetched through
`use_skill` is told that such rules take precedence over its own instructions.

**The auto toggle's forced search asks about the world.** `needs_web_search`
stands down when the person is telling rather than asking (their team, memo,
rule, code), when the question is about the conversation (「아까」 "earlier",
「그 규칙」 "that rule", 「지금 코드」 "the current code"), when it is arithmetic on supplied numbers or a rule of thumb
(「몇 장이 적당해?」 "how many slides is right?", 「보통 얼마」 "usually how much"), and when a sentence has a time word but no
world cue (news, price, version, law, office). The search tool stays offered;
only the forced first hop is withheld.

**Auto search is for the world, not the conversation.** The auto instruction
names what never needs a search (the conversation itself, the user's own
figures and the arithmetic on them, code behaviour, textbook principles, rules
of thumb) and ties searching to answers that depend on events after training
or today's values; `create_chart` is for charts the user asked for, not for
trend questions.

**A rough figure still says where to check.** A question about the usual level
of a changing value (「왕복 요금은 보통 얼마나 해?」, "what does a round trip usually cost?") forces no search, but the
figure comes from training data, so the turn runs with `caveat_request`
(`freshness.ballpark_current_value`): the answer streams live and closes with
the same light caveat an unverified current-fact answer gets, unless the
conversation's files ground it.

**Length follows the question.** The chat contract asks for two to four
sentences of prose on a casual question or quick fact, with structure only when
the user asks for detail or the material has parallel parts.

**One answer, once.** A small model sometimes restates its whole answer after
the prompt's silent self-check. When a tool-less hop ends with one block written
twice in a row, the agent keeps the first copy and takes the second back with a
`retract` event (`_repeated_tail`), so neither the stream nor the stored message
shows the paragraph twice. The style guide's worked example is marked as showing
form only and uses a neutral domain, so its content is not repeated as fact.

### Other boundaries

- A `stdio` connector is a command the API server runs: only an administrator
  may register one, and the child inherits a minimal environment (`PATH`,
  `HOME`, locale, `TZ`), never the API's own secrets (`mcp._INHERITED_ENV`).
- A cached conversation summary is masked under the policy in force when it is
  reused, not only the one in force when it was written.
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
- A video job passes the same gate as a chat turn: egress policy, the intent
  filter, and this account's model allowlist, before the prompt leaves.
- An index delete that fails leaves the file's record saying where its vectors
  are, for the next attempt; a vector hit for a document no longer on the shelf
  is dropped before it can become evidence.
- Credit settlement is an atomic SQL increment; two turns of one account
  settling at once both land.
- A finished video is claimed once across replicas by flipping its stage with
  a conditional update; a cancel that landed first wins, and nothing is stored
  or charged after it.
- A chat turn is claimed on the session row (`running_turn`, `running_since`)
  in the same transaction as its question, released when the
  answer settles or stop is pressed, and ignored once older than ten minutes.
  Two replicas serialize on the row; the second sees the claim and answers 409.
- The first signup is decided under a transaction-scoped advisory lock, so two
  simultaneous first signups cannot both become the administrator.
- Office and HWPX archives are opened only after their declared sizes pass a
  limit (64 MB unpacked, 4,000 entries, 32 MB a member), and parsing runs in a
  worker thread so one upload cannot stall the event loop.
- The shared view renders a section through the same component as the owner's
  view, so an edited HTML body shows there too.
- The release workflow publishes an image only after the CI workflow passed on
  the same commit (`verify` calls `ci.yml`).
- Sidebar day buckets compare local calendar dates, not elapsed milliseconds.

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
지우기」 (`?files=true`, bulk `files: true`): then the files attached or made in it go too,
a project's or an agent's excepted (`delete_session_files`). Each file records how it came
(`files.origin`: `upload`, or `generated` for a code run's output, an image, a chart, a
figure, audio, video, an export); the file box (`/files` in the web app) lists the two
apart, and a composer's 「파일함에서 고르기」 attaches one again by id. Deleting
the project drops the collection; `POST /projects/{id}/knowledge/reindex`
backfills files uploaded before the index existed. The lexical shelf the tool
also reads includes the project's files, so a passage the budget could not
carry whole is still reachable by lookup.

Project knowledge is also injected: it shares the turn's file budget and is
excerpted around the question once it no longer fits whole (§7).

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

---

## 9. Frontend structure

```
apps/web/src/
  pages/        one per route; settings/ holds the account tabs and the admin
                System screen's sections, customize/ the agent, skill, template
                and publication editors
  components/   chat · report · slides · chart · artifacts · media · agents ·
                customize · publishing · issues · memory · share · auth ·
                layout · ui
  store/        useStore.ts — a single zustand store
  lib/          api.ts (the backend seam) · kinds.ts · i18n.ts · useT.ts ·
                reportMarkdown.ts · mermaid.ts · shortcuts.ts
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
