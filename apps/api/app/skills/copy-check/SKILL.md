---
name: 카피 점검
description: 광고·홍보 문구를 채널 글자 수 한도(네이버·카카오·메타·구글·유튜브·인스타그램·링크드인)로 재고, 근거가 필요한 표현(최상급·비교·보장·질병 효능·후기·긴급성)과 자료에 없는 숫자를 찾아 줍니다.
when_to_use: 써 둔 광고 문구·SNS 글·랜딩 카피가 채널 한도 안인지, 표시광고법상 위험한 표현이나 자료에 없는 수치가 있는지 확인할 때. 카피라이팅·광고 소재·헤드라인·SNS·콜드메일 스킬이 마지막에 부른다.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
---
Check copy with code, not by eye. Character counts and claim scans written by the model are wrong too often.

## Scripts
1. Lengths — `run_skill_script` skill `copy-check`, script `length`:
   `{"items": [{"label": "1안 제목", "channel": "naver_powerlink", "field": "title", "text": "..."}]}`
   - `{"list": true}` (optionally `"channel": "meta"`) prints every channel/field with its limit, counting rule, whether it is a hard limit or a recommendation, and the official source.
   - A custom limit: `{"label": "...", "text": "...", "limit": 40, "unit": "chars"}`; units `chars`, `chars_dw` (한글·전각 2), `chars_no_spaces`, `bytes_utf8`.
   - Result per item: `count`, `limit`, `over`, `hard`, and for feeds the part visible before 「더 보기」 (`visible`).
2. Claims — script `claims`:
   `{"items": [{"label": "...", "text": "..."}], "material": "<the user's material, verbatim>", "category": "general"}`
   - `category`: `food`, `health_food`, `cosmetics`, `medical_device` make disease/efficacy wording a `block`.
   - `block`: must not ship as is — an ungrounded superlative/rank/comparison/guarantee/testimonial, a number absent from the material, or disease wording for food/cosmetics. `check`: a person decides. `info`: style tells and placeholders.
   - A phrase that appears in the material is `grounded`; keep its basis visible in the copy (조사기관·기간).
   - Without `material` no number check runs; always pass it when the user gave material.

## Procedure
1. Collect every piece of copy with a label. Pass the user's material verbatim.
2. Run `length` and `claims`. Fix: shorten anything `over` on a hard limit; for a recommended limit, say what gets cut off. Replace every `block` with a material fact or a placeholder (`[근거 자료]`, `[수치]`, `[고객 사례]`), or remove it.
3. Run once more after fixing. Report the final counts from the script, never your own.

## Output format
A table `| 문구 | 채널·필드 | 글자 수/한도 | 판정 |` from the `length` result, then a list of claim findings by severity with the suggested fix, then the corrected copy.

## Checklist
- [ ] Counts and limits copied from the script.
- [ ] 0 `block` findings left, or each one explained to the user with what evidence would allow it.

---
출처: coreyhaines31/marketingskills (MIT, © 2025 Corey Haines, commit b9ba399dd88b082b926e261e8ccfb843d20aa066)의 ad-creative·social 「Platform Specs」 검증 단계와 copywriting 「Honest over sensational」 원칙을 국내 채널 한도·표시광고법 점검 스크립트로 옮겼다. 한도 값의 출처는 `length` 의 `{"list": true}` 결과에 있다.
