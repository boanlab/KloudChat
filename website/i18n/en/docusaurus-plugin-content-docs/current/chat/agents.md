---
title: Agents and skills
---

# Agents and skills

An agent is an assistant with a role, instructions, tools, and material already configured. You make your own for work you do often, such as English conversation practice, meeting minutes, or paper review, or import one a colleague has published.

Field-by-field examples are in [Agent and template examples](agent-examples).

![The agent selection menu](/img/guide/at-menu.png)

## Starting a conversation with an agent

**+** → **Agents** in the composer and **Run** in the agent list both start a new conversation with that agent.

Jobs such as minutes or a bullet-point report are **official skills**. For minutes, on the report screen choose **+** → **Skills** → **회의록 작성** (Meeting minutes) in the composer, attach or paste the recording transcript or notes, and send 「회의록으로 정리해 주세요」. Work you do often becomes an agent of your own with that skill kept on ([Working with skills and building agents](skill-recipes)).

- The first screen of the conversation shows the agent's **How to use** text and its **opening prompts**, up to six of them. Select an opening prompt to send it as it is.
- An agent conversation uses the model, tools, and material set on the agent. When material is registered, the model searches it with the **Find in files** tool. Not all of the material is sent with every request.
- Within the same project, other conversations reference the conclusions that the agent left through **Shared notes**.

There are no built-in agents. Jobs such as Regulations and procedures, Bullet-style briefing, Compare and decide, Extract and tally and Minutes are done by official skills of the same names. The built-in agents for these jobs do not show in the lists or among the base processes, but conversations with them, copies you took and chatbots published from them keep working.

## Creating an agent

**Customize** → **Agents** → **Add** → **Create an agent**. **Create agent** at the end of the composer's **+** → **Agents** list opens it too.

![The agent list](/img/guide/agents.png)

The form runs from top to bottom like this:

1. The **name**, the **one-line description** and the **instructions** (the role and rules the agent always follows). Instructions for one surface only go under a line `[채팅에서]` (chat), `[보고서에서]` (report) or `[슬라이드에서]` (slides); what comes before the first such line applies everywhere.
2. **How it works** (진행 과정): Answer directly · Start from a base process · Set it up yourself (advanced) (see "How it works" below).
3. **Extra work**: the bundle checkboxes (see "Bundles"). Set it up yourself has none; you build steps instead.
4. **Detailed settings — The process's calculations, checks and outline**: shown only for an agent that writes reports (see "Detailed settings and your own templates").
5. **Missing-information questions**: what to ask before writing when the material lacks it; up to four at a time while "Ask for missing information first" is on. A * marks a field you must fill.
6. Under **More settings**: **Template & theme**, **Conversation start**, **Sources**, **Skills and tools**, **Advanced** and **Share** (see "Template & theme and sources").

A new agent is saved only when you press **Create**. The instructions go with every conversation of that agent, so write only the role and rules it should always follow. An agent works when you open a conversation and ask; it never runs by itself on a schedule such as weekly, nor re-summarises its sources.

The enable toggle pauses an agent. Deleting it also deletes its sources, index and custom order; past conversations stay. The overview says how it works in one line, with the ticked bundles behind **Show details**.

## How it works

Pick one of three under **How it works** in the create-agent form. A new agent and its process are saved together: if the process is refused, no agent is made and the form stays as it was.

- **Answer directly**: answers the request right away. An agent that answers in the chat makes no file (DOCX, HWPX, PDF).
- **Start from a base process**: follows the working order of a built-in agent (Bullet-style briefing, Minutes, Compare and decide, …). When no base process can be picked this option is hidden; it shows only on an agent that already started from one (which keeps it). A new agent does the same job with **Answer directly** plus skills set to **Always** and bundles. To get a file, set **Advanced** › first screen to **Report** and tick **Export a file when done** under Finish.
- **Set it up yourself (advanced)**: pick questions, calculations, checks, reviews, other agents and exports one step at a time. It starts with one "This agent" step. Choose **Report (document)** or **Chat (answer)** under **What this agent makes**; an export step works only with Report (document). With Chat (answer) and an export step, the button under the warning switches that field to Report (document), and **Undo** on the notice switches it back. Presentation has no custom order.

## Condition steps and run records

