---
title: Choosing a model
---

# Choosing a model

Select the model name to the right of the composer to open the list. When eight or more models are registered, a **Find a model** search box appears with it.

![The model picker list](/img/guide/model-picker.png)

## What the model list shows

| Label | Meaning |
|---|---|
| **strict-local** | The model is confirmed to run only on organization servers and to send nothing outside. Selecting it disables web search, page reading, and connectors. |
| **self-hosted · strict unverified** | The model runs on organization servers, but it is not confirmed that there is no external fallback path. |
| **May switch to an external provider** | The model normally runs on organization servers, but it may switch to an external service during an outage. |
| **External provider** | The model is supplied by an external service, so requests are sent outside the organization. |
| **Boundary unverified** | No information about the processing location is provided for this model. |
| Price | Input and output credits per 1,000 tokens, or **Free** |
| Context | How much can be processed at once |
| 👁 · 🔧 | Image recognition supported · Tool calling supported |

Depending on how the gateway registers models, one model name can appear as two entries that differ only in the data boundary label. Check the label before you select one.

When a **May switch to an external provider** model fails to answer and the gateway's fallback model answers instead, the answer carries an **Answered by external fallback** badge (**Answered by fallback model** when the fallback also runs on organization servers). Hover over the badge to see the model you asked for and the one that answered. The same event is recorded in the administrator's audit log as **Fallback model answered**.

## Super Agent

When the administrator turns it on, two entries appear at the top of the list. They apply to the conversation, not to a model.

| Entry | What it does |
|---|---|
| Super Agent | Sorts each question by task (simple question, translation or summary, calculation, code, current facts, complex reasoning and so on) and picks a fitting model and reasoning effort. With personal data or internal material it uses an in-house model. |
| Super Agent (strict-local) | Picks the same way but only among strict-local in-house models. Questions and material never leave, and the web is not searched. |

Each entry shows the usual credits for one answer. Every answer shows "This answer: model · task type" and the reason in its processing row. Saying "again" or "more detail" answers once more with a stronger setup; short follow-ups in the same conversation and revisions of the same document stay on the same model.

## Scope of a model selection

- **Selecting on the home screen** saves the model to your account as the **default model** for that screen, such as Chat, Reports, or Slides. It applies when you sign in from another device, and you can also change it in Settings → Preferences.
- **Selecting during a conversation** applies to that conversation only.
- An agent conversation uses the model set on the agent. An agent with no model of its own (**Default model**) follows that screen's default model.
- When the administrator limits which models an account may use, only the allowed models appear.

