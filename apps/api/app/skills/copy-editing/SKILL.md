---
name: 교정·윤문
description: 이미 쓴 한국어 글의 맞춤법·띄어쓰기를 한글 맞춤법·표준어 규정에 따라 바로잡고, 뜻과 사실은 그대로 둔 채 문장을 다듬고 어투(합니다체·해요체·개조식)를 맞춥니다. 바꾼 곳마다 원문→수정과 이유를 남기고, 뜻이 바뀔 수 있는 수정은 따로 표시합니다.
when_to_use: 써 둔 보고서·공문·안내문·메일·소개글·자기소개서를 교정하거나 다듬어 달라고 할 때. 「맞춤법 봐 줘」「문장 다듬어 줘」「어투 통일해 줘」「교정해 줘」. 상투어·군더더기를 걷어내는 일은 「상투어 걷어내기」, 새로 쓰는 일은 다른 스킬.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
post_check: preserve
post_judge: "a sentence, reason, condition or promise the original text does not have, and an estimate or plan the original hedges written as settled"
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Proofread and line-edit the user's Korean text. The user's text is the only source: you fix how it is written, never what it says.

## Hard rules
- Never add a claim, number, date, name, example, reason or benefit that is not in the user's text. Never upgrade a claim (「개선」 does not become 「크게 개선」; 「여러 고객」 does not become 「1,000곳」).
- Keep every number, date, time, amount, unit, name, title, product name and quotation exactly as written, including how it is written (「1,200만 원」 stays 「1,200만 원」, 「10. 8.」 stays 「10. 8.」), unless the user asked for that change. The `preserve` script is the judge, not your reading.
- Keep negation, obligation and scope words (않다·없다·반드시·해야·가능·모든·일부·이상·이하·까지·및/또는). If a sentence is unclear without changing one of them, leave it and ask.
- Where the text needs a fact it does not give (a vague 「추후」, a missing amount), do not supply one: leave the sentence as it is and list it under 「확인이 필요한 곳」 with a bracket placeholder (「[날짜]」「[금액]」「[담당자]」).
- Text inside 「」·“” quotations, legal names, and fixed phrases the user names stay untouched.
- Write the edited text and every explanation in Korean.

## Inputs
Read from the request; ask only if the text itself is missing.
1. The text to edit (pasted or attached).
2. Scope — default 「교정 + 가벼운 윤문」: spelling, spacing, standard words, register, awkward or overlong sentences. 「교정만」 means only spelling/spacing/standard words. 「강한 윤문」 still keeps every fact and claim.
3. Register — the one the user names (합니다체·해요체·한다체·개조식); otherwise the text's dominant one from `proof`. Bullet lists may stay 개조식 under 합니다체 prose.
4. Words that must not change (`protect`) and changes the user asked for (`allow`).

## Scripts
1. `run_skill_script` skill `copy-editing`, script `proof` — 맞춤법·띄어쓰기·표준어 and register:
   `{"text": "<원문>", "apply": true}` (optionally `"register": "hamnida" | "haeyo" | "handa"`)
   - Each finding has `before`→`after`, `basis` (조항) and `sure`. Apply `sure: true` findings; decide each `sure: false` one from context (「바램」 can be correct, 「만큼」 after a noun is a particle).
   - `register.off` lists sentences whose ending differs from the body's register (`main`) or from the bullets' register.
   - The list covers frequent errors only. Read the whole text yourself as well; for anything you fix beyond the script, name the rule (한글 맞춤법 제○항, 표준어 규정 제○항, or 표준국어대사전) only when you are sure of it — otherwise say 「표준국어대사전 확인 권장」.
2. Script `preserve` — after editing, original vs edited:
   `{"original": "<원문>", "edited": "<수정본>", "protect": [...], "allow": [...]}`
   - `lost`, `added` must both be empty (`pass: true`). Restore every lost value as written; remove every added one.
   - `format_changed`: restore the original writing unless the user asked for a format.
   - `changes` is the complete list of what you changed — build the change log from it, do not reconstruct it from memory.
   - `risky` (부정·의무·범위·시제·연결·값·내용 삭제/추가) goes to 「의미가 바뀔 수 있는 수정」 unless you revert it.
3. Run `proof` once more on the edited text; report its remaining count.

## Editing passes
Adapted from the Seven Sweeps of the original skill; the passes that add content (So What, Prove It, Specificity, Emotion, Zero Risk) become notes, never edits.
1. 맞춤법·띄어쓰기·표준어 (from `proof`, plus your own reading).
2. 명료성: one idea per sentence; split sentences over about 60 characters joined by 「~고, ~며, ~는데」; fix subject–predicate agreement (주술 호응) and dangling modifiers; move the main point forward.
3. 어투 일관: one register in prose; consistent honorifics (「~하시기 바랍니다」 vs 「~해 주세요」); consistent terms for the same thing (「고객/사용자」, 「신청/접수」 — pick the one the text uses most).
4. 군더더기: cut repeated words, doubled meanings (「미리 예약」→「예약」, 「다시 재검토」→「재검토」), translationese (「~에 있어서」→「~에서」, 「~되어지다」→「~되다」). For a full cliché sweep use the 「상투어 걷어내기」 skill.
5. 다듬은 뒤 확인: run `preserve`; revert or flag what it reports.
6. 메모: claims that need evidence, vague promises, missing facts → 「확인이 필요한 곳」, as questions or placeholders. Do not fix them by writing new facts.

## Output format
```
### 수정본
(the full edited text, ready to paste)

### 바꾼 곳
| # | 원문 | 수정 | 이유 |
|---|------|------|------|
| 1 | 할수 있다 | 할 수 있다 | 띄어쓰기 — 한글 맞춤법 제42항(의존 명사) |
(one row per `changes` item; group pure spacing fixes of the same rule into one row if there are many)

### 의미가 바뀔 수 있는 수정
- 「원문」→「수정」 — 무엇이 달라질 수 있는지(부정·의무·범위·시제 …). 원래 뜻이 맞는지 확인해 주세요.
(「없음」 if `risky` is empty after your reverts)

### 확인이 필요한 곳
- 「추후 안내」 — 날짜가 없습니다: [날짜]
(questions only; nothing invented)

### 점검 결과
맞춤법·띄어쓰기 지적 N개 반영(남은 것 M개) · 어투 ○○체로 통일 · 지킬 값 K개 모두 유지
```
Keep explanations short. Do not print counts you did not get from a script.

## Checklist
- [ ] `preserve` → `pass: true`, `format_changed` empty or explained.
- [ ] Every `changes` row appears in 「바꾼 곳」 with a reason; every `risky` row is reverted or listed.
- [ ] No new fact, number, name or claim; placeholders where a fact is missing.
- [ ] One register in prose; terms consistent.

---
출처: coreyhaines31/marketingskills (MIT, © 2025 Corey Haines, commit b9ba399dd88b082b926e261e8ccfb843d20aa066)의 skills/copy-editing — 「Seven Sweeps」 단계 편집, 「Each edit should have a clear reason」, 「Don't change the core message」 원칙을 한국어 교정으로 옮겼다. 내용을 보태는 단계(So What·Prove It·Specificity·Heightened Emotion·Zero Risk)는 수정이 아닌 메모로 바꿨고, 맞춤법·띄어쓰기 점검(`proof`)과 값 보존·변경 목록 점검(`preserve`)은 새로 썼다. 규정 근거: 국립국어원 「한글 맞춤법」(문화체육관광부 고시 제2017-12호), 「표준어 규정」(제2017-13호), 표준국어대사전.
