Build this presentation with the **대비형 덱** template. It is shown in large type on a
dark screen in a big, dimmed room. It must be legible from the back row, so a slide holds
far fewer characters than the editorial format. Layout and colors are already set; write
only the content.

In this format, **every slide shares one horizontal line drawn near the top of the
screen.** The slide title hangs right above that line and the body always starts below
it. So the title must be one line — at most 20 characters; wrapping to two lines pushes
the line up.

Each slide is one `layout` plus that slide's body HTML fragment. There are five layouts.

- `cover` — the cover. In one `<p class="lead">` line, state who this presentation addresses and what it tells them. The only slide whose whole screen is painted in the accent color.
- `bullets` — the default slide. 3–4 `<ul><li>`. One item is one line of at most 25 characters. If it wraps to two lines, it is a sentence, not an item.
- `quote` — the one sentence to leave from this presentation. A single `<blockquote>` only. This slide's title is not a place to repeat the quote but a short heading naming what that sentence is — like `이번 분기에 남길 한 줄`.
- `split` — to set two things side by side. `<div class="cols"><div><h3>이름</h3>…</div><div><h3>이름</h3>…</div></div>` The two columns are separated only by space, without a divider, so always give each column an `<h3>` name.
- `table` — comparing figures or items. One `<table>`. Up to three columns. Do not write invented numbers.

The only allowed tags are `p, h3, ul, ol, li, strong, em, code, blockquote, table, thead,
tbody, tr, th, td, div`. Do not use `<h2>` — the slide title is added separately.
The only allowed `class` values are `lead` and `cols`; any other tag, `class`,
`style`, links and images are discarded.

Wrap anything whose meaning survives only when copied **character for character** —
commands, paths, identifiers, exception names — in `<code>`. `<code>` is a single
fragment — this format has no multi-line code blocks, so if it is long, copy only the
most important line and describe the rest in words.

- A slide is a screen to look at, not text to read. Do not string paragraphs together.
- When the same layout runs for more than four slides, the presentation gets dull. At most 2 `quote` slides in total.
- Do not write figures without grounds. If there are none, write without numbers.
- Emphasis is `<strong>` in one place only. On a dark screen, when several things are bold, none of them is.
