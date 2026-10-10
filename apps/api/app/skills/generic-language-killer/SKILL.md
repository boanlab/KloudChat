---
name: 상투어 걷어내기
description: 글에서 「~하는 것이 중요합니다」「다양한」「효과적으로」「혁신적인」「~라고 할 수 있습니다」 같은 상투어·군더더기·번역투·AI 투를 찾아, 자료에 있는 구체적인 말로 바꾸거나 지웁니다. 자료에 없는 사실은 지어내지 않고 [구체적 수치] 같은 빈칸으로 남깁니다.
when_to_use: 써 둔 글이 뻔하고 밋밋하다, 「AI가 쓴 것 같다」, 「군더더기 빼 줘」「구체적으로 바꿔 줘」「번역투 고쳐 줘」라고 할 때. 소개글·보고서·제안서·보도자료·자기소개서·광고 문구 모두. 맞춤법 교정은 「교정·윤문」.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
stage: post
entry: scan
post_check: scan
post_judge: "a fact, reason, condition, result or experience the original text does not have, and a plan or judgment the original hedges written as settled"
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Strip generic, vague and machine-sounding wording from the user's text and put concrete wording in its place — concrete only from the user's text and material. Clear beats clever; a fact beats an adjective.

## Hard rules
- A concrete replacement must come from the user's text or material. Quote the material line you used. If the material has no fact for it, write a bracket placeholder (「[구체적 수치]」「[구체적 항목]」「[방법]」「[차별점]」「[근거]」) or delete the phrase — never invent a number, name, customer, result, time or comparison.
- Keep every number, date, name and claim of the original. Removing an unsupported superlative (「최고의」) is allowed; replacing it with a different claim is not.
- Do not touch quotations (「」·“”), legal or official names, or wording the material itself uses as a fixed term (`in_material: true` — e.g. a program called 「역량 강화 사업」).
- Keep the text's register (합니다체·해요체) and length roughly the same or shorter.
- Write in Korean (English text stays English).

## Script
`run_skill_script` skill `generic-language-killer`, script `scan`:
`{"text": "<글>", "material": "<사용자 자료 원문>", "apply": true}` (or `"items": [{"label": "...", "text": "..."}]`; `"categories"` to narrow)
- Categories: `filler` 채움말, `hedge` 얼버무림, `intensifier` 빈 강조어, `buzzword` 유행어, `ai` AI 투, `translationese` 번역투, `english`.
- Each finding: `match`, `line`/`start`/`end`, `action`, `suggestion`, `placeholder`, `auto`, `quoted`, `in_material`.
- `apply: true` returns `fixed` with only the `auto` fixes (safe rewrites such as 「~함에 있어서」→「~할 때」, 「~라고 할 수 있습니다」→「~입니다」, 「되어지는」→「되는」, 강조 부사 삭제). Start from `fixed`.
- `density` = weighted findings per 100 어절, `grade` 낮음/보통/높음; `after` is the same for `fixed`.

## Procedure
1. Run `scan` with the material. Start from `fixed`.
2. Work through the rest by `action`:
   - `delete`: remove; reread the sentence.
   - `replace`: use `replace` or the suggestion.
   - `concretize`: find the fact in the material (what, how much, for whom, how). 예: 「다양한 기능」→「회의 녹음·요약·할 일 정리」 (자료에 그 세 기능이 있을 때). Not in the material → 「[구체적 항목]」 or delete the phrase if the sentence stands without it.
   - `rewrite`: undo the frame (「단순한 A가 아닙니다. B입니다」→「B입니다」; 「~하는 것이 중요합니다」→「~해야 합니다. 이유: …」 with a reason from the material, else 「[이유]」).
   - `check`: judge in context; leave it when it is literal (「집에 있어서 못 갔다」) or necessary.
3. Run `scan` again on your result. Report `density` before → after from the script.
4. If numbers or names were near your edits, run `run_skill_script` skill `copy-editing`, script `preserve` with `{"original": "<원문>", "edited": "<고친 글>"}`; `lost` and `added` must be empty.

## Output format
```
### 고친 글
(full text)

### 바꾼 표현
| 원래 표현 | 분류 | 바꾼 말 | 근거 |
|-----------|------|---------|------|
| 다양한 기능 | 채움말 | 회의 녹음·요약·할 일 정리 | 자료 「기능: 녹음, 요약, 할 일」 |
| 획기적으로 줄였습니다 | 빈 강조어 | [구체적 수치]만큼 줄였습니다 | 자료에 수치 없음 |

### 채울 빈칸
- [구체적 수치] — 무엇을 넣어야 하는지 한 줄

### 밀도
상투어 밀도 12.4(높음) → 2.1(낮음), N개 중 M개 고침
```

## Server check (stage: post)
When a writer attaches this skill as a check (`checks = ["generic-language-killer"]` in an agent, `check_skills` in a 서식), the server runs it over the finished text — the model is not left to call it.
- `scan.check(text, material, computed=None, options=None)` returns only `auto` findings outside quotes and not in the material, with `placeholder` = the replacement; `scan.fix(text, findings)` applies them; `scan.summary(findings)` is the step line. Findings that need a fact or judgment are never fixed by the server.

---
출처: realkimbarrett/advertising-skills의 skills/qa/generic-language-killer (commit 45f4a4a1dabe24113193369b55b929b1de4ff04a; README와 SKILL.md 앞머리에 MIT로 표기되어 있으나 저장소에 LICENSE 파일은 없음). 원문은 「Generic phrases·Fluff·Buzzwords를 찾아 Specifics·Direct statements로 바꾼다」는 12줄 지침뿐이라, 이 스킬의 지침·한국어 표현 목록·점검 스크립트는 새로 썼다. 번역투·이중 피동 항목은 국립국어원의 우리말 다듬기 안내(「~에 있어서」「~되어지다」 등)를 참고했다. 「Replace with the fact, not a synonym」과 [NEED: …] 빈칸 원칙은 coreyhaines31/marketingskills (MIT, commit b9ba399dd88b082b926e261e8ccfb843d20aa066) copy-editing 「AI-Tell Check」에서 가져왔다.
