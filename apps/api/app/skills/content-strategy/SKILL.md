---
name: 콘텐츠 전략
description: 블로그·SNS·뉴스레터 콘텐츠의 축(필러)과 주제 목록, 우선순위 점수, 발행 일정을 자료와 고객 질문에서 뽑아 짭니다. 검색량·성과 수치는 지어내지 않고 근거가 없으면 「가정」으로 표시합니다.
when_to_use: 무엇을 써야 할지 정할 때, 콘텐츠 캘린더·편집 계획·블로그 주제·필러 콘텐츠를 짤 때. 「콘텐츠 전략」「블로그 주제 뽑아 줘」「다음 분기 발행 계획」.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
copy_check: true
copy_kinds: [claim, medical, guarantee]
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Plan content as a product: pillars, prioritised topics, and a dated calendar, built from the user's business and customer evidence.

## Inputs
Ask in ONE message (at most 3 questions) if missing, then stop:
1. Business and audience: what is sold, to whom, and the goal of content (유입, 리드, 브랜드 인지, 고객 교육).
2. Customer evidence: questions customers ask, sales/support notes, reviews, existing posts and what performed. Without any, say the plan is a hypothesis.
3. Channels and capacity: 네이버 블로그·브런치·자사 블로그·인스타그램·링크드인·유튜브·뉴스레터; how many pieces per week; start date.
Search volumes, rankings and past performance numbers come only from the user (예: 네이버 검색광고 키워드 도구, 서치어드바이저). Never estimate them; score such criteria as assumed.

## Procedure
1. Pillars: 3–5 themes where the business's problems-solved meet the audience's questions. For each: why (link to evidence), 3–6 subtopics.
2. Topics: for each candidate give 제목 안, 축, 유형 — 검색형(searchable: 「~하는 법」「A vs B」「비용」 등 찾아 들어오는 글), 공유형(shareable: 의견·자체 데이터·사례), 실험(새 형식) — 구매 단계(인지·비교·결정·사용), 대표 검색어(사용자가 준 것 또는 「[검색어 확인 필요]」).
3. Score 1–10 on four criteria and write the basis for each score: impact(고객 영향: 얼마나 자주·많이 나오는 문제), fit(제품과 이어지는가), search(검색 잠재력: 사용자가 준 검색량·경쟁도만), resources(만들기 쉬움). Leave a criterion empty if there is nothing to judge it on.
4. Run `run_skill_script` skill `content-strategy`, script `plan`, args:
   `{"topics": [{"title": "...", "pillar": "...", "type": "searchable", "scores": {"impact": 8, "fit": 9, "search": 6, "resources": 7}, "basis": {"impact": "상담 기록에 월 12회 언급", "fit": "...", "search": "사용자 제공 월 검색량 1,300"}}], "calendar": {"start": "YYYY-MM-DD", "weeks": 8, "per_week": 2, "weekdays": ["화", "목"]}}`
   Default weights 40/30/20/10 and mix 검색형 60·공유형 30·실험 10; change only if the user asks (새 블로그는 검색형 비중을 높일 수 있음 — suggest, don't impose). Copy the ranking and calendar from the result; do not recompute scores or dates yourself.
5. Do not state legal or regulatory facts (적용 대상 인원, 의무 여부, 기한) unless the material gives them; write the topic and mark `[법령 확인 필요]`. Do not attribute features to the product beyond the material.
   Proposed titles and briefs follow the same grounding: no numbers, savings, durations or offers the material lacks (「3분이면 끝」「주유비 10% 아끼는 법」「14일 무료 체험」 are invented) — use `[수치]` or a wording without a number.
6. For the top 3 topics write a brief: 제목 후보 3개, 핵심 질문, 목차, 근거로 쓸 자료, CTA, 재가공(블로그 → 인스타 카드뉴스·링크드인 글·뉴스레터).
7. Channel norms (Korea): 네이버 블로그 — 검색어를 제목 앞쪽에, 경험·사진·구체적 절차, 광고·협찬이면 「광고」「협찬」 표시(공정위 추천·보증 심사지침); 인스타그램 — 첫 줄이 잘림 전 노출, 카드뉴스 6–10장; 링크드인 — 실무자 1인칭, 숫자·사례 중심, 해시태그 3–5개; 유튜브 — 제목에 핵심어, 설명 첫 2줄.

## Output format
```
### 콘텐츠 축
| 축 | 왜(근거) | 하위 주제 |
### 우선순위
(plan 의 table_markdown 그대로; 「근거 없는 기준」 열 유지)
### 발행 일정
| 날짜 | 요일 | 유형 | 주제 | 축 |
### 상위 3개 기획안
### 확인이 필요한 것
- [검색어 확인 필요] 등, 가정으로 둔 점수
```

## Checklist
- [ ] Every number (검색량, 성과, 고객 수) is from the user; otherwise marked as 가정/빈칸.
- [ ] Scores and dates copied from the `plan` result.
- [ ] Each pillar traces to evidence; mix stated.
- [ ] Sponsored/ad content flagged for 「광고」·「협찬」 disclosure.

---
출처: coreyhaines31/marketingskills `skills/content-strategy` (MIT, © 2025 Corey Haines), commit b9ba399dd88b082b926e261e8ccfb843d20aa066, https://github.com/coreyhaines31/marketingskills. 점수 가중치(40/30/20/10)와 60/30/10 비율을 따르고 국내 채널 규범에 맞춰 다시 썼다.
