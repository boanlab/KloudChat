Build this document with the **회의록** template. The title block and the layout are already
set; write only the content.

Each section is one `layout` plus that section's body HTML fragment.

- `cover` — the title block. In one `<p class="lead">` line, state what the meeting was
  gathered to decide, then inside `<ul class="meta">` write 일시·장소·참석·기록 one per line,
  like `<li><strong>일시</strong> 2026-08-18 (화) 10:00~11:05</li>`.
  This list stands vertically in the narrow column on the right of the document, so keep each line under 20 characters.
- `section` — one agenda item, or a group of 결정 사항 or 실행 항목. Agenda numbers are added
  automatically, so do not write "1." in the title yourself.

## 오른쪽 눈금자

The backbone of this format is one column fixed vertically on the right of the document. In
the title block the date and place, and in the body every owner and deadline, are pinned
right-aligned in that column. All this format is for is that two months later, skimming just
the right edge shows each person their own name and date.

That is why 결정 사항 and 실행 항목 are written as `<ul class="rows">`, not paragraphs.
One item looks like this.

    <li><strong>정해진 것 한 줄</strong> 왜 그렇게 정했는지 한두 문장. <small>[이름] · [M월 D일]</small></li>

- `<strong>` is the decision or the task itself in one line. It need not end with punctuation.
- The sentences after it are grounds or conditions. If there are none, leave them out.
- `<small>` is the value that hangs on the ruler, one per `<li>`. In the form `담당 · 기한`,
  under 15 characters — short, like `[이름] · [M월 D일]`, `이견 없음`, `전결 규정 §12`.
  A sentence there overflows the column into two or three lines.
- If the owner or deadline was not decided in the meeting, write only 「미정」 in the value
  cell, like `<small>미정 · 미정</small>`, and put why it was not decided and when and who will
  decide it (e.g. 9월 7일까지 담당 지정) in the sentence before. If it is not in the material
  at all, it is 「미정」 too — never invented, never moved over from another item, never a
  bracket blank — and one line after the list names what needs checking (「※ 3번 담당자와
  5번 기한은 회의에서 정하지 않아 확인이 필요합니다.」). Write a deadline only
  as one of 날짜(요일)·「미정」, and keep original wording such as
  「다음 주 금요일까지」 in the sentence before. A line without `<small>` is later read by no one
  as their own.

Tables use the same ruler. With three or more columns, keep the last column for short values
such as owner or deadline, and put long explanations in the earlier columns.

## 쓸 수 있는 것

The only allowed tags are `p, h3, ul, ol, li, strong, em, code, blockquote, table, thead,
tbody, tr, th, td, small`. Do not use `<h2>` — section titles are added separately.
Use only the three `class` values `lead`, `meta`, `rows` above; any other tag, `style`,
links and images are discarded.

Wrap anything whose meaning survives only when copied **character for character** —
commands, paths, identifiers, exception names — in `<code>`. `<code>` is a single
fragment — this format has no multi-line code blocks, so if it is long, copy only the
most important line and describe the rest in words.

- Do not mix what was said with what was decided. Write discussion as paragraphs and decisions as `rows`.
- Every action item must carry an owner and a deadline. If the meeting did not decide them,
  write "미정" — whether the meeting deferred them or the material does not give them — with
  the reason, and one 「확인 필요」 line after the list.
- Do not write an agenda item with no conclusion as if it were concluded; state who decides
  it next and when.
- Do not invent background that did not come up in the meeting. A name or date the material does not give is 「미정」 or left out; brackets only for what the writer fills in (작성자).
- Do not transcribe remarks verbatim; reduce them to what was argued. Use `<blockquote>`
  only when the wording itself must be kept.

## 표·절차

The layout draws these two separately. The format controls size and color, so do not
use `style`.

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

- **참석** — who came and who could not.
- **논의** — what was discussed. Do not mix it with what was decided.
- **결정 사항** — what was decided and why.
- **남은 쟁점** — what was not concluded. State who decides it next and when.
- **실행 항목** — who does what by when.
