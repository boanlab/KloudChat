---
title: Sharing a chatbot
---

# Sharing a chatbot

Share an agent that answers in chat as a **chatbot**, and other people can ask it. A chatbot is fixed to the instructions, material and checks it had when shared, and answers with this organization's strict-local models only. The credits it spends are its publisher's, and questions are handled under the same policy as if the publisher had typed them.

## Sharing

The **Share** button at the top of your agent's page. Before sharing you see:

- The strict-local model it will answer with. Without a strict-local model you can use, it cannot be shared.
- What a chatbot does not use: your memory, personal settings, other files and projects, MCP servers, web search, documents and slides, skills. A chatbot uses only **material search** and **calculation**.
- Values in the material that look like personal details (mobile numbers, emails and so on). Remove or check them before sharing.
- A warning when there is no material. **Add a source** takes you to the agent's material section. You can share without material, but the chatbot then answers only from its instructions.

The dialog asks one thing: who to share with. "Anyone with the link" and "Anyone" also ask for a **daily credit limit**, which starts at 3,000. Everything else (name, greeting, suggested questions, retention and so on) starts at its default and is changed on the dashboard's **Settings** after sharing.

After sharing, each scope shows its own state, **Open** or **Pending**. Until approval, send the address only to people in your organization. You are told when it is approved.

## Who can use it

| Scope | Who uses it | Opens |
|---|---|---|
| Your organization | Members of this organization, signed in. Members see it as a **shared agent** | At once |
| Anyone with the link | Anyone with the address, without signing in. Listed nowhere | After an administrator approves |
| Anyone | Anyone, without signing in. Listed on the public chatbot page (`/b`) and may show up in search | After an administrator approves |

- Choosing "Anyone with the link" or "Anyone" needs a **daily credit limit** (3,000 to start). When the day's spending reaches it, the chatbot stops until the next day.
- While the request waits, members can use the chatbot as usual. On the management page that scope shows as **Pending**, and **Withdraw request** takes it back.
- When a request is declined, the administrator's reason shows in that scope's row. The chatbot keeps answering its previous audience.
- Narrowing the scope (Anyone → Anyone with the link → Your organization) needs no approval and applies at once.

## Versions and approval

After changing the agent, use **Versions** under **Settings** → **Publish new version** on the management page for the chatbot to pick it up. Roll back to an earlier version if something is wrong.

On a chatbot open to people without an account, a new version is also reviewed by an administrator before it answers. Until then the approved version keeps answering, and the new one shows as **Awaiting approval** in the version list. Rolling back to a version that was approved before applies at once.

## Channels: other sites, API, MCP

A chatbot open to people without an account can also be reached through these channels besides its own page. Switch them under **Channels** on the dashboard's **Integrations** tab. It shows only the channels the current scope can carry and says in one line what unlocks the rest. Switching one on opens once an administrator approves; switching off applies at once.

| Channel | What it does | Available at |
|---|---|---|
| Embed on other sites | Puts the chat on your own sites, such as your organization's homepage | Anyone |
| API | Calls the bot from your service or program, OpenAI-compatible | Anyone with the link, Anyone |
| MCP | Connects the bot as a tool in AI tools such as Claude | Anyone with the link, Anyone |

- **Embed on other sites**: list the sites (starting with `https://`, up to 10) and ask. Once approved, paste the one-line embed code before `</body>`: a chat button appears in the corner and opens the chat (full screen on a phone). The chat shows only on approved sites, and inside them it always acts as a visitor without an account.
- **API and MCP**: on the dashboard's **Integrations** tab, make a key that calls this bot only. A key is shown once, when it is made; keep it then. **Revoke** a key you no longer use and it stops at once.
  - Call the API at `/api/bots/v1/chat/completions` with the bot's address as `model`. Only checked answers go out, so with `stream` on the answer arrives once it is finished. Cited material is in `kloudchat.citations`.
  - Connect MCP at `/api/bots/mcp/<bot address>` with the same key; it offers one tool, `ask`.
  - Questions through a key have per-minute and per-day limits for that key, and the daily credit cap and the bot's own limits still apply.
- The **Integrations** tab and the statistics on **Overview** show questions by channel (bot page, other sites, API, MCP).
- When an administrator closes the outside scope, or the scope is narrowed, the channels it can no longer carry close with it.

## What the publisher sees

The management page (**Chatbot** → **Manage** on your agent's page) shows:

- Statistics on **Overview**: questions, conversations, visitors, frequent topics, unanswered questions (sentences with personal details masked), 👍👎.
- **Visitor conversations**: only conversations a visitor sent with **Send to the maker**, personal details masked. With **Conversation access** on, conversations after the visitor's consent show too. Turn it on only for chatbots whose conversations are the work itself, such as counselling or complaints.
- **Allow copies** under **Settings**: off (locked) by default. Turn it on and members can take the chatbot, instructions included, as their own agent. Leave it off to keep the instructions to yourself.
- Visitors show as "Visitor ABC123" with only whether they signed in. **Block this visitor** on a visitor conversation stops them from asking; unblock them under **Settings** → **Blocked visitors**.

When an administrator reads conversations to check abuse, the date and reason appear under **Administrator reads** on **Overview**.

When a chatbot stops (credits used up, paused by an administrator and so on) or an administrator decides a request, a number appears next to **Customize** in the sidebar.

## When the maker opens it

When the maker opens their own chatbot's address, it looks as a visitor sees it, with a **Preview** strip on top. If the link is not open outside the organization yet, the strip says why (waiting for approval, or declined). **Go to the dashboard** in the strip takes you to the management page.

An agent's card under Customize wears one sharing badge ("Your organization", "Your organization · Link: pending approval" and so on); it opens the management page.

## On a member's side

A chatbot shared with your organization (or wider) shows to other members as an agent. There is no separate chatbot menu.

- It is listed under **Shared agents** in **Customize** → **Agents** → **Explore**, with its publisher's name. Agents shared with everyone sit in the same place, and an agent that is both shows once.
- In the composer, **+** → **Agents** lists it under **Shared agents**, below your own agents.
- **Use now** or its name opens a conversation on the chatbot page. Answers spend the publisher's credits.
- Chatbots are locked by default: members can only **Use now**. **Add** shows only on chatbots whose publisher turned on **Allow copies**; it copies the published version's instructions and settings as your own agent. Material (knowledge files) does not come along. Even with copies allowed, an agent whose maker keeps the instructions private cannot be copied. A locked chatbot shows a lock instead of **Add**.

## On the visitor's side

- The first screen shows the greeting and suggested questions. To ask, tick the one-line notice above the box ("Conversations are handled under the maker's account and deleted after N days. Do not enter personal information."). **Details** opens the full notice, including the retention period and whether the publisher can read conversations.
- An address that is not open yet (including a link waiting for approval) shows a visitor without an account only "It is not open right now". It never says that an approval is pending.
- A visitor without an account continues their conversations only through this browser's cookie. Clearing cookies loses past conversations. They cannot upload files or download artifacts.
- Questions are limited per person, per address (visitors without an account) and per chatbot, per minute and per day. Lower the limits under **Settings** → **Limits** on the management page.
- Conversations are deleted once the retention period (30 days by default) passes after the last question.
- Moving to another conversation under **My conversations** while an answer is being written does not stop it; it is there when you go back. If the connection drops, the question stays and the answer the server finished is picked up.
- **My conversations** shows the latest 50 first; **Show more** opens the rest.
