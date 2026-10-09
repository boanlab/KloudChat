---
name: 경쟁사 분석
description: 직접·간접 경쟁사를 고르고 포지셔닝·기능·가격·고객·최근 움직임·강약점을 출처 있는 비교표로 정리한 뒤, 차별화 기회를 근거와 함께 제안합니다. 확인하지 못한 기능·가격·점유율은 「확인 필요」로 두고 지어내지 않습니다.
when_to_use: 경쟁 구도 파악, 사업계획서·IR 의 경쟁 분석, 포지셔닝·가격 결정 전, 「경쟁사 분석」「경쟁사 비교표」「경쟁 지형」. 영업용 한 장 대응 자료는 「경쟁 대응 카드」. 별칭 competitor-analysis.
kinds: [chat]
required_tools: [run_skill_script, web_search, fetch_url]
version: 0.1.0
status: draft
post_check: source-check/post
post_judge: "claims about the company, competitors, market or customers, what an interview or survey found, what a cited source [n] says, and capabilities the material does not give"
---
Map the competition for the user's product in Korean, cell by cell from sources. The comparison table is the product; the recommendations follow from it.

## Hard rules
- Every claim about a competitor — feature, price, plan limits, customers, funding, 매출, 점유율 — comes from the user's material or a page you opened, with `[n]` (source, URL, date checked). Prices carry the plan name, VAT 기준 and date (「Pro 월 9,900원(VAT 별도), 2026-09-01 확인」).
- Not found → 「확인 필요」 in the cell. A blank or 「-」 is not allowed; 「자료 없음」 when you looked and nothing exists.
- No vague cells: 「우수」「강력」「보통」 alone are not facts. Write what differs (「화자 구분: 한국어 지원 [3]」).
- Claims about the user's own product come from the user's material too → 「(자사 자료)」.
- A 우위 judgement only where both sides of that row are sourced; else 「판단 보류」.
- 점유율·시장 순위 only from a named study with its year; never from impression. No 「업계 1위」 without that.
- Reviews (앱 마켓, G2, 블로그) are opinions: quote with count and date, do not generalise from one.

## Where to look (if `web_search`/`fetch_url` are available and the user did not restrict you to their material)
- Each competitor's own pages: 요금, 기능, 고객 사례, 보도자료, 변경 이력(release notes), 채용.
- DART 전자공시 (매출·주요 고객·사업 내용), KIND, 벤처 투자 보도(투자사·라운드·날짜).
- 조달청 나라장터(공공 낙찰), 앱 마켓 리뷰(평점·리뷰 수·날짜), 해외 제품은 G2/Capterra.
- If the user gave a material pack or said 「자료만으로」, use only that.

## Procedure
1. Scope: the user's product, the customer segment and the decision this analysis serves. Ask once if unclear.
2. Pick competitors: 직접(같은 고객·같은 문제), 간접/대체(다른 방식으로 같은 문제: 엑셀, 외주, 수작업). 3–5 direct unless the user names them. Say why each is in.
3. Build the comparison table: rows = 비교 항목 (목표 고객, 핵심 기능 3–6개, 가격·요금제, 도입 방식, 고객·사례, 최근 움직임), columns = 자사 + 경쟁사들 + 출처. One fact per cell.
4. Check the table: `run_skill_script` skill `source-check`, script `claims`, args `{"text": "<table and bullets>", "material": "<user material>", "fix": true}`. Fix every finding (add the source, or use `fixed_text`), run again until 0 `unsourced_cell`/`empty_cell`/`vague_cell`/`verdict_without_evidence`.
5. 강점·약점 per competitor: bullets each tied to a cell or `[n]`.
6. 차별화 기회: only where the table shows a gap (a need in the material or a review pattern that no competitor covers). Each opportunity cites the rows it rests on. Mark guesses 「가설」 and say how to test them.
7. Final check: script `sources` over the whole answer.

## Output format
```
## 범위
(자사 제품·대상 고객·이 분석으로 정할 것, 자료 기준일)
## 경쟁사와 고른 이유
| 경쟁사 | 구분(직접/간접) | 고른 이유 [n] |
## 비교표
| 항목 | 자사 | A사 | B사 | 출처 |
## 경쟁사별 강점·약점
### A사
- 강점: … [n]
- 약점: … [n]
## 차별화 기회
1. 기회 — 근거: 비교표 「…」 행, [n] (가설이면 검증 방법)
## 지켜볼 것
## 확인 필요 목록
## 출처
```

## Checklist
- [ ] Every cell sourced or 「확인 필요」/「자료 없음」; no vague cells.
- [ ] Prices with plan, VAT 기준, date; 점유율 only with a named study.
- [ ] Opportunities cite table rows; guesses labelled 가설.
- [ ] `claims` 0 findings, `sources` 0 `unsourced`.

---
출처: phuryn/pm-skills `pm-market-research/skills/competitor-analysis` (MIT, © 2026 Pawel Huryn), commit 8607e3b077817f89bf4a9b623246219734ac3be0, https://github.com/phuryn/pm-skills — 시장 범위 → 경쟁사 선정 → 강약점 → 차별화 기회 순서를 따랐다. 칸별 출처·「확인 필요」 규칙과 검사는 KloudChat 에서 새로 썼다. coreyhaines31/marketingskills 의 competitor-profiling(MIT)도 참고 대상으로 확인했으나 내용은 옮기지 않았다.
