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
다 쓴 글의 값을 자료와 대조한다. 머리로 판단하지 말고 스크립트로 찾는다.

1. `run_skill_script` 를 skill `grounding-check`, script `trace` 로 부른다.
   args: `{"op": "repair", "text": "<다 쓴 글>", "material": "<요청·자료·사용자 답>",
   "computed": ["<코드가 계산한 값: run_code 출력, calculate 결과, 확정 수치 블록>"],
   "options": {"bare": false, "numbers": "all", "titles": false, "products": false}}`
   - 데이터 분석 답은 `"bare": true, "numbers": "computed"` — 숫자는 실행 결과에서만 온다.
   - 경력 문서는 `"titles": true, "products": true` — 직함·도구 이름도 대조한다.
   - `"today": "YYYY-MM-DD"` 는 요청이 「오늘」이라고 했을 때만 근거가 된다.
2. 결과 `text` 가 고친 글이다. 근거 없는 값은 「[수치]」「[일자]」「[시기]」「[시각]」「[이름]」
   「[기관명]」「[직함]」「[제품명]」으로 바뀌고, 자료에 기술 목록으로만 있는 도구로 일했다는 문장은 빠진다.
3. 근거로 치는 것: 요청·자료·사용자 답에 적힌 값, 코드가 계산한 값, 그 값들끼리의 단순 계산
   (합·차·비율·증감률, 같은 줄이나 같은 표에 함께 적힌 값끼리, 큰 합계끼리), 반올림.
   목차·순서 번호(「2차」「3학년」「표 1」), 글자 수, 연도 하나, 문서가 제 항목을 센 것(「3가지」)은 대조하지 않는다.
4. 빈칸으로 바꾼 값이 있으면 무엇을 비웠는지 한 줄로 알리고, 그 값을 사용자에게 물어 채운다.

## 서버가 쓰는 인터페이스

`scripts/trace.py` 는 표준 라이브러리만 쓰고, 서버는 같은 파일을 `skill_scripts` 로 들여와 부른다.

- `check(text, material, computed=None, options=None) -> list[dict]` — 찾은 것마다
  `{"kind", "text", "line", "start", "end", "placeholder"}`. `line` 은 `text` 를 `\n` 으로 나눈 줄 번호,
  `start`·`end` 는 그 줄 안의 위치. `kind` 는 `number·date·month·time·person·org·title·product·claim`.
- `fix(text, findings) -> str` — 찾은 것을 빈칸으로 바꾸거나(값), 문장째 뺀다(claim, 지어낸 값만 담긴 문장).
- `summary(findings) -> str` — 단계 기록용 한 줄: 「근거 없는 값 3개 → 빈칸 (수치 2 · 시기 1): …」.
- CLI: `python trace.py '<JSON>'` — `op` 은 `check`·`fix`·`repair`, 마지막 줄이 결과 JSON.

서버는 붙은 에이전트(`agents/*.toml` 의 `checks`)나 서식(`template.toml` 의 `check_skills`)에서 쓰기가
끝난 뒤 `check` → `fix` 를 한 번 하고 `check` 로 다시 찾아, 남은 것과 함께 단계에 적는다.
