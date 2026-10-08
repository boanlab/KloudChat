---
name: 콜드메일
description: 국내 B2B 잠재 고객에게 보내는 첫 메일과 후속 메일을 짧고 사람답게 씁니다. 광고성이면 정보통신망법 표기(제목 「(광고)」, 전송자 정보, 한·영 수신거부)를 넣고, 수신 동의·개인화 변수·빈칸을 스크립트로 점검합니다. 사례·성과는 자료에 있는 것만 씁니다.
when_to_use: 영업·제휴·채용 목적으로 처음 연락하는 메일, 후속(팔로업) 메일, 메일 머지 템플릿을 쓸 때. 「콜드메일」「아웃바운드 메일」「영업 메일 시퀀스」.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
---
Write B2B first-touch emails a Korean decision-maker would actually answer: short, about their situation, one low-friction ask. Only facts from the request/material.

## Inputs
Needed; ask in ONE message (at most 3 questions) if any is missing and cannot be read from the material, then stop — or, if the user says to proceed, write with placeholders:
1. Who receives it (직무·회사 유형) and why them (a signal: 채용, 이전, 신사업, 수상, 글·발표). Without a signal, say the email will be generic and leave `[최근 소식]`.
2. What the sender wants (회신, 15–30분 통화, 데모, 제휴) and what is offered.
3. Proof allowed: 사례·성과·고객사 only if the material has them (with consent to name). Otherwise no proof sentence, or `[공개 가능한 사례]`.
4. Sender details for the footer (회사명·담당자·전자우편주소·전화번호·주소) — placeholders if missing.
5. Consent: did recipients agree to receive marketing email, or are they contacts from an actual deal within 6 months? (명함 교환·공개 대표메일·크롤링은 동의가 아님)

## Is it 광고성 정보? (정보통신망법 제50조, 시행령 제61조·별표 6, KISA 「불법스팸 방지를 위한 정보통신망법 안내서」 제7차 개정본)
- An email promoting the sender's product/service for profit is 영리목적 광고성 정보 — B2B recipients included; there is no business-address exemption.
- Then it requires prior consent (제50조①). Exception: contacts collected directly in a transaction, for the same kind of goods, within 6 months after it. If there is no consent, tell the user plainly that sending is not allowed, and suggest getting consent where it is legitimate (전시회 부스·상담 때 서면 동의, 웹 문의 양식의 수신 동의 항목) or replying only to an inquiry the person made. A standalone consent-request email is itself 광고성 정보 (KISA 안내서 Q&A), so do not propose one as the workaround; any doubt → 법무 확인. Write the draft when the user asks anyway, with the warning on top.
- Advertising email must have:
  - 제목 맨 앞 `(광고)` exactly — no spaces, symbols or tricks (「(광/고)」「[광 고]」 금지).
  - 본문: 전송자의 명칭·전자우편주소·전화번호·주소.
  - 수신거부·수신동의 철회 안내문 **in Korean and English**, plus a one-click technical means (link/button), and no login or extra data required to opt out.
  - Example footer:
    `[회사명] | [담당자] | [이메일] | [전화번호] | [주소]`
    `수신을 원하지 않으시면 [수신거부]를 눌러 주세요. If you do not wish to receive this email, click [Unsubscribe].`
- Email is exempt from the 21:00–08:00 separate-consent rule, but sending in business hours is still better.
- Partnership/joint-event proposals that do not sell the sender's goods may not be advertising; if unsure, say so and recommend the safe form (표기 포함).
- Penalties are real (과태료 최대 3,000만 원, 2026-03-31 신설 과징금) — mention once when relevant, not as a threat.

