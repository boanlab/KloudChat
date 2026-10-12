---
name: 카피라이팅
description: 랜딩·상세·서비스 소개 페이지의 헤드라인·서브카피·본문·CTA를 자료에 있는 사실로만 씁니다. 없는 수치·후기·순위는 지어내지 않고 [빈칸]으로 남기며, 표시광고법상 위험 표현을 점검합니다.
when_to_use: 웹페이지·랜딩페이지·상세페이지·서비스 소개 문구를 새로 쓰거나 고칠 때. 헤드라인·가치 제안·CTA 문구·슬로건이 필요할 때. 광고 소재는 「광고 소재」, SNS 글은 「SNS 콘텐츠」, 메일은 「콜드메일」.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
copy_check: true
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Write persuasive page copy in Korean from the user's request and material only. Clarity beats cleverness; a fact beats an adjective.

## Inputs
Needed before writing. If any of the first three is missing and cannot be read from the material, ask in ONE short message (at most 3 questions) and stop:
1. What the page is and the one action the reader should take (예: 「무료 체험 신청」, 「도입 상담 예약」).
2. What is being sold and for whom (대상 고객, 그들이 겪는 문제).
3. The facts you may use: features, price, numbers, customers, awards, testimonials. Only these may appear as claims.
Optional (use if given, otherwise infer and say so in one line): tone (B2B → 합니다체, 소비자 → 해요체), words to avoid, traffic source, alternatives the reader compares.
If the user says to just write it, write it from the facts given, leaving out what they do not give.
If the material gives fewer than three usable facts (예: 이름·위치·한 가지 특징뿐), ask first and stop — thin material is where invented atmosphere and process creep in (「조용한 골목」「매일 로스팅」「로스팅 과정을 볼 수 있습니다」).

## Grounding rules (hard)
- Every number, date, price, customer or company name, rank, award, certification, review or quote must appear in the request/material. Otherwise write the copy without it — drop the line or say it without the number. A customer case or figure the material does give is used as finished copy, word for word. Ask one short question only when a value the copy cannot do without (the price on a pricing page) is missing. No bracket placeholders and no 「(가상)」-style source notes in copy a customer will read.
- Never upgrade a claim: material 「처리 시간 단축」 does not become 「처리 시간 70% 단축」; 「여러 기업」 does not become 「1,000개 기업」.
- No superlatives, rankings or comparisons (최고, 최초, 유일, 1위, No.1, 최저가, 업계 최고, 타사보다, 기존 대비 N배) unless the material gives the objective basis; then keep the basis next to it, 예: 「2025 ○○조사 고객만족도 1위」.
- No guarantees (보장, 무조건, 100%, 부작용 없음) beyond the material's exact terms.
- Food, 건강기능식품, cosmetics, medical devices: no disease prevention/treatment claims (치료, 예방, 완치, 면역력 강화, 체지방 감소…). For 건강기능식품 copy only the 식약처-recognised functional wording found in the material (「~에 도움을 줄 수 있음」).
- No invented urgency (오늘만, 마감 임박, 선착순) unless the material states the deadline or quantity.
- No invented offers or services: 무료 체험, 상담 예약, 데모, 환불, 배송, 설치 지원, 사은품 only if the material offers them. Otherwise the CTA is an action that exists (「가입하기」, 「문의하기」, 「매장 위치 보기」) or `[CTA: 제공하는 혜택]`.
- Don't stretch a fact into a bigger promise: 「앱에서 사진으로 청구」 ≠ 「서류 없이 청구」; 「매장에서 로스팅」 ≠ 「로스팅 날짜를 확인할 수 있음」.
- Outcome lines say what the product does, not a guaranteed end state: 「회의가 끝나면 요약을 메일로 보냅니다」, not 「정리할 일이 남지 않습니다」「메모를 놓칠 일이 없습니다」.
- The 문제 공감 section describes the situation in words, without durations, counts or percentages (「한두 시간이 사라집니다」 is an invented number).

## Procedure
1. Fact sheet: list the usable facts from the material in 3–8 short lines. Note what is missing.
2. Choose one core message and one primary CTA. Headline states the outcome for the reader, not the company's pride.
3. Write the page in this order (drop sections the material cannot support; never pad with invented proof):
   헤드라인 → 서브헤드(1–2문장) → 주 CTA → 문제 공감 → 이점 3–5개(기능 → 그래서 고객에게 무엇이 좋은지) → 이용 방법 3–4단계 → 근거(자료에 있는 수치·사례만) → 자주 묻는 질문 → 마지막 CTA.
4. Korean style:
   - Benefits before features, specific before vague: 「매주 보고서 정리에 쓰던 시간을 줄입니다」 > 「업무 효율 극대화」.
   - Short sentences. One idea per sentence. Use the customer's words from the material.
   - Avoid AI tells: 「단순한 ~가 아닙니다」, 「~가 아닌, ~입니다」 reveals, 「결과는? ~」, 혁신적·획기적·차원이 다른·게임 체인저, em dashes (—), stacked 「!!」, emoji bullets.
   - CTA = verb + what they get: 「무료로 14일 써 보기」 (only if the trial length is in the material), 「도입 상담 예약하기」. Not 「제출」, 「클릭」, 「자세히」.
   - Translate jargon; keep English only for product names.
5. Give alternatives: 3 headlines (different angles) and 2 CTAs, each with a one-line reason.
6. Check: call `run_skill_script` with skill `copy-check`, script `claims`, args
   `{"items": [{"label": "헤드라인", "text": "..."}, ...], "material": "<the user's material text>", "category": "general"}`
   (`category`: `food`, `health_food`, `cosmetics`, `medical_device` when relevant). Fix every `block` finding (remove it or replace with a placeholder) and run once more. Mention remaining `check` findings to the user.
   If page title / meta description is requested, also check lengths with script `length` (see 「카피 점검」).

## Output format
```
### 페이지 카피
**헤드라인** …
**서브헤드** …
**CTA** …
(섹션마다 굵은 소제목 + 본문)

### 대안
- 헤드라인 A: … — 이유
- 헤드라인 B: … — 이유
- 헤드라인 C: … — 이유
- CTA: … / … — 이유

### 근거
| 문구 속 사실 | 자료의 출처 |

### 쓸 때 주의
- 2~3 lines at most: a condition that must stay with an offer (베타·기존 고객 한정·기간), a claim to soften, what to confirm before publishing
```
Keep the explanation outside the copy short. Do not print character counts you did not measure.

## Checklist
- [ ] Every number, name, price, date and quote is in the material; nothing is left as a bracket blank.
- [ ] No ungrounded 최고·1위·유일·최초·타사 비교·보장 표현; health/food/cosmetic copy has no disease claims.
- [ ] One primary CTA; CTA says what the reader gets.
- [ ] Headline alternatives differ in angle, not just wording.
- [ ] `copy-check` claims: 0 block.

---
출처: coreyhaines31/marketingskills `skills/copywriting` (MIT, © 2025 Corey Haines), commit b9ba399dd88b082b926e261e8ccfb843d20aa066, https://github.com/coreyhaines31/marketingskills. 한국어·국내 규범(표시광고법, 건강기능식품 표시·광고)에 맞춰 다시 썼다.
