---
name: 카피 점검
description: 광고·홍보 문구를 채널 글자 수 한도(네이버·카카오·메타·구글·유튜브·인스타그램·링크드인)로 재고, 근거가 필요한 표현(최상급·비교·보장·질병 효능·후기·긴급성)과 자료에 없는 숫자를 찾아 줍니다.
when_to_use: 써 둔 광고 문구·SNS 글·랜딩 카피가 채널 한도 안인지, 표시광고법상 위험한 표현이나 자료에 없는 수치가 있는지 확인할 때. 광고·홍보 문구를 쓰는 스킬이 마지막에 부른다.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.2.0
status: draft
stage: post
entry: claims
copy_check: true
own_stage: true
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
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
2. Run `length` and `claims`. Fix: shorten anything `over` on a hard limit; for a recommended limit, say what gets cut off. Replace every `block` with a material fact or rewrite the line without the claim; a bracket only for a value the person must fill in themselves.
3. Run once more after fixing. Report the final counts from the script, never your own: a count cell holds the last run's value, and copy shortened to fit is measured again before you write that it fits.

## Output format
A table `| 문구 | 채널·필드 | 글자 수/한도 | 판정 |` from the `length` result, then a list of claim findings by severity with the suggested fix, then the corrected copy. Name a channel and a field by the result's `name` and `word` (「링크드인 도입 문구」), never by the `channel`/`field` keys (`linkedin`, `ad_intro`) — those are for calling the script.

## Checklist
- [ ] Counts and limits copied from the script.
- [ ] 0 `block` findings left, or each one explained to the user with what evidence would allow it.

## Server check (stage: post)
When one of the copy skills is on the turn, the server runs this check over the finished chat answer as the `check:copy` stage — the model is not left to call it.
- A judge reads the answer against the material once with `resources/judge.md` (fixed JSON reply: `unsupported` statements with exact quotes, and the pieces of copy per channel field the request names) and the reply is passed to `claims.check` in `computed`. A quote whose every word the material holds is dropped as a misreading.
- `claims.check(text, material, computed=None, options=None)` → findings `{"kind", "text", "line", "start", "end", "placeholder", "action", "why"}`; `action` is `blank` (number → 「[수치]」, offer → 「[혜택]」), `remove` (qualifier such as 「매일」「실시간」), `drop` (a quoted, attributed testimonial: the sentence goes) or `rewrite` (superlative, comparison, guarantee, urgency, disease wording, the judge's statements). Short quoted mentions, notes about the copy (「…은 쓰지 않았습니다」「…이 있으면 넣으세요」) and ordinals (「2주차」) are not findings. `options.kinds` narrows the kinds: a content plan keeps its own numbers, dates and ranks.
- `claims.fix(text, findings)` fixes by code: a value blanked, a word taken out, a sentence dropped, a one-sentence list item or headline row removed; `claims.summary(findings)` is the step line.
- `length.targets(request)` names the limits (channel fields up to 150 characters, or a stated 「N자 이내」); `length.table_items` reads the pieces off table columns headed by a field (제목·설명·기본 문구 …), the judge adds the rest; `length.measured` measures them and `length.restate_counts` puts the measured counts beside them.
- Order: code fixes → one rewrite by the writer with the `rewrite` findings and the pieces over a hard limit → scan again; what the scan finds and the judge's quotes the writer kept word for word are fixed by code. Both counts go in the 「카피 점검」 step.

---
출처: coreyhaines31/marketingskills (MIT, © 2025 Corey Haines, commit b9ba399dd88b082b926e261e8ccfb843d20aa066)의 ad-creative·social 「Platform Specs」 검증 단계와 copywriting 「Honest over sensational」 원칙을 국내 채널 한도·표시광고법 점검 스크립트로 옮겼다. 한도 값의 출처는 `length` 의 `{"list": true}` 결과에 있다.
