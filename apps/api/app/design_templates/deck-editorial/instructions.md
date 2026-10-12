Build this presentation with the **편집형 덱** template. Layout and colors are already
set; write only the content. Slide numbers are added automatically.

This format uses only a little over half the screen width. A single rule across the
slide under the title opens each slide; the body sits only in the left seven columns
below it, and the right five columns stay empty to the end. In print that empty space
becomes the note margin. So lines are short, and each slide says one thing.

Each slide is one `layout` plus that slide's body HTML fragment. There are five
layouts, each with its own width and structure.

- `cover` — the cover. The only slide where the rule sits above the title and the title
  block settles to the bottom of the screen. In one `<p class="lead">` line, state who
  this presentation addresses and what it tells them, and what it is used for
  (presentation length, whether it is distributed).
- `bullets` — the default slide. 3–5 `<ul><li>`. One item is one sentence. Items get a
  dash instead of a dot and overflow lines hang indented, so two lines are fine; at
  three lines the item is really two points and must be split.
- `quote` — the one sentence to leave from this presentation. Use a single
  `<blockquote>`; put its source or speaker in one `<small>` line below it. Do not prefix
  an em dash (—) — the format adds it. At most 2 such slides in total.
- `split` — to set two things side by side. `<div class="cols"><div>…</div><div>…</div></div>`;
  open each column with a one-word `<h3>` label. `<h3>` is a small name tag, not a
  subheading, so keep it to one line.
- `table` — comparing figures or items. Only this slide uses the full screen width. One
  `<table>`, 3–4 columns and at most 4 rows. Do not write invented numbers.

The only allowed tags are `p, h3, ul, ol, li, strong, em, code, blockquote, small, table,
thead, tbody, tr, th, td, div`. Do not use `<h2>` — the slide title is added separately.
Any other tag, links and images are discarded. Of `class`, only `lead` and `cols` above
mean anything; the rest are ignored.

Wrap anything whose meaning survives only when copied **character for character** —
commands, paths, identifiers, exception names — in `<code>`. `<code>` is a single
fragment — this format has no multi-line code blocks, so if it is long, copy only the
most important line and describe the rest in words.

- A slide is a screen to look at, not text to read. Do not string paragraphs together.
- When the same layout runs for more than three slides, the width never changes and the
  presentation looks like one slide. Change the width now and then with `split` or `table`.
- Emphasis is `<strong>` only, at most one place per slide. Do not emphasize with color
  or symbols.
- Do not write figures without grounds. If there are none, write without numbers.
- Do not put emoji in front of titles or items.
