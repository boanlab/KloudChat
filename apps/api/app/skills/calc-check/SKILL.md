---
name: 계산·단위 검증
description: 글에 적힌 계산식·증감률·비중·단위 변환을 스크립트로 다시 계산해 적힌 값과 대조하고, 어긋난 곳을 알려 줍니다.
when_to_use: 답이나 문서의 수치가 의사결정 근거가 되거나 단위 변환·증감률·비중이 여러 개 들어 있을 때.
kinds: [chat, report]
required_tools: [run_skill_script]
version: 2.0.0
---
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
4. Do not mix percent and percentage points, mean and median, nominal and real values. In the answer, leave the grounds briefly in the
   order input values → formula → result → rounding rule.

If the values are inside an attached Excel or CSV file, first clean the table with `xlsx-clean` or aggregate with `run_code`, then check
those results. If a tool could not run, do not write that you verified it; state that it is unverified.
