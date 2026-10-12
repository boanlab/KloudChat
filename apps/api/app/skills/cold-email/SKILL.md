---
name: 콜드메일
description: 국내 B2B 잠재 고객에게 보내는 첫 메일과 이미 만난 고객에게 보내는 후속 메일을, 요청한 통수만 짧고 사람답게 씁니다. 조사한 사실 하나를 첫 문단에 쓰고, 사례·성과는 자료에 있는 것만 쓰며, 정보통신망법 확인(수신 동의·「(광고)」·수신거부)은 끝에 「보내기 전에」 한 줄로 알립니다.
when_to_use: 영업·제휴·채용 목적으로 처음 연락하는 메일, 후속(팔로업) 메일, 메일 머지 템플릿을 쓸 때. 「콜드메일」「아웃바운드 메일」「영업 메일 시퀀스」.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
copy_check: true
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Write B2B emails a Korean decision-maker would actually answer: short, about their situation, one low-friction ask. Only facts from the request, the material and what you looked up.

## Scope
- Write exactly the emails asked for: 「첫 메일 한 통」 is one email, 「제목 3안이랑 본문 1안」 is three subjects and one body. A sequence (1차·2차·3차) only when the person asks for one.
- A mail to someone the sender already met, called or sent material to is a **follow-up**, not a cold email: refer to that contact (date, what was sent) in the first lines, add one new thing, and write one mail — no sequence.

## Inputs
Write right away from what the request and material give. Ask (one message, at most 2 questions) only when the mail cannot be written at all — no recipient, or no idea what is offered. Otherwise:
1. Recipient (직무·회사) and why them. When a search tool is available and the person asks for research, look the company up and put **one** confirmed fact in the first paragraph (「서울 본사와 오송·향남 공장이 떨어져 있어」), with its source (매체·날짜·링크) under the mail in a short 「조사 메모」. What you could not confirm (망분리 운영 여부) is written as a condition (「~한 환경이라면」), never as a fact. With no fact to use, open with the recipient's role and the problem, and say in the memo that the opener is general.
2. The ask: what the request offers (미팅, 체험, 자료 송부). Two offers in the request → both in the mail as one easy choice.
3. Proof: 사례·성과·고객사 only as the material gives them; otherwise no proof sentence.
4. Sender: name, team and company as the material gives them. Contact details the material does not give stay as `[전화번호]` `[이메일]` — the only brackets in the mail, since the sender fills them in.

## Writing
- Subject: short and plain, like a colleague wrote it (권장 30자 이내): 「정산 마감 일정 관련」, 「{{회사}} 출고 검수 건」. No 「RE:/FW:」 tricks, no 「긴급」「무료!!」, no emoji.
- Opening: their world, not ours — the researched fact or the follow-up context. Not 「바쁘신 와중에 실례가 많습니다」 + company intro.
- Body 3–5 short paragraphs, under about 500자: observation → the problem it usually brings → what we do (the material's capabilities only) → proof only if in material → one ask.
- Ask: one, easy to answer: 「다음 주 중 20분 통화가 괜찮으실까요?」 or 「관련 자료를 보내 드려도 될까요?」 — not a 1-hour meeting.
- Tone: 정중한 합니다체, 과장 없는 동료 어투. Avoid 「귀사의 무궁한 발전」, 「혁신적인 솔루션」, 「업계 최고」, 「단순한 ~가 아닙니다」.
- Describe the product only with the capabilities in the material; no added mechanics (「주문번호로 배송일 안내」) or effects (「상담이 필요한 문의만 남습니다」). No numbers, conditions or offers the material lacks (「10분이면」); proposing a call length is fine. Do not invent material that was sent or will be sent (a demo video, a case study).
- Personalization variables (`{{이름}}`, `{{회사}}`) only for a mail-merge template the person asks for.
- When a sequence is asked for, each follow-up adds something new (다른 문제 각도, 자료, 짧은 질문), never only 「확인차 연락드립니다」, and each works alone.

## 보내기 전에 (one line at the end)
The law check is not part of the mail. No warning header, no 「광고성 정보: 예」 line above the mail, no (광고) in the subject and no opt-out footer unless the person asks for a labelled version. End the answer with one line:
「보내기 전에: 수신 동의(또는 6개월 안 거래관계)를 확인하고, 광고성 메일로 보낸다면 제목 앞 「(광고)」, 회사 정보(명칭·이메일·전화·주소), 수신거부 문구와 링크를 넣으세요.」
For a follow-up to someone already met, the line says the person decides whether it is advertising (an ongoing deal is usually not cold advertising, but promotion of a new product can be).
Background for that line (정보통신망법 제50조, 시행령 제61조·별표 6, KISA 「불법스팸 방지를 위한 정보통신망법 안내서」 제7차 개정본): an email promoting the sender's product for profit is 광고성 정보, B2B included, and needs prior consent unless the contact came from an actual deal within 6 months for the same kind of goods; a standalone consent-request email is itself advertising. When the person asks for the labelled version, write it in full: 제목 맨 앞 `(광고)` exactly, 본문 끝 전송자의 명칭·전자우편주소·전화번호·주소, and a 수신거부 문구 with a link (Korean; add the English line 「If you do not wish to receive this email, click [Unsubscribe].」 when they ask for the full legal footer). When the person says there is no consent, say once, plainly, that it cannot be sent as advertising, and still write the draft if asked.

## Procedure
1. Draft the mail(s) asked for.
2. Check: `run_skill_script` skill `cold-email`, script `check`, args
   `{"advertising": false, "sender": {"name": "한빛소프트"}, "emails": [{"label": "본문", "subject": "...", "body": "..."}]}` (`"advertising": true` only for a labelled version the person asked for). Fix every `block` (empty subject, fake RE:, unfilled variables) and run once more.
3. Run `copy-check` script `claims` with the material; remove invented numbers, cases, rankings.

## Output format
```
### 제목 (택1)          ← only as many as asked
1. …
### 본문
…
김현수 드림
한빛소프트 영업팀 | [전화번호] | [이메일]
### 조사 메모            ← only when something was looked up: the fact used, its source, what was not confirmed
보내기 전에: …          ← one line
```

## Checklist
- [ ] Exactly the number of mails and subjects asked; a follow-up is one follow-up.
- [ ] One researched fact in the first paragraph, its source in the memo; unconfirmed things written as conditions.
- [ ] No invented cases, client names, % results, prices, materials sent.
- [ ] One ask; under about 500자; no warning header; the law check is the one 「보내기 전에」 line.

---
출처: coreyhaines31/marketingskills `skills/cold-email` (MIT, © 2025 Corey Haines), commit b9ba399dd88b082b926e261e8ccfb843d20aa066, https://github.com/coreyhaines31/marketingskills. 쓰기 원칙(동료 어투, 개인화가 문제로 이어짐, 한 가지 요청, 새 각도의 후속)을 따르고, 법 요건은 정보통신망법 제50조·시행령 제61조 별표 6(https://www.law.go.kr, 시행 2026-10-02)과 KISA 안내서 제7차 개정본(https://spam.kisa.or.kr/spam/na/ntt/selectNttInfo.do?mi=1020&bbsId=1002&nttSn=3001)으로 확인했다.
