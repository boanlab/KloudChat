---
name: 엑셀 파일 만들기
description: 대화에 붙여 넣은 표, 첨부한 CSV·엑셀, 코드로 계산한 표를 내려받을 수 있는 엑셀(.xlsx) 파일로 만듭니다. 합계 행과 계산 열은 수식으로 넣고, 원하면 엑셀 차트도 붙입니다.
when_to_use: 표를 엑셀 파일로 받고 싶을 때, 합계·비율 같은 열을 수식으로 넣은 엑셀이 필요할 때, CSV를 서식 있는 엑셀로 바꿀 때.
kinds: [chat]
required_tools: [run_skill_script]
version: 1.0.0
---
The file is built by a fixed script, never by numbers you type into a file yourself. Answer in Korean (in the user's language if they write in another one).

1. Where the rows come from:
   - A table pasted in the conversation (up to about 300 rows): pass it as `sheets`, each cell exactly as the person wrote it (「1,200」 「12.5%」 「2026-10-13」 are typed by the script). Do not round, convert units or fill a missing cell — leave it empty.
   - An attached CSV or Excel file: pass `"file": "<첨부 파일 이름>"` (add `"sheet"` for one sheet). If the sheet is messy (title rows, totals, notes), run 「엑셀 정리」 (`xlsx-clean`) first and build from `outputs/<이름>_정리.xlsx`.
   - A computed table (aggregates, pivots): have `run_code` save the result table as `outputs/<이름>.csv` and print it, then build from that file with `"file"`. Never copy computed numbers into `rows` by hand.
2. Call `run_skill_script` with skill `xlsx-build`, script `build`. args:
   `{"output": "<파일 이름>.xlsx", "sheets": [{"name": "<시트>", "title": "<표 제목, 선택>", "columns": [...], "rows": [[...], ...], "formulas": {...}, "formats": {...}, "total_row": true, "chart": {...}}]}`
   - `formulas`: a computed column as an Excel formula over the same row, columns named in braces: `{"합계": "=SUM({1월}:{3월})", "증감률": "=({9월}-{8월})/{8월}"}`. `{r}` is the row number. Use a formula wherever the value follows from other cells (합계, 차이, 비율, 단가×수량) so the file stays live when someone edits it.
   - `total_row`: `true` adds a 합계 row with `=SUM(...)` under every amount column (codes, years, rates are skipped), or a list of the columns to sum. A formula column repeats its formula on that row, so a rate there is total ÷ total, never a sum of rates.
   - `formats`: number formats by column, e.g. `{"증감률": "0.0%", "금액": "#,##0"}`. A ratio formula needs `"0.0%"` or it shows as a decimal.
   - `chart` (only when asked): `{"type": "bar" | "line" | "stacked", "x": "<항목 열>", "y": ["<값 열>", ...], "title": "..."}` — an Excel chart beside the table.
   - With `"file"`, give `formulas`, `formats`, `total_row` and `chart` at the top level (or per sheet by name in `sheets`).
3. From the summary, tell the person in a few lines: file name, sheets with row counts, which columns are formulas, the 합계 row. Totals and formula values quoted in the answer come only from `totals` and `formula_values` in the summary. If `recalculated` is false, say the values appear when the file is opened in Excel and do not state them.
4. If the script fails (a formula names a column that does not exist, no header), fix the args and run once more; do not write the numbers into the answer instead.

The file is attached to the step as a download button: do not paste its contents back as a long table, write no link or file path, and do not say it was emailed or saved anywhere else. A chart the person wants to see in the conversation is drawn with `create_chart` (「차트 만들기」), from the same values.
