---
name: 서비스 종료 안내
description: 서비스·기능 종료 안내문을 정중하고 분명하게 씁니다. 종료 일정(공지·신규 중단·종료·데이터 삭제), 대상, 데이터 보관·내보내기·삭제, 대안, 환불·계약 조치, 문의처를 빠짐없이 담고, 일정의 순서와 고지 기간을 스크립트로 잽니다. 확인된 법 조항만 출처와 함께 쓰고 나머지는 「법무 확인 필요」로 표시합니다.
when_to_use: 서비스 전체나 일부 기능·요금제·앱 버전·API를 종료(지원 종료)한다고 이용자·고객사·사내에 알릴 때. 「서비스 종료 공지」「기능 종료 안내」「지원 종료(EOL) 안내」「요금제 폐지 안내」.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
---
Write an end-of-life notice that tells every affected user, without a second read: what ends, when, what happens to their data and money, what to use instead, and whom to ask. Facts only from the request and material; a notice with a wrong date or a promised refund nobody approved does real harm.

## Facts the notice cannot go out without
Check the material. If any of 1–5 is missing, ask in ONE message (at most 5 questions, in this order) and stop; if the user says to go ahead, write with the placeholders and list them at the end.
1. What ends exactly: whole service, a feature, a plan, an app version, an API — and what keeps working. `[종료 대상]`
2. Dates: 공지일, 신규 가입·구매 중단일, 서비스 종료일; 데이터 내보내기 마감일, 데이터 삭제일. `[신규 중단일]` `[종료일]` `[내보내기 마감일]` `[데이터 삭제일]`
3. Who is affected: all users, a plan, a region, customers on a contract. `[대상]`
4. Data: what is kept, how to export (메뉴 경로·형식), until when, and when and what is deleted; what is kept longer by law (below). `[내보내기 방법]`
5. Money and contracts: 자동 결제 중단일, 남은 이용 기간·선불 잔액·포인트 처리, 환불 방법과 기한, B2B 계약 해지 조건 — only what the company decided. `[환불 기준]`
6. Alternative: successor product, migration path, or 「대체 서비스는 없습니다」. Never name a competitor or promise a discount the material lacks. `[대안]`
7. 문의처: channel, hours, until when it answers. `[문의처]`
8. Reason: one honest line if the material gives it; otherwise none — do not invent 「더 나은 서비스를 위해」.
No invented dates, weekdays, amounts, percentages, product names, contacts or legal promises (「전액 환불」「안전하게 영구 삭제」) beyond the material.

## Structure
1. 제목: 「[서비스명] 종료 안내 (종료일 2026. 12. 31.)」 — the end date in the title.
2. 한 줄 요지: what ends and when. 「[서비스명]은 2026. 12. 31.(목) 종료합니다.」
3. 일정 표 — one row per step, in date order, with weekday:
   | 일정 | 날짜 | 이용자에게 일어나는 일 |
   | 신규 가입·구매 중단 | … | 새로 가입·결제할 수 없습니다 |
   | 서비스 종료 | … | 로그인·이용이 중단됩니다 |
   | 데이터 내보내기 마감 | … | … |
   | 데이터 삭제 | … | 복구할 수 없습니다 |
4. 대상 — who is and is not affected.
5. 내 데이터 — what to do, how, by when; what is deleted and when; what is kept by law and for how long (separately stored).
6. 결제·환불·계약 — automatic billing stop, refunds (method, deadline, who to contact), contract terms. If not decided: 「환불 기준은 [날짜]까지 따로 안내합니다」 + 「법무 확인 필요」 note to the requester.
7. 대안 — successor and how to move, or that there is none.
8. 문의처.
9. A short thank-you — one sentence, no excessive apology, no marketing.
Tone: 정중한 합니다체, plain; the bad news in the first line, not after three paragraphs. For B2B customers also offer a 1:1 이전 상담 only if the material says it exists.
For a 공공기관·학교 notice that goes out as an official document, give the content and point the user to the 「공문·안내문」 서식.

