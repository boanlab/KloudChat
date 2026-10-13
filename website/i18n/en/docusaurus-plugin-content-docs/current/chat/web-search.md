---
title: Web search and tools
---

# Web search and tools

Choose **Web search** from the **+** button in the composer and set it to **Auto**, **Always** or **Off**. Auto is the default; Always or Off shows as a chip in the composer. The setting applies to that request only, and it resets to **Auto** in a new conversation.

![The web search setting in the composer](/img/guide/composer.png)

| Mode | What it does |
|---|---|
| Auto | Searches first when you ask about information that changes over time, such as news, prices, schedules, latest versions, and weather, or that must be exact: phone numbers and hotlines, laws, tax rates, thresholds and benefits, fees, rates and limits, event dates, whether a product or service is available, organizations, and statistics. Legal, tax, financial and health advice is searched first too. Otherwise it searches only when the model judges it necessary. |
| Always | Always performs a search for that request. |
| Off | Does not search unless you ask explicitly, with wording such as "search for" or "look it up". |

When a search runs, the answer shows numbers in the form `[n]` and a source list. One request performs at most three searches and reads at most six pages. Weather uses a dedicated tool instead of search. If you select a strict-local model, web search cannot be chosen.

Writing, translation and proofreading, code and spreadsheet formulas, role-play, concept explanations, and questions about attached or pasted material are not searched first in Auto. A follow-up (for example "it's 30 million won I need within a year" after an investment question) is judged with the conversation before it. Writing "don't search" or choosing **Off** keeps the turn offline.

Requests about current information need a search. "Exchange rate trends this quarter" and "Changes in Python 3.14" are examples. Calculation, translation, summarizing an attached text, and fixing code do not need a search. Setting the mode to **Off** for those makes the answer faster and avoids unnecessary sources.

## Grounding check for legal, tax, financial and health questions

Legal, tax, financial and health advice gets the **grounding check** by default. An amount, rate or threshold, a phone number or hotline, a dose, or a legal consequence such as a criminal penalty or a fine that is not in this turn's search results or in what you gave is marked **(확인 필요)** ("needs checking") where it stands. When there are many marks, they come out of the text and are listed once at the end of the answer. Practical guidance (what to do, which documents to prepare, when to see a professional) stays as written. The result appears in the processing steps as an item such as **근거 확인(법률)** ("grounding check (legal)").

## Tools the model uses

Models that support tool calling, marked 🔧 in the list, use the following tools as the request requires. Their use appears in the processing steps.

| Tool | What it does |
|---|---|
| Web search · Page reading · Weather | Runs searches and reads the body text of the pages it needs. |
| Code execution | Performs calculations, table processing, and chart generation with real code. This path is accurate for arithmetic on large numbers. |
| Arithmetic check | Calculates arithmetic and powers exactly on the server, and compares the result with answer choices or a submitted answer. It needs no extra server, and requests that call for a calculation use it first. |
| Find in files | Searches inside attached files and agent material. |
| Artifacts · Chart creation | Produces an artifact: a document or a chart in the right panel. |
| Shared notes | Leaves a note for other conversations in the same project to reference. |
| Connectors | External tools that you installed. [Connectors](connectors) |

Where the administrator has not connected a search server or a code execution server, those tools are not available.
