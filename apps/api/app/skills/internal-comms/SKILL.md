---
name: 사내 공지·안내
description: 조직 안 공지·제도 변경 안내·시스템 점검 안내·주간 진행 보고(3P)·자주 묻는 질문을 사내 메일·메신저·공문체로 씁니다. 한 줄 요지부터 바뀌는 것·대상·시점·할 일·문의처 순서로 쓰고, 날짜·요일·연락처·수치가 자료와 맞는지 스크립트로 점검합니다. 자료에 없는 일정·담당자·연락처는 빈칸으로 두고 묻습니다.
when_to_use: 전 직원·특정 부서에 알리는 공지, 제도·규정·근무 방식 변경 안내, 시스템 점검·이전 안내, 팀 주간 보고(Progress·Plans·Problems), 사내 FAQ를 쓸 때. 「공지문 써 줘」「변경 안내 메일」「점검 공지」「주간 3P」.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
post_check: check
post_judge: "promises of a follow-up notice, procedures, contacts, deadlines and who must do what"
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Write internal announcements a busy employee understands from the first line and can act on without asking back. Use only what the request and material say.

## Decide first
1. Type: 공지·안내 (`notice`), 제도·규정 변경 (`change`), 시스템 점검·중단 (`maintenance`), 주간 진행 보고 (`3p`), 자주 묻는 질문 (`faq`), other (`general`).
2. Register — ask once if unclear, default 사내 메일:
   - 사내 메일 (`email`): 제목 line + body, 합니다체, 5–15 lines.
   - 사내 메신저 (`messenger`): first line ≤ 60자, then 3–6 short bullet lines, 해요체 or 합니다체 matching the request; no 「끝.」.
   - 공문체 (`official`): 개조식, 「~함/~바람」 or 「~합니다」, ends with 「  끝.」. For a document that goes through 결재 or out under the organisation's name (공공기관·학교·협회), do not lay out the official form in chat: tell the user to make it with the 「공문·안내문」 서식 (수신·시행·담당, 근거→내용→협조 요청→붙임) or, for an internal approval, the 「간이기안문」 서식, and give them the content blocks to paste.
3. Audience: everyone, a department, or specific people. Write for the least-informed reader in it.

## Facts a notice cannot go out without
Before writing, check the material for these. If any is missing, ask in ONE message (at most 4 questions, most important first) and stop; if the user says to go ahead, write with the placeholder and list the blanks at the end.
- What changes (전 → 후) — `[변경 내용]`
- Who it applies to — `[대상 부서]`
- When: 시행일 / 점검 일시 with start and end time — `[시행일]`, `[점검 일시]`
- What readers must do, and by when (or that nothing is needed) — `[할 일·기한]`
- Where to ask: 부서·담당자·내선·메일 — `[문의처]`
- For maintenance: what will not work during it — `[영향 범위]`
Never fill these from general knowledge: no invented dates, weekdays, times, names, departments, extension numbers, emails, URLs, versions, amounts or headcounts. 「다음 주」「내일」 in the material become a date only if the base date is known (use `date-calc`); otherwise keep the material's words (「다음 주 중」).

## Structure (notice / change / maintenance)
1. 한 줄 요지 — what, for whom, from when, in one sentence: 「11월 1일부터 출장비 정산은 새 경비 시스템에서만 받습니다.」
2. 무엇이 바뀌나 — before → after, one item per line. Why, in one line, only if the material gives the reason.
3. 누가·언제 — 대상, and for a rule or system that changes from a day on 시행일; for something with a start and an end (공사·점검·배포·행사) its 일시 from start to end (「10. 23.(금) 19:00~10. 25.(일) 18:00」), never 「시행 … 부터」. 경과 조치 if any.
4. 해야 할 일 — numbered steps with deadline; if nothing: 「별도로 하실 일은 없습니다.」
5. 문의처 — exactly as in the material.
Write only the blocks this kind of notice needs; a block the material has nothing for is left out (a missing 문의처 stays one 「[문의처]」 under 「보내기 전에 채울 것」).
Optional: 붙임/링크 (only those given), 자주 묻는 질문 2–4개 when the change raises obvious questions — answers only from the material; otherwise 「정해지는 대로 다시 안내드리겠습니다」.
- Dates as 「2026. 11. 1.(일)」 or 「11월 1일(일)」, times as 「02:00~06:00」; one format through the notice.
- Lead with the reader's action, not the background. No 「~관련하여 알려드립니다」 openers, no 「많은 관심 부탁드립니다」, no apology for a routine change.
- Bad news and inconvenience: say it plainly once (「점검 시간에는 메일을 보낼 수 없습니다」), then what helps.

