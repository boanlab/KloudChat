---
name: 릴리스 노트
description: 변경 목록·커밋 로그·이슈 목록을 사용자가 읽는 릴리스 노트(새 기능·개선·수정·알려진 문제·주의)로 묶고 쉬운 말로 다시 씁니다. 입력 항목이 하나도 빠짐없이 노트에 들어갔거나 뺀 이유가 있는지, 버전·날짜가 맞는지, 호환성이 깨지는 변경을 「주의」로 알렸는지 스크립트로 점검합니다. 입력에 없는 기능·수치는 쓰지 않습니다.
when_to_use: 이번 버전에서 무엇이 바뀌었는지 사용자·고객·사내에 알릴 때. 커밋 로그·PR 목록·지라 이슈·변경 메모를 붙이며 「릴리스 노트」「업데이트 내역」「패치 노트」「변경 사항 정리」를 요청할 때.
kinds: [chat]
required_tools: [run_skill_script]
version: 0.1.0
status: draft
post_check: notes
post_judge: "procedures and screens (where to click, what to set), guarantees (「누락 없이」「추가 설정 없음」), example values, endpoints and compatibility claims"
# Kept for bundles, old conversations and editors that name it; not shown in lists.
listed: false
---
Turn a change list into release notes a user reads in a minute: what they get, what got fixed, and what they must do. Every line comes from an input item; nothing is added.

## Inputs
Only the change list is essential; without it, ask for it in one line and stop. Everything else, write from what is given:
1. The change list itself (commit log, PR/issue list, memo).
2. Product name, version (and previous version), release date — as given. A missing release date is written around (「이번 업데이트에서」), not a blank; the version heading is the one the material gives.
3. Audience: 일반 사용자 / 관리자·고객사 담당자 / 개발자(API). This decides how technical the wording is and whether internal items stay out. Write only the versions asked for (웹 공지·앱 스토어 → two), each under its own label (「## 웹 공지용」).
4. For anything that removes or changes behaviour: what users must do, and by when — as the material gives it; with no date, 「일정은 확정되는 대로 안내합니다」.

## Procedure
1. Split the input into items: `run_skill_script` skill `release-notes`, script `notes`, `{"op": "parse", "log": "<원문 그대로>"}`. Each item gets an `id`, a suggested `kind` and section, and a `breaking` flag (removal, rename, migration, `feat!:`, 「호환되지 않음」).
2. Decide each item: include (which section) or omit with a reason. Omit by default only internal work with no user effect (CI, 테스트, 리팩터링, 의존성 갱신 without behaviour change) — say so in the omitted list. Never omit a `breaking` item; if the flag is wrong (e.g. a removed internal helper), mark it `"breaking": false` with the reason.
3. Group and rewrite:
   - Sections, in this order, only those with entries: **새 기능** · **개선** · **수정** · **주의** (호환성 변경·지원 종료·할 일) · **알려진 문제**. Put 주의 first when it has any entry.
   - One entry = 1–2 sentences, user benefit first, plain Korean, no ticket numbers, hashes, code names or internal codenames (keep them only in developer notes).
     「feat(export): PDF 내보내기에 페이지 번호 추가」 → 「PDF로 내보낼 때 쪽 번호가 붙습니다.」
     「fix: 엑셀 업로드 시 날짜가 하루 밀리던 문제」 → 「엑셀 파일을 올리면 날짜가 하루씩 밀리던 문제를 고쳤습니다.」
   - Several items about one feature may become one entry with several `sources`.
   - Only what the item says: 「대시보드 쿼리 최적화」 becomes 「대시보드가 더 빨리 열립니다」 at most — never 「3배 빨라졌습니다」 or 「40% 단축」 unless the item has that figure. No added causes, scope, platforms or dates.
   - 주의 entries say what changes, who is affected, and what to do: 「v1 보고서 API(/api/v1/reports)가 2026년 12월 31일에 종료됩니다. 그 전에 /api/v2/reports로 옮겨 주세요.」 — the old and new endpoint and the end date as the material gives them, in the customer version too. When a policy changes from the release on (청구일 1일 → 3일), work out with date-calc the first date it applies (배포 11/4 → 12월 청구분부터) and write that date.
   - Add no restriction, permission rule or 「안내하지 마세요」 the change list does not state.
   - 알려진 문제 only from items the user labels as known/open issues; with a workaround only if given. No such items → no 알려진 문제 section.
   - An app-store version fits the store's limit (Google Play 「이번 버전의 새로운 기능」 500자).
4. Check: script `notes`, `{"op": "check", "items": <parse 결과 items>, "entries": [{"section": "수정", "text": "...", "sources": ["#102"]}], "omitted": [{"id": "1234abc", "reason": "내부 CI 설정"}], "version": "2.5.0", "previous_version": "2.4.3", "date": "2026-10-20", "notes": "<완성한 노트 전문>"}`.
   An entry that writes a date worked out from the material (the first-apply date of step 3) lists it in `"derived": ["2026-12-03"]`; a deprecation announced ahead (「… 종료 예정」) is a notice, not a breaking change, and still goes in 주의 with what to do.
   Fix every `block` (missing item, entry without source, invented figure, breaking change not in 주의 with an action, version/date mismatch); answer each `check` (major version not bumped despite a breaking change → ask, do not change the version yourself). Run again until 0 `block`.
5. For product-wide announcements to customers, also run `internal-comms` script `check` with the material for invented contacts and dates.

## Output format
```
# 한빛메모 2.5.0 (2026. 10. 20.)

## 주의
- …할 일과 기한…

## 새 기능
- **PDF 쪽 번호**: …

## 개선
- …

## 수정
- …

## 알려진 문제      ← only when the change list names one
- …
```
The answer is the notes only. Do not add a 「뺀 항목」 list or the script's coverage line (「항목 5개: 노트 4 · 뺌 1 · 빠짐 0」) under them: the script's step shows both to the person who asked.

## Checklist
- [ ] Every input item is in the notes or left out for a reason the script reports (0 빠짐).
- [ ] No feature, figure, platform or date that is not in an item.
- [ ] Breaking changes in 주의 with what to do; version bump question raised if needed.
- [ ] Version and date the same everywhere; plain language for the chosen audience.

---
출처: phuryn/pm-skills `pm-execution/skills/release-notes` (MIT, © 2026 Pawel Huryn), commit a372bee16dc2275e26078ca70a2eb7614ea316f7, https://github.com/phuryn/pm-skills. 분류(새 기능·개선·수정·호환성 변경·지원 종료)와 「기술 변경 → 사용자 이익」 다시 쓰기 원칙을 따르되, 원본 예시의 근거 없는 「3× faster」 같은 수치는 금지로 바꿨다. 항목 대조·버전·날짜·호환성 점검 스크립트는 새로 썼다.
