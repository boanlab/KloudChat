---
name: 시장 세분화
description: 시장을 판별 가능한 기준으로 3~5개 고객 세그먼트로 나누고, 세그먼트마다 규모(출처 또는 자료 없음)·니즈·불편·현재 대안·제품 적합성을 근거와 함께 적습니다. 자료에 없는 규모·비율·고객 특성은 지어내지 않습니다.
when_to_use: 목표 고객 정하기, 사업계획서의 고객 정의, 신규 시장 진입 시 어느 고객부터 갈지 고를 때, 「시장 세분화」「타깃 세그먼트」「고객군 나누기」. 별칭 market-segments.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
post_check: source-check/post
post_judge: "claims about the company, competitors, market or customers, what an interview or survey found, what a cited source [n] says, and capabilities the material does not give"
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Segment a market in Korean so the user can act on it: each segment is defined by criteria someone could check, sized from a source or honestly 「자료 없음」, and described from evidence.

## Hard rules
- A segment's 구분 기준 is observable (업종·종사자 수·지역·구매 방식·사용 빈도), not a mood (「혁신을 추구하는 기업」).
- Size: a sourced value (KOSIS 전국사업체조사, 인구·가구 통계, user CRM) or `{"status": "자료 없음", "to_find": ...}`. A size derived by calculation goes through `market-sizing` `size` and its formula is shown.
- 니즈·불편·현재 대안 come from evidence: interviews, surveys, VOC, sales notes, reviews, published studies. Without it the item is 「자료 없음」 and you say what research would fill it. Do not write persona-style invented details (나이, 연봉, 성격).
- Never invent percentages, counts or quotes. A quote is copied verbatim from the material with its source.
- Segments do not overlap; if they must, say where.

## Where to look (if `web_search`/`fetch_url` are available and the user did not restrict you to their material)
- 통계청 KOSIS: 전국사업체조사(업종·규모·지역별 사업체 수), 인구총조사, 가계동향.
- 공공데이터포털(data.go.kr), 중소벤처기업부·소상공인시장진흥공단 상권·업종 통계.
- The user's own CRM exports, interview notes, survey results, sales notes — the best source for needs.

## Procedure
1. State the market and the decision the segmentation serves (예: 「첫 영업 대상 고르기」).
2. Choose 1–2 segmentation dimensions that the evidence supports; say why.
3. Get the frame: `run_skill_script` skill `source-check`, script `frame`, `{"template": "segments"}`. Fill 3–5 segments (fewer if the evidence supports fewer; say so).
4. Check and render: script `frame` with `{"framework": "segments", "today": "YYYY-MM-DD", "segments": [...]}`. Fix blocking findings (`no_criteria`, `no_size`, `missing_factor`, `no_evidence`, `unsourced`, `generic`) and run again.
5. Priority: rank only on criteria you state (규모·니즈 강도·도달 가능성·적합성), each scored from the evidence; a criterion without evidence is scored 「판단 보류」, not guessed.
6. Run script `sources` over the whole answer.

## Output format
```
## 시장과 목적
## 나눈 기준
(기준과 그 이유)
## 세그먼트 한눈에
(frame 개요 표 그대로)
## 세그먼트별 근거
(frame 상세 그대로)
## 우선순위
| 세그먼트 | 규모 | 니즈 강도 | 도달 가능성 | 적합성 | 판단 |
(각 칸 근거 [n] 또는 판단 보류)
## 확인할 것
- 자료 없음 칸을 채울 조사(인터뷰 n곳, 설문 문항, KOSIS 표 이름)
## 출처
```

## Checklist
- [ ] Each segment: observable criteria, sourced size or 자료 없음, evidence-backed needs.
- [ ] No invented persona details, percentages or quotes.
- [ ] Priority uses stated criteria only.
- [ ] `frame` ok, `sources` 0 `unsourced`.

---
출처: phuryn/pm-skills `pm-market-research/skills/market-segments` (MIT, © 2026 Pawel Huryn), commit 8607e3b077817f89bf4a9b623246219734ac3be0, https://github.com/phuryn/pm-skills — 세그먼트 3~5개, JTBD·불편·현재 대안·제품 적합성 구성을 따랐다. 근거·「자료 없음」 규칙과 검사는 KloudChat 에서 새로 썼다.
