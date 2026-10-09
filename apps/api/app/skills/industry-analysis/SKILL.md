---
name: 산업 환경 분석
description: PESTEL(정치·경제·사회·기술·환경·법률)과 Porter 5 Forces(기존 경쟁·공급자·구매자·대체재·신규 진입)로 산업을 분석합니다. 요인마다 근거 사실과 출처·날짜를 붙이고, 자료가 없으면 「자료 없음」과 확인할 곳을 적으며, 「경쟁이 치열하다」 같은 근거 없는 일반론은 쓰지 않습니다.
when_to_use: 사업계획서·전략 보고서의 외부 환경 분석, 신규 시장 진입 검토, 「PESTEL 분석」「5 Forces 분석」「산업 구조 분석」. 별칭 pestel-analysis, porters-five-forces.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
post_check: source-check/post
post_judge: "claims about the company, competitors, market or customers, what an interview or survey found, what a cited source [n] says, and capabilities the material does not give"
---
Analyse an industry's environment in Korean. A framework filled with plausible generalities is worse than an honest half-empty one: each cell is a fact with a source, or 「자료 없음」.

Aliases: `pestel-analysis`, `porters-five-forces`. They are one skill because they share the input (one industry, one evidence pack), the evidence rules and the checker, and users usually want both; run only the one the user names.

## Hard rules
- Each factor item = judgement (claim) + fact (evidence: a number, an event, a regulation, a quote) + source (name, URL, date). From the user's material → 「사용자 자료」.
- No fact → `{"status": "자료 없음", "to_find": "어디서 무엇을 확인할지"}`. Never fill a factor with a general statement to make the frame look complete.
- Banned unless the same item carries concrete evidence: 「경쟁이 치열하다」「성장 가능성이 크다」「시장이 빠르게 성장」「디지털 전환이 가속」「규제가 강화되는 추세」「소비자 니즈가 다양해진다」「진입 장벽이 높다」.
- 5 Forces 강도 is 높음/중간/낮음 only with at least one sourced item; otherwise 「판단 보류」.
- Never invent laws, bill numbers, statistics, dates or company moves. A regulation needs its name and date (국가법령정보센터 law.go.kr, 입법예고, 관보, 부처 보도자료).

## Where to look (if `web_search`/`fetch_url` are available and the user did not restrict you to their material)
- 정치·법률: 국가법령정보센터(law.go.kr), 국회 의안정보시스템, 부처 보도자료·고시, 공정거래위원회.
- 경제: 한국은행 ECOS(금리·환율·물가), 통계청 KOSIS(산업·가계 통계), 산업통상자원부.
- 사회: KOSIS 인구·가구·사회조사, 공공데이터포털(data.go.kr).
- 기술: 특허정보검색 KIPRIS, 정부 R&D·표준 발표, 업계 협회 자료.
- 환경: 환경부·한국환경공단 고시, 탄소·포장재 규제.
- 5 Forces: DART 전자공시(경쟁사 매출·원가 구조·주요 공급처), 공정위 시장구조 자료, 요금 페이지, 나라장터(공공 수요), 협회 통계.
- If the user said 「자료만으로」 or gave a material pack, do not search.

## Procedure
1. Define the industry, region and time frame in one line. Ask once if it is ambiguous (max 3 questions).
2. Get the frame: `run_skill_script` skill `source-check`, script `frame`, args `{"template": "pestel"}` and/or `{"template": "five_forces"}`.
3. Fill it from the material and the pages you actually opened. Several items per factor are fine; one honest 「자료 없음」 is fine.
4. Check and render: script `frame` with `{"framework": "pestel", "subject": "...", "today": "YYYY-MM-DD", "factors": {...}}` (and `five_forces` with `forces`). Fix every blocking finding (`missing_factor`, `no_evidence`, `unsourced`, `generic`, `rating_without_evidence`) by adding real evidence or turning the item into 「자료 없음」, then run again.
5. Copy the returned `markdown` tables. Then write the implications — only from the sourced items: the 2–3 factors that matter most, and what they mean for the user's decision.
6. Run script `sources` over the whole answer.

## Output format
```
## 분석 범위
(산업·지역·기간, 자료 기준일)

## PESTEL
(frame 표 그대로)

## 5 Forces
(요약 표 + 근거 표 그대로)

## 시사점
1. (출처 있는 항목에 기대어, [n] 인용)

## 자료 없음 — 확인할 것
- 요인: 어디서 무엇을

## 출처
[1] 기관, 「자료명」, 날짜, URL
```

## Checklist
- [ ] Every row has evidence + source/date, or is 「자료 없음」 with where to look.
- [ ] No generic statement without evidence; no rating without a sourced item.
- [ ] Implications cite items; nothing new is introduced there.
- [ ] `frame` ok, `sources` 0 `unsourced`.

---
출처: porters-five-forces 의 다섯 힘 구분과 강도·전략 시사점 순서는 phuryn/pm-skills `pm-product-strategy/skills/porters-five-forces` (MIT, © 2026 Pawel Huryn), commit 8607e3b077817f89bf4a9b623246219734ac3be0, https://github.com/phuryn/pm-skills 를 따랐다. pestel-analysis 는 deanpeters/Product-Manager-Skills 에 같은 이름이 있으나 CC BY-NC-SA 4.0(비영리)이라 쓰지 않고 새로 썼다. 근거·「자료 없음」 규칙과 검사 스크립트는 KloudChat 에서 새로 썼다.
