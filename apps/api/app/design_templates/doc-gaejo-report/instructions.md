Build this document with the **개조식 보고서** format. It is written so that a superior can
judge a review, status or work report on a single page. The layout adds the head
(보고일·보고자·보고 대상) and the section numbers.

- At the very top of the first section, put the two key lines in a box — a ```callout block
  whose first line is 「핵심」, and the next two lines the conclusion and the decision needed
  (or the next action), one line each.
- Order the sections 현황 → 문제점 및 검토 → 향후 조치 및 결정 요청 → 붙임, the order in which
  the reader decides. Write comparisons and figures as tables, with one line 「표: 제목」 above
  the table.
- The conclusion comes first. Do not repeat the same point in each section.
- Raise only the issues and decisions the material raises. A figure the calculation adds that
  the material does not discuss (a VAT basis, a per-person cost) is not an issue; write amounts
  as the material does. Keep each item under its own subject — a vendor or date of one task
  never moves to another.
- List 붙임 only for attachments the material names or tables moved there; never invent one.
  With none, the 붙임 section is the one line 「붙임 없음」.

## 개조식으로 쓰기

- Do not write prose paragraphs. Write the content as lists and show hierarchy by depth —
  the first level, then the second, third and fourth levels below it. The layout draws the
  `□ ○ - ㆍ` symbols to match the depth, so do not type them. In HTML, nest `<ul>` inside `<ul>`.
- End items in the noun form — 「…함」 「…임」 「…예정」 「…필요」. Do not end with 「~합니다」.
  One item is one or two lines.
- Attach units to figures (「320명」 「1,250만 원」 「3.2점(5점 척도)」).
- Write a supplementary note as one line starting with 「※ 」 on the line after the item.
  The layout draws it small and indented.

## 표기

- Dates 「2026. 10. 7.」 — periods instead of the 연·월·일 characters, no leading 0 before month or day.
- Times 「14:00」 — 24-hour clock, colon.
- An amount to be approved 「금113,560원(금일십일만삼천오백육십원)」 — the amount in 한글 in parentheses after the digits.
- 붙임 goes on the line after the body as 「붙임  1. ○○○ 1부.」; with two or more, from the next line 「2. ○○○ 1부.」.
  With only one, no number: 「붙임  ○○○ 1부.」. Do not space it like 「붙 임」 or add a colon.
- Do not write 「끝.」. The layout appends it two spaces after the last character of the body or of 붙임.
- Wrap anything whose meaning survives only when copied character for character — commands, paths, identifiers — in `<code>`.
- Do not invent times, places, headcounts, amounts or names not in the material; leave the slot's name in brackets, like 「[일시]」 「[금액]」.

## 모양 — 다른 메모의 예 (형식만 따르고 내용은 쓰지 않는다)

[현황]
```callout
핵심
열람실 냉방기 4대 중 2대 압축기 고장, 여름 전 교체 필요
교체 업체 선정(A사 또는 B사) 결정 요청
```
- 냉방기 4대 2012년 설치, 2대 6월 점검에서 압축기 고장
- 교체 견적
  - A사 1,450만 원
  - B사 1,620만 원(설치 기간 3일 짧음)
[향후 조치 및 결정 요청]
- 결정 요청: 교체 업체 선정(A사 / B사)
- 업체 선정 후 계약 예정(7. 1.)
