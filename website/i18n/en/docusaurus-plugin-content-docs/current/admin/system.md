---
title: System settings
---

# System settings

The **System** screen in the Manage menu is made up of eight tabs. Saved changes apply at once, without a restart.

## Proxy

The model gateway (LiteLLM) address and master key (stored encrypted, never shown again), **Connection test** (confirms n models), **Refresh model list**, and revert to the environment variable values. If models are listed as hidden for lack of price information, enter the model prices in the gateway settings.

## Models

Decides which model does which job when the user has not chosen one. The values in the server's `.env` file are used once, at installation; after that, what is saved here takes precedence.

- **Default model for each screen**: chat, report, slides, image, audio and video. Chat and image must be chosen. Left empty, report and slides follow the chat default, and audio and video use the cheapest model. A model the user chose always comes first.
- **Side-work models**: the model for titles, memories and summaries, the code-writing model and its fallback. These read the conversation, so only models this instance runs itself (self-hosted) can be chosen. In a strict-local conversation the strict-local version of the chosen model does the work.
- **Thinking**: whether self-hosted and external models reason before answering. On makes judgements more exact but adds tens of seconds to minutes to each call. Both are off by default.

The **planning model** for reports and slides and Auto's **classifier model** are on the **Routing** tab.

### Self-hosted models of the default install

The defaults use two models the organization runs itself. Each is registered in the gateway under two names. A `local/` model runs on the organization's servers first, but an external fallback may answer when it fails, so it is treated as outside the data boundary. A `strict-local/` model never leaves, and it is the only route for internal material.

| Model | Jobs |
|---|---|
| `qwen3.8-27b` | Default report and slides model, code model |
| `qwen3.8-flash` | Default chat model, planning model (outline and judgments for reports and slides), titles, memory and summaries, Auto classifier and cost-saving model, vision model for text reading, code fallback model |

All of them can be changed on this tab and on the **Routing** and **Features** tabs.

## Routing

The routing settings are described in [Policy and routing](policy#auto-routing).

## Features

- **Features to use**: Turn reports, slide decks, images, and audio/video on and off. Chat is always on. Disabling one removes it from the menu, and the server refuses new conversations for it as well. Images and video work in practice only if the gateway has those models. If you enable them without a model, the user screen shows a notice that generation is not possible.
- **Feature integration**: One feature server address, or an address per feature (web search · document import · code execution · deep research · speech transcription · material search · text from scans (OCR)). **Connection test** individually and for all of them. Setting a speech transcription address enables dictation and reading audio and video files. With a material search (indexing) address, agent material and conversation attachments are searched by vector (word search only without it).
- **Text from scans (OCR)**: Reads the text of scanned PDF pages and document photos. Set whether it is on, how pages are read (OCR engine + vision model · OCR engine only · vision model only), the vision model, and the most pages read in one document (300 by default). Only a strict-local model that reads pictures can be chosen as the vision model; any other is refused. With both, the engine reads first and only pages with a low score or a table are read again by the vision model; where the two differ, the page is marked “to check”. Without an OCR engine address, PDF pages are not read and only photos are read by the vision model. Pages read appear in usage as “n pages read” (0 credits).

The safe search level and blocked domains for web search are set on the search server. For details, see the backend (KloudChat-LLM) documentation. By default, safe search is off.

## Branding

The service name, the contact address (used by the mail link on the waiting screen; the first administrator's address if left empty), and the logo (PNG, JPG, WebP, up to 2MB, no SVG, with revert to default). Changing the service name also changes the browser tab icon to the matching initials.

## Mail

The SMTP host, port, security, account, and sender address, the service address (for mail links, with **Use the current address**), and **Send a test**. Once it is set, a password reset link appears on the sign-in screen and you can turn on email verification.

## Sign-up

- **Sign-up method**: Use after approval / Use immediately / Not accepted. The value chosen on this screen overrides the `SIGNUP_MODE` environment variable.
- **Email domains allowed to sign up**: Several, separated by commas. Subdomains have to be listed separately.
- **Email verification**: Not effective without a mail server.
- **Monthly credits for new accounts**: granted at sign-up or approval. You can change it for each account when approving.

## Connector catalogue

The MCP servers people can install from **Customize** › **Connectors**. **Add entry** takes a name, slug, transport (run a command over stdio, HTTP or SSE), the command or address, environment variables and surfaces. Write tool addresses as placeholders such as `${TOOLS_STT_URL}`. Pick a **variable that gets the gateway key** and `TOOLS_GATEWAY_KEY` is added only while that variable is the chosen tool's address (this is how YouTube transcription calls speech-to-text). Stored values are never shown again; type one only to replace it. Servers that run as a command can only be added here. Turn off **Show in the list** (목록에 보이기) and the entry leaves the members' list but can still be installed, and what is installed keeps working (the admin list marks it Hidden). Turn off **Enabled** and nobody can install it anew. Turn on **Built in** (기본 제공) and the entry is installed at once for every active member, and for everyone who signs up or is approved later (the admin list marks it Built in). Members can switch a built-in connector off but cannot remove it. An entry that needs each person's credentials cannot be built in. Time, YouTube transcription and Deep research are built in.

## Management items outside the System tab

- The **Usage** screen: charts by period, by model, by screen, and by user, CSV export, and storage space (disk usage, orphan file cleanup, and automatic cleanup from the oldest when it fills up).
- **Theme**: An administrator can **provide a theme to every user** (shared, read only).
- **Store**: Agents published by an administrator account carry an **Official** badge; skills carry the name of whoever shared them. Built-in skills start as skills of the first administrator account, shared with everyone. Administrators can edit them or stop sharing them in **Customize** › **Skills** like any other skill, and an edited skill is not overwritten by a newer version (unedited ones, and users' unedited copies, follow newer versions). Built-in skills hidden from the lists (kept only as parts) show to administrators only with **Show hidden** (숨김 포함).
- **Performance** screen: first-token and full-answer latency and error rate per model, turns in progress, API replica, host and GPU metrics, with items past their thresholds flagged as warning or overload. The first screen is a dashboard with the verdict, key figures and the GPU node and model server tables; clicking a figure or a table row opens the host or model trends, or the detail screen of one node or server. The thresholds are set with the API's `PERF_*` environment variables.
- The trusted proxy range, the file size limit, and similar settings are configured through environment variables rather than the screen. See `docs/configuration.md` in the repository.