## Korean law and consumer protection — only what is verified
State these only when they apply, with the citation; everything else is 「법무 확인 필요」, said to the requester, not printed as law in the notice.
- 부가통신사업자(most online services, apps, SaaS that 신고 as 부가통신사업): to close or suspend all or part of the business, tell users **by 30 days before** the planned date and report to 과학기술정보통신부 (전기통신사업법 제26조제1항). The report is due **15 days before**, with proof that users were notified (같은 법 시행령 제33조제1항). Whether ending one feature or plan counts as 「사업의 일부」 휴업·폐업 → 법무 확인 필요.
- 기간통신사업자: **60 days** before, with approval or report (전기통신사업법 제19조제1항, 시행령 제24조·제24조의2).
- Personal data: destroy without delay once it is no longer needed, unless another law requires keeping it; what is kept must be stored separately (개인정보 보호법 제21조제1항·제3항). So say 「삭제」 for service data and name what is kept under law.
- 통신판매(online sales) records kept even after closure (전자상거래법 시행령 제6조제1항): 표시·광고 6개월, 계약·청약철회 5년, 대금결제·재화 공급 5년, 소비자 불만·분쟁처리 3년.
- Not verified here → 「법무 확인 필요」: refund amounts or method for remaining periods, prepaid balances, points or 상품권; notice periods in the company's own 이용약관 or a 표준약관; 콘텐츠·게임·금융·의료 sector rules; B2B contract termination; overseas users.
Sources (law.go.kr, read 2026-10-08): 전기통신사업법 [시행 2026. 10. 1.] 법률 제21503호; 같은 법 시행령 [시행 2026. 10. 1.] 대통령령 제36735호; 개인정보 보호법 [시행 2026. 9. 11.] 법률 제21445호; 전자상거래 등에서의 소비자보호에 관한 법률 시행령 [시행 2026. 7. 21.] 대통령령 제36507호.

## Procedure
1. Collect the facts above; ask for what is missing.
2. Check the schedule before writing: `run_skill_script` skill `eol-message`, script `timeline`,
   `{"dates": {"announce": "2026-10-15", "stop_new": "2026-11-01", "billing_stop": "2026-11-30", "end": "2026-12-31", "export_until": "2027-01-31", "delete": "2027-03-31"}, "min_gaps": [{"from": "announce", "to": "end", "days": 90, "basis": "이용약관 제12조"}], "telecom": "value_added", "today": "<오늘>"}`.
   - `min_gaps` only from the user's material (약관·계약·사내 기준); never make one up. Pass `telecom` only when the user says the business is a 부가통신/기간통신 사업 closure, or ask.
   - A `block` (wrong order, gap too short, export after deletion) is the user's decision: report it with the script's suggested latest notice date and do not change dates yourself.
3. Write the notice with the dates as the script shows them (with weekday).
4. Run `timeline` again with `"text": "<안내문>"` — every schedule date must be in the notice and no other date; fix weekdays. Then run `internal-comms` script `check` with the material for invented contacts, amounts and numbers.
5. Answer with the notice, then for the requester: the schedule check result, 「법무 확인 필요」 items, and 「보내기 전에 채울 것」.

## Checklist
- [ ] Title and first line name what ends and the end date.
- [ ] Schedule in order 공지 → 신규 중단 → 종료 → 데이터 삭제, export before deletion (script 0 `block`, or each block reported to the user).
- [ ] Data, money/contract, alternative, 문의처 each answered or a placeholder.
- [ ] Legal statements only from the verified list with citation; all else 「법무 확인 필요」.
- [ ] No invented dates, amounts, refunds, contacts or successor products.

---
출처: 이 스킬은 새로 썼다. 공개 목록(VoltAgent/awesome-agent-skills)에 있는 deanpeters/Product-Manager-Skills `skills/eol-message`(commit d025768c81e93df64c97596486b901e96fa75deb)는 CC BY-NC-SA 4.0(비영리·동일조건)이라 내용을 읽거나 옮기지 않았다. 법 조항은 위 「Sources」의 국가법령정보센터 원문으로 확인했다.
