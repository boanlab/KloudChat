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
쓰기 전에 빠진 사실만 한 번 묻는다. 자료가 이미 답한 것은 묻지 않는다.

1. `run_skill_script` 를 skill `gap-check`, script `gaps` 로 부른다.
   args: `{"op": "check", "facts": "<서식 또는 에이전트 키>", "request": "<요청>", "material": "<자료>"}`
   `facts`: `minutes`·`client_proposal`·`gaejo`·`comparison`·`career_history`·`career-docs`·`data-analysis`.
2. 결과 `findings` 가 비어 있으면 묻지 않고 쓴다. 있으면 질문(최대 4개)을 한 번에, 무엇이 없고 왜 필요한지와
   함께 묻는다. 같은 질문을 두 번 잇달아 묻지 않는다.
3. 사용자가 답하면 그 답을 자료로 삼아 쓴다. 「있는 자료로 진행」이면 결과의 `rule` 대로 그 자리를
   대괄호 빈칸(「[회의 일자]」「[부가세 기준]」)으로 두고 지어내지 않는다.

## 서버가 쓰는 인터페이스

- `check(request, material, computed=None, options=None) -> list[dict]` — `options["facts"]` 로 사실 목록을
  고른다. `computed` 는 서버 추출기가 이미 읽은 값(`meeting_date`, `clients`, `period`, `staff`, `priced`,
  `vat`, `periods`); 있으면 그 값을 믿고 없으면 글에서 찾는다. 찾은 것마다
  `{"kind": "gap", "fact", "label", "question", "why", "placeholder"}`.
- `rule(findings) -> str` — 「자료대로 진행」 때 작성자에게 줄 빈칸 규칙 한 줄.
- `FACTS` — 서식·에이전트별로 선언한 필수 사실(문서를 바로 쓰지 못하게 막는 것만).
- CLI: `python gaps.py '<JSON>'` — `op` 은 `check`·`rule`.
