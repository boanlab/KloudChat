---
title: Publishing a chatbot
---

# Publishing a chatbot

Publish an agent that answers in chat as a **chatbot**, and other people can ask it. A chatbot is fixed to the instructions, material and checks it had when published, and answers with this institution's internal models only. The credits it spends are its publisher's, and questions are handled under the same policy as if the publisher had typed them.

## Publishing

The **Publish as a chatbot** button at the top of your agent's page. Before publishing you see:

- The internal model it will answer with. Without an internal model you can use, it cannot be published.
- What a chatbot does not use: your memory, personal settings, other files and projects, connectors, web search, documents and slides, skills. A chatbot uses only **material search** and **calculation**.
- Values in the material that look like personal details (mobile numbers, emails and so on). Remove or check them before publishing.

## Audience

| Audience | Who uses it | Opens |
|---|---|---|
| Members | Members of this institution, signed in. Members see it as a **shared agent** | At once |
| Anyone with the link | Anyone with the address, without signing in. Listed nowhere | After an administrator approves |
| Public | Anyone, without signing in. Listed on the public chatbot page (`/b`) and may show up in search | After an administrator approves |

- Choosing a link or public audience needs a **daily credit limit**. When the day's spending reaches it, the chatbot stops until the next day.
- While the request waits, members can use the chatbot as usual. The request shows at the top of the management page, and **Withdraw request** takes it back.
- When a request is declined, the administrator's reason shows on the management page. The chatbot keeps answering its previous audience.
- Narrowing the audience (public → link → members) needs no approval and applies at once.

## Versions and approval

After changing the agent, use **Versions** → **Publish new version** on the management page for the chatbot to pick it up. Roll back to an earlier version if something is wrong.

On a chatbot open to people without an account, a new version is also reviewed by an administrator before it answers. Until then the approved version keeps answering, and the new one shows as **Awaiting approval** in the version list. Rolling back to a version that was approved before applies at once.

## Channels: other sites, API, MCP

A chatbot open to people without an account can also be reached through these channels besides its own page. Switch them under **Channels** on the dashboard's **Overview**. Switching one on opens once an administrator approves; switching off applies at once.

| Channel | What it does | Available at |
|---|---|---|
| Embed on other sites | Puts the chat on your own sites, such as your institution's homepage | Open to everyone |
| API | Calls the bot from your service or program, OpenAI-compatible | By link, open to everyone |
| MCP | Connects the bot as a tool in AI tools such as Claude | By link, open to everyone |

- **Embed on other sites**: list the sites (starting with `https://`, up to 10) and ask. Once approved, paste the one-line embed code before `</body>`: a chat button appears in the corner and opens the chat (full screen on a phone). The chat shows only on approved sites, and inside them it always acts as a visitor without an account.
- **API and MCP**: on the dashboard's **API** tab, make a key that calls this bot only. A key is shown once, when it is made; keep it then. **Revoke** a key you no longer use and it stops at once.
  - Call the API at `/api/bots/v1/chat/completions` with the bot's address as `model`. Only checked answers go out, so with `stream` on the answer arrives once it is finished. Cited material is in `kloudchat.citations`.
  - Connect MCP at `/api/bots/mcp/<bot address>` with the same key; it offers one tool, `ask`.
  - Questions through a key have per-minute and per-day limits for that key, and the daily credit cap and the bot's own limits still apply.
- The **API** tab and **Statistics** show questions by channel (bot page, other sites, API, MCP).
- When an administrator closes the outside audience, or the audience is narrowed, the channels it can no longer carry close with it.

## What the publisher sees

The management page (**Chatbot** → **Manage** on your agent's page) shows:

- **Statistics**: questions, conversations, visitors, frequent topics, unanswered questions (sentences with personal details masked), 👍👎.
- **Sent conversations**: only conversations a visitor sent with **Send to the maker**, personal details masked. With **Conversation access** on, conversations after the visitor's consent show too. Turn it on only for chatbots whose conversations are the work itself, such as counselling or complaints.
- **Allow copies** under **Settings**: off (locked) by default. Turn it on and members can take the chatbot, instructions included, as their own agent. Leave it off to keep the instructions to yourself.
- Visitors show as "Visitor ABC123" with only whether they signed in. **Block this visitor** on a sent conversation stops them from asking; unblock them under **Settings** → **Blocked visitors**.

When an administrator reads conversations to check abuse, the date and reason appear under **Administrator reads** on **Overview**.

When a chatbot stops (credits used up, paused by an administrator and so on) or an administrator decides a request, a number appears next to **Customize** in the sidebar.

## On a member's side

A chatbot published to members (or to the public) shows to other members as an agent. There is no separate chatbot menu.

- It is listed under **Shared agents** in **Customize** → **Agents** → **Explore**, with its publisher's name. Agents shared with everyone sit in the same place, and an agent that is both shows once.
- In the composer, **+** → **Agents** lists it under **Shared agents**, below your own agents.
- **Use now** or its name opens a conversation on the chatbot page. Answers spend the publisher's credits.
- Chatbots are locked by default: members can only **Use now**. **Add** shows only on chatbots whose publisher turned on **Allow copies**; it copies the published version's instructions and settings as your own agent. Material (knowledge files) does not come along. Even with copies allowed, an agent whose maker keeps the instructions private cannot be copied. A locked chatbot shows a lock instead of **Add**.

## On the visitor's side

- Visitors read the notice before their first question. It states how long conversations are kept and whether the publisher can read them.
- A visitor without an account continues their conversations only through this browser's cookie. Clearing cookies loses past conversations. They cannot upload files or download artifacts.
- Questions are limited per person, per address (visitors without an account) and per chatbot, per minute and per day. Lower the limits under **Settings** → **Limits** on the management page.
- Conversations are deleted once the retention period (30 days by default) passes after the last question.
