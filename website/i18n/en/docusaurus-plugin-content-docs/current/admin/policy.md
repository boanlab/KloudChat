---
title: Policy and routing
---

# Policy and routing

This screen sets the level of personal data protection, the audit record, and automatic model switching (Auto).

## Policy settings

| Item | Meaning |
|---|---|
| Personal data masking | Always hides national ID numbers, card numbers, phone numbers, and email addresses in every request, and stores them hidden. The original cannot be sent |
| Personal data protection for external models | In Chat and comparison, asks the user to choose when personal data is detected for a model that may switch to an external provider |
| strict-local safe model | The model that "Switch to a safe local model" sends to. Only chat models the gateway declares as strict-local are candidates. **If you leave it empty**, users see a notice that there is no safe model |
| Allow sending the original outside | Whether users may ignore the warning and send. Not possible while the masking policy is on |
| Intent-based filter + topics to block | Refuses the request without using credits and records it in the audit log |
| Automatic sign-out when idle | In minutes. 0 turns it off |
| Conversation retention period | In days. The body is deleted once it passes (applied retroactively, with the number deleted shown). Records and usage remain |

If the server cannot read the policy, it blocks sending in Chat, model comparison, reports, and slide decks for safety (503).

In an environment where every model runs on organisation servers, **Personal data protection for external models** has little effect. An organisation model that is not declared strict-local is treated as a model that may switch to an external provider, so a warning appears. Declare strict-local on the gateway and designate a safe model, or disable the protection feature.

## Audit log

Time, account, action, target, and access location. You can search sign-ins, failures, approvals, suspensions, permission changes, setting changes, key issuance, discards, password resets, token reuse detection, and more by account, action, and IP. It cannot be edited or deleted from the screen.

## Auto routing

- **Auto · Cost saving** — Turn it on, the difficulty classification model (free strict-local only), and up to 3 saving models (in priority order). A saving model is used only when its output price is lower than the quality model the user chose, its data boundary is no wider, and its context length is sufficient. In an environment where every model is free, there is nothing to save, so it does not run.
- **Auto · Quality first** — Turn it on, and up to 3 upgrade models.
- **Structuring stage model** — The model that sets the outline for a report or slide deck. If you leave it empty, it is the same as the writing model.

The default model per screen is not an administrator setting. It sits under **each user's Settings → Preferences**. Administrators only restrict the allowed models per user.
