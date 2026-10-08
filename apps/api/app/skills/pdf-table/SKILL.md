---
name: PDF 표 → 엑셀
description: PDF에 들어 있는 표를 쪽을 넘어 이어 붙이고 반복 머리글을 지워 엑셀 시트로 옮깁니다.
when_to_use: 첨부한 PDF의 표(거래내역, 단가표, 명세서 등)를 엑셀로 옮기거나 표의 숫자로 계산해야 할 때.
kinds: [chat, report]
required_tools: [run_skill_script]
version: 1.0.0
---
For PDF tables a fixed script is more accurate than a parser the model writes. Always use this script first when extracting a table.

1. Call `run_skill_script` with skill `pdf-table`, script `extract`.
   args: `{"file": "<PDF 이름>"}`. For specific pages only, add `"pages": "2-4"`.
2. For each table in the summary, look at the page range, row count, column names and first row, and tell the user briefly. If a table
   was split into several or the column names come out like `열1`, the original table has an unusual shape; say so.
3. To do more calculation, aggregation or format conversion on the extracted table, hand it to `run_code`, saying to use
   `outputs/<이름>_표.xlsx` as input. Non-table PDF work such as extracting, merging or rotating pages is also done with `run_code`.

Scanned PDFs (text as images) cannot be read. If the script says it could not read the text, report that as it is and do not
invent numbers.
