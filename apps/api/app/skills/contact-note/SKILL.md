---
name: 담당 부서 안내
description: 답이 자료로 다 답하지 못했다고 말하면, 에이전트에 적은 담당 부서·연락처를 적힌 그대로 덧붙입니다(없으면 「담당 부서에 문의해 주세요」).
when_to_use: 규정·업무 안내처럼 자료 밖의 질문을 담당 부서로 보내야 하는 대화 에이전트. 서버가 답을 다 쓴 뒤 돌린다.
kinds: [chat]
required_tools: [run_skill_script]
version: 1.0.0
stage: post
entry: note
listed: false
---
When the material does not answer the question, or answers only part of it, end the answer with the 담당 부서·연락처 exactly as the agent gives it.

## What the server does
`scripts/note.py` runs over a chat answer after it is written: when the answer says the material does not cover the question (「자료에서 찾지 못했습니다」「자료에는 나와 있지 않습니다」「확인되지 않습니다」) and the contact is not already in it word for word, it adds 「담당 부서·연락처: …」 before the source list, or 「담당 부서에 문의해 주세요.」 when the agent has no contact. It runs on chat answers only (`options.surface`).
