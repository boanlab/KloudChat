---
name: 경쟁 대응 카드
description: 영업 담당자가 통화·미팅 중에 바로 보는 경쟁사 한 곳 대응 카드(배틀카드)를 만듭니다. 한 줄 포지셔닝, 출처 있는 비교표, 우리가 이기는 곳·지는 곳, 반론 대응, 물어볼 질문, 수주·실주 패턴. 경쟁사 기능·가격은 출처가 없으면 「확인 필요」로 둡니다.
when_to_use: 「○○ 대비 배틀카드」「경쟁사 대응 자료」「고객이 ○○가 더 싸다고 할 때 뭐라고 해?」, 영업 교육·제안 준비. 여러 경쟁사 구도 분석은 「경쟁사 분석」. 별칭 competitive-battlecard.
kinds: [chat]
required_tools: [run_skill_script, web_search, fetch_url]
version: 0.1.0
status: draft
post_check: source-check/post
post_judge: "claims about the company, competitors, market or customers, what an interview or survey found, what a cited source [n] says, and capabilities the material does not give"
tables: true
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Make a Korean battlecard against ONE competitor that a 영업 담당자 can read in 30 seconds and defend in front of a customer. A wrong claim in front of a customer costs more than a missing one, so everything is sourced or marked.

## Hard rules
- Competitor facts (features, prices, contract terms, customers, outages, reviews) from the user's material or pages you opened, each with `[n]` and the date checked. Prices with plan, VAT 기준, date.
- Own-product claims from the user's material → 「(자사 자료)」, and only the capabilities it lists: never say our product includes something the material does not name (메일, 드라이브). Do not promise features, discounts, SLAs or roadmap items the material does not contain.
- A competitor's lack of something is stated only as what you checked: 「공개 자료에서 확인하지 못함」, never 「제공하지 않습니다」.
- Prices are compared on one basis first — a small table with 월/연, 1인당/전체, 계약 조건, 부가세 포함 여부 aligned — before any judgement on price.
- Unknown → 「확인 필요」. 우위 column → 「판단 보류」 unless both cells are sourced.
- 반론 대응 answers use facts from the table, not adjectives. No disparagement: no claims about the competitor's finances, security or stability without a public source; never 「곧 망한다」 style talk. Comparative advertising rules (표시광고법) apply to anything that may reach customers: objective, verifiable, same conditions.
- 수주·실주 패턴 only from the user's win/loss notes or CRM; without them, the section says 「수주·실주 기록이 없어 비워 둠」 and lists what to record.

## Where to look (if `web_search`/`fetch_url` are available and the user did not restrict you to their material)
- Competitor 요금·기능·약관·고객 사례·변경 이력 pages; DART/KIND 공시; 나라장터 낙찰; 앱 마켓 리뷰(평점·수·날짜).
- The user's own sales notes, call memos, lost-deal reasons — the most valuable input; ask for them if absent.
- If the user gave a material pack or said 「자료만으로」, use only that.

## Procedure
1. Confirm: our product, the one competitor, the deal type (규모·업종). Ask once for win/loss notes if not given.
2. Build the comparison table (5–8 rows that decide deals here: 가격, 핵심 기능, 도입 기간, 지원, 보안·인증, 연동).
3. Check: `run_skill_script` skill `source-check`, script `claims`, args `{"text": "<card>", "material": "<material>", "fix": true}`. Fix until 0 findings (sourced, or 「확인 필요」/「판단 보류」).
4. Write 이기는 곳 / 지는 곳 from sourced rows only; for each 지는 곳, a factual counter (what we do instead, or when it does not matter).
5. 반론 대응: 3–5 objections in the customer's words → a 1–2 sentence answer + the fact it rests on `[n]`.
6. 물어볼 질문: questions that surface where we are stronger, phrased neutrally (no trap or false premise).
7. Final check: script `sources` over the card.

## Output format
```
# ○○ 대비 경쟁 대응 카드 (자료 기준일 YYYY-MM-DD)
**한 줄 포지셔닝**: (자료에 근거한 한 문장)

| 항목 | 자사 | ○○ | 우위 | 출처 |
|---|---|---|---|---|

## 우리가 이기는 곳
- … [n]
## 우리가 지는 곳과 대응
- … [n] → 대응: … (자사 자료)
## 반론 대응
| 고객이 하는 말 | 이렇게 답한다 | 근거 |
## 물어볼 질문
- …?
## 수주·실주 패턴
## 확인 필요
## 출처
```
Short bullets and tables; no paragraph longer than two sentences.

## Checklist
- [ ] One competitor; every cell sourced or 「확인 필요」; verdicts only on sourced rows.
- [ ] No promise beyond the material; no disparagement without a public source.
- [ ] Win/loss only from records.
- [ ] `claims` 0 findings, `sources` 0 `unsourced`.

---
출처: phuryn/pm-skills `pm-go-to-market/skills/competitive-battlecard` (MIT, © 2026 Pawel Huryn), commit 8607e3b077817f89bf4a9b623246219734ac3be0, https://github.com/phuryn/pm-skills — 회사 개요·비교표·이기는 곳/지는 곳·반론 대응·질문·수주/실주 구성을 따랐다. 칸별 출처 규칙, 표시광고법 주의, 검사는 KloudChat 에서 새로 썼다.
