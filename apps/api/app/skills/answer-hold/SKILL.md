---
name: 근거 없는 답 보류
description: 에이전트 자료에서 찾은 대목이 없거나 답의 어느 문장도 찾은 대목과 맞지 않으면, 답 대신 「제공된 자료에서 이 질문에 대한 내용을 찾지 못했습니다」와 담당 부서·연락처를 보냅니다.
when_to_use: 규정·업무 안내처럼 자료에 있는 것만 답해야 하는 대화 에이전트. 서버가 답을 다 쓴 뒤 돌린다.
kinds: [chat]
required_tools: [run_skill_script]
version: 1.0.0
stage: post
entry: hold
listed: false
---
Answer only from what `search_knowledge` returns. When it returns nothing that answers the question, say so in one sentence — 「제공된 자료에서 이 질문에 대한 내용을 찾지 못했습니다.」 — and point to the 담당 부서·연락처; do not answer from general knowledge.

## What the server does
`scripts/hold.py` runs over a chat answer after it is written (`check` → `fix`):

- nothing of the agent's material was found this turn (and no earlier answer in the conversation cited it), or
- the material was found but no sentence of the answer shares its words,

then the answer is replaced by the not-found reply with the agent's 담당 부서·연락처 copied as written (「담당 부서에 문의해 주세요.」 when the agent has none). An answer that already says the material does not cover the question is left as it is. It runs on chat answers only (`options.surface`).
