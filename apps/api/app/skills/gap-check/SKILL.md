---
name: 빠진 정보 묻기
description: 문서를 쓰기 전에, 그 서식에 꼭 필요한 사실(회의 일시·참석자, 고객사·사업 기간·단가·부가세, 보고 대상·보고자, 지원 회사·문항·글자 수, 분석 기준) 중 자료에 없는 것을 찾아 한 번에 묻습니다.
when_to_use: 회의록·제안서·개조식 보고·비교 보고서·경력 문서·자기소개서·데이터 분석을 자료로 쓰기 전에, 자료가 답하지 않는 필수 사실이 있는지 볼 때. 서버가 붙은 에이전트·서식에서는 쓰기 전에 자동으로 돌린다.
kinds: [chat, report]
required_tools: [run_skill_script]
version: 1.0.0
stage: pre
entry: gaps
---
Before writing, ask once, only for missing facts. Do not ask about what the material already answers.

1. Call `run_skill_script` with skill `gap-check`, script `gaps`.
   args: `{"op": "check", "facts": "<서식 또는 에이전트 키>", "request": "<요청>", "material": "<자료>"}`
   `facts`: `minutes`·`client_proposal`·`gaejo`·`comparison`·`career_history`·`career-docs`·`data-analysis`.
2. If `findings` in the result is empty, write without asking. Otherwise ask the questions (at most 4) all at once, each with what is
   missing and why it is needed. Do not ask the same question twice in a row.
3. When the user answers, write using the answer as material. On 「있는 자료로 진행」, follow the result's `rule`: leave those slots as
   bracketed blanks (「[회의 일자]」「[부가세 기준]」) and do not invent them.

## 서버가 쓰는 인터페이스

- `check(request, material, computed=None, options=None) -> list[dict]` — `options["facts"]` selects the fact list.
  `computed` holds the values the server extractors already read (`meeting_date`, `clients`, `period`, `staff`, `priced`,
  `vat`, `periods`); if present they are trusted, otherwise they are looked for in the text. Each finding is
  `{"kind": "gap", "fact", "label", "question", "why", "placeholder"}`.
- `rule(findings) -> str` — the one-line blank rule given to the writer on 「자료대로 진행」.
- `FACTS` — the required facts declared per format and agent (only those that block writing the document right away).
- CLI: `python gaps.py '<JSON>'` — `op` is `check` or `rule`.
