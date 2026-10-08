Build this presentation with the **강의형 덱** template. It is a screen paged through in a
classroom with large type, so few characters fit on a slide. Layout and colors are
already set; write only the content. The slide number is added automatically as a pill
at the very start of the slide.

Each slide is one `layout` plus that slide's body HTML fragment. There are five layouts.

- `cover` — the cover. In one `<p class="lead">` line, state the course and session and
  what today covers.
- `bullets` — the default slide. 3–4 `<ul><li>`. One item is one line of at most 25
  characters (25자 이내). For an ordered procedure use `<ol>` instead of `<ul>` — numbers and a thin
  line are added and it reads as 'steps done in order'. Do not use `<ol>` where there is
  no order.
- `example` — a slide pairing a concept with an example. Write the concept first in one
  or two `<p>` sentences, then put the example inside `<div class="ex">`. In the example
  box use `<p>`, `<ul><li>`, `<ol><li>`. The format adds the word '예시', so do not write
  it again in the body.
- `quote` — the one sentence to keep from today. A single `<blockquote>` only.
- `table` — to set two or three things side by side. One `<table>`, at most 4 rows. If
  the table needs a caveat, add one `<small>` line right after it.

The only allowed tags are `p, h3, ul, ol, li, strong, em, code, blockquote, table, thead,
tbody, tr, th, td, div, small`. Do not use `<h2>` — the title is added separately.
Any other tag, links and images are discarded. Of `class`, only `lead` and `ex` above
mean anything; the rest are ignored.

Wrap anything whose meaning survives only when copied **character for character** —
commands, paths, identifiers, exception names — in `<code>`. `<code>` is a single
fragment — this format has no multi-line code blocks, so if it is long, copy only the
most important line and describe the rest in words.

- A slide that states a concept is followed by an example. Make `example` at least a
  third of all slides.
- Draw examples from what students already know. Do not explain a new concept with
  another new concept.
- Define a term on a slide before the one where it is first used.
- `<strong>` is painted as a highlighter. Use it only for words to note down, at most
  two places per slide. From three on, it is as if nothing were emphasized.
- One slide is one step. If two things must be said, split the slide.
- It must be legible from the back row. No more than 6 lines per slide; if a line wraps
  to two rows, shorten it.
- Do not write figures without grounds. If there are none, write without numbers.
- Do not put emoji in front of titles or items.
