---
name: 엑셀 정리
description: 제목·빈 행·합계·주석이 섞인 엑셀이나 CSV를 머리글 한 줄과 데이터만 남은 표로 정리하고, 바꾼 내용을 변경내역 시트로 남깁니다. 중복으로 보이는 행은 지우지 않고 변경내역에 표시만 합니다.
when_to_use: 첨부한 엑셀·CSV를 정리·변환하거나 분석하기 전에 표 모양을 바로잡아야 할 때. "1,234" 같은 글자 숫자, 천원·백만원 단위, 여러 형식의 날짜, 합계·소계 행이 섞여 있을 때.
kinds: [chat, report]
required_tools: [run_skill_script]
version: 1.0.0
---
When cleaning an attached table, do not write code yourself; run the fixed script first.

1. Call `run_skill_script` with skill `xlsx-clean`, script `clean`.
   args: `{"file": "<첨부 파일 이름>"}`. To clean only one sheet add `"sheet"`; if the user named the header
   position add `"header_row"` (1-based); to re-add total rows as formulas add `"add_totals": true`.
   Total formulas are recalculated with LibreOffice and saved with their values.
   Fully identical rows are kept and only marked (`중복 의심` in the summary and the 변경내역 sheet). Add `"dedupe": true`
   only when the user asks to remove them; rows with different IDs (전표번호·접수번호) are separate records even when the
   other values match.
2. From the result summary, check each sheet's header row, remaining row count, deleted rows (제목·합계·주석), rows marked
   중복 의심, and the columns converted to numbers or dates, and tell the user briefly. If a deleted row looks like data (e.g. a branch-name row dropped as a note),
   give its row number and ask whether to rerun with a different `header_row`.
3. Hand follow-up work on the cleaned file (pivots, merges, splitting columns, conditional marking, aggregates for charts) to `run_code`.
   In the task, say to use `outputs/<이름>_정리.xlsx` as input.

When copying numbers from the file into the conversation, use only values in the summary JSON or the output file. The cleaned file
is attached to the step for the user to download, so do not rewrite its contents at length.
