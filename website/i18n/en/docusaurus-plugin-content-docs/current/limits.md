---
title: Limits and troubleshooting
---

# Limits and troubleshooting

This page describes the limits the service allows and what to do for each error message shown on the screen.

## Limits

| Item | Limit |
|---|---|
| A single file | 200MB |
| Images | PNG, JPG, GIF, WebP, up to 4MB, on a model that supports image recognition |
| Audio and video files | 25MB, when speech transcription is connected |
| Number of slides | Up to 50 slides (5 to 12 if you do not say) |
| Number of report sections | 3 to 12 sections |
| Skills | 3 per request |
| Model comparison | 2 to 3 |
| Memory | Up to 40 entries passed per request |
| Opening sentences (agent) | 6 |
| API keys | 10 per account |
| Slide image upload | 5MB |
| Volume of files in one conversation | About 35% of the model window (up to about 150,000 characters). Beyond that, only the relevant parts |
| Answer length | No upper limit. Stops automatically when the text starts repeating |

## Processing time limits

- A single request runs for up to 15 minutes, and it ends with an error beyond that.
- If the model sends nothing for 3 minutes, the request ends with an error. In that case use **Try another model**.
- Reports and slide decks are written in order, section by section or slide by slide. A 20-slide deck takes several minutes, and video generation takes longer and shows progress.

## What to do for each error message

Search for the message exactly as it appears on the screen.

| Message on the screen | Meaning | What to do |
|---|---|---|
| You have used all of your credits this month | The monthly limit is used up | Wait for the refill on the 1st of next month. Free models are not affected |
| You do not have enough credits this month | The remaining credits are less than the estimated cost of this request | Reduce the options such as slide count or length, or use a free model |
| Cannot connect to the model server | Model gateway failure | Try again shortly. Contact your administrator if it continues |
| Could not get an answer because of a model server error | The gateway refused the request (including when the files or the conversation exceed the model window) | Upload the files again in a new conversation, or narrow the question |
| The model did not respond, so the request was stopped. Try generating again with another model | No text arrived for 3 minutes | **Try another model** |
| The model server request limit was exceeded | Gateway rate limit | Try again shortly |
| The model server refused authentication | Gateway key problem | Contact your administrator |
| The model server does not have this model | The model list has changed | Choose another model. The administrator refreshes the list |
| This model is not allowed for this account | A model the administrator has restricted | Choose another model |
| This model cannot be used on this screen right now | The screen (a report, for example) and the model do not match | Change the model |
| No models are available right now | The gateway is not connected | Contact your administrator |
| The request was not sent because the personal data checker is unavailable | Protection feature failure | Contact your administrator. Sending is blocked for safety |
| An administrator policy blocked this request | The request matches a blocked topic | Check what the request says |
| The answer was cut off, so only this much remains | The connection dropped (tab closed or network) | Select **Try again**. It generates again from the start |
| The attachment could not be found. Attach it again | The file was deleted | Attach it again |
| The selected skill cannot be applied to this request | The skill conditions (tool or model) are not met | Remove the skill or change the model |
| This agent reads its instructions from the original, and the original has been deleted or is no longer public | The original of an agent with private content is gone | Use another agent |
| The agent for this conversation is turned off, so you cannot send | The agent is disabled or deleted | Start a new conversation |
| The microphone is unavailable. Check the microphone permission in your browser | No microphone or no permission | Browser permission |
| No text could be read from this file | A scanned copy or an unsupported format | Use a file that contains text |
| Sign-in failed five times in a row, so the account is locked for 15 minutes | Locked | Try again after 15 minutes |
| Sign-ups are not being accepted right now | The administrator has blocked sign-up | Ask your administrator for an account |

## Features that are not provided

- Editing a sent message and sending it again. Copy the prompt and send it as a new message. An answer that failed to generate offers **Try again** and **Try another model**.
- Several users editing one conversation together. Sharing is read only.
- Two-factor authentication.
- Model comparison and keyboard shortcuts on the mobile screen. The tool buttons are inside **More tools**.

## Model limitations

A model can present things it does not know as if they were fact. For information that changes or cannot be memorised, such as organisation details, dates, and figures, turn on web search or attach source material. An answer that shows no sources is an answer that did not go through search. You can check a report against its sources with **Fact check**, and verify calculations with **Code execution**.
