---
name: 날짜 계산
description: 「다음 주 금요일」「이번 달 말」「2주 뒤」 같은 상대 날짜를 회의일·메일 발신일 기준으로 풀고, 경력 기간(겹침 제외)·영업일·납기를 셉니다.
when_to_use: 회의록·일정·기한에 상대 날짜가 있을 때, 경력·사업 기간의 개월 수와 총합을 셀 때, 발주일에서 납기·영업일을 계산할 때.
kinds: [chat]
required_tools: [run_skill_script]
version: 1.0.0
---
Do not count dates in your head; resolve them with the script.

1. Call `run_skill_script` with skill `date-calc`, script `dates`. Choose with `op` in args.
   - Relative dates: `{"op": "resolve", "base_date": "2026-10-12", "expressions": [{"text": "다음 주 금요일", "source_line": "<그 말이 나온 줄>"}]}`
   - Find them across the material: `{"op": "scan", "text": "<메모·전사본>", "base_date": "2026-10-12"}` — from a 「[메일 — 10/9(금) …]」 line on, that mail's sending date is the reference.
   - Periods: `{"op": "span", "periods": [{"label": "A사", "start": "2019-04", "end": "2021-01"}, {"label": "B사", "start": "2023-03", "end": "현재"}], "as_of": "2026-10"}` — start and end months inclusive, overlapping months counted once, gaps are reported too. To leave a concurrent activity out of the total, use `"counted": false`.
   - Adding and deadlines: `{"op": "add", "date": "2026-10-19", "weeks": 8, "deadline": "2026-12-18"}`
   - All lead times and periods in the material at once: `{"op": "lead_times", "text": "<자료 전체>"}` — pairs 「발주 2026. 10. 19.」 with 「발주 후 8주」, resolves them to dates, and checks them against the material's completion deadline (works without a meeting date).
   - Business days: `{"op": "business_days", "start": "...", "end": "..."}`, `{"op": "add_business_days", "date": "...", "days": 5}`
2. The reference day is the meeting day for remarks, and that mail's sending date for a mail. If the meeting day is unknown, ask first.
3. `weekend` and `holiday` in the result are for display only. Move a date only when the material says so.
4. For `unresolved` (not settled to a single date) and `unclear` (inaudible through noise), do not guess a date:
   leave `unresolved` as 「미정」 and `unclear` as 「[기한]」, and keep the original wording in 비고. Write a repeat (`repeat`) like 「매주 월요일(2026-10-19부터)」.

Weekends and public holidays follow 「관공서의 공휴일에 관한 규정」 (amended April 2026: 노동절·제헌절 added, substitute holidays included) and
the 우주항공청 「2026년 월력요항」「2027년 월력요항」 (published 2026-06-29). Holidays outside 2026–2027 are not included (`holidays_unknown`).
