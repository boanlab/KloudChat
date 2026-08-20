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

### What is recorded about access

`audit_events` carries a sign-in, a failed sign-in, a password change and a key
issue, each with the client's address and browser. Two screens read it: an
account's own **접속기록** (`GET /auth/me/access`, the account's rows only) and
the administrator's audit trail (`GET /audit`, everything).

The address is only as good as the proxy in front. `core/deps.py:client_ip`
reads the first hop of `X-Forwarded-For`, and the web container resolves that
hop from trusted proxies only — see
[Behind a reverse proxy](configuration.md#behind-a-reverse-proxy).

A region is shown beside the address when, and only when, a MaxMind GeoLite2
file is mounted (`services/geoip.py`). The lookup is offline by design:
resolving addresses through a third-party service would send every visitor's
address off the instance. With no file the screens show the address alone,
and private ranges read `내부망` rather than a guess.

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

Thirty-one migrations under `alembic/versions/`. The principal tables:

- `users` · `refresh_tokens` (family-based rotation) · `password_resets` ·
  `api_keys` · `audit_events`
- `sessions` · `messages` — conversation. `messages.usage` is JSONB
- `credit_ledger` — the ledger
- `projects` · `files` · `artifacts` · `artifact_versions` · `skills` ·
  `memories` · `agents` · `connectors`
- `templates` — starting points a user added, optionally carrying a form file
  whose text is attached when the template is picked
- `design_systems` — the look a project's output wears. `projects.design_system_id`
  is nullable and null by default, and `ON DELETE SET NULL`: removing a look
  costs the projects their look, not the projects
- `sessions.render_template_id` — the rendering template a session writes into.
  A plain string, not a foreign key: the catalogue ships inside the API image,
  and an id that disappears in an upgrade degrades to "no template"
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
- `messages.failure` — how a turn ended when it did not end in an answer.
  `no_answer` sits on the question, `interrupted` on a partial reply
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

**The turn outlives the connection.** The work runs in its own task and the SSE
response only relays what it emits, so closing the tab cancels the relay and
not the turn — the answer is still stored, charged and titled. Which makes
stopping a separate act rather than a dropped socket: `POST
/sessions/{id}/stop` sets a flag the token loop checks, and the turn settles
where it is with the partial answer kept and the row marked `interrupted`.

### Report and slides

Both are two-pass: one outline call returns the title and the table of
contents, then one call per section or slide, each carrying what the previous
ones wrote. Six listed sections means six are coming, which is what makes the
progress indicator honest.

A failed section is marked and the rest continues.

**The plan is checked before anything is written.** A small model reaches for
the first layout it is offered and stays there: measured over the decks this
instance had already produced, `bullets` was 77% of every body slide and 101 of
128 decks ran three or more identical slides in a row. `services/outline.py`
holds the rule both tracks use — no run of three, and at least three of the
offered layouts (or as many as exist) — and a plan that fails it is asked for
once more, naming exactly the layouts it skipped. The second answer is kept
only if it is less flat, so the worst case is one extra outline call and the
plan it started with. Nothing is coerced: a layout assigned to a heading it
does not suit would be a worse slide than a repeated one.

Both receive the workspace blocks the chat surface gets — project
instructions, memories, skills, and any file attached to the turn — so a
request naming an uploaded form is written against that form.

Reports search the web before writing and cite what they found; with no search
backend the shelf is empty and the citation rule drops out of the prompts.

Slides use four layouts — `title`, `bullets`, `quote`, `two-column` — each
implemented in all three renderers (preview, `.pptx`, `.pdf`). The frontend type
permits six; `image` has no producer and `chart` would draw invented numbers.
The outline also picks the deck's accent from a fixed palette — unless the
project wears a design system, in which case the palette rule is dropped from
the prompt entirely and the accent arrives from the project. A slide count
stated in the request is honoured up to 50.

**The outline can run on a different model.** `governance.outline_model_id`
names one; empty — the default everywhere — plans with the surface's own model.
It applies to all three writing tracks and only to the planning call, which is
one call per document however many blocks follow it.

Its tokens are billed at its own price: the turn reports planning and writing
apart, because a frontier outline charged at a local writer's rate is a ledger
that says the wrong thing about where the money went.

**Measured, it changed nothing here.** Three decks per arm on the same prompt
and template, planner `claude-sonnet-5` against the local writer planning for
itself: both arms produced eight blocks, four distinct layouts and a longest
run of one or two. The flatness a stronger planner was meant to fix had
already been fixed by the prompt, and what these numbers cannot see — whether
the *order* of a deck is better argued — is not something this instance can
measure. The setting stays, off by default, and the claim under it is now the
measurement rather than the expectation.

The outline carries the same request and the same context the body does, so it
is subject to everything the body is: the id is checked when it is saved, and
again per turn against this account's allowlist, this surface, and the boundary
the turn was decided on. A turn privacy routed to a strict-local model gets no
planner at all — that route exists so the text does not leave — and a planner
may never be less contained than the writer, or a field on the admin screen
would quietly widen the egress of every document. Anything that fails falls
back to the writing model rather than failing the turn.

### Design templates

The rendering catalogue: shapes the model writes into, as opposed to the
prompt templates a person writes for themselves. They ship inside the API image
under `app/design_templates/<id>/`: a `template.toml`, a `seed.html` and a
`sample.html` always, plus the `instructions.md` and `checklist.md` a writing
turn reads — which the media recipes, having no writing turn, do without.

What ships is a small set chosen for the people this product has — decks for a
seminar, a lecture and a proposal; documents for a report, a one-pager, minutes,
a lab notebook and a notice; prompt recipes for posters, covers, diagrams, clips
and narration. **None of it is ported.** OpenDesign's own catalogue runs to a
hundred entries whose seeds carry keyboard runtimes and parent-relative assets,
neither of which survives a `sandbox=""` iframe; these were written against the
constraints this product actually has. The number is meant to stay readable —
a list somebody reads, not a catalogue they scroll past.

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
to a fixed tag vocabulary first. Script elements, event handlers, remote
`src`/`href`, and `h1`/`h2` are removed with their contents: the first three
because the file is downloaded and opened outside the sandbox, the last because
the wrapper already wrote that heading and a second one prints the title twice.

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

**Every format carries it, `.hwpx` included.** That one took a detour worth
recording. A picture in OWPML is three things that have to agree — the bytes in
`BinData/imageN.png` (stored, not deflated), an
`<opf:item id="imageN" … isEmbeded="1"/>` in `Contents/content.hpf`, and
`<hc:img binaryItemIDRef="imageN">` inside an `<hp:pic>` in the section. That
`<opf:item>` id is the entire link; `isEmbeded` has one `d`, which is OWPML's
own spelling, and Hancom drops the picture without it. Nothing is declared in
`header.xml`: `<hh:binDataList>` belongs to the older HML format and no HWPX
contains one.

The part that cost the time was not the picture at all. The first attempt
opened in Hancom with the text correct and no picture anywhere, and the reason
was a missing `<hp:secPr>` — page geometry, which every `.hwpx` this wrote had
been leaving out. Text lays out on Hancom's defaults without it; an object
sized in absolute units has no page box to sit in, and is read and then never
drawn. Every document now carries A4 and its margins in the first paragraph's
run, which is also where Hancom itself puts them.

None of this could be checked here — LibreOffice's Hancom filter reads the v5
binary format and not HWPX, and no independent OWPML implementation is
installable — so it was settled the only way left: the structure was
transcribed from genuine Hancom output (hwpxlib's test corpus), three variants
differing by one element each were generated, and somebody opened them in
Hancom Office.

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

Two constraints shape every seed:

**No script.** Artifacts render in a `sandbox=""` iframe, so a deck navigates
by CSS scroll-snap rather than a keyboard runtime. That is a smaller deck than
open-design's, and one that cannot run model-written JavaScript in a browser.

**Print is the export.** There is no headless browser in this image — see
`report_export`, which chose reportlab over an HTML engine — so every seed
carries `@media print` rules that put one slide or section on one page. The
`.html` file is the faithful copy, and printing it in a browser is how it
becomes a PDF. The file is deliberately not opened in a tab from the app: a
`blob:` URL inherits this origin.

The other formats come from `services/page_export.py`, which reads the markup
back with the standard library's `HTMLParser`. That is possible because
`assemble` wrote it out of a closed vocabulary, so the structure is known
rather than guessed: each `<section>` becomes a slide or a section, keeping its
heading, its lines, which column each line was in, and its table rows. A deck
then goes through `deck_export` to `.pptx` and `.pdf`, a document through
`report_export` to `.docx`, `.pdf` and `.hwpx`.

What that conversion buys is **editability, not fidelity**. The deck opens in
PowerPoint as real slides in the right order with the design system's accent
and face, laid out by this product's own deck renderer — not by the template's
stylesheet, which needs a browser. Two things were added to `deck_export` to
carry it: a `table` layout, because flattening a table into bullets leaves the
reader to reassemble it, and an explicit `columns` field, because an HTML deck
knows which column each line was in and halving a merged list would put the
wrong items on the wrong side. A JSON deck, which has neither, still halves its
own list exactly as before.

A template marked `dark` presents on a dark ground, and its `.pptx` follows —
the design system's ink is a colour chosen for paper and would be unreadable
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
makes it safe. An artifact written before blocks kept their markup is refused
rather than rebuilt out of whichever pieces happened to be stored.

**Media templates are prompt templates.** Image, video and audio produce no
document, so what a template gives them is the sentence itself: an
`example_prompt` with blanks written `{name}`, and an `[[arguments]]` entry per
blank carrying a label, a default and — where the answer is a closed set — the
options. The gallery renders those as a small form, substitutes them, and puts
the finished sentence **in the composer**. On these surfaces the prompt is the
entire input, so a template that sent something the person never read would be
one they could not correct.

A `[defaults]` table carries the settings the shape implies — aspect, duration,
resolution, voice, whether the clip has sound — and picking the card sets those
chips. Only the keys it names: a template silent about duration leaves whatever
was last chosen rather than resetting it.

Image templates additionally carry an English `prompt_suffix`, folded into
`imagegen.compose_prompt` after the style chip and before the project's design
system. That is the one thing kept out of the composer, and the rule is:
**guardrails invisible, brief visible.** `no lettering, no logos` is true of
every picture that template makes and would be noise in a sentence somebody is
editing. Video and audio templates have no such standing rule and therefore no
suffix — their whole expertise is in the prompt.

`GET /design-templates` lists the catalogue with both a Korean and an English
half — names, descriptions, example prompts, argument labels and their option
lists — and the client picks by language. `GET /design-templates/{id}/preview`
renders the seed around its own sample and is **unauthenticated**, like the
branding logo: the body is a constant that ships in this image, an iframe `src`
cannot carry an Authorization header, and `current_viewer`'s `?t=` escape hatch
would put a live access token in the proxy log for a static asset.

That preview takes the four tokens as query parameters — `accent`, `ink`,
`muted`, `font` — because a card is only worth looking at if it is in the look
the file will come out in: the gallery opened inside a project asks for that
project's design system, and the design editor asks for whatever the form
currently holds, which is what makes three hex fields into something you can
see. The values go through `design.normalise_tokens` on the way in, the same
call the renderers make, so a bad one falls back per field and nothing but a
validated colour and one of two known faces reaches the document. The look
travels as itself rather than as an id, which is what keeps the route free of
anybody's rows and therefore still safe to leave open.

### Checking what was written

Three surfaces carry the same rules in their prompts — do not invent figures,
do not pad, keep emoji out of headings — stated in `craft`, in the per-surface
system prompts, and in the starter skills. Nothing read the answer back to see
whether they held. `services/lint.py` does, on every report, deck and HTML
artifact, and it costs no model call: the check is free, and acting on it stays
explicit. Findings are stored on the artifact, so a document that was fine when
it was made does not start reporting problems because the rules were tightened
afterwards.

**Half of OpenDesign's `lint-artifact` rules are deliberately absent.** Its P0
list is mostly visual — default indigo accents, two-stop gradients, rounded
cards with a coloured left border — because there the model writes CSS. Here it
cannot: the seed owns every colour and face, and `sanitise` drops `style`,
every remote address and every tag outside a fixed vocabulary before anything
is stored. `class` survives — a block needs `lead` and `cols` to reach what its
own seed styles — so a model that invents `card` or `columns-2` gets markup
that renders as nothing rather than markup that fights the template. Those
rules hold by construction, and re-stating them would be a check that can never
fire. What is left is what the model does choose — the words.

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
numbers rule is narrow on purpose — an ordinary "12% 증가" is what a report is
*for*, so only round marketing figures and the multiplier-with-a-verb form are
flagged. A check that fires on ordinary writing is one people learn to ignore.

Nothing is corrected automatically. The panel shows a count and a list; the
report surface already has "이 절만 다시 쓰기" for acting on it, and an HTML
artifact has the same per-block rewrite.

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

**One reviewer, one pass.** OpenDesign's Critique Theater seats a five-person
jury and runs up to three rounds, refusing to ship under 8.0 — five to fifteen
model calls per artifact. Here every call is somebody's credit and the bill is
shown before the turn, so the panel is one reviewer and the pass is asked for
explicitly.

The rubric comes from the template the document was written into
(`checklist.md`), or from a default for the built-in report and deck tracks.
Reviewing rules are kept apart from writing rules on purpose: folded into the
brief, a rubric becomes a checklist the model writes *to* rather than one it
can be measured by.

### Reading a design system out of a document

Four colours and a paragraph of house style is the part nobody types from
scratch, which is why the only design systems most accounts had were the three
seeded ones. The material is usually already on hand — the 공문 template
everything is filed on, an earlier report, a page on the department site.

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

Costs one call on the cheapest chat model, charged like any other.

### Design systems

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

`craft` names brand-agnostic rules the design system opts into, filtered per
surface: a rule about heading depth reaches a report and not a chat turn.

The tokens are **copied onto the artifact** when it is made, and the exporters
read them from there. A deck presented last month does not repaint itself
because the project changed its design system since — the same rule the
per-slide accent already followed.

A project with no design system produces exactly what it produced before this
existed, down to the greys in the PDF. That is the property the export tests
pin.

### The artifact gallery

`GET /artifacts` returns **a page of cards**, not the workspace. It used to
return everything whole — on the instance this was written against, 385 rows
and 4.0 MB, of which 2.8 MB was the markup of 69 HTML documents the screen
draws as thumbnails. Opening the gallery now costs one 30 KB page plus the
handful of documents actually on screen.

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

**Both are stored as ordinary turns.** The prompt is a user message; the reply
is an assistant message with an empty body carrying `artifact_ids`, and the
transcript renders the picture or the player where the answer goes. Nothing is
written *about* the result — prose invented for a picture would be the model
quoted saying something it never said. A request that produced nothing marks
the prompt `no_answer` and leaves no reply; a batch that broke halfway keeps
what arrived and marks the reply `interrupted`.

Image generation is a single upstream call with a prompt rather than a turn
with a system message, so it never goes through `assemble`. It resolves the
project's design system on its own (`workspace_context.design_for`) — without
that, the one surface whose entire output is a look was the one surface the
look did not reach.

Both call OpenRouter through LiteLLM via `chat/completions`. Audio **requires
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
instructions → project instructions → design system → memories → skills → files
attached to this turn → project knowledge → tool rules → conversation history.

The design block sits after the project's own instructions and before the
skills: the look is a property of the project, and a skill switched on for this
turn is the more specific instruction, so it comes later and wins.

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
LiteLLM logging and `disable_fallbacks`. The classifier receives the complete
answer-visible messages and quality-model tool schema snapshot. If that payload
exceeds 8,000 characters, it is not truncated and the quality ceiling is kept.
Only a high-confidence low-complexity JSON verdict can select an economy model;
every timeout, malformed response or uncertain state keeps the quality ceiling.

Economy candidates are administrator-ordered and revalidated against the live
catalogue, caller allowlist, context window, data boundary and both token
prices. `hybrid`, `unknown` and `privacyOnly` models are excluded. The chosen
answer call receives no tools and also disables fallback, so an unbounded tool
result cannot invalidate the checked context fit and a failed economy route
cannot cause a second, hidden premium charge. Message routing stores the
quality, selected and actual models plus enum-only decision metadata; classifier
prompts and free-form model reasoning are never persisted.

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
- **A single API instance is assumed.** Migrations run on container start,
  runtime settings are cached in-process, and the stop signal for a running
  turn is held in that process — `POST /sessions/{id}/stop` reaches the turn
  only when it lands on the replica running it. See
  [deployment.md](deployment.md#scaling-notes).
- **A region needs a mounted GeoLite2 file.** There is no network lookup, by
  design, so without one every screen shows an address and no place.
- **Media conversations created before message recording hold no turn.** They
  keep their title and their result; nothing backfills a conversation nobody
  had.
