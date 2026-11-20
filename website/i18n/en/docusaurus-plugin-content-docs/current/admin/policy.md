---
title: Policy and routing
---

# Policy and routing

This screen sets the level of personal data protection, the audit record, and automatic model choice (Super Agent).

## Policy settings

| Item | Meaning |
|---|---|
| Personal data masking | Always masks national ID numbers, card numbers, phone numbers, and email addresses in every request, and stores them masked. The original cannot be sent |
| Personal data protection for external models | In Chat, asks the user to choose when personal data is detected for a model that may switch to an external provider |
| strict-local safe model | A priority list, ordered with the arrow buttons or by dragging. When internal material moves a turn to strict-local, the chosen model's strict-local version is used first, else the first model of this list. On the privacy card, "Switch to a safe local model" lets the person pick any checked model (the first is preselected). Only chat models the gateway declares as strict-local are candidates. **If you leave it empty**, users see a notice that there is no safe model |
| Internal material stays strict-local | A request carrying attachments, project files or memory is answered by the strict-local twin of the same model, and the conversation never moves to an external model afterwards. Without a twin, the safe model answers. Without that, the request is refused |
| Allow sending the original outside | Whether users may ignore the warning and send. Not possible while the masking policy is on |
| Intent-based filter + topics to block | Refuses the request without using credits and records it in the audit log |
| Automatic sign-out when idle | In minutes. 0 turns it off |
| Conversation retention period | In days. Once the period is up, the conversation body is deleted (applied retroactively, with the number deleted shown). Records and usage remain |

If the server cannot read the policy, it blocks sending in Chat, reports, and slide decks for safety (503).

In an environment where every model runs on organization servers, **Personal data protection for external models** has little effect. An organization model that is not declared strict-local is treated as a model that may switch to an external provider, so a warning appears. Declare strict-local on the gateway and designate a safe model, or disable the protection feature.

## Audit log

Time, account, action, target, and access location. The log covers sign-ins, failures, approvals, suspensions, permission changes, setting changes, key issuance, discards, password resets, token reuse detection, and more. Search by account, action, or IP. The Time, Action, Target and Access location headings sort the log on the server. **Export CSV** downloads every page under the current filters and order (up to 50,000 rows), and the export itself is recorded in the audit log. It cannot be edited or deleted from the screen.

## Super Agent

Super Agent is always there. The model picker always lists its two entries (Super Agent and Super Agent (strict-local)) at the top; the table below and each user's allowed models bound what they pick.

- **Classifier model** — a free strict-local model that gets only the requests the rules cannot sort, briefly.
- **Table by task type** — for each task type, the Super Agent model, the strict-local model and the reasoning effort. The "after a retry request or empty answer" row handles requests to redo and empty answers. **Reset table to defaults** restores the evaluated default table.
- **"Dashboards and charts" row** — takes code and artifact requests that draw dashboards, charts or graphs ("make a dashboard from this monthly sales table"). Other code and artifacts (calculators, shop pages, games, Python code) stay on the "Code" row. A question that asks for nothing to be built, such as what a chart means, does not go to this row.
- **"Work on internal material" row** — sends questions over attached files, project knowledge or an agent's files (except simple questions, reports and retries) to this row's model. A column set to **Keep the task type's model** follows the original row. By default both columns are empty, so turns follow their original row. When a strict-local model other than Qwen3.8 Flash fails or shows nothing for 4 minutes, Qwen3.8 Flash carries the same answer on, and the failed model is left out for a while (1 minute when busy, 5 minutes otherwise).
- **"Complex document" row** — sends documents that follow a form (PowerPoint, Word, HWPX), documents that must cover many files (a request about each file, or six or more source files), slide decks with a chart from data or a paper's figures, and documents with a very long source to this row's model. By default that is the in-house Qwen3.8 Flash (both columns) at high reasoning; ordinary documents stay on the "Reports and slides" row, written by the same model without reasoning. A column left empty means the row moves nothing in that column. The reasoning chip's "Fast" turns reasoning off without changing this row's model, and "Deep" raises it to the highest level. Revisions keep the model that wrote the first version. If this row names a model other than the "Reports and slides" row's and it fails or takes too long, the "Reports and slides" row's model carries the same answer on. If you put a slow in-house model here, the environment variable `COMPLEX_DOCUMENT_SLOTS` (0 by default, no limit) caps how many such documents run at a time across all API servers; the rest wait as "Waiting · no. n in line" and, after `COMPLEX_DOCUMENT_WAIT_SEC` (120 s by default), are written by the "Reports and slides" row's model.
- **Structuring stage model** — The model that sets the outline for a report or slide deck. If you leave it empty, it is the same as the writing model.

New conversations start on the per-screen default under **System › Models**; for chat, reports and slides that is Super Agent. A model the user picked on the home screen or under **Settings → Preferences** comes first.
