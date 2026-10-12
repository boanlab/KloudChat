---
name: 계산·단위 검증
description: 글에 적힌 계산식·증감률·비중·단위 변환을 스크립트로 다시 계산해 적힌 값과 대조하고, 어긋난 곳을 알려 줍니다.
when_to_use: 답이나 문서의 수치가 의사결정 근거가 되거나 단위 변환·증감률·비중이 여러 개 들어 있을 때.
kinds: [chat, report, slides]
required_tools: [run_skill_script]
version: 2.1.0
process: {code_calc: true}
---
Do not compute derived values in your head: a value comes from code (the script in chat, the server's block in a report or deck).

[채팅에서]
Before explaining figures, recalculate the values as written. A single arithmetic operation is enough with `calculate`; use this
script when checking several values at once.

1. For each input value, note its source, unit and reference date. Distinguish given values from assumed ones.
2. Call `run_skill_script` with skill `calc-check`, script `check`. args is `{"items": [...]}`, and
   each item is one of these four.
   - Expression: `{"label": "부가세", "expr": "1250000 * 0.1", "expected": 125000}`
   - Rate of change: `{"label": "매출 증가율", "kind": "change", "old": 120, "new": 150, "expected": 25}`
     (percentage points: `"as": "pp"`)
   - Share: `{"label": "비중", "kind": "share", "part": 30, "whole": 120, "expected": 25}`
   - Unit: `{"label": "예산", "kind": "convert", "value": 1.2, "from": "억원", "to": "천원", "expected": 120000}`
   If the rounding place is set, add `"decimals"`; if there is a tolerance, add `"tolerance"`.
3. Correct mismatched or erroneous items from the result in the answer, together with the formula. For an item whose denominator
   is 0 or whose units do not match, stop calculating and say what is missing.
4. A total built from several amounts is written as an explicit sum line (「합계: 119,600 + 150,000 + 80,000 = 349,600원」) whose terms are exactly the table's amounts, and that line is one expression item; a total stated again elsewhere (the first sentence, a summary) must be the same value. Count 「N박 M일」 as M days and N nights.
5. Do not mix percent and percentage points, mean and median, nominal and real values. In the answer, leave the grounds briefly in the
   order input values → formula → result → rounding rule.

If the values are inside an attached Excel or CSV file, first clean the table with `xlsx-clean` or aggregate with `run_code`, then check
those results. If a tool could not run, do not write that you verified it; state that it is unverified.

[보고서·슬라이드에서]
Before you write, the server runs the calculation on the material by code and hands you the 「코드로 계산한 값」 block: derived values
(sums, ratios, rates of change between a table's rows) and the results of a procedure the request asks for (a CPU scheduling run's
Gantt chart, each process's waiting time and the average). Copy every value from that block, and write the same value wherever the
same item appears — body, comparison table and speaker notes alike. Never redo the arithmetic or the procedure yourself. A value the
block does not give and the material does not state is left out; say in one line what it would need. After writing, the server sets
any number naming a computed value back to it.
