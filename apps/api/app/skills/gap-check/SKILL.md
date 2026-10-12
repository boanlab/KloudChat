---
name: 빠진 정보 묻기
description: 문서를 쓰기 전에, 템플릿과 에이전트가 정해 둔 「빠진 정보 질문」(회의 일시·참석자, 보고 대상·보고자, 대안별 가격, 분석 기준 등) 중 자료가 답하지 않는 것을 찾아 한 번에 묻습니다.
when_to_use: 자료로 문서나 분석을 쓰기 전에, 자료가 답하지 않는 필수 사실이 있는지 볼 때. 「빠진 정보 먼저 묻기」를 켠 에이전트에서는 서버가 쓰기 전에 자동으로 돌린다.
kinds: [chat, report]
required_tools: [run_skill_script]
version: 1.0.0
stage: pre
entry: gaps
---
Before writing, ask once, only for missing facts. Do not ask about what the material already answers.

The questions are data: each 템플릿 lists the ones its format needs (`[[questions]]` in `template.toml`), and each
agent adds its own in the agent form (「빠진 정보 질문」). The server merges them and runs this check itself.

1. Call `run_skill_script` with skill `gap-check`, script `gaps`.
   args: `{"op": "check", "questions": [<질문>, ...], "request": "<요청>", "material": "<자료>"}`
   A question is `{"id", "label", "question", "why", "placeholder", "detector"}`; `detector` is one of
   `{"type": "builtin", "name": "<DETECTORS 이름>"}`, `{"type": "keyword", "words": [...], "when": [...]}`,
   `{"type": "regex", "pattern": "...", "when": "..."}` or `{"type": "judge"}` (decided by the server only).
2. If `findings` in the result is empty, write without asking. Otherwise ask the questions (at most 4) all at once, each with what is
   missing and why it is needed. Do not ask the same question twice in a row.
3. When the user answers, write using the answer as material. On 「있는 자료로 진행」, follow the result's `rule`: do not invent those
   facts; write sentences that hold without them, 「미정」 in a table cell with one 「확인 필요」 line, and brackets only for what the
   person fills in themselves (a head field such as 「[작성자]」).
4. Ask only what the writing cannot go on without. A fact the request already states in other words (「다음 주 목요일(10/15)」 is a
   date; a pasted abstract and table name the talk's subject) is answered, and a fact with a sensible default (기한 없는 할 일 →
   「미정」) is written with the default rather than asked.

## 서버가 쓰는 인터페이스

- `check(request, material, computed=None, options=None) -> list[dict]` — `options["questions"]` is the question list.
  `computed` holds the values the server extractors already read for the built-in detectors (`meeting_date`, `clients`,
  `period`, `staff`, `priced`, `vat`, `periods`) and the judge model's readings (`judged`: id → answered); if present they
  are trusted, otherwise they are looked for in the text. Each finding is
  `{"kind": "gap", "fact", "label", "question", "why", "placeholder"}`.
- `rule(findings) -> str` — the one-line blank rule given to the writer on 「자료대로 진행」.
- `DETECTORS` — the built-in detectors a question may name.
- CLI: `python gaps.py '<JSON>'` — `op` is `check` or `rule`.