A **condition** step in a custom order looks at what earlier steps produced and goes on along one branch only. Steps are joined by lines; a condition has one line per branch.

- **Rule**: compares a value an earlier step left (greater than · equals · is empty · contains). No model is called; the branches are **Yes** and **No**. Values: the request (attachments · attachment types · text), an agent (result kind · sections · characters · text), a calculation (result), a check (found · left), a review (findings · fixed · left), the question card (blanks · answers), the outline approval (sections).
- **AI judgement**: the judge model answers your question by picking one of 2–4 branches, and its reason goes into the run record. A strict-local conversation uses only models that stay inside; the tokens count toward that turn's credits.
- **No value**: when the step a rule reads did not run or left no value, the rule takes **No**; when the judgement names no branch, the last branch is taken. The record says why.
- **Checked before saving**: a line back to an earlier step (a loop), a branch with nothing after it, a step the first step never reaches, an unfinished condition, or two lines out of a step that is not a condition stop the save. Every branch's last agent must make the same thing (a document or an answer), and no condition may sit between the question card and its agent. Bundles hold no conditions.
- **Run records**: steps on a branch not taken are recorded as skipped. Each run shows every step's status, a short summary of what it got and handed on, and the branch taken; only the person who ran it sees it.

## Bundles

A bundle groups a few things an agent does before and after writing. Each bundle is one checkbox under **Extra work** in the create-agent form; **Details** opens its items as checkboxes.

| Bundle | Items |
|---|---|
| Prepare before writing | Ask for missing information first · Work out dates ahead · Work out amounts and totals ahead |
| Review after writing | Check everything against the material (accept numbers only from tool output · check job titles and product names too) · Format checks · Fix what is off and check again |
| Finish | Strip stock phrases · Export a file when done (DOCX, PDF, HWPX or MD) |

- **Get a file**: with **Finish** ticked, a separate **Export a file when done** checkbox shows under it; tick it and pick the format beside it (HWPX for 공문-type documents, else DOCX). The base processes come with Finish ticked and export off. Export works only in a process that writes a document.
- On the report screen, date and amount calculations, format checks and fix-and-check-again always happen in report writing ("Always done"). The chat screen has no format checks and no export.
- Base processes are built from the same bundles: Minutes is Prepare + Review + Finish (export off). The **회의록 작성** skill and **Minutes** template carry those checks.
- **Your own bundles**: **Customize** → **Agents** → **Add** → **Create a bundle**, or **Save this order as a bundle** in a custom order. They appear under Extra work in all your agents; edit and share them from **Manage bundles**.
- Sharing works like skills (Only me · Everyone). A bundle or custom order that exports files, has another model review or calls another agent reaches others only once an administrator approves it under **Process review**; base processes and built-in bundles are shared at once.

## Template & theme and sources

These sit under **More settings** in the create-agent form.

