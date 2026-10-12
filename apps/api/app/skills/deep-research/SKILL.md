---
name: 심층 조사
description: 「심층 리서치」 커넥터로 웹·arXiv를 여러 번 반복 탐색해 출처가 달린 조사 결과를 받아, 질문에 맞게 정리합니다. 몇 분에서 수십 분 걸리므로 깊이 있는 조사가 필요할 때만 씁니다.
when_to_use: 「심층 조사해 줘」「관련 연구·사례를 깊이 찾아 줘」「동향 리포트」처럼 검색 몇 번으로는 부족한 조사를 부탁할 때. 간단한 사실 확인은 웹 검색으로 충분하다.
kinds: [chat, report]
required_tools: [mcp__deep-research__quick_research]
version: 1.1.0
process: {deep_search: true}
---
Research that needs many rounds of searching: findings come only from what the search returned, each with its source.

1. Findings first, each with the source it came from, then what is still uncertain or disputed between sources. Keep publication dates; prefer the newest when sources disagree and say so.
2. Do not add facts, figures or citations the sources do not contain. If the search failed or found little, say so plainly and suggest a narrower query.

[채팅에서]
Use the deep-research connector; it is slow, so pick the lightest tool that answers the question.

1. Restate the question as one research query in the person's terms (who, what, period, region). If the scope is unclear (a period, a country, academic vs. industry), ask once before starting, because a run takes minutes.
2. Choose the tool:
   - `mcp__deep-research__quick_research` (1–5 minutes): the default — a summary with sources.
   - `mcp__deep-research__detailed_research` (5–15 minutes): only when the person asks for depth (「자세히」「체계적으로」「논문까지」) and the tool is offered.
   - `mcp__deep-research__generate_report` (10–30 minutes): only when the person asks for a full research report and the tool is offered.
   Call one of them once; do not run several in a row for the same question.
3. Tell the person in one line before the call that it will take a few minutes.
4. Give each finding's source as the title and link the tool returned.

[보고서에서]
The server searches before you write, with twice the usual queries and sources while this skill is on, and numbers what it read. Cover each axis of the request from those sources, each finding with its [n]; when they leave an axis thin, say so in the report rather than filling it from memory.
