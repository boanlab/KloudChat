---
title: Approving published chatbots
---

# Approving published chatbots

When a member publishes an agent as a chatbot, other members can use it at once. Opening it to people without an account — **anyone with the link** or **public** — waits for an administrator's approval. Handle these under the account menu's **Admin** → **Published chatbots**. A number appears on the menu while requests are waiting.

## What you review

Opening a request shows the version that would answer once approved:

- Instructions, the department and contact line, the greeting, the tools and checks it uses, the strict-local model it answers with
- The material and how many values in it look like personal details
- The publisher, the daily credit limit, and the history so far (requests, decisions, new versions)

**Approve** opens the requested audience and that version together. **Decline** needs a reason, which the publisher sees on their management page. A declined chatbot keeps answering its previous audience.

When a new version arrives on a chatbot open to people without an account, it is reviewed the same way. Until approved, the approved version answers.

## Channel requests

The publisher of a chatbot open by link or to everyone can ask for the **Embed on other sites**, **API** and **MCP** channels. A request appears in the same review queue as "Channel request", and the review shows the channels and sites asked for.

- **Approve** opens the channels asked for. An embedded chat shows only on the sites you approved; adding a site is reviewed again.
- **Close to people without an account** closes the embed, API and MCP channels too. From then on the embedded chat, the API and MCP are refused at once.
- The channels open now are listed under **Open channels** on the review screen.

## Reading conversations

**Conversation access** on the review screen is for checking abuse reports only. The list and the conversations open after you give a reason, with personal details masked. The date and reason go to the audit log and to the publisher's dashboard (**Administrator reads**). Conversations through an API or MCP key are marked "API/MCP key".

## Closing and pausing

| Action | Result |
|---|---|
| Close to people without an account | Returns it to members only. Visitors without an account cannot open it from their next request |
| Pause / Reopen | Stops or resumes questions from every visitor |
| Close chatbot | Nobody but the publisher can open it |

Every action needs a reason and is recorded in the audit log. Reading a published chatbot's conversations for an abuse review also needs a reason and is recorded.

## Operating notes

- Visitors without an account are told apart by a browser cookie; per-address limits per minute and per day, and a daily cap on new visitors and conversations per address, guard against abuse. The address comes from the `X-Forwarded-For` the proxy in front sends, so narrow `KCHAT_TRUSTED_PROXIES` on the web container to the actual proxy.
- Questions answered at once by published chatbots across the organization stay under a cap (16 by default); the rest wait briefly and, if no turn comes, are told the chatbot is busy.