- **Template & theme**: pick the **template** (the structure of a report or deck) and the **theme** (colours, typeface, writing style). **New template** under the template makes your own; **Manage themes** under the theme opens Settings › Themes. An agent started from a base process uses the base's template when the template is left empty ("The base's template"), and that template sets the document format. The **Document format** field shows only for a report-writing agent on Answer directly or Set it up yourself.
- **Conversation start**: **How to use** (shown on the conversation's first screen) and **Conversation starters** (one per line, up to six).
- **Sources**: files and web addresses the agent searches and cites (**Attach file**, **Add URL**). They are not sent whole with every conversation; the agent looks things up when needed. A URL stores the page as it was when added and does not follow later changes. Sources are not re-read or re-summarised on a schedule, and an agent never runs by itself at set times; it works when you open a conversation and ask.
- **Skills and tools** and **Advanced** (screens, first screen, model, output language, web search default, variety, colour, use memory). Adding a screen while no first screen is chosen makes it the first screen. Setting a skill that works only on reports or slides to **Always** (회의록 작성, 개조식 보고, …) sets the first screen to that screen when none is chosen, and a line under the skills says where each one works. **Share**: **Only me** or **Everyone**, and when shared, **Let others take and edit it** or **Let others take it but keep the content private**.

## Output language, web search and memory

These are under **Advanced** in the agent form.

- **Output language**: pick **Auto (the request's language)**, 한국어, English, 日本語 or 中文, or type a language name under **Type one in**. Once set, every answer, report and slide deck of this agent is written in that language, whatever language the question or the material is in. A turn that asks for another language itself ("영어로", "in Korean") follows that request. A published bot answers in it too.
- **Web search default**: whether the composer's web search starts on **Auto**, **Always** or **Off** in a new conversation with this agent. You can change it per turn in the composer. With **Off**, a question about current facts is not searched first (asking "search for it" still searches). A turn sent through the API without a web search setting uses this value.
- **Use memory**: when off, your memories are not added to this agent's conversations, and those conversations create no automatic memories or shared notes.

## Detailed settings and your own templates

An agent that writes reports can turn on **Set for this agent** under **Detailed settings — The process's calculations, checks and outline** below the process and change what its template sets, starting from the template's values. Turned off, the template's (or document format's) settings apply as they are.

- **Calculate before writing**: built-in calculations such as dates, quote totals, staffing, total cost per option, must-haves and weighted scores, career periods; and the **date basis** (the material's date or the meeting day).
- **How it writes**: no web search, figures only when asked, a fixed outline.
- **Structure rules**: sections in order, required tables (columns), summary lines at the top, bullet style, page limit. Checked by code.
- **Review rules**: rules in your own words. After writing, a judge model shows the sentences that break them; nothing is rewritten.
- **Calculation checks**, **Judged checks**, **Value fixes**, **Fill in what is missing**: built-in checks and repair steps attached by name.
- **When the material disagrees**: left empty, the writing says which value it used and why.

Make your own document template with **New template** under **Template & theme** › Template (or in the template picker). **Start from a built-in template** fills in all of its settings: the document format's head fields, numbering, head layout, bullet style and closing, the instructions, genre rule, checklist, missing-information questions, checks and detailed settings. An uploaded DOCX/HWPX sample document is read by the writing model as reference. The theme's colours and fonts draw it. Shared with **Everyone**, others can pick it; only its maker edits it. Your templates appear under **My templates** in the template picker.

## An agent's skills

**Skills and tools** › **Skills** lists the skills the agent uses, one per row, each with how it applies.

- **Always**: added to the instructions on every turn of every conversation with this agent, the same way as a skill picked in the composer; one in both goes in once. Up to six, about 8,000 tokens together. If a skill later grows past that, they go in in order and only what no longer fits is left out of that turn.
- **When needed**: not added every turn; on the Chat screen a tool-calling model reads the skill's description and pulls it in when it needs it.
- Each row shows **Preview the body** and its rough token count, and the **Always** total is shown.
- **Use defaults**: all your skills are available when needed, and any skill can be picked in the composer. Add only the ones to use every turn and set them to **Always**.
- **Choose**: only the skills in the list are used, and only these can be picked in the composer. With none listed the agent uses no skills, not even ones picked in the composer; the form says so.
- **+ Add a skill (store included)** lists your skills and the store's (official skills and ones colleagues shared). **Install and add** on a store skill copies it into your skills and lists it.

## Make an agent from a skill

**Make an agent from this skill** on a skill's page (or the agent button on a skill card) opens the create-agent screen filled in. Check the name and press **Create**.

- The name and one-line description come from the skill; the instructions from its description and when-to-use. The skill's body goes in every turn as an **Always** skill, so it is not copied into the instructions.
- The skill is set to **Always**; the screens follow the skill's, and the tools its required tools (none required: **Use defaults**).
- A store skill is installed into your skills when you press **Create**. One already installed is used as it is, not installed again.

## Save as agent

Keep a conversation that went well as an agent. **Save as agent** in the **⋯** menu of an artifact card, or of the report or slides panel, opens the create-agent screen filled in. Nothing is made until you press **Save**.

- The **instructions** start with "Settled in this conversation" (the reader, the length, what to always include …). Only your own messages are read, by the model the conversation used. A strict-local conversation is summarised only by its strict-local model and nothing leaves. The other fields fill in first; the instructions are added when the summary is done. If it cannot be made, the banner says so and offers **Retry**.
- The conversation's template, theme, skills, base process and bundles are filled in, and you choose whether to save the conversation's files as sources.
- A privately shared agent's instructions are never copied.

## Workspace store

**Shared agents** under **Explore** on **Customize** → **Agents** shows, with their makers' names, the agents other users have published and the agents [published to members as chatbots](bots.md). An **Official** badge marks an item published by an administrator. Select **Add** to create a **copy** in your own list.

- A copy does not include the original author's knowledge files. Its process and bundles come along (a custom order still waiting for approval stays behind; the copy uses the base process).
- A copy of an agent published as "contents stay private" cannot be edited, and any change to the original applies to the copy as well.
- Other copies are managed independently, so a change to the original does not affect them.

## Skills

A skill is a working method kept on in a conversation: select up to three from **+** → **Skills** (again to turn one off). Unpicked skills are used too (see "Skills used when needed"). An **official skill** is added to your skills when picked. A skill brings its template, checks and questions.

![The Skills screen](/img/guide/skills.png)

- Use **Customize** → **Skills** to install a skill, stop using one, or build your own with a name, description, when to use it, steps, required tools, and visibility. A published skill is listed in the store, and other users import it as a copy.
- **Customize › Skills › Add › Upload skill** takes a SKILL.md (.md) or a .zip/.skill file containing one, several at a time. A .md file needs YAML front matter with `name` and `description`; .md and .txt files under `resources/` are appended to the body under 「참고 자료」 (References). Before anything is saved, each file shows a preview and its security-scan findings (blocked or warning). Python scripts under `scripts/` are stored with the skill and run only in the code execution sandbox; other scripts (.sh, .js and the like) are left out. A published skill with scripts can be copied by others only after an administrator approves it under **Skill review**. The checks are heuristic and will not catch every risk. **Upload as draft** saves the skill switched off.
- A skill's **⋯ → Export (SKILL.md)** downloads your skill as a SKILL.md file. Sharing is refused while a skill contains key- or token-like strings or invisible direction-control characters.
- A skill that requires tools works only on the Chat screen, with a tool-calling model and that tool connected. When a condition is not met, the reason is shown.
- Skills you pick with **Choose skills** on a project's **Skills** tab appear first in that project's composer.

## Skills used when needed

A skill does not have to be picked to be used. On the Chat screen with a tool-calling model, the model sees the descriptions of your switched-on skills that this turn does not already apply (store copies you installed included, up to twelve) and pulls one in when the request needs it. In a conversation with an agent set to **Choose**, only the skills in its list are offered. To have a skill used on every turn, pick it in the composer or set it to **Always** in an agent.

A skill's own Python scripts (the `scripts/` of an uploaded skill) are stored with it. On a chat turn that uses the skill, the model runs them with the skill-script tool, only in the code execution sandbox and never on the server itself.

## Tool skills on reports and slides

On the Report and Slides screens you can pick a skill that needs tools when the server does its work around the writing: it calculates or checks before or after, such as **Calculation and unit check**, **Date calculation** and **Quote calculation**. The picker shows "The server calculates and checks around the writing". Other skills that need tools can be picked only on the Chat screen.

## Built-in skills

Built-in skills are skills the administrator account made and shared with everyone. They appear under **Explore** in **Customize** › **Skills** as **By: Official**; **Add** puts a copy in your list (**Original: Official**). A copy follows the administrator's newer versions while you leave it unedited, and stays yours once you edit it. The checks agents and templates use (grounding check, asking for missing information and so on) run without adding anything.

The lists show the thirteen work skills (including Translation, Press release, Make an Excel file and Make a chart) under [Official skills](skill-recipes#official-skills) and **Grounding check** (근거 대조), **Ask for missing information** (빠진 정보 묻기), **Calculation and unit check** (계산·단위 검증), **Date calculation** (날짜 계산), **Quote calculation** (견적 계산), **Excel cleanup** (엑셀 정리), **PDF table → Excel** (PDF 표 → 엑셀) and **Mask personal information** (개인정보 가리기).

- **Excel cleanup** strips title, blank, total and note rows and records what it changed on a change-log sheet. Rows whose values are all the same are not removed, only marked 「중복 의심」 (suspected duplicate); ask for them to be removed if you want that.
- **PDF table → Excel** reads only PDFs whose text can be selected (text PDFs). Scanned PDFs (images) cannot be read.
- The other skills (copy, market research, proofreading, stock-phrase stripping, character-count fitting and so on) are not shown in the lists, the store and the pickers. They still run in bundles (such as Finish's stock-phrase stripping) and in conversations that used them, and the editors still let you pick them by name. Administrators see them with **Show hidden** (숨김 포함).
