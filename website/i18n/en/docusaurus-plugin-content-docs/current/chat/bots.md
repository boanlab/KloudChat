---
title: Publishing a chatbot
---

# Publishing a chatbot

Publish an agent that answers in chat as a **chatbot**, and other people can ask it. A chatbot is fixed to the instructions, material and checks it had when published, and answers with this institution's internal models only. The credits it spends are its publisher's, and questions are handled under the same policy as if the publisher had typed them.

## Publishing

On the agent's page, **Show more** → **Publish as a chatbot**. Before publishing you see:

- The internal model it will answer with. Without an internal model you can use, it cannot be published.
- What a chatbot does not use: your memory, personal settings, other files and projects, connectors, web search, documents and slides, skills. A chatbot uses only **material search** and **calculation**.
- Values in the material that look like personal details (mobile numbers, emails and so on). Remove or check them before publishing.

## Audience

| Audience | Who uses it | Opens |
|---|---|---|
| Members | Members of this institution, signed in. Listed under **Chatbots** | At once |
| Anyone with the link | Anyone with the address, without signing in. Listed nowhere | After an administrator approves |
| Public | Anyone, without signing in. Listed on the public chatbot page (`/b`) and may show up in search | After an administrator approves |

- Choosing a link or public audience needs a **daily credit limit**. When the day's spending reaches it, the chatbot stops until the next day.
- While the request waits, members can use the chatbot as usual. The request shows at the top of the management page, and **Withdraw request** takes it back.
- When a request is declined, the administrator's reason shows on the management page. The chatbot keeps answering its previous audience.
- Narrowing the audience (public → link → members) needs no approval and applies at once.

## Versions and approval

After changing the agent, use **Versions** → **Publish new version** on the management page for the chatbot to pick it up. Roll back to an earlier version if something is wrong.

On a chatbot open to people without an account, a new version is also reviewed by an administrator before it answers. Until then the approved version keeps answering, and the new one shows as **Awaiting approval** in the version list. Rolling back to a version that was approved before applies at once.

## What the publisher sees

The management page (**Chatbots** → **Manage** on your chatbot) shows:

- **Statistics**: questions, conversations, visitors, frequent topics, unanswered questions (sentences with personal details masked), 👍👎.
- **Sent conversations**: only conversations a visitor sent with **Send to the maker**, personal details masked. With **Conversation access** on, conversations after the visitor's consent show too. Turn it on only for chatbots whose conversations are the work itself, such as counselling or complaints.
- Visitors show as "Visitor ABC123" with only whether they signed in. **Block this visitor** on a sent conversation stops them from asking; unblock them under **Settings** → **Blocked visitors**.

When a chatbot stops (credits used up, paused by an administrator and so on) or an administrator decides a request, a number appears next to **Chatbots** in the sidebar.

## On the visitor's side

- Visitors read the notice before their first question. It states how long conversations are kept and whether the publisher can read them.
- A visitor without an account continues their conversations only through this browser's cookie. Clearing cookies loses past conversations. They cannot upload files or download results.
- Questions are limited per person, per address (visitors without an account) and per chatbot, per minute and per day. Lower the limits under **Settings** → **Limits** on the management page.
- Conversations are deleted once the retention period (30 days by default) passes after the last question.
