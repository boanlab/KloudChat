---
name: 시장 규모 추정
description: TAM·SAM·SOM 을 하향식(전체 시장 × 비율)과 상향식(고객 수 × 객단가)으로 코드 계산합니다. 단위·통화·기준 연도를 맞추고, 값마다 출처 또는 「가정」과 근거를 붙이며, 민감도(±)와 TAM ≥ SAM ≥ SOM 정합성을 함께 냅니다. 출처 없는 시장 규모·성장률은 지어내지 않습니다.
when_to_use: 사업계획서·IR·정부지원사업 신청서의 시장 규모, 신규 진입 검토, 「TAM SAM SOM 계산」「시장 규모 얼마나 돼?」. 별칭 market-sizing, tam-sam-som-calculator.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
---
Estimate market size in Korean with every number traceable. Do not compute in your head; the script computes, you copy.

Aliases: `market-sizing`, `tam-sam-som-calculator` (one skill: the calculator is the core of sizing, and two skills would give two answers to one question).

## Hard rules
- Never invent a market size, share, growth rate, customer count or price. Each input is one of:
  1. from the user's material → source 「사용자 자료(파일명·쪽)」;
  2. from a web result you actually opened → source name, URL, publication date;
  3. an assumption → `"assumption": true` and a `basis` (why this value: a comparable, a rule of thumb, the user's plan). Shown as 「가정」.
  Anything else is 「출처 미확인」 and the result says so. Never upgrade a 가정 into a fact in the prose.
- Say the market definition (who, what, where, which year, revenue or volume) before any number. A number from a source with a different definition (global vs 국내, 하드웨어 포함 vs 제외) is not reused without saying so.
- SOM is a share the company can win in a stated period with stated capacity; it is always 가정 unless the user gives a plan.

## Where to look (if `web_search`/`fetch_url` are available and the user did not restrict you to their material)
- 통계청 KOSIS (kosis.kr): 전국사업체조사(업종·규모별 사업체 수·종사자), 인구·가구, 가계동향.
- 공공데이터포털 (data.go.kr), 부처 실태조사: 과기정통부·NIPA 「SW산업실태조사」, 중소벤처기업부 통계, 산업통상자원부 산업통계.
- 한국은행 ECOS (ecos.bok.or.kr): 환율·물가 — use for currency conversion and name the date.
- 업계 협회·연구기관 보고서, DART 전자공시 (dart.fss.or.kr): listed competitors' 매출 for a cross-check or bottom-up price.
- Prefer primary sources over articles quoting them; when only an article is available, cite the article and name the original in it.
- If the user said 「자료만으로」 or gave a material pack, do not search; use only that.

## Procedure
1. Market definition in 2–4 lines. If the target customer, region or year is unclear, ask once (max 3 questions) or state your choice as 가정.
2. Build the inputs as JSON and call `run_skill_script` skill `market-sizing`, script `size`:
   `{"market": "...", "year": 2026, "unit": "억원", "fx": {"USD": {"rate": ..., "source": "한국은행 ECOS", "date": "..."}},
   "top_down": {"tam": [{"label": "국내 ○○ 시장", "value": 5200, "unit": "억원", "year": 2024, "source": "...", "url": "...", "date": "..."}],
   "growth": {"label": "연평균 성장률", "value": 8, "unit": "%", "source": "..."},
   "sam": [{"label": "중소기업 비중", "value": 40, "unit": "%", "source": "KOSIS ...", "date": "..."}],
   "som": [{"label": "3년 내 점유율", "value": 2, "unit": "%", "assumption": true, "basis": "..."}]},
   "bottom_up": {"tam": [{"label": "대상 사업체 수", "value": 12, "unit": "만개", ...}, {"label": "연 객단가", "value": 480, "unit": "만원", ...}],
   "sam": [...], "som": {"direct": [{"label": "3년 차 고객 수", ...}, {"label": "연 객단가", ...}]}}}`
   - Units: 원·만원·억원·조원, 달러·백만달러·USD million, %, 만개·천명. Foreign currency needs `fx` with a source.
   - A data year different from `year` is grown only with a sourced `growth`; otherwise the script reports `year_mismatch` — keep the original year in the text (「2024년 기준」).
   - Do both approaches when the material allows; when only one is possible, say why.
3. Read `issues`: fix `unsourced`, `assumption_without_basis`, `order`, `rate_out_of_range` and run again. `approach_gap` (the two TAMs differ ≥2×) is reported to the user with the likely cause, not averaged away. `stale` sources are kept only with 「(YYYY년 기준)」 beside the number.
4. Copy `table_markdown`, `formulas`, `assumptions_markdown` and `sensitivity_markdown` as they are.
5. Before answering, run `source-check` script `sources` over the final text with the material.

## Output format
```
## 시장 정의
(누구·무엇·어디·기준 연도·금액/물량)

## 결과
(table_markdown)

## 계산식
(formulas 그대로)

## 가정과 출처
(assumptions_markdown)

## 민감도
(sensitivity_markdown) — 결과를 가장 크게 흔드는 가정 1–2개를 한 줄로

## 확인할 것
- 출처 미확인·가정 값을 무엇으로 확인할지 (예: 「KOSIS 전국사업체조사 2024 업종별 사업체 수」)

## 출처
[1] 기관, 「자료명」, 발행일, URL
```
Write 「약 72.8억원」 only when the script said 72.8. Do not round further than the script without saying so.

## Checklist
- [ ] Every input has a source with date, or is 가정 with a basis, or is 「출처 미확인」.
- [ ] Formulas and tables copied from the script; no number computed by hand.
- [ ] TAM ≥ SAM ≥ SOM, units and currency stated, base year stated.
- [ ] `source-check` `sources`: 0 `unsourced`.

---
출처: phuryn/pm-skills `pm-market-research/skills/market-sizing` (MIT, © 2026 Pawel Huryn), commit 8607e3b077817f89bf4a9b623246219734ac3be0, https://github.com/phuryn/pm-skills — 하향식·상향식 교차 검증, SAM·SOM 범위, 가정 목록의 구성을 따랐다. tam-sam-som-calculator 는 deanpeters/Product-Manager-Skills 에 같은 이름이 있으나 CC BY-NC-SA 4.0(비영리)이라 쓰지 않았고, 계산 스크립트 `size.py` 는 새로 썼다.
