Build this document with the **추진 계획(안)** format. Before an event, project or training is
approved, it shows at once what will be done, when, where and for how much. The layout adds
the head (작성일·작성 부서·작성자) and the section numbers.

- Order the sections 추진 배경 및 목적 → 추진 개요 → 세부 추진 내용 → 소요 예산 → 추진 일정 →
  기대 효과.
- 추진 개요 is a table (구분 | 내용) with one row each for 일시·장소·대상·인원·주관.
- 소요 예산 is a table (항목 | 산출 근거 | 금액). 산출 근거 is 「단가 × 수량」; the last row is
  the total and must equal the sum of the item amounts.
- 추진 일정 is a table (일정 | 내용 | 담당).
- Write 기대 효과 as what changes, without guessed figures.

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
- Do not invent times, places, headcounts, amounts or names not in the material. Do not leave bracket blanks for them either: write the line so it holds without the value (「장소: 확정 후 안내」), or 「미정」 in a table cell with one 「확인 필요」 line under the table. Brackets only for what the person fills in themselves (작성자, 서명, 연락처).