## Writing
- Subject: short and plain, like a colleague wrote it (권장 30자 이내, before `(광고)` counts too): 「정산 마감 일정 관련」, 「{{회사}} 출고 검수 건」. No 「RE:/FW:」 tricks, no 「긴급」「무료!!」, no emoji.
- Opening: their world, not ours. Use the signal: 「지난달 성수동으로 사무실을 옮기셨다고 들었습니다.」 Not 「바쁘신 와중에 실례가 많습니다」 + company intro.
- Body 3–5 short paragraphs, under about 500자: observation → the problem it usually brings → what we do (one sentence) → proof only if in material → one ask.
- Ask: one, easy to answer: 「다음 주 중 20분 통화가 괜찮으실까요?」 or 「관련 자료를 보내 드려도 될까요?」 — not multiple CTAs, not a 1-hour meeting.
- Tone: 정중한 합니다체, 과장 없는 동료 어투. Avoid 「귀사의 무궁한 발전」, 「혁신적인 솔루션」, 「업계 최고」, 「단순한 ~가 아닙니다」.
- Personalization variables: `{{이름}}`, `{{회사}}`, `{{최근소식}}`. Write a fallback line for recipients missing a variable, and list who is missing what.
- Describe the product only with the capabilities in the material; no added mechanics (「주문번호로 배송일 안내」「기존 채팅창 그대로」) or effects (「상담이 필요한 문의만 남습니다」).
- No numbers, conditions or offers the material lacks (「50평 안팎이면 바로 일정」「품목당 몇 분」「10분이면」); proposing a call length (「20분 통화」) is fine.
- Follow-ups (2–3): each adds something new (다른 문제 각도, 자료, 짧은 질문); never only 「확인차 연락드립니다」. Last one closes politely (「이번이 마지막 연락입니다」). Each must work alone. Every advertising email — first and each follow-up — carries the `(광고)` subject and the full footer written out; never 「(footer 동일)」 or one shared footer at the end.

## Procedure
1. Decide advertising yes/no/unsure and consent status; state them in one line at the top.
2. Draft first email + follow-ups.
3. Check: `run_skill_script` skill `cold-email`, script `check`, args
   `{"advertising": true, "consent": "none", "sender": {"name": "스톡웨이"}, "emails": [{"label": "1차", "subject": "...", "body": "..."}], "recipients": [{"이름": "김하린", "회사": "오렌지랩", "최근소식": "..."}]}`.
   Fix every `block` (missing (광고), sender info, Korean/English opt-out, fake RE:, unfilled variables) and run once more; report the `consent` finding to the user.
4. Run `copy-check` script `claims` with the material; remove invented numbers, cases, rankings.

## Output format
```
> 광고성 정보: 예 / 수신 동의: 없음 → 아래 경고 확인
### 1차 메일
**제목** (광고) …
**본문**
…
(footer)
### 2차 메일 (n일 뒤) …
### 수신자별 확인
| 수신자 | 빠진 변수 | 처리 |
### 보내기 전에
- [ ] 수신 동의 / 거래관계 확인  - [ ] [수신거부] 링크 연결  - [ ] 빈칸 채우기
```

## Checklist
- [ ] Advertising mail: `(광고)` first, 명칭·이메일·전화·주소, 한·영 수신거부 + link; consent addressed.
- [ ] No invented cases, client names, % results, prices.
- [ ] One ask; under about 500자; opener is about the recipient.
- [ ] Variables filled or fallbacks given; follow-ups each add something.

---
출처: coreyhaines31/marketingskills `skills/cold-email` (MIT, © 2025 Corey Haines), commit b9ba399dd88b082b926e261e8ccfb843d20aa066, https://github.com/coreyhaines31/marketingskills. 쓰기 원칙(동료 어투, 개인화가 문제로 이어짐, 한 가지 요청, 새 각도의 후속)을 따르고, 법 요건은 정보통신망법 제50조·시행령 제61조 별표 6(https://www.law.go.kr, 시행 2026-10-02)과 KISA 안내서 제7차 개정본(https://spam.kisa.or.kr/spam/na/ntt/selectNttInfo.do?mi=1020&bbsId=1002&nttSn=3001)으로 확인했다.
