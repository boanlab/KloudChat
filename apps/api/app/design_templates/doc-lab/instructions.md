Build this document with the **실험 노트** template. Section numbers and the layout are
already set; write only the content.

Each section is one `layout` plus that section's body HTML fragment.

- `cover` — the cover. In one `<p class="lead">` line, state what this note sets out to
  confirm, then record 과목·측정일·장비·소자·원자료 위치 in a two-column `<table>`. The left
  column is the label, the right the value; value cells are set in monospace, so put there
  what must be copied exactly, such as dates, model names and paths.
- `section` — one section. Write in the order 목적 → 방법 → 결과 → 한계; split long results
  into several sections. Section titles stand with their numbers in the left margin column,
  so keep them to two or three characters, like `목적`, `방법`, `결과`, `한계`, `측정 조건`.
  A long title becomes four lines in the margin column.

The only allowed tags are `p, h3, ul, ol, li, strong, em, code, blockquote, table, thead, tbody,
tr, th, td, small, figure, figcaption, dl, dt, dd, hr`. Do not use `<h2>` — the title is
added separately. Any other tag, `style`, links and external images are discarded. The only
allowed classes are `lead` and `formula`; any other has no effect.

Wrap anything whose meaning survives only when copied **character for character** —
commands, paths, identifiers, exception names — in `<code>`. `<code>` is a single
fragment — this format has no multi-line code blocks, so if it is long, copy only the
most important line and describe the rest in words.

## 측정값

- Write measurements as tables. **Only the first column is the item name; every other
  column is numeric.** Those columns are set as monospace numbers aligned right, so putting
  units or sentences in them misaligns the values and breaks comparison.
- Write the unit once, in the column title. Like `측정 f_c (Hz)`, `편차 (%)`.
- Align decimal places within a column. When putting calculated and measured values side by
  side, make the column titles show which is calculated and which is measured.
- On the line right after the table, add `<small><strong>표 1.</strong> 무엇을 몇 번 재었는지, 어떤 조건에서
  잰 값인지</small>`. This line is the table's footnote; do not move footnotes to the end of
  the section.

## 계산과 기호

- Write formulas one step per `<p class="formula">` line. Consecutive lines are grouped
  into one block, so the derivation shows as is. Do not use LaTeX syntax; write plain text —
  `sqrt(...)`, `^2`, `f_c`, `·`, `→` are enough.
- Do not write only the resulting number; keep the formula it came from. The TA looks at the
  derivation, not the value.
- To define symbols and units, use `<dl>`. Put the symbol in `<dt>` and, in `<dd>`, one or two
  sentences on exactly what that symbol refers to in this note.

## 그 밖에

- Write the method so that someone else can repeat it exactly. Do not leave out equipment
  names, model names, versions, settings or repeat counts. Write an ordered procedure as `<ol>`.
- Describe in sentences inside `<figure>` what is explained with a picture — circuit
  diagrams, layouts, waveforms — and put `<figcaption><strong>그림 1.</strong> …</figcaption>`
  last inside it.
- Do not mix what was observed and what was interpreted in one sentence. Write the
  interpretation in separate sentences, with its grounds.
- Do not delete values that deviate from expectations; write them as they are, and if the
  cause is unknown, say it is unknown. If you suspect a cause, state in numbers how much it
  explains and how much remains.
- The last section is the limitations. State what this note cannot say, and what breaks if
  conditions change.

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
