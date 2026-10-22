---
title: Working with skills and building agents
---

# Working with skills and building agents

Jobs such as minutes, a bullet-style briefing (개조식 보고) or comparing options are done with **official skills**. Turn a skill on in a conversation and it does that job; for work you do often, combine a few skills into an agent of your own. There are no separate built-in agents.

## Official skills

Skills the administrator shares with everyone. They show under **Official skills** in the composer's **+** → **Skills**; picking one adds it to your skills and turns it on.

| Skill | Screen | What it does |
|---|---|---|
| Meeting minutes (회의록 작성) | Report | Keeps decisions, action items (task · owner · due date · source · note) and open items from a transcript, notes and follow-up mails. Writes in the **회의록** template. A meeting length, period, time of day, question, guess, next-meeting agenda or promise to tell more later the material does not give, an open point the memo never left open (「담당자가 지정되지 않았습니다」, 「배포 방법은 확인 필요」), advice (「…하는 것이 좋습니다」), what follows an undone task (「미제출 시 …」), a next-meeting agenda table, a claim that everything is set beside a 미정 row, and an action row nobody was given, go back for one fix; what remains is taken out or marked 「(확인 필요)」. A 「오전」 put before the memo's bare 「10시」 is dropped by code. A reason the material does not give (「비용 절감 차원에서」), a verdict like 「알 수 없습니다」 and a note on how the minutes were written (「날짜를 해석하는 기준일은」) also go back for a fix and are taken out or marked if they stay. A day, time, place or attendance the memo gives but the minutes call missing (「자료에 없음」, 「미정」) is filled from the memo or the claim removed, an empty 회의명, 장소 or 참석자 in the head takes the memo's value (else a blank such as 「[장소]」), and a memo decision missing from the decision table gets a row. The meeting held is told in the past tense, and prose calling a deadline open that the action table dates from the memo takes the table's date. |
| Bullet-style briefing (개조식 보고) | Report | Turns notes and mails into a one-page □ ○ - ㆍ briefing a superior can decide from. **개조식 보고서** template, one page by default. |
| Compare-and-decide report (비교·결정 보고) | Report | Weighs 2–5 options on exactly the criteria asked and recommends one. **비교·분석 보고서** template. |
| Official document draft (공문·기안 초안) | Report | Drafts plans, result reports, approval drafts and notices. No web search; pictures only when asked. |
| Presentation deck (발표 자료 구성) | Slides | One claim per slide, the conclusion first. |
| Extract and tally (자료 추출·집계) | Chat | Reads Excel and CSV with code, pulls columns, totals by item into tables and charts, and records the rows it removed or changed. |
| Answers from regulations (규정 근거 답변) | Chat | Answers from regulations and guidelines with the article cited as [n], and points what the material does not cover to the department. |
| YouTube transcript and summary (YouTube 영상 전사·요약) | Chat | Gets the captions (or a transcription of the audio) through the **YouTube 전사** connector and summarises from that alone. |
| Deep research (심층 조사) | Chat | Searches the web and papers many times through the **심층 리서치** connector and sums up the sourced findings. Takes a few minutes. Saying 「심층 리서치로」 in the request runs the detailed mode (5–15 minutes), and the step shows which mode ran (quick or detailed). |
| Translation (번역) | Chat, Report | Translates between Korean and English (other languages on request) without leaving anything out. Names, numbers, units, tables, lists and code stay as they are; one term stays one term throughout; only genuinely ambiguous spots get a 「번역 메모」 (translator's note). Ask for 「직역」 (literal) or 「의역」 (free), 「격식체」 (formal) or 「평어체」 (plain) and it follows. |
| Press release (보도자료) | Chat, Report | A press release with headline, subheadline, a who-what-when-where-why lead, the body most important first, the organisation's boilerplate and contact. Quotes only words the material gives to a named person (a name alone gets a quote draft marked 「가안」), and no figures the material does not give. Only the release date, embargo and contact are left as brackets for you to fill. Give a length (「A4 1장」, 「800자 이내」) and it keeps to it. |
| Make an Excel file (엑셀 파일 만들기) | Chat | Turns a pasted table, an attached CSV or Excel file, or a computed table into a downloadable Excel (.xlsx) file. Total rows and columns such as totals or rates go in as formulas; on request it adds an Excel chart too. |
| Make a chart (차트 만들기) | Chat | Draws a bar, line or stacked bar chart from a table's numbers beside the conversation. Only values in the material or computed by code. |

## Other skills, and what a skill brings

There are also Date calculation (with the **시간** (time) connector it reads today's date from there), Quote calculation, Excel cleanup, PDF table → Excel, Grounding check, Ask for missing information, Calculation and unit check and Mask personal information.

A skill brings its template, checks and missing-information questions. Turn on **Meeting minutes** on the report screen, for instance, and the template becomes **회의록** (if you picked another template, it writes in that one), it asks for the meeting date and attendees before writing, and checks sources and format afterwards. A skill you turn on stays on for the rest of the conversation, approving the outline included.

## Using a skill in a conversation

1. Choose **Report** on the home screen.
2. In the composer choose **+** → **Skills** → **회의록 작성**. The template chip shows **회의록**.
3. Paste the meeting notes or transcript and send 「정식 회의록으로 만들어 줘」.

Skills turned on in chat also go along when you ask for a document there and it moves to a report or deck. Skills that need tools cannot run on that screen and are left out, with a notice naming them.

Use **개조식 보고** (「과장님께 올릴 1쪽 보고로」) and **비교·결정 보고** (「세 제품을 연간 비용과 필수 조건으로 비교해서 하나 골라 줘」) the same way.

## Translation, press releases, Excel files and charts

- **번역** (Translation): turn it on in a conversation, paste the text and send something like 「영어로, 의역, 격식체로」 (into English, free, formal). To fix terms, add them: 「용어: 학생지원팀 = Student Support Office」. To translate a whole document as a document, turn it on on the report screen; it follows the source's headings and order and adds no summary or recommendation.
- **보도자료** (Press release): paste the event, agreement or award material and send 「보도자료로, A4 1장」. If the material has no quote, it writes no quote paragraph and says so at the end. When only a spokesperson's name is given (a CEO, say), it drafts one quote for that person from the material's purpose and plan and marks it 「(가안, 본인 확인 필요)」. On the report screen, say 「보도자료」 in the request and the page head gets the release date, embargo and contact fields.
- **엑셀 파일 만들기** (Make an Excel file): paste a table or attach a CSV or Excel file and send 「엑셀 파일로, 합계 행과 증감률 열은 수식으로」 (as an Excel file, total row and change-rate column as formulas). The file appears under the answer as a download button. For a messy sheet, turn on **엑셀 정리** (Excel cleanup) too; for aggregates, **자료 추출·집계** (Extract and tally), and you get the computed table as a file. Code execution (the sandbox) must be on.
- **차트 만들기** (Make a chart): paste a table and send 「지점별 월 매출을 막대 차트로」 (monthly sales by branch as a bar chart); the chart opens beside the conversation. To put the chart inside the Excel file too, turn on **엑셀 파일 만들기** as well and add 「엑셀 차트도」.

## Before the meeting: the agenda

The **회의 안건** (Meeting agenda) template is the minutes' counterpart. On the report screen choose **+** → template picker → **회의 안건** (or say 「회의 안건」 or 「안건지」 in the request) and send the items and their owners. It writes the same head as the minutes (meeting, date and time, place, attendees, author), an agenda table (order · item · owner · time · goal), points to review per item, pre-reads and the decisions needed. The goal is one of 보고 (information), 논의 (discussion) or 결정 (decision); an owner the material does not name is 「미정」 with one line saying what to confirm. Without a meeting date or attendees in the material, it asks before writing.

## Building an agent from skills

In **Customize** → **Agents** → **Add** → **Create an agent**, skills you add with **+ Add a skill (store included)** under **Skills and tools** › **Skills** and set to **Always** are on in every conversation with that agent (up to six). The list shows your skills and the store's; **Install and add** copies a store skill into your skills. To start from one skill, press **Make an agent from this skill** on its page. Setting a skill that works only on reports or slides to **Always** sets the agent's first screen to that screen. Each skill's template, checks and missing-information questions come with it; a template or detailed settings set on the agent itself come first. A skill set to **When needed** is not added every turn; the model pulls it in when it needs it. With **Choose** only the listed skills are used, and only these can be picked in the composer ([An agent's skills](agents#an-agents-skills)).

## A minutes agent

| Field | Value |
|---|---|
| Name | Our team's minutes |
| Instructions | 작성자는 「기획팀 [이름]」으로 적는다. 실행 항목은 담당 팀 순서로 정렬한다. (Only your team's specifics; the skill knows how to write minutes) |
| Always | 회의록 작성 |
| Extra work | Prepare before writing · Review after writing · Finish (export a file when done: HWPX) |
| Template & theme › Theme | 문서용 명조 |
| Advanced › Screens | Report |

## A briefing agent

**개조식 보고** set to Always, theme **문서용 명조**, screen **Report**. Put only what never changes in the instructions, such as the addressee (「시설과장님」) and the approval line.

## A purchase review agent

**비교·결정 보고** + **견적 계산** set to Always, screen **Report**. Write your organization's criteria in the instructions (「판단 기준은 3년 총비용, 유지보수, 도입 일정 순서」) so no request has to repeat them.

## A tally agent

**자료 추출·집계** set to Always; under Skills and tools › Tools, **파일 작업**, **차트 그리기** and **수치 계산**; screen **Chat**. Upload a spreadsheet and ask 「부서별 합계와 비율을 표로」; it first asks for the deadline, period or denominator basis when the material does not give it.

## A regulations chatbot

1. Create an agent with **규정 근거 답변** + **날짜 계산** set to Always.
2. Upload the regulation files under **Sources** and fill in **Department and contact**.
3. Under **Extra work**, tick **공개 전 점검** (the pre-publication check: hold answers without grounds · point to the department · mask personal information).
4. Try it, then [publish it as a chatbot](bots.md). The bot answers with the skills' instructions frozen into the published version.

## Video and research agents

- **Lecture video notes**: **YouTube 영상 전사·요약** set to Always, screen **Chat**. Write the shape you want in the instructions (「핵심 다섯 줄과 용어 풀이 표」). The **YouTube 전사** connector must be on.
- **Trend research**: **심층 조사** + **근거 대조** set to Always, screen **Chat**. The **심층 리서치** connector must be on, and one run takes a few minutes.

## Making the same as your own skill

You can make a skill like the official ones yourself. In **Customize** → **Skills** → **Add**, write the steps, and under **Server checks** choose **Template to write in**, **Checks it brings**, the strictness, **Web search** and **Pictures**, and **Missing-information questions**. In an uploaded SKILL.md, put `template`, `checks`, `check_options`, `questions` and `process` in the front matter.
