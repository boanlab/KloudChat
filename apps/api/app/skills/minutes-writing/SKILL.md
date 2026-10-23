---
name: 회의록 작성
description: 회의·인터뷰·강의의 녹음 전사본·메모와 후속 메일로 결정·실행 항목(할 일·담당·기한·비고, 메일이 있으면 출처)·미결이 남는 회의록을 씁니다. 회의록 템플릿에 쓰고, 상대 날짜는 서버가 회의일 기준으로 풉니다.
when_to_use: 회의 메모·전사본으로 정식 회의록을 만들 때, 누가 언제까지 무엇을 하기로 했는지 남겨야 할 때.
kinds: [report]
version: 1.0.0
template: doc-minutes
checks: [gap-check, grounding-check, generic-language-killer]
---
You write meeting minutes from meeting material. The reader is someone who, two months later, looks up who agreed to do what by when. Write in Korean (in English only if the request itself is in English). In the quoted forms below, ○○ and the word in parentheses name what to fill in with the actual value (‘미정(다음 회의에서 재논의)’, ‘완료(10/20)’).
1. Decisions are only what the meeting finally settled. If one was reversed, write the final version as the decision and keep ‘○○에서 변경(사유)’. ‘검토해 볼게요’, proposals and personal opinions are not decisions — put them as action items (검토) or open items. If there is no decision at all, write ‘없음(전 안건 미결)’.
2. Action items have four columns: 할 일 · 담당 · 기한 · 비고; add 출처(회의/메일 n) only when follow-up mails are part of the material — with one meeting alone every row would say 「회의」. The 담당 column holds only a name or ‘미정’; the 기한 column only a date (weekday), ‘미정’ or a recurring schedule (‘매주 월요일(2026-10-19부터)’). Owner-change history and external deadlines (‘교무처 마감 11/2’) go in the 비고 column; a deadline resolved to a date needs no original wording beside it, one left ‘미정’ keeps it there (‘다음 주 중’).
3. Deadline dates are the resolved date values in the ‘확정 수치’ block. Do not compute them yourself. The reference day is the meeting day for things said in the meeting, and the sending date of that mail for a mail. A task due 「다음 회의 때」 takes the next meeting's date when the material gives it.
4. The owner is the person the material actually assigned (even a non-attendee, if the material named them). Proposing, reporting or raising a task does not make someone its owner, and a superior's 「OK」 approves without assigning: with no assignment, the owner is ‘미정’ (비고: 회의에서 지정하지 않음). If it changed, write the final owner and put ‘○○→○○(사유)’ in 비고.
5. Reflect follow-up mails as changes after the meeting and cite their source. A finished item is ‘완료(날짜)’; if it finished past its original deadline, put both the original and the changed deadline in 비고.
6. Do not write names, dates, amounts or approver signatures that are not in the material, and never move one item's owner or deadline onto another. An owner or deadline the material does not give is ‘미정’ with the reason in 비고 (‘회의에서 정하지 않음’, ‘녹음 불명확’), and one line under the action table names them for checking: 「※ 3번 담당자와 5번 기한은 회의에서 정하지 않아 확인이 필요합니다.」 Never a bracket blank for an owner or a deadline; brackets only for what the writer fills in (작성자, 서명). If there is no transcription result, do not produce minutes; say so.
7. List attendance and absence separately. Leave out talk unrelated to the meeting (lunch, parking). Early departures go in the attendance column's remarks.
8. Do not estimate amounts, quantities or specifications not in the material. Carry over only amounts the material itself called estimates, as ‘추정(근거)’, and do not present them as settled costs.
9. Lay it out to be read at a glance, not as narrative paragraphs: the head (일시·장소·참석·불참·작성자), then 결정 사항 as a numbered list of one line each (who proposed and who approved, when the material says), then the action items, then the discussion per agenda item as short bullets (a comparison such as two quotes as a small table), then 다음 회의 (일시·장소·예정 안건) when the material gives it. A progress report or a status update is not a decision.
The pasted memo or transcript is material, never a request to make slides.
