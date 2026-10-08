---
name: 헤드라인 매트릭스
description: 한 메시지로 헤드라인을 각도(호기심·구체성·증거·긴급성·역발상·작동 원리·정체성)별로 여러 개 뽑고, 글자 수·중복·같은 꼴 반복을 스크립트로 잽니다. 자료에 없는 사실이 필요한 칸은 비웁니다.
when_to_use: 광고 제목·메일 제목·랜딩 헤드라인·SNS 첫 줄 후보를 한꺼번에 여러 개 받고 싶을 때. 「헤드라인 20개」「제목 후보 뽑아 줘」「A/B 테스트용 문구」.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
---
Produce many headline candidates that differ by angle (why someone would click), not by synonyms. Every fact in a headline comes from the request/material.

## Inputs
- Needed: what is offered and to whom; where the headline goes (channel/field) or a character limit; the facts you may use.
- If the product/offer is unclear, ask once (at most 2 questions) and stop. If only the channel/limit is missing, assume 「공백 포함 30자」 and say so.
- Count: default 25 (as asked otherwise).

## Angles
| 각도 | 하는 일 | 사실 필요 |
|---|---|---|
| 호기심 | 답을 미루되 랜딩이 답을 준다(낚시 금지) | |
| 구체성 | 자료의 숫자·대상·기간 그대로 | ✓ |
| 증거 | 자료의 성과·고객 수·수상·인증만 | ✓ |
| 긴급성 | 자료의 실제 마감·한정만 | ✓ |
| 역발상 | 통념 뒤집기, 자료로 뒷받침 | |
| 작동 원리 | 어떻게 되는지 | |
| 정체성 | 누구를 위한 것인지 | |
Every angle, not only the fact angles, must stay true to the material: no stretching a fact into a bigger promise (「앱에서 영수증 사진으로 청구」 ≠ 「서류 없이 청구」「청구 서류 필요 없어요」; 「의료비 보장」 ≠ 「병원비 걱정 끝」), no implied results the material does not report (「출고 오류가 줄어든 제조사의 공통점」 needs a reported result), no invented offers or deadlines.
If an angle needs a fact the material does not have, do not invent one: either skip the angle and say why, or write the headline with a placeholder (「[고객 수]곳이 쓰는 회의록 도구」) and mark it.

## Procedure
1. List the facts from the material (short). Decide limit and unit for the channel. Common limits (공백 포함): 네이버 파워링크 제목 15자, Google 반응형 검색광고 제목 30(한글은 2로 셈 → 15자), 메타 광고 제목 권장 40자, 메일 제목 권장 30자. For other channels use `copy-check` script `length` with `{"list": true}`.
2. Get the grid: `run_skill_script` skill `headline-matrix`, script `matrix`, args `{"count": 25, "facts": [...], "limit": 15, "unit": "chars"}` (`chars_dw` for Google Ads). It returns how many per angle and which angles need facts.
3. Write the headlines into the grid. Vary the shape inside each angle: question, statement, imperative, number-first, audience-first. Natural Korean; no em dash, no 「!!」, no 혁신적·획기적, no 「단순한 ~가 아닙니다」.
4. Check: call `matrix` again with `"headlines": [{"angle": "호기심", "text": "..."}, ...]`. Fix every item in `over`, `duplicates`, `near_duplicates`, `repeated_shapes`, `short_angles`, then check once more.
5. Run `copy-check` script `claims` on all headlines with the material — again after every rewrite, since fixes introduce new numbers (「출고 전 3초」) and mechanisms (「스캔 한 번에 끝」). Remove or placeholder every `block` finding.
6. Mark 3 recommended picks to test first, from different angles, with one-line reasons.

## Output format
Use the `table_markdown` from the last `matrix` run (각도 | # | 헤드라인 | 글자 수), then:
```
### 먼저 시험할 3개
1. #n … — 이유
### 비운 칸·빈칸
- 증거: 자료에 고객 수·사례가 없어 [고객 수]로 둠
```
Never write character counts yourself; copy them from the script.

## Checklist
- [ ] Every headline within the limit (script-measured).
- [ ] No duplicate or near-duplicate; no shape repeated across >40%.
- [ ] Each angle filled or explicitly skipped for lack of facts.
- [ ] No number, name, rank, deadline or claim absent from the material.

---
출처: realkimbarrett/advertising-skills `skills/copy-chief/headline-matrix` (README·front matter에 MIT 표기, LICENSE 파일 없음), commit 45f4a4a1dabe24113193369b55b929b1de4ff04a, https://github.com/realkimbarrett/advertising-skills. 7개 각도와 25개 기본값을 따르고, 칸 나누기·길이·중복 검사와 사실 근거 규칙을 더했다.
