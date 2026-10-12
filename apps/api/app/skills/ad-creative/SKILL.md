---
name: 광고 소재
description: 네이버 파워링크·카카오(키워드·비즈보드·디스플레이)·메타·구글·유튜브 광고 문구를 채널 글자 수 한도 안에서 각도별로 여러 안 만들고, 성과 자료가 있으면 이긴 문구를 바탕으로 새 안을 냅니다. 자료에 없는 수치·후기·순위는 쓰지 않습니다.
when_to_use: 검색광고·디스플레이·SNS 광고의 제목·설명·기본 문구 안을 만들거나, 집행 중인 소재의 성과(CTR·전환)를 보고 새 안이 필요할 때. 「파워링크 소재」「메타 광고 문구」「구글 반응형 검색광고 제목 15개」.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
copy_check: true
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Write ad copy sets that fit each platform's fields and limits, built only from the request/material.

## Inputs
Ask in ONE message (at most 3 questions) if missing, then stop:
1. Platform and format (네이버 파워링크, 카카오 키워드/비즈보드/디스플레이, 메타, Google 검색/디맨드젠/동영상, 유튜브).
2. What is promoted and the offer (제품·서비스, 가격, 혜택·기간) and the landing page goal.
3. The facts allowed as claims (수치, 인증, 수상, 후기). Optional: target audience, brand words to avoid, existing ads with performance.
If the platform is clear and the material has the offer, proceed without asking.

## Limits (공백 포함; verified 2026-10-08 — `copy-check` `length` `{"list": true}` has the full table and sources)
| 채널 | 필드 | 한도 |
|---|---|---|
| 네이버 파워링크 | 제목 / 설명 | 15자 / 20~45자 (hard; 설명에 전화번호·URL 금지, 「!!」 같은 반복 부호 금지) |
| 카카오 키워드광고 | 제목 / 설명 | 15자 / 45자 |
| 카카오 비즈보드 | 메인·서브 카피 | 글자 수 한도 없음, 이미지 안 폭 796px 권장(메인 48pt 기준 한글 약 16자) · 이모지·특수기호 금지 · 메인≠서브 |
| 카카오 디스플레이(네이티브) | 타이틀 / 홍보문구 / 프로필명 | 25자 / 45자 / 20자 · ☆♡☞ 금지 · 행동유도 버튼은 목록에서 고름 |
| 메타 | 기본 문구 / 제목 / 설명 | 권장 125 / 40(페이스북 피드 27) / 30자; 최대 1,024 / 255 / 255 |
| Google 반응형 검색광고 | 제목 ×15 / 설명 ×4 / 경로 | 30 / 90 / 15 — 한글은 2자로 셈(한글만이면 15 / 45 / 7자) |
| Google 동영상 | 제목 / 긴 제목 / 설명 | 30 / 90 / 90 (한글 2자 셈으로 맞춤) |

## Grounding and legal rules (hard)
- Numbers, prices, dates, customer counts, ratings, awards, certifications, reviews: only from the material; otherwise `[가격]`, `[고객 수]`, `[인증명]` or leave the angle out.
- Keep material facts exact: 「다음 영업일 지급」 must not become 「당일 지급」; a 30-person panel result is stated with its base (「자사 패널 30명 중 27명, 2주 사용」), never as 「90% 효과」.
- 최상급·순위·비교·보장 (최고, 1위, 유일, 최초, 최저가, 타사 대비, 100%, 보장) only with objective evidence in the material, shown with its source (조사기관·기간). Naver rejects a named competitor in any comparison.
- Cosmetics: no 미백·주름 개선·재생·치료 unless the material says it is a reviewed 기능성 화장품. Food/건강기능식품: no disease wording; 건강기능식품 ads need 자율심의 — remind the user.
- Insurance, finance, medical, education ads: no guarantees of results or payouts.
- No invented urgency; real deadlines only (「10월 31일까지 1+1」).
- No added qualifiers the material lacks: 실시간, 즉시, 바로, 자동으로 모두, 완벽 대응, 사고 예방, 기준 준수 보장.
- No invented offers (무료 체험·상담·데모·사은품) and no stretching a fact into a bigger promise (「클라우드형」 ≠ 「설치 지원 무료」; 「사진으로 청구」 ≠ 「서류 필요 없음」). Insurance/finance: no 「걱정 끝」「다 보장」.

## Procedure
1. List the usable facts. Pick 3–5 angles (different reasons to click): 문제 공감, 결과·이점, 증거(자료가 있을 때만), 대상 호명, 방법·간편함, 혜택·기한(자료가 있을 때만), 비교 없는 차별점.
2. Write per angle, varying shape (질문·평서·명령·숫자 앞). Each Google headline must make sense alone and in any combination; include at least one keyword headline, one benefit headline, one CTA headline. Descriptions add proof, objection handling or the offer, not a repeat of the headline.
3. Korean ad tone: 짧은 명사형·해요체·합니다체 중 하나로 일관, 영어 남용 금지. No AI tells: 「단순한 ~가 아닙니다」, 「No X, no Y」 lists translated, 「지금 바로 경험하세요」, 혁신적·차원이 다른, em dash, 「!!」, emoji (Kakao bans most symbols).
4. Check lengths: `run_skill_script` skill `copy-check`, script `length`, args `{"items": [{"label": "1안 제목", "channel": "naver_powerlink", "field": "title", "text": "..."}, ...]}` (channels: `naver_powerlink`, `kakao_keyword`, `kakao_bizboard`, `kakao_display`, `meta`, `google_rsa`, `google_video`, `youtube`). Rewrite anything over a hard limit or under a minimum and check once more.
5. Check claims: script `claims` with all copy and the material (`category` `cosmetics`/`health_food`/`food` when relevant). Fix every `block`.
6. With performance data: name the metric that matters, list winning and losing patterns (theme, structure, length) from the data only, then write new variants that extend winners plus 1–2 new angles; add an iteration log.

## Output format
Per channel, an upload-ready table, counts copied from the `length` result:
```
### 네이버 파워링크
| # | 각도 | 제목 | 글자 | 설명 | 글자 |
### 근거·주의
- 「1+1」: 자료의 10월 31일 기한 그대로
- 채울 빈칸: [고객 수] …
```
Then one short note on which 2–3 sets to test first and why.

## Checklist
- [ ] All fields within limits (script-measured), Google counted with 한글 2자.
- [ ] Every claim and number traceable to the material; no 1위·최고·타사 비교 without evidence.
- [ ] No disease/기능성 wording for food/cosmetics; real deadlines only.
- [ ] Angles differ; Google headlines stand alone.

---
출처: coreyhaines31/marketingskills `skills/ad-creative` (MIT, © 2025 Corey Haines), commit b9ba399dd88b082b926e261e8ccfb843d20aa066, https://github.com/coreyhaines31/marketingskills. 각도·성과 반복·「Grounded Inputs」 원칙을 따르고, 한도는 국내 채널 공식 문서(네이버 광고등록기준, 카카오비즈니스 가이드, Meta 광고 가이드, Google Ads 고객센터)로 다시 확인했다.
