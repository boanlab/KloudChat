---
name: 기업 조사
description: 특정 회사의 개요(설립·대표·주소·사업)·재무·제품과 가격·고객·최근 동향·리스크를 공시·공식 자료·보도로 조사해 출처·날짜와 함께 한 장으로 정리합니다. 확인하지 못한 매출·직원 수·고객사는 지어내지 않고 「출처 미확인」으로 둡니다.
when_to_use: 미팅·영업·투자·제휴·입찰 전에 상대 회사를 빠르게 파악할 때, 「○○ 회사 조사해 줘」「기업 개요 정리」. 별칭 company-research.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
---
Research one company and write a Korean one-pager a 영업·기획 담당자 can use before a meeting. Every fact carries where it came from and when.

## Hard rules
- Facts only from the user's material or pages you actually opened in this conversation. Do not fill gaps from memory: model memory about a company's revenue, headcount, CEO or customers is often outdated or wrong.
- Each fact gets `[n]` → 출처 list entry 「기관/매체, 「자료명」, 발행일 또는 확인일, URL」. 공시 values carry the 사업연도 (「2025 사업연도 매출액」).
- Not found → 「출처 미확인」 (and what you tried), never an estimate dressed as a fact. An estimate the user asks for is labelled 「가정」 with its basis.
- Same-name companies: confirm identity by 사업자등록번호, 법인등록번호, 대표자 or address before using a record; if you cannot, say which one you used.
- Distinguish 연결 vs 별도 financials, 매출액 vs 영업수익, and fiscal year.
- Opinions from news or reviews are attributed (「○○일보는 …라고 보도」), not stated as fact.

## Where to look (if `web_search`/`fetch_url` are available and the user did not restrict you to their material)
1. DART 전자공시 (dart.fss.or.kr): 사업보고서·분기보고서(상장·대형 비상장), 감사보고서(외부감사 대상 비상장), 주요사항보고서, 대규모기업집단 현황. KIND (kind.krx.co.kr) for 상장 공시.
2. The company's own site: 회사 소개, 제품·요금 페이지, IR, 보도자료, 채용 공고(조직·기술 신호).
3. 특허정보검색 KIPRIS, 조달청 나라장터(공공 수주 실적), 중소벤처기업부 벤처확인·이노비즈 공시.
4. News: name the outlet and date; prefer two independent outlets for anything material.
- If the user gave a material pack or said 「자료만으로」, use only that and do not search.

## Procedure
1. Pin the company (정식 상호, 사업자/법인 번호 if known) and the user's purpose (영업 미팅, 투자 검토, 제휴, 입찰). Ask once if the company is ambiguous.
2. Collect facts into the sections below. Record each source as you go.
3. Financial ratios or growth rates: compute with `run_skill_script` (`calc-check` if offered) or show the formula from the cited figures; never round silently.
4. Check: `run_skill_script` skill `source-check`, script `sources`, args `{"text": "<answer>", "material": "<user material>", "today": "YYYY-MM-DD", "max_age_years": 2}`. Fix `unsourced`/`undefined_ref`; for `stale`, keep the value with 「(YYYY년 기준)」.

## Output format
```
## ○○(주) 한눈에
| 항목 | 내용 | 출처 |
| 정식 상호 / 대표 / 설립 / 본사 | … | [1] |
| 주요 사업·제품 | … | [2] |
| 매출·영업이익(사업연도, 연결/별도) | … 또는 출처 미확인 | [1] |
| 임직원 수(기준일) | … 또는 출처 미확인 | |
| 주요 고객·파트너 | 공개된 것만 | |

## 제품과 가격
## 최근 동향 (최근 12개월, 날짜순)
- YYYY-MM-DD 무엇이 있었나 [n]
## 리스크·확인할 점
- (공시·보도에 근거한 것만; 추측은 「확인 필요」)
## 미팅에서 물어볼 것
- 자료로 확인하지 못한 항목을 질문으로
## 출처
[1] 금융감독원 DART, 「○○ 2025 사업보고서」, 2026-03-20, URL
```
Keep it to what helps the stated purpose; drop sections with nothing sourced rather than padding them.

## Checklist
- [ ] Every fact has [n] with date; nothing from memory.
- [ ] Financials name the year and 연결/별도.
- [ ] Unknowns are 「출처 미확인」 and turned into questions.
- [ ] `source-check` `sources`: 0 `unsourced`, 0 `undefined_ref`.

---
출처: 새로 씀(KloudChat). deanpeters/Product-Manager-Skills 에 같은 이름(company-research)이 있으나 CC BY-NC-SA 4.0(비영리)이라 내용을 쓰지 않았다. 국내 공시·공공 자료 경로와 출처 규칙은 KloudChat 제품 규칙을 따랐다.
