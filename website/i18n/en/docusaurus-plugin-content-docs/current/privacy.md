---
title: Privacy
---

# Privacy

This page explains where your input and attachments are stored, how far they are sent, and how detected personal data is handled.

## Where data is stored and sent

| Data | Storage location | Sent outside | Deletion point | Administrator access |
|---|---|---|---|---|
| Conversations and answers | Organization server | Follows the data boundary of the model. Requests to models marked **external provider** and **may switch to an external provider** are sent to that service; requests to **strict-local** models are not. | When the user deletes it, or when the retention period set by the administrator has passed | No screen for viewing conversation content is provided. |
| Attachments and project knowledge | Organization server | Same as conversations. | When the file, the conversation, or the project is deleted | Only the storage volume can be checked. |
| Memory | Organization server | Sent to the model together with the request. | When the user deletes it | Cannot be viewed. |
| Web search terms | Not stored. | Sent to the search engine when search mode is on or Auto. | Not applicable | Cannot be viewed. |
| Image and video prompts | Organization server | Follows the data boundary of the selected model, and in most cases is sent to an external service. | When the output is deleted | Cannot be viewed. |
| Usage and sign-in history | Organization server | Not sent. | Follows the policy set by the administrator. | Can be viewed. It includes tokens, credits, sign-ins, and policy application records. The values of detected personal data are not recorded; only the type and the count are kept. |

The **data boundary**, which differs by model, is shown in the model list. [Choosing a model](chat/models)

## Personal data detection

KloudChat detects phone numbers, national ID numbers, payment card numbers, IP addresses, private keys, and email addresses. Names and addresses are excluded from detection.

### Chat and model comparison

If the administrator has enabled **Personal data protection for external models** and the selected model may send data outside, the **"This request contains personal data"** dialog appears before the request is sent. It states the detected types and counts, and does not show the values themselves.

| Option | Meaning |
|---|---|
| Switch to a safe local model | Sends this request to the strict-local model designated by the administrator. |
| Mask and keep the current model | Replaces the personal data with placeholders before sending. |
| Send the original text to the external model | Sends the original text as it is. Shown only when the administrator allows it. |
| Return to editing | Returns to the input box without sending. |

To avoid choosing every time, set the default action in **Default handling for personal data detection** under Settings → Preferences.

### Reports, slide decks, images, and the API

When you write a report or a slide deck, generate an image, or call the API, detected personal data is masked and sent without a confirmation step.

### When always-on masking is set

Personal data is masked in every request, and **the conversation history is also stored in masked form**. When you open it again later, only the placeholders are shown, and sending the original text is not allowed. "Stored masked in the history" appears at the bottom of the answer.

## strict-local models

These are models confirmed not to send anything outside. Selecting one disables web search, page reading, and connectors. Selecting one in model comparison cancels the comparison and answers with that single model. A name starting with `local` does not make a model strict-local, so check the label in the list.

## Share links

A shared conversation can be viewed by all signed-in users or by anyone who knows the link, depending on the scope you set. [Sharing](write/share)
