---
name: 출처 점검
description: 기업 조사·경쟁사 분석·시장 규모·산업 분석 글에서 숫자마다 출처·날짜가 있는지(없으면 가정·출처 미확인 표시), 오래된 출처, 근거 없는 일반론, 비교표의 출처 없는 칸·근거 없는 우위 판정을 코드로 찾고, PESTEL·5 Forces·세분화 틀을 검사해 표로 그립니다.
when_to_use: 시장·경쟁 조사 결과를 내기 전 마지막 확인. 「기업 조사」「경쟁사 분석」 스킬이 부른다. 직접 쓴 조사 글의 출처가 빠짐없는지 볼 때도.
kinds: [chat, report]
required_tools: [run_skill_script]
version: 0.2.0
status: draft
post_check: post
post_judge: "claims about the company, competitors, market or customers, what an interview or survey found, what a cited source [n] says, and capabilities the material does not give"
tables: true
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Check sourcing with code, not by eye. A model reviewing its own research misses unsourced numbers.
- A fact with a link you opened is sourced: never mark it 「출처 미확인」. The mark is only for what you could not open or find.
- What the text calls 확인 필요 in one place is not stated as settled anywhere else in it.

출처: 새로 씀(KloudChat). 「근거 없는 일반론」 목록은 이 저장소의 lint `_FILLER`·copy-check 의 방식을 시장 조사 문장에 맞춰 옮겼다.

[채팅에서]
## Scripts (`run_skill_script`, skill `source-check`)
1. `sources` — every line with a business number (금액·%·명·개사·곳·위·배…) must carry a source or a mark.
   `{"text": "<finished answer>", "material": "<user's material, verbatim>", "today": "YYYY-MM-DD", "max_age_years": 3, "fix": true}`
   - Counts as a source: `[n]` defined in the 출처 list, a URL, 「출처:」, the table's 출처/근거 column, a number found in `material`.
   - Counts as a mark: 「가정: 근거」, 「출처 미확인」, 「확인 필요」, 「자료 없음」, a placeholder such as 「[시장 규모]」.
   - Kinds: `unsourced`, `undefined_ref` (block); `stale`, `ref_no_date`, `ref_no_link`, `generic` (fix or explain).
   - `fix: true` returns `fixed_text` with 「출처 미확인」 added to each unsourced line.
2. `claims` — comparison tables and battlecards: every claim cell needs a source or 「확인 필요」/「자료 없음」.
   `{"text": "...", "material": "...", "label_columns": 1, "fix": true}`
   - Kinds: `unsourced_cell`, `empty_cell`, `vague_cell` (「우수」「강력」 alone), `verdict_without_evidence` (a 우위 column decided while a cell in that row is open → 「판단 보류」), `unsourced_bullet` (bullets under 강점·약점·이기는·지는·가격·기능·반론 headings).
   - Own-company cells need a source too: 「(자사 자료)」 or `[n]` pointing at the user's document.
3. `frame` — frameworks as structured JSON, checked and rendered.
   - `{"template": "pestel" | "five_forces" | "segments"}` returns the empty frame and the rules.
   - `{"framework": "pestel", "factors": {"정치": [item], ...}}`, `{"framework": "five_forces", "forces": {...}}`, `{"framework": "segments", "segments": [...]}`.
   - item = `{"claim", "evidence", "source", "url", "date"}` (+ `"impact": "기회|위협|중립"` for PESTEL), or `{"assumption": true, "basis": "..."}`, or `{"status": "자료 없음", "to_find": "어디서 무엇을"}`.
   - Blocks: an empty factor, a claim without evidence or source, a generic statement (「경쟁이 치열하다」「성장 가능성이 크다」) without concrete evidence, a 5 Forces rating other than 「판단 보류」 without a sourced item.
   - Copy the returned `markdown` table into the answer as it is.

## Procedure
1. Run the script that fits the output (usually `sources`; add `claims` for tables, `frame` for frameworks).
2. Fix every blocking finding: add the source you actually have, label it 가정 with its basis, or replace the value with 「출처 미확인」 (a sentence that holds without it, never a bare bracket blank). Never fix a finding by inventing a source.
3. Run once more. Report the script's final summary line, not your own count.

[보고서에서]
The sources the server read are numbered for you. Write so every claim can be traced:
1. Every line with a business number (금액·%·명·개사·곳·위·배…) carries its source [n] or a mark: 「가정: 근거」, 「출처 미확인」, 「확인 필요」, or a placeholder such as 「[시장 규모]」.
2. A comparison-table cell that claims something carries [n], 「(자사 자료)」 or 「확인 필요」/「자료 없음」; 「우수」「강력」 alone is not a claim. A 우위 column stays 「판단 보류」 while a cell in its row is open.
3. Prefer sources from the last three years and give their dates; say so when only an older one exists.
4. No generic statement (「경쟁이 치열하다」「성장 가능성이 크다」) without the concrete evidence behind it. Never invent a source to fill a gap.
