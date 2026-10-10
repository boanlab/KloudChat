---
name: 개인정보 가리기
description: 답과 문서에 들어간 휴대전화 번호·개인 이메일·주민등록번호·카드 번호를 [전화번호]·[이메일]·[주민번호]·[카드번호]로 가립니다. 기관 대표번호·부서 메일과 에이전트에 적은 담당 부서·연락처는 그대로 둡니다.
when_to_use: 여러 사람이 읽는 답(공개 챗봇, 공지, 안내문, 회람)에 개인정보가 섞여 나가지 않게 할 때. 에이전트나 묶음에 붙이면 서버가 다 쓴 뒤에 가립니다.
kinds: [chat, report]
required_tools: [run_skill_script]
version: 1.0.0
stage: post
entry: mask
post_check: mask
---
Personal details that identify one person do not appear in the answer: a mobile number, a personal email address, a resident registration number, a card number. The server masks what is left after writing; write so that nothing needs masking.

## Rules
- Do not copy a person's mobile number, personal email address, resident registration number or card number into the answer, even when the material holds it. Write the role or the office instead (「담당자」「학사지원팀」).
- An organisation's own contact stays as written: a main or office landline (02-760-1234), a service number (1588-0000), a role mailbox (help@…, minwon@…), and the agent's 담당 부서·연락처.
- Keep everything else as written. Masking is not a reason to drop a sentence.

## What the server does
`scripts/mask.py` runs over the finished answer (`check` → `fix`, or `post` when the skill is switched on for a chat turn). Each line with a personal detail is rewritten with the value replaced:

| 원래 | 가린 뒤 |
|------|---------|
| 담당 조교 010-1234-5678로 연락 | 담당 조교 [전화번호]로 연락 |
| hong.gildong@gmail.com | [이메일] |
| 900101-1234567 | [주민번호] |

On the server the script uses the instance's own detector (the one that masks text before it leaves for an external model), so both agree on what counts as personal. In the sandbox it uses the same patterns written out in the script.

To check a text yourself, call `run_skill_script` with skill `pii-mask`, script `mask`: `{"text": "<글>", "options": {"contact": "<담당 부서·연락처>"}}`; the result's `text` is the masked text.

CLI: `python scripts/mask.py '{"text": "...", "options": {"contact": "학사지원팀 02-760-1234"}}'` — the last line is the result JSON.
