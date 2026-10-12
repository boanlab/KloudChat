---
name: 근거 대조
description: 다 쓴 문서·답의 수치·날짜·시각·이름·기관·직함·제품명이 요청·자료·사용자 답·코드 계산값에 있는지 대조하고, 근거 없는 값은 대괄호 빈칸으로 바꿉니다.
when_to_use: 주어진 자료로 쓴 문서나 답을 내기 전에, 자료에 없는 값을 지어 넣지 않았는지 확인할 때. 서버가 붙은 에이전트·서식에서는 쓰고 나서 자동으로 돌린다.
kinds: [chat, report]
required_tools: [run_skill_script]
version: 1.0.0
stage: post
entry: trace
---
Check the values in the finished text against the material. Do not judge in your head; find them with the script.

1. Call `run_skill_script` with skill `grounding-check`, script `trace`.
   args: `{"op": "repair", "text": "<다 쓴 글>", "material": "<요청·자료·사용자 답>",
   "computed": ["<코드가 계산한 값: run_code 출력, calculate 결과, 확정 수치 블록>"],
   "options": {"bare": false, "numbers": "all", "titles": false, "products": false}}`
   - For a data-analysis answer, `"bare": true, "numbers": "computed"` — numbers come only from execution results.
   - For career documents, `"titles": true, "products": true` — job titles and tool names are checked too.
   - `"today": "YYYY-MM-DD"` counts as grounds only when the request said 「오늘」.
   - `"requirements": true` — a sentence telling the reader to bring or hand in an item (「신분증 지참」 「노트북을 가져오세요」)
     the material never names is dropped. The server sets it on chat answers.
2. The result's `text` is the corrected text. Ungrounded values become 「[수치]」「[일자]」「[시기]」「[시각]」「[이름]」
   「[기관명]」「[직함]」「[제품명]」, and sentences saying work was done with a tool that the material lists only as a skill are dropped.
3. What counts as grounds: values written in the request, material or user answers; values computed by code; simple calculations among those values
   (sums, differences, ratios, rates of change — between values on the same line or in the same table, or between large totals); rounding.
   Table-of-contents and ordinal numbers (「2차」「3학년」「표 1」), character counts, a single year, and the document counting its own items (「3가지」) are not checked.
   When the material lists people (a roster: a 성명·이름 column, or names beside 학번·사번), a name in the text's tables
   or list items must be one of them — and beside a 학번 the roster gives, that person's; otherwise it becomes 「[이름]」.
   Names written in the request count as material.
4. If any value was turned into a blank, say in one line what was blanked, and ask the user for the value to fill it.

## 서버가 쓰는 인터페이스

`scripts/trace.py` uses only the standard library; the server imports the same file through `skill_scripts` and calls it.

- `check(text, material, computed=None, options=None) -> list[dict]` — each finding is
  `{"kind", "text", "line", "start", "end", "placeholder"}`. `line` is the line number when `text` is split on `\n`;
  `start` and `end` are positions within that line. `kind` is `number·date·month·time·person·org·title·product·claim·requirement`.
- `fix(text, findings) -> str` — turns findings into blanks (values) or drops the whole sentence (claim, requirement, a sentence holding only invented values).
- `summary(findings) -> str` — one line for the step record: 「근거 없는 값 3개 → 빈칸 (수치 2 · 시기 1): …」.
- CLI: `python trace.py '<JSON>'` — `op` is `check`, `fix` or `repair`; the last line is the result JSON.

For an attached agent (`checks` in `agents/*.toml`) or format (`check_skills` in `template.toml`), once writing
is done the server runs `check` → `fix` once, then `check` again, and writes what remains to the step.
A chat answer on the person's own material (attached or earlier files, project files, pasted material) gets the
same pass with no agent attached; a turn without material is left alone.
