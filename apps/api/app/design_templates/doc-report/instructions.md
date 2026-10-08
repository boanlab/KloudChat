Build this document with the **보고 문서** template. The cover and layout are already set;
write only the content.

Each section is one `layout` plus that section's body HTML fragment.

- `cover` — the cover. It takes a full page. In one `<p class="lead">` line, state who this
  document addresses and what it tells them, then write the four items **목적 · 독자 · 기간 · 작성**
  as `<dt>`/`<dd>` pairs in a `<dl>`. Leave out items you do not know.
- `section` — one body section. Mix paragraphs and lists. The layout adds section numbers, so
  do not write numbers like "1." in the body.

## 근거는 각주로 단다

Mark the end of a sentence with a figure or quotation with `<sup>*</sup>`, and write the
footnote text in `<small>` within that section. The layout gathers footnotes at the foot of the
section and also adds the `*`, `**`, `***` numbering, so **you only need to keep the marks
and the footnotes in the same order.**

    <p>사내 녹음 1,200문장으로 다시 측정했다.<sup>*</sup></p>
    <p>오탐은 3.2%로 떨어졌다.<sup>**</sup></p>
    <small>녹음 조건 — 실내 45dBA, 화자 12명. 원본은 연구소 NAS.</small>
    <small>2026년 7월 사내 측정. 직전 분기 5.1% 대비.</small>

- Inside `<sup>` write only `*`, `**`, `***`. Count from `*` again in each section.
- `<small>` must be a direct child of `<section>`. Put inside a `<p>`, it gets mixed into the body.
- The order you write `<small>` in is the `*`, `**` order. If it does not match the order of
  the marks, a footnote points to the wrong sentence.
- In footnotes, write **what breaks the flow of the body but without which it cannot be
  verified** — sources, measurement conditions, reference dates, the range you could not
  confirm. Do not push what belongs in the body down here.
- Attach a footnote to figures and quotations. If there are no grounds to attach, better not
  to write that figure.
- Keep footnotes short, one or two sentences. If a footnote is longer than a paragraph, move it into the body.
- Do not write a `<small>` without a mark or a `<sup>` without a footnote. They are a pair.

The only allowed tags are `p, h3, ul, ol, li, strong, em, code, sup, blockquote, table,
thead, tbody, tr, th, td, small, dl, dt, dd`. Do not use `<h2>` — section titles are added
separately. Any other tag, `style`, links and images are discarded.

Wrap anything whose meaning survives only when copied **character for character** —
commands, paths, identifiers, exception names — in `<code>`. `<code>` is a single
fragment — this format has no multi-line code blocks, so if it is long, copy only the
most important line and describe the rest in words.

- Write comparisons, figures and schedules as tables. Do not put blank lines between rows —
  with blank lines it is drawn as sentences, not a table. Write it in the form `<table><thead><tr><th>…`.
- A section runs 2–4 paragraphs. Do not fill a section with lists alone.
- Write only figures with grounds, and state in a footnote where each value came from.
- Do not repeat what an earlier section said.

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

## 절 이름

These are the sections this format uses. Do not invent new names per document — if two
documents in the same format have different tables of contents, the reader must learn the
structure anew each time.

- **검토 범위** — what was and was not examined. If not stated, what is missing reads as a mistake.
- **확인한 것** — facts with footnote marks on their figures and quotations.
- **비교** — where things are compared on the same criteria. For a comparison value, state what it is relative to.
- **권고와 남은 확인** — what is proposed, and what has not been confirmed yet.
- **읽는 사람이 할 일** — what is left at the end.
