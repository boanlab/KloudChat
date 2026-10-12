---
name: 날짜 계산
description: 「다음 주 금요일」「이번 달 말」「2주 뒤」 같은 상대 날짜를 회의일·메일 발신일 기준으로 풀고, 경력 기간(겹침 제외)·영업일·납기를 셉니다.
when_to_use: 회의록·일정·기한에 상대 날짜가 있을 때, 경력·사업 기간의 개월 수와 총합을 셀 때, 발주일에서 납기·영업일을 계산할 때.
kinds: [chat, report]
required_tools: [run_skill_script]
post_check: dates
version: 1.1.0
process: {prepass: [date-calc], checks: [lead-dates]}
---
Do not count dates in your head: a date comes from code (the script in chat, the server's block in a report).

1. Weekends and holidays are for display only. Move a date only when the material says so.
2. A date that does not settle to a single day stays 「미정」, an inaudible one 「[기한]」, with the original wording kept in 비고. Write a repeat like 「매주 월요일(2026-10-19부터)」.
3. An offset counts from the date said before it in the same sentence, or from the event it names when the text dates that event elsewhere: 「개강은 3월 2일」 and 「매 학기 개시 30일 전」 give 2027-01-31.

Weekends and public holidays follow 「관공서의 공휴일에 관한 규정」 (amended April 2026: 노동절·제헌절 added, substitute holidays included) and
the 우주항공청 「2026년 월력요항」「2027년 월력요항」 (published 2026-06-29). Holidays outside 2026–2027 are not included (`holidays_unknown`).

[채팅에서]
## With the script
1. Call `run_skill_script` with skill `date-calc`, script `dates`. Choose with `op` in args.
   - Relative dates: `{"op": "resolve", "base_date": "2026-10-12", "expressions": [{"text": "다음 주 금요일", "source_line": "<그 말이 나온 줄>"}]}`
   - Find them across the material: `{"op": "scan", "text": "<메모·전사본>", "base_date": "2026-10-12"}` — from a 「[메일 — 10/9(금) …]」 line on, that mail's sending date is the reference.
   - Periods: `{"op": "span", "periods": [{"label": "A사", "start": "2019-04", "end": "2021-01"}, {"label": "B사", "start": "2023-03", "end": "현재"}], "as_of": "2026-10"}` — start and end months inclusive, overlapping months counted once, gaps are reported too. To leave a concurrent activity out of the total, use `"counted": false`.
   - Adding and deadlines: `{"op": "add", "date": "2026-10-19", "weeks": 8, "deadline": "2026-12-18"}`
   - All lead times and periods in the material at once: `{"op": "lead_times", "text": "<자료 전체>"}` — pairs 「발주 2026. 10. 19.」 with 「발주 후 8주」, resolves them to dates, and checks them against the material's completion deadline (works without a meeting date).
   - Business days: `{"op": "business_days", "start": "...", "end": "..."}`, `{"op": "add_business_days", "date": "...", "days": 5}`
2. When no date in the material or the question anchors 「오늘」「이번 주」 and the 「시간」 connector is on, call `mcp__time__get_current_time` (timezone `Asia/Seoul` unless the person names another) for today's date and use it as `base_date`; never guess today. For a time in another city, use `mcp__time__convert_time`.
   The reference day is the meeting day for remarks, and that mail's sending date for a mail. If the meeting day is unknown, ask first.
3. In the result, `weekend` and `holiday` are display marks; `unresolved` and `unclear` are the dates rule 2 leaves as 「미정」 and 「[기한]」.

After the answer is written, the server runs `dates` over it (`post_check`): a date the person's relative words resolve to (「개강 2027-03-02, 30일 전」 → 2027-01-31) that the answer missed by a few days, or left as a blank, is set to the worked-out date with its weekday. Every chat answer gets the same check for offsets counted from a date the person gave, with or without this skill.

[보고서에서]
The server resolves the material's relative dates, lead times and periods before you write and gives them in the 「확정 날짜(코드 계산)」 block. Copy every date and weekday from that block; never count one yourself. A date the block does not give stays in the material's own words, or 「[기한]」 where the report needs a date.
