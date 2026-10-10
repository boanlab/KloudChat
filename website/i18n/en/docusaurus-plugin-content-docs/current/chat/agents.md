---
title: Agents and skills
---

# Agents and skills

An agent is an assistant with a role, instructions, tools, and material already configured. You make your own for work you do often, such as English conversation practice, meeting minutes, or paper review, or import one a colleague has published.

![The agent selection menu](/img/guide/at-menu.png)

## Starting a conversation with an agent

**+** → **Agents** in the composer and **Run** in the agent list both start a new conversation with that agent.

- The first screen of the conversation shows the agent's **How to use** text and its **opening prompts**, up to six of them. Select an opening prompt to send it as it is.
- An agent conversation uses the model, tools, and material set on the agent. When material is registered, the model searches it with the **Find in files** tool. Not all of the material is sent with every request.
- Within the same project, other conversations reference the conclusions that the agent left through **Shared notes**.

The built-in agents are **Bullet-style briefing** (개조식 보고), **Compare and decide** (비교·결정), **Extract and tally** (자료 추출·집계) and **Minutes** (회의록: meetings, interviews, lectures). Bullet-style briefing and Minutes write straight into the institution's format and show the format check results when done. Extract and tally pulls set columns out of internal Excel and CSV files, totals them by item, and keeps a change log of the rows it removed or changed. **Official letter and draft** (공문·기안 초안) and **Presentation** (발표 자료) are published once they pass evaluation; until then only administrators see them. Copies taken under an earlier name (요약·정리, 기록 정리, 데이터 분석, 문서 작성) take the new name unless you renamed them. The cover letter and career history agent and the client proposal agent were retired: they no longer appear in any list or start new conversations, but conversations and results made with them still open and export.

## Creating an agent

**Customize** → **Agents** → **Add** → **Create an agent**. **Create agent** at the end of the composer's **+** → **Agents** list opens it too.

![The agent list](/img/guide/agents.png)

The form runs from top to bottom like this:

1. The **name**, the **one-line description** and the **instructions** (the role and rules the agent always follows). Instructions for one surface only go under a line `[채팅에서]` (chat), `[보고서에서]` (report) or `[슬라이드에서]` (slides); what comes before the first such line applies everywhere.
2. **How it works** (진행 과정): Answer directly · Start from a base process · Set it up yourself (advanced) (see "How it works" below).
3. **Extra work**: the bundle checkboxes (see "Bundles"). Set it up yourself has none; you build steps instead.
4. **Detailed settings — The process's calculations, checks and outline**: shown only for an agent that writes reports (see "Detailed settings and your own templates").
5. **Missing-information questions**: what to ask before writing when the material lacks it; up to four at a time while "Ask for missing information first" is on. A * marks a field you must fill.
6. Under **More settings**: **Output shape**, **Conversation start**, **Sources**, **Skills and tools**, **Advanced** and **Share** (see "Output shape and sources").

A new agent is saved only when you press **Create**. The instructions go with every conversation of that agent, so write only the role and rules it should always follow. An agent works when you open a conversation and ask; it never runs by itself on a schedule such as weekly, nor re-summarises its sources.

The enable toggle pauses an agent. Deleting it also deletes its sources, index and custom order; past conversations stay. The overview says how it works in one line, with the ticked bundles behind **Show details**.

## How it works

Pick one of three under **How it works** in the create-agent form.

- **Answer directly**: answers in the chat. It makes no file (DOCX, HWPX, PDF); to get a file, choose **Start from a base process** with a base that writes a document. When the name, description or instructions mention Word, DOCX or a file, a **Switch to “Bullet-style briefing (Bullet-style briefing (개조식))”** button appears. It switches to that base process and also ticks **Export a file when done** under **Finish** (HWPX, as a 개조식 document, or DOCX or PDF when the instructions name that format).
- **Start from a base process**: pick a built-in agent (Bullet-style briefing, Minutes, Compare and decide, …) from the **Base process** list and the agent follows the order it works in. Its steps appear as up to five plain lines, and the agent gets that built-in agent's questions, document format, template, checks and bundles. A base that writes a document still starts with file export off (see "Get a file" under Bundles).
- **Set it up yourself (advanced)**: pick questions, calculations, checks, reviews, other agents and exports one step at a time. It starts with one "This agent" step. Choose **Report (document)** or **Chat (answer)** under **What this agent makes**; an export step works only with Report (document). With Chat (answer) and an export step, the button under the warning switches that field to Report (document), and **Undo** on the notice switches it back. Presentation has no custom order.

