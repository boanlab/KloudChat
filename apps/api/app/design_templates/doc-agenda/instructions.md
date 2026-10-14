Build this document with the **회의 안건** format. It goes to the attendees before the meeting
so each knows what will be covered, who leads each item, how long it takes and what the meeting
must decide. The layout adds the head (회의명·일시·장소·참석자·작성자); write only the content.

Each section is one `layout` plus that section's body HTML fragment.

- Order the sections 회의 목적 → 안건 → 안건별 검토 사항 → 사전 자료 → 결정 필요 사항.
- 회의 목적 — one `<p class="lead">` of one or two lines: what must be settled when the
  meeting ends.
- 안건 — one table (순서 | 안건 | 담당 | 시간 | 목표), one row per item in the material's order.
  목표 is one word: 보고, 논의 or 결정. 결정 when the material asks for a decision or approval,
  보고 when it only shares a result, otherwise 논의.
- 시간 is the item's time box (「14:10~14:30(20분)」) as the material gives it. When the material
  gives only the meeting's start and end, you may propose time boxes that add up exactly to the
  meeting length and head the column 「시간(안)」. With no meeting length either, leave the
  column out rather than inventing one.
- 담당 is a person or team the material names for that item. With none named, write 「미정」 and
  one line under the table naming those items (「※ [안건 번호]번 안건 담당은 자료에 없어 확인이
  필요합니다.」). When no item has a named lead, leave the 담당 column out instead.
- 안건별 검토 사항 — one `<h3>` per item (its title), then two to four short bullets: the
  background in one line and what the attendees should look at. No figures, options or positions
  the material does not give.
- 사전 자료 — a table (자료 | 준비 | 공유 기한) of the documents the material names. If it names
  none, the section is only the line 「사전 자료 없음」 — no sentence explaining why there is no
  table, no advice on what to prepare.
- 결정 필요 사항 — an `<ol>` of the questions the meeting must answer, each with the options the
  material gives (「A안 정기권 / B안 주차권 지급」). If nothing needs a decision, one line saying so.

## 쓸 수 있는 것

The only allowed tags are `p, h3, ul, ol, li, strong, em, code, blockquote, table, thead,
tbody, tr, th, td, small`. Do not use `<h2>` — section titles are added separately. Use only
the class `lead` above; any other class, `style`, links and images are discarded.

## 지키는 것

- This is not minutes. The meeting has not happened: write no decisions, outcomes or action
  items as if it had. A proposal from the material is written as a proposal (「총무팀 안: …」).
- Dates 「2026. 10. 20.(화)」, times 「14:00」 (24-hour clock). A date with a weekday must be
  the calendar's; a date the material gives without a weekday stays without one.
- Do not invent attendees, times, places, documents or names the material does not give. Do not
  leave bracket blanks for them either: leave the column out, write 「미정」 in a table cell with
  one 「확인 필요」 line, or write the line so it holds without the value. Brackets only for what
  the organiser fills in themselves (작성자).
