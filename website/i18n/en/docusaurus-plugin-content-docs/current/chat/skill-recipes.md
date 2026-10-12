---
title: Working with skills and building agents
---

# Working with skills and building agents

Jobs such as minutes, a bullet-style briefing (개조식 보고) or comparing options are done with **official skills**. Turn a skill on in a conversation and it does that job; for work you do often, combine a few skills into an agent of your own. There are no separate built-in agents. Conversations with the former built-in agents, copies you took of them, and chatbots you published from them keep working.

## Official skills

Skills the administrator shares with everyone. They show under **Official skills** in the composer's **+** → **Skills**; picking one adds it to your skills and turns it on.

| Skill | Screen | What it does |
|---|---|---|
| Meeting minutes (회의록 작성) | Report | Keeps decisions, action items (task · owner · due date · source · note) and open items from a transcript, notes and follow-up mails. Writes in the **회의록** template. |
| Bullet-style briefing (개조식 보고) | Report | Turns notes and mails into a one-page □ ○ - ㆍ briefing a superior can decide from. **개조식 보고서** template, one page by default. |
| Compare-and-decide report (비교·결정 보고) | Report | Weighs 2–5 options on exactly the criteria asked and recommends one. **비교·분석 보고서** template. |
| Official document draft (공문·기안 초안) | Report | Drafts plans, result reports, approval drafts and notices. No web search; pictures only when asked. |
| Presentation deck (발표 자료 구성) | Slides | One claim per slide, the conclusion first. |
| Extract and tally (자료 추출·집계) | Chat | Reads Excel and CSV with code, pulls columns, totals by item into tables and charts, and records the rows it removed or changed. |
| Answers from regulations (규정 근거 답변) | Chat | Answers from regulations and guidelines with the article cited as [n], and points what the material does not cover to the department. |
| YouTube transcript and summary (YouTube 영상 전사·요약) | Chat | Gets the captions (or a transcription of the audio) through the **YouTube 전사** connector and summarises from that alone. |
| Deep research (심층 조사) | Chat | Searches the web and papers many times through the **심층 리서치** connector and sums up the sourced findings. Takes a few minutes. |

## Other skills, and what a skill brings

There are also Date calculation (with the **시간** (time) connector it reads today's date from there), Quote calculation, Excel cleanup, PDF table → Excel, Grounding check, Ask for missing information, Calculation and unit check and Mask personal information.

A skill brings its template, checks and missing-information questions. Turn on **Meeting minutes** on the report screen, for instance, and the template becomes **회의록** (if you picked another template, it writes in that one), it asks for the meeting date and attendees before writing, and checks sources and format afterwards. A skill you turn on stays on for the rest of the conversation, approving the outline included.

## Using a skill in a conversation

1. Choose **Report** on the home screen.
2. In the composer choose **+** → **Skills** → **회의록 작성**. The template chip shows **회의록**.
3. Paste the meeting notes or transcript and send 「정식 회의록으로 만들어 줘」.

Skills turned on in chat also go along when you ask for a document there and it moves to a report or deck. Skills that need tools cannot run on that screen and are left out, with a notice naming them.

Use **개조식 보고** (「과장님께 올릴 1쪽 보고로」) and **비교·결정 보고** (「세 제품을 연간 비용과 필수 조건으로 비교해서 하나 골라 줘」) the same way.

## Building an agent from skills

In **Customize** → **Agents** → **Add** → **Create an agent**, the skills you choose under **Skills and tools** › **Skills kept on** are on in every conversation with that agent (up to six). Each skill's template, checks and missing-information questions come with it; a template or detailed settings set on the agent itself come first. **Skills you can pick in the composer** narrows what can be turned on during a conversation.

## A minutes agent

| Field | Value |
|---|---|
| Name | Our team's minutes |
| Instructions | 작성자는 「기획팀 [이름]」으로 적는다. 실행 항목은 담당 팀 순서로 정렬한다. (Only your team's specifics; the skill knows how to write minutes) |
| Skills kept on | 회의록 작성 |
| Extra work | Prepare before writing · Review after writing · Finish (export a file when done: HWPX) |
| Template & theme › Theme | 문서용 명조 |
| Advanced › Screens | Report |

## A briefing agent

Skills kept on **개조식 보고**, theme **문서용 명조**, screen **Report**. Put only what never changes in the instructions, such as the addressee (「시설과장님」) and the approval line.

## A purchase review agent

Skills kept on **비교·결정 보고** + **견적 계산**, screen **Report**. Write your institution's criteria in the instructions (「판단 기준은 3년 총비용, 유지보수, 도입 일정 순서」) so no request has to repeat them.

## A tally agent

Skills kept on **자료 추출·집계**; under Skills and tools › Tools, **파일 작업**, **차트 그리기** and **수치 계산**; screen **Chat**. Upload a spreadsheet and ask 「부서별 합계와 비율을 표로」; it first asks for the deadline, period or denominator basis when the material does not give it.

## A regulations chatbot

1. Create an agent with **규정 근거 답변** + **날짜 계산** kept on.
2. Upload the regulation files under **Sources** and fill in **Department and contact**.
3. Under **Extra work**, tick **공개 전 점검** (the pre-publication check: hold answers without grounds · point to the department · mask personal information).
4. Try it, then [publish it as a chatbot](bots.md). The bot answers with the skills' instructions frozen into the published version.

## Video and research agents

- **Lecture video notes**: **YouTube 영상 전사·요약** kept on, screen **Chat**. Write the shape you want in the instructions (「핵심 다섯 줄과 용어 풀이 표」). The **YouTube 전사** connector must be on.
- **Trend research**: **심층 조사** + **근거 대조** kept on, screen **Chat**. The **심층 리서치** connector must be on, and one run takes a few minutes.

## Making the same as your own skill

You can make a skill like the official ones yourself. In **Customize** → **Skills** → **Add**, write the steps, and under **Server checks** choose **Template to write in**, **Checks it brings**, the strictness, **Web search** and **Pictures**, and **Missing-information questions**. In an uploaded SKILL.md, put `template`, `checks`, `check_options`, `questions` and `process` in the front matter.
