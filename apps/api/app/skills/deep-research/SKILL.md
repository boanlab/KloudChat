---
name: 심층 조사
description: 「심층 리서치」 커넥터로 웹·arXiv를 여러 번 반복 탐색해 출처가 달린 조사 결과를 받아, 질문에 맞게 정리합니다. 몇 분에서 수십 분 걸리므로 깊이 있는 조사가 필요할 때만 씁니다.
when_to_use: 「심층 조사해 줘」「관련 연구·사례를 깊이 찾아 줘」「동향 리포트」처럼 검색 몇 번으로는 부족한 조사를 부탁할 때. 간단한 사실 확인은 웹 검색으로 충분하다.
kinds: [chat, report]
required_tools: [mcp__deep-research__quick_research]
version: 1.2.0
process: {deep_search: true}
---
Research that needs many rounds of searching: findings come only from what the search returned, each with its source.

1. Findings first, each with the source it came from, then what is still uncertain or disputed between sources. Keep publication dates; prefer the newest when sources disagree and say so.
2. Do not add facts, figures or citations the sources do not contain. Every paragraph of findings carries its source; an asked point no source covers is said as not found (「찾은 자료에서 확인하지 못함」), not explained from general knowledge. If the search failed or found little, say so plainly and suggest a narrower query.
3. The research date is today. For each issue, look for this year's announcements and revisions first and say which is the latest; a timeline reads best as a table (시기 | 주체 | 내용 | 출처).
4. When the request names columns (「대학명·시기·대상·핵심」), the table has exactly those columns. A figure you could not find is written as 「확인하지 못함」 in a sentence, never as a bracket blank or an empty table.

[채팅에서]
Use the deep-research connector; it is slow, so pick the lightest tool that answers the question.

1. Restate the question as one research query in the person's terms (who, what, period, region). If the scope is unclear (a period, a country, academic vs. industry), ask once before starting, because a run takes minutes.
2. Choose the tool:
   - `mcp__deep-research__quick_research` (1–5 minutes): the default — a summary with sources.
   - `mcp__deep-research__detailed_research` (5–15 minutes): when the person asks for depth (「심층 리서치」「자세히」「체계적으로」「논문까지」) and the tool is offered. A request naming 「심층 리서치」 starts this mode on the server.
   - `mcp__deep-research__generate_report` (10–30 minutes): only when the person asks for a full research report and the tool is offered.
   Call one of them once; do not run several in a row for the same question.
3. Tell the person in one line before the call that it will take a few minutes.
4. Give each finding's source as the title and link the tool returned.
5. Open the answer with one line naming the mode that ran (「자세한 조사(5~15분)로 찾은 결과입니다.」 or 「빠른 조사로 찾은 결과입니다. 더 깊게 보려면 「심층 리서치로」라고 요청해 주세요.」).

[보고서에서]
The server searches before you write, with twice the usual queries and sources while this skill is on, and numbers what it read. Cover each axis of the request from those sources, each finding with its [n]; when they leave an axis thin, say so in the report rather than filling it from memory.