## Bundles

A bundle groups a few things an agent does before and after writing. Each bundle is one checkbox under **Extra work** in the create-agent form; **Details** opens its items as checkboxes.

| Bundle | Items |
|---|---|
| Prepare before writing | Ask for missing information first · Work out dates ahead · Work out amounts and totals ahead |
| Review after writing | Check everything against the material (accept numbers only from tool output · check job titles and product names too) · Format checks · Fix what is off and check again |
| Finish | Strip stock phrases · Export a file when done (DOCX, PDF, HWPX or MD) |

- **Get a file**: with **Finish** ticked, a separate **Export a file when done** checkbox shows under it; tick it and pick the format beside it (HWPX for 공문-type documents, else DOCX). The base processes come with Finish ticked and export off. Export works only in a process that writes a document.
- On the report screen, date and amount calculations, format checks and fix-and-check-again always happen in report writing ("Always done"). The chat screen has no format checks and no export.
- Built-in agents use the same bundles: Minutes is Prepare before writing + Review after writing (including each action item's owner and deadline) + Finish (export off).
- **Your own bundles**: **Customize** → **Agents** → **Add** → **Create a bundle**, or **Save this order as a bundle** in a custom order. They appear under Extra work in all your agents; edit and share them from **Manage bundles**.
- Sharing works like skills (Only me · Everyone). A bundle or custom order that exports files, has another model review or calls another agent reaches others only once an administrator approves it under **Process review**; base processes and built-in bundles are shared at once.

## Output shape and sources

These sit under **More settings** in the create-agent form.

- **Output shape**: pick the **template** (the structure of a report or deck) and the **theme** (colours, typeface, writing style). **New template** under the template makes your own; **Manage themes** under the theme opens Settings › Themes. An agent started from a base process uses the base's template when the template is left empty ("The base's template"), and that template sets the document format. The **Document format** field shows only for a report-writing agent on Answer directly or Set it up yourself.
- **Conversation start**: **How to use** (shown on the conversation's first screen) and **Conversation starters** (one per line, up to six).
- **Sources**: files and web addresses the agent searches and cites (**Attach file**, **Add URL**). They are not sent whole with every conversation; the agent looks things up when needed. A URL stores the page as it was when added and does not follow later changes. Sources are not re-read or re-summarised on a schedule, and an agent never runs by itself at set times; it works when you open a conversation and ask.
- **Skills and tools**, **Advanced** (screens, first screen, model, variety, colour) and **Share**: **Only me** or **Everyone**, and when shared, **Let others take and edit it** or **Let others take it but keep the content private**.

## Detailed settings and your own templates

An agent that writes reports can turn on **Set for this agent** under **Detailed settings — The process's calculations, checks and outline** below the process and change what its template sets, starting from the template's values. Turned off, the template's (or document format's) settings apply as they are.

- **Calculate before writing**: built-in calculations such as dates, quote totals, staffing, total cost per option, must-haves and weighted scores, career periods; and the **date basis** (the material's date or the meeting day).
- **How it writes**: no web search, figures only when asked, a fixed outline.
- **Structure rules**: sections in order, required tables (columns), summary lines at the top, bullet style, page limit. Checked by code.
- **Review rules**: rules in your own words. After writing, a judge model shows the sentences that break them; nothing is rewritten.
- **Calculation checks**, **Judged checks**, **Format repairs**, **Fill in what is missing**: built-in checks and repair steps attached by name.
- **When the material disagrees**: left empty, the writing says which value it used and why.

Make your own document template with **New template** under **Output shape** › Template (or in the template picker). **Start from a built-in template** fills in all of its settings: the document format's head fields, numbering, head layout, bullet style and closing, the instructions, genre rule, checklist, missing-information questions, checks and detailed settings. An uploaded DOCX/HWPX form is read by the writing model as reference. The theme's colours and fonts draw it. Shared with **Everyone**, others can pick it; only its maker edits it. Your templates appear under **My templates** in the template picker.

## Save as agent

Keep a conversation that went well as an agent. **Save as agent** in the **⋯** menu of a result card, or of the report or slides panel, opens the create-agent screen filled in. Nothing is made until you press **Save**.

- The **instructions** start with "Settled in this conversation" (the reader, the length, what to always include …). Only your own messages are read, by the model the conversation used. A strict-local conversation is summarised only by its strict-local model and nothing leaves. The other fields fill in first; the instructions are added when the summary is done. If it cannot be made, the banner says so and offers **Retry**.
- The conversation's template, theme, skills, base process and bundles are filled in, and you choose whether to save the conversation's files as sources.
- A privately shared agent's instructions are never copied.

## Workspace store

**Explore** on **Customize** → **Agents** shows the agents that other users have published. An **Official** badge marks an item published by an administrator. Select **Import** to create a **copy** in your own list.

- A copy does not include the original author's knowledge files. Its process and bundles come along (a custom order still waiting for approval stays behind; the copy uses the base process).
- A copy of an agent published as "contents stay private" cannot be edited, and any change to the original applies to the copy as well.
- Other copies are managed independently, so a change to the original does not affect them.

## Skills

A skill is a working method that applies **to one request only**. Select up to three from **+** → **Skills** in the composer. They are cleared once you send. Installing a skill alone does not apply it. The built-in ones are listed under "Built-in skills" below.

![The Skills screen](/img/guide/skills.png)

- Use **Customize** → **Skills** to install a skill, stop using one, or build your own with a name, description, when to use it, steps, required tools, and visibility. A published skill is listed in the store, and other users import it as a copy.
- **Customize › Skills › Add › Upload skill** takes a SKILL.md (.md) or a .zip/.skill file containing one, several at a time. A .md file needs YAML front matter with `name` and `description`; .md and .txt files under `resources/` are appended to the body under 「참고 자료」 (References). Before anything is saved, each file shows a preview and its security-scan findings (blocked or warning). Scripts (.py, .sh, .js and the like) are never stored or run; they are left out. The text check is heuristic and will not catch every risk. **Upload as draft** saves the skill switched off.
- A skill's **⋯ → Export (SKILL.md)** downloads your skill as a SKILL.md file. Sharing is refused while a skill contains key- or token-like strings or invisible direction-control characters.
- A skill that requires tools is enabled only when all of these hold: you are on the Chat screen, the model supports tool calling, and that tool is connected. When a condition is not met, the reason is shown.
- When you set **Recommended skills** on a project, they appear first in the composer of that project.

## Built-in skills

The lists show seven: **Grounding check** (근거 대조), **Ask for missing information** (빠진 정보 묻기), **Calculation and unit check** (계산·단위 검증), **Date calculation** (날짜 계산), **Quote calculation** (견적 계산), **Excel cleanup** (엑셀 정리) and **PDF table → Excel** (PDF 표 → 엑셀).

- **Excel cleanup** strips title, blank, total and note rows and records what it changed on a change-log sheet. Rows whose values are all the same are not removed, only marked 「중복 의심」 (suspected duplicate); ask for them to be removed if you want that.
- **PDF table → Excel** reads only PDFs whose text can be selected (text PDFs). Scanned PDFs (images) cannot be read.
- The other skills (copy, market research, proofreading, stock-phrase stripping, character-count fitting and so on) are hidden from the lists, the store and the pickers. They are not deleted: they still run in bundles (such as Finish's stock-phrase stripping) and in earlier conversations, and the editors still let you pick them by name. Administrators see them with **Show hidden** (숨김 포함).
