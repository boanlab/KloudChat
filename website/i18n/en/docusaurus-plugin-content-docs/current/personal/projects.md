---
title: Projects
---

# Projects

A project is a workspace that binds materials, instructions, and conversations together. Use it to reference the same materials from several conversations, or to avoid entering common rules every time.

![Projects screen](/img/guide/projects.png)

## Creating

Go to the sidebar → **Projects** → New project, then enter a name, a description, and **project instructions**. After creation, the detail screen lets you set the icon, the **default form files**, and the **design brief**. You do not pick a structure for reports and slides: it is chosen automatically from the request.

## Tabs

| Tab | Function |
|---|---|
| Conversations | Start a new conversation in this project, and add existing conversations to the project or remove them from it. |
| Knowledge | Add files and register addresses as **web material**. Web material stores the content as it was at registration time. It shows how many tokens each file uses and how much of the context it takes up. You can check the recognized content, download the original, and delete it. A file that meaning-based search does not cover yet is marked **Not indexed**; **Index now** sends it again. A deleted file can be brought back with **Undo** at the bottom of the screen for a few seconds. |
| Studio | Makes a study guide, FAQ, briefing, timeline, glossary, mind map, quiz or flashcards from the sources you pick. See [Knowledge shelf](./knowledge-shelf.md). |
| Notes | Collects answers, selected passages and notes written by hand, and makes a note a source. |
| Skills | Skills you pick with **Choose skills** appear first in this project's composer. |
| Memory | Memory shared at the project level. Conclusions that an agent leaves through **Share note** also accumulate here. |

## How knowledge is delivered

Project knowledge is delivered automatically to every conversation in the project. If the volume fits within the processing range, all of it is delivered. If it exceeds the range, only the parts related to the question are selected and delivered. If even the selected volume exceeds the range, only the file list is delivered. The processing steps of the answer show how much of it was used. In reports it is cited with source numbers.

## Instructions

Describe rules that apply to every conversation, such as "answer in polite language" or "give English terms alongside". They take precedence over personalization settings.

## Default form files

Upload a PowerPoint or Word form, or pick one from the file box, under **Default form files** beside the project details, and every PowerPoint file and every report written as a Word file in the project follows it. Forms are kept apart from the project's knowledge and are not read as material. An old Hangul file (`.hwp`) cannot be a form: in Hangul, use **Save As › Hangul standard document (*.hwpx)** and upload that.

A new report or slides conversation opened in this project shows a **Project form** chip beside the format chip; ✕ removes it for that conversation only. A form attached to the conversation takes precedence.

## Design brief

The **design brief** is a single text field that describes, in words, how the project's reports and slides look: colors, fonts, how tables are drawn, how much goes on a page. For example: "Navy as the only accent color. Serif body, sans-serif headings. Shade only the header row of tables, no vertical lines." The values read from it appear below the field as **Read as** chips. Text marked (테마에서 옮김) can be edited like any other guidance. When there is a form file, the brief fills only what the form leaves open.

## Deleting a project

Deleting a project deletes its instructions and knowledge files. Conversations are detached from the project and kept, and artifacts and memory also remain.

## Comparison with conversation attachments

| Item | Conversation attachment | Project knowledge |
|---|---|---|
| Scope | That conversation | Every conversation in the project |
| Retention | Until the conversation is deleted | Until the file is deleted |
| Share link | Exposed only to the extent that the answer quotes it | Not exposed |