## 3P (Progress · Plans · Problems)
Ask for the team name and period if missing. Format, nothing else:
```
[팀 이름] ([기간])
Progress: 1–3 sentences — what shipped or was decided, with the figures in the material.
Plans: 1–3 sentences — the top priorities for the next period.
Problems: 1–3 sentences — what is blocking, and what help is needed. 「없음」 if none.
```
Readable in under a minute. Labels may be 「진행·계획·문제」 if the user writes in Korean. No emoji unless the user's team uses them.

## FAQ
`Q:` one sentence / `A:` 1–2 sentences from official material. If the material does not answer it, write 「확인 중입니다. 정해지는 대로 다시 안내드리겠습니다.」 and flag questions that need a decision by management.

## Check (always, before answering)
`run_skill_script` skill `internal-comms`, script `check`:
`{"text": "<초안>", "material": "<요청·자료·답변 원문 그대로>", "kind": "change", "register": "email", "today": "<오늘 날짜>"}`
- `block`: a value not in the material (date, time, phone/내선, email, URL, version, number) or a date whose weekday is wrong. Replace with the material's value or the placeholder; never keep it.
- `check`: relative dates, a missing part (`questions` gives the question and placeholder), register slips, a first line too long.
- `info`: placeholders still open — list them under 「보내기 전에 채울 것」.
Fix, run once more. The check is yours, not the reader's: no 「점검 결과」 paragraph, counts or 「요일이 달력과 일치합니다」 under the notice — only 「보내기 전에 채울 것」 when something is still open.

## Output format
```
**제목** 정기 점검으로 11/8(토) 그룹웨어 사용 중지          ← 메일일 때만; 요지를 짧게, 대괄호 없이

안녕하세요, 정보화팀입니다.          ← 인사와 보내는 부서 한 줄(공지·메일 모두)

11월 8일(토) 22시부터 24시까지 그룹웨어를 쓸 수 없습니다.          ← 한 줄 요지

**무엇이 바뀌나**
- …
**대상·시행**                        ← 공사·점검·배포는 **대상·일시**, 「일시: 시작~끝」
- 대상: …
- 시행: 2026. 11. 1.(일)부터
**해야 할 일**
1. … (기한: …)
**문의** …

감사합니다.
정보화팀 드림          ← 맺음과 발신 부서(자료의 부서명; 없으면 [발신 부서])

---
보내기 전에 채울 것: [시행일], [문의처]
```

## Checklist
- [ ] First line alone tells what, who, when.
- [ ] Every date, weekday, time, contact and number is from the material (script: 0 `block`).
- [ ] Action and deadline stated, or 「별도로 하실 일은 없습니다」.
- [ ] 문의처 present or `[문의처]` flagged.
- [ ] Opens with a greeting and the sending team, closes with a sign-off and sender line; the template's example wording (점검·정보화팀) is replaced by the material's, and the title carries no brackets (r7 A14-T1·T2).
- [ ] 공문 for 결재·대외 발송 → pointed to the 「공문·안내문」/「간이기안문」 서식.

---
출처: anthropics/skills `skills/internal-comms` (Apache License 2.0, © Anthropic), commit 683bc88e56f3e09ba94f7055977f3d3aa499f202 (해당 폴더 최종 변경 b9e19e6f44773509fbdd7001d77ff41a49a486c1), https://github.com/anthropics/skills. 3P 형식(Progress·Plans·Problems 각 1–3문장, 1분 안에 읽힘), FAQ 문답 형식과 답변 원칙(공식 자료 기준·불확실하면 밝힘·경영진 판단이 필요한 질문 표시), 일반 공지 원칙(대상·목적·어조 확인, 중요한 것 먼저)을 한국어 사내 공지 구조로 옮기고 고쳐 썼다(Apache-2.0 제4조에 따라 변경 사실을 밝히고 라이선스 전문은 `resources/LICENSE-anthropics-skills.txt`). 공지 필수 요소·말투 구분·점검 스크립트는 새로 썼다.
