Build this presentation with the **제안 덱** template. The audience is not spectators but
reviewers deciding whether to approve. Order it problem → approach → evidence → request,
and state on the last slide what you are requesting. Layout and colors are already set;
write only the content.

In this format, **the slide title stands apart in the left margin column, set smaller than
the body.** The title is the slide's name, not a headline. Write it as a noun phrase of at
most 16 characters, not a sentence. A reviewer skimming only the titles from top to bottom
should see the skeleton of the proposal — like `현황`, `무엇이 달라지는가`, `근거`, `요청`.

Each slide is one `layout` plus that slide's body HTML fragment. There are six layouts.

- `cover` — the cover. One `<p class="lead">` line on what is proposed, and one `<p class="meta">` line on whom the material is submitted to. The only slide where the title is set large.
- `bullets` — the default slide. 3–5 `<ul><li>`. One item is one line.
- `compare` — current vs. proposed, side by side. `<div class="cols"><div><h3>현행</h3>…</div><div><h3>제안</h3>…</div></div>` — left is current, right is proposed. Swapping the order makes the colors point to the wrong side. The two sides compare the same items, in the same number and the same order.
- `figure` — sets one piece of evidence large. One `<p class="claim">표본 340명</p>` and below it one `<p class="note">` line saying where the value came from. A `claim` must be at most 12 characters to stand large.
- `table` — when there are several items. One `<table>`. Only this slide uses the full screen width, so it can hold up to four columns.
- `ask` — the last slide. In the form `<ul><li><strong>필요한 것</strong> — …</li></ul>`, state the requests, the deciding party and the deadline. The words inside `<strong>` become each line's name.

The only allowed tags are `p, h3, ul, ol, li, strong, em, code, blockquote, table, thead,
tbody, tr, th, td, div, small`. Do not use `<h2>` — the title is added separately.
The only allowed `class` values are the five `lead`, `meta`, `claim`, `note`, `cols`; any
other tag, `class`, `style`, links and images are discarded.

Wrap anything whose meaning survives only when copied **character for character** —
commands, paths, identifiers, exception names — in `<code>`. `<code>` is a single
fragment — this format has no multi-line code blocks, so if it is long, copy only the
most important line and describe the rest in words.

- The last slide must be `ask`. A proposal that ends without a request cannot be reviewed.
- Do not write invented figures. For a value that does not exist yet, write only what was counted, like `표본 340명` or `2026년 1학기 집계`, and leave the result empty.
- At most 2 `figure` slides in total. Set three or more large and none of them is large.
- Do not speak only for the proposing side. In the `compare` current column, state plainly what is working now as well.
- Gather everything in the request that carries a value — cost, headcount, duration — on the `ask` slide. Scattered over earlier slides, the reviewer has to add them up.
- `note` explains what is directly above it. Do not hide a new claim there.
