Build this document with the **안내문** template. The official-letter form and section numbers
are already laid out; write only what is to be announced.

Each section is one `layout` plus that section's body HTML fragment.

- `cover` — the document head. In one `<p>` line, state whom the document informs of what,
  then write 수신·시행·담당 in a two-column `<table>`. All three lines must be there, and 담당
  must include a way to ask back (내선·메일).
- `section` — one section. Section numbers are added automatically, so do not write "1." yourself.

## 계단

This format is a single staircase of numbers and content. The section number stands in the
left margin, and every line in that section is indented one step to align with the line to
the right of the number. To subdivide within a section, use `<ol>` — it is lettered 가·나·다.
For a list whose order carries no meaning, use `<ul>`; it is marked with dashes.

Order the sections **근거 → 내용 → 협조 요청 → 붙임**. This order is the order of a document
passing through the approval line.

- The first section is the grounds. State which regulation, meeting or notice the announcement
  follows; if there are no grounds, state at least what it was decided on.
- In the content section, do not scatter dates, times, places and targets through sentences;
  list them one per line. If there are several values per target, write a `<table>` — use a
  table only for something read across, like a timetable, and do not turn a two-line list into a table.
- In the 협조 요청 section, gather what the recipients must do, and state all three: what, by
  when, and to where. If there is nothing to do, say so.
- The last section is 붙임. List document names and copies in an `<ol>`, one per line, like
  `임시 좌석 배치도 1부.`, and append `끝.` two spaces after the end of the last item.
  If there is no attachment, write only the single line `붙임 없음  끝.`.
- Attach an added note as one or two `<small>` lines right below the table or paragraph it
  explains. Do not write a section's body in `<small>`.

## 쓸 수 있는 것

The only allowed tags are `p, h3, ul, ol, li, strong, em, code, blockquote, table, thead,
tbody, tr, th, td, small`. Do not use `<h2>` — the title is added separately.
`class`, `style`, links, images and any other tag are discarded.

Wrap anything whose meaning survives only when copied **character for character** —
commands, paths, identifiers, exception names — in `<code>`. `<code>` is a single
fragment — this format has no multi-line code blocks, so if it is long, copy only the
most important line and describe the rest in words.

- Write in 합쇼체. Do not include greetings and flourishes such as "많은 관심 바랍니다".
- Emphasis is `<strong>` only. Do not emphasize with color or symbols — this document is
  photocopied in black and white and posted on a notice board, where only weight and lines survive.
- Do not write an undecided schedule as if it were decided. If it is undecided, write 미정 and
  state when it will be announced again.

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
