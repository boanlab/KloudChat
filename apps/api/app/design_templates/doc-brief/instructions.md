Build this document with the **한 장 요약** template. The layout is already set; write
only the content. The reader looks, within 30 seconds, only for "what must be decided" and
"what happens next". The moment it runs to two pages, this format has failed.

Each section is one `layout` plus that section's body HTML fragment.

- `cover` — the title block at the top. In one `<p class="lead">` line, state the
  question this document answers. It is the head of a single page, not a cover page, so keep it short.
- `section` — one item. A short title and a body of at most 5 lines. Items sit in two columns.

## Rules

- **It must fit on one page.** 4–6 items, each no longer than 5 lines.
- Each item answers one thing only. Split them like 결정할 것 · 지금 상황 · 대안 · 걸려 있는 것 ·
  미루면 생기는 일.
- **The last item is always the next action.** The layout spreads only the last item
  across the full page width and colors it, so putting anything else last highlights the
  wrong thing. Write who does what by when, one per line.
- Use a table only to compare alternatives on the same criteria, and at most **3 rows**
  excluding the header. A table must fit in one column. No more than 3 columns.
- Attach caveats, assumptions and calculation grounds as one `<small>` line at the end of
  the item. They must not lengthen the body.

The only allowed tags are `p, h3, ul, ol, li, strong, em, code, blockquote, table, thead,
tbody, tr, th, td, small, dl, dt, dd`. Do not use `<h2>` — item titles are added
separately. Any other tag, `style`, links and images are discarded.

Wrap anything whose meaning survives only when copied **character for character** —
commands, paths, identifiers, exception names — in `<code>`. `<code>` is a single
fragment — this format has no multi-line code blocks, so if it is long, copy only the
most important line and describe the rest in words.

## 표·강조 수치·절차

The layout draws these three separately. The format controls size and color, so do not
use `style`.

- **강조 수치** — if two or three numbers are the conclusion of a section, write them
  inside `<div class="kpi">`, one `<div><strong>값</strong><span>이름</span></div>` per cell.
  At most 4. Keep values short, like `32%`, `1.4초`. Do not copy figures from a table — a
  table is for comparing, this block is for **making one thing remembered**.
- **절차** — write steps done in order as `<ol class="steps">` with `<li><strong>이름
  </strong> <span>설명</span></li>`. The layout adds the numbers, so do not write them.
  A branching flow is not a procedure.
- **표** — write comparisons, figures and schedules as `<table>`. 3–5 rows is right.

## 그래프

If the **shape** of the numbers is the point, write `<figure class="chart" data-source="…">`.
Put the numbers in `data-source`; the layout draws it.

    <figure class="chart" data-source="bar | 건
    분기 | 처리 건수 | 반려 건수
    1분기 | 120 | 8
    2분기 | 210 | 11"></figure>

- The first line is `종류 | 단위`. `bar` compares items, `line` shows a trend over time.
  The second line is `가로축 이름 | 계열 이름들`. The rest are values.
- At most 8 x-axis items and at most 2 series. **Every line must have as many values as
  there are series** — if even one is missing, that whole line is dropped.
- **Do not write invented figures.** A chart reads as more factual than numbers. Without
  grounds, write a sentence instead of a chart.
