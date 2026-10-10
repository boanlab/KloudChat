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

The built-in agents are **Summary** (요약·정리), **Records** (기록 정리: meetings, interviews, lectures), **Compare and decide** (비교·결정) and **Data analysis**. **Document writing** (문서 작성) and **Presentation** (발표 자료) are published once they pass evaluation; until then only administrators see them. The cover letter and career history agent and the client proposal agent were retired: they no longer appear in any list or start new conversations, but conversations and results made with them still open and export.

## Creating an agent

**Customize** → **Agents** → **Add** → **Create an agent**.

![The agent list](/img/guide/agents.png)

1. Write the **name**, the **one-line description** and the **instructions** (the role and rules the agent always follows). Instructions for one surface only go under a line `[채팅에서]` (chat), `[보고서에서]` (report) or `[슬라이드에서]` (slides); what comes before the first such line applies everywhere.
2. Choose **How it works** (진행 과정):
   - **Answer directly**: takes the request and answers right away.
   - **Start from a base process**: follows the order a built-in agent (Summary, Records, …) works in. Its steps appear as up to five plain lines, and the agent gets that built-in agent's question card, document format, template and checks.
   - **Set it up yourself (advanced)**: pick questions, calculations, checks, reviews, other agents and exports one step at a time. It starts with one "This agent" step. Presentation has no custom order.
3. Tick **bundles** under **Extra work** (below). A base process comes with the bundles its built-in agent uses already ticked; left as they are, the agent works exactly like the built-in one.
4. Under **Output look**, pick the **template** (the structure of a report or deck) and the **theme** (colours, typeface, writing style). **Manage themes** opens Settings › Themes. An agent that writes reports can also fix a **document format**.
5. Enter **How to use** and up to six **opening prompts** under **Conversation start**, and files or web addresses under **Sources**. A web address stores the page as it was when registered.
6. Set **Skills and tools**, **Advanced** (screens, first screen, model, variety, colour) and **Sharing**: **Only me** or **Everyone**, and when shared, **Let others take and edit it** or **Let others take it but keep the content private**.

The enable toggle pauses an agent. Deleting it also deletes its sources, its index and its custom order; past conversations stay. The agent's overview says how it works in one line ("How it works: …"), with the ticked bundles behind **Show details**.

## Bundles

A bundle groups a few things an agent does before and after writing. Each bundle is one checkbox under **Extra work** in the agent settings; **Details** opens its items, each its own checkbox.

| Bundle | Items |
|---|---|
| Prepare before writing | Ask for missing information first · Work out dates ahead · Work out amounts and totals ahead |
| Review after writing | Check everything against the material (accept numbers only from tool output · check job titles and product names too) · Format checks · Fix what is off and check again |
| Finish | Strip stock phrases · Export a file when done (DOCX, PDF, HWPX or MD) |

- On the report screen, date and amount calculations, format checks and fix-and-check-again always happen inside report writing, so they show as "Always done". The chat screen has no format checks and no export.
- The built-in agents are made of the same bundles. Records, for example, is Prepare before writing + Review after writing (including each action item's owner and deadline) + Finish (export off).
- **Your own bundles**: **Customize** → **Agents** → **Add** → **Create a bundle**, or **Save this order as a bundle** in a custom order. Your bundles appear as checkboxes under Extra work in all your agents. Edit and share them from the **Bundles** row on the Agents tab (Manage bundles).
- Sharing works like skills (Only me · Everyone). A bundle or custom order that exports files, has another model review, or calls another agent reaches others only after an administrator approves it under **Process review**. An agent that uses only a base process and the built-in bundles is shared at once.

## Process settings and your own templates

An agent that writes reports can turn on **Set for this agent** under **More settings** › **Process settings** and change what its template sets, starting from the template's values. Turned off, the template's (or document format's) settings apply as they are.

- **Calculate before writing**: built-in calculations such as dates, quote totals, staffing, total cost per option, must-haves and weighted scores, career periods; and the **date basis** (the material's date or the meeting day).
- **How it writes**: no web search, figures only when asked, a fixed outline.
- **Structure rules**: sections in order, required tables (columns), summary lines at the top, bullet style, page limit. Checked by code.
- **Review rules**: rules in your own words. After writing, a judge model shows the sentences that break them; nothing is rewritten.
- **Calculation checks**, **Judged checks**, **Format repairs**, **Fill in what is missing**: built-in checks and repair steps attached by name.

Make your own document template with **New template** under **Output look** › Template (or in the template picker). **Start from a built-in template** fills in all of its settings: the document format's head fields, numbering, head layout, bullet style and closing, the instructions, genre rule, checklist, missing-information questions, checks and process settings. An uploaded DOCX/HWPX form is read by the writing model as reference. The theme's colours and fonts draw it. Shared with **Everyone**, others can pick it; only its maker edits it. Your templates appear under **My templates** in the template picker.

## Save as agent

Keep a conversation that went well as an agent. **Save as agent** in the **⋯** menu of a result card, or of the report or slides panel, opens the create-agent screen filled in. Nothing is made until you press **Save**.

- The **instructions** start with "Settled in this conversation" (the reader, the length, what to always include …). Only your own messages are read, by the model the conversation used. A strict-local conversation is summarised only by its strict-local model and nothing leaves.
- The conversation's template, theme, skills, base process and bundles are filled in, and you choose whether to save the conversation's files as sources.
- A privately shared agent's instructions are never copied.

## Workspace store

**Explore** on **Customize** → **Agents** shows the agents that other users have published. An **Official** badge marks an item published by an administrator. Select **Import** to create a **copy** in your own list.

- A copy does not include the original author's knowledge files. Its process and bundles come along (a custom order still waiting for approval stays behind; the copy uses the base process).
- A copy of an agent published as "contents stay private" cannot be edited, and any change to the original applies to the copy as well.
- Other copies are managed independently, so a change to the original does not affect them.

## Skills

A skill is a working method that applies **to one request only**. Select up to three from **+** → **Skills** in the composer. They are cleared once you send. Installing a skill alone does not apply it. Available skills include citation formatting, calculation and unit checking, comparison tables, paper structure, and one-page summaries.

![The Skills screen](/img/guide/skills.png)

- Use **Customize** → **Skills** to install a skill, stop using one, or build your own with a name, description, when to use it, steps, required tools, and visibility. A published skill is listed in the store, and other users import it as a copy.
- **Customize › Skills › Add › Upload skill** takes a SKILL.md (.md) or a .zip/.skill file containing one, several at a time. A .md file needs YAML front matter with `name` and `description`; .md and .txt files under `resources/` are appended to the body under 「참고 자료」 (References). Before anything is saved, each file shows a preview and its security-scan findings (blocked or warning). Scripts (.py, .sh, .js and the like) are never stored or run; they are left out. The text check is heuristic and will not catch every risk. **Upload as draft** saves the skill switched off.
- A skill's **⋯ → Export (SKILL.md)** downloads your skill as a SKILL.md file. Sharing is refused while a skill contains key- or token-like strings or invisible direction-control characters.
- A skill that requires tools is enabled only when all of these hold: you are on the Chat screen, the model supports tool calling, and that tool is connected. When a condition is not met, the reason is shown.
- When you set **Recommended skills** on a project, they appear first in the composer of that project.
