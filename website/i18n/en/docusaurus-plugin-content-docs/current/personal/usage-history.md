---
title: Usage and conversation history
---

# Usage and conversation history

Check your credit usage and tidy up past conversations.

## Usage

Check it in the account menu → **Usage**.

![Usage screen](/img/guide/usage.png)

- It provides the remaining credits and the allowance for this month, a period selector, and a daily trend of credits or answer counts.
- It shows usage separately **by model**, **by screen** (Chat, Reports, Slides, Images, and so on), and **by API key**. API key usage is counted separately but shares the same monthly limit.
- Voice transcription is shown separately in seconds and material indexing in chunks.

## Credits

A credit is the unit that expresses model usage, and the basis differs by type.

| Type | Billing unit |
|---|---|
| Text | Per 1,000 tokens. Input and output are counted separately. |
| Image | Per image |
| Audio | Per call |
| Video | Per second × the combination of resolution and sound. Charged only when the generation completes. |

Models marked **Free** do not consume credits. Most of them are models the organisation runs itself, but models that an external service offers free of charge are marked the same way, and in that case the request is transmitted outside the organisation. Check the data boundary label in the model list as well.

Credits are reset to the allowance on the first day of each month and do not carry over. When they are used up, you cannot send new requests until the next refill, but outputs already created are kept. A request whose generation was stopped is recorded as an estimate.

## Conversation history

Check it in the account menu → **Conversation history**.

![Conversation history screen](/img/guide/history.png)

- View all conversations by date and search them by title.
- You can use **Select all** on the listed items and then **Delete selected**, or run **Delete all conversations**. A confirmation dialog is shown before it runs.
- Deleting a conversation also deletes the outputs created in that conversation and its share links. Projects and memory are kept.
- In an environment where the administrator has set a retention period, the body of a conversation past that period is deleted automatically. Sign-in history and usage totals are kept.
