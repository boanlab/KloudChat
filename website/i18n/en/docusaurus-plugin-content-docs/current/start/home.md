---
title: Home screen layout
---

# Home screen layout

Signing in opens the home screen. Use the sidebar on the left to find past conversations, and the middle area to start new work.

![Home screen](/img/guide/home.png)

## Sidebar

| Area | Description |
|---|---|
| Search | Searches past conversations (titles and messages) and artifacts. Shortcut `Ctrl+K`. |
| New chat | Goes to the home screen so you can start new work. Shortcut `Alt+Shift+N`. |
| Projects · Artifacts · Files · Customize | Goes to the workspace that holds your grouped files, the gallery of generated artifacts, the files you uploaded or made, and the place to manage agents (including ones others shared), skills and connectors. |
| Conversation list | Shown in the order **Pinned**, **Recent**. **All conversations** opens the history page. Icons show the type of each item. The `…` menu on an item lets you rename it, pin it, remove it from a project and delete it. |
| Credit this month | The usage you have left. Press it to go to the usage screen. A model marked **Free** does not consume credit. |
| Account menu | Customize, History, Agent setup, API access, Usage, Settings (including memory and themes), Feedback & issues, Keyboard shortcuts, the Manage menu (administrators), Sign out. |

## Choosing a screen and using the composer

Choose the screen you want from the **Chat · Report · Slides · Image · Audio/Video** chips above the prompt box and type your request. Image and Audio/Video appear only if the administrator has enabled them. Examples suited to the chosen screen sit under the chips. Chat is for questions and answers. Report and Slides confirm the structure first and then write the document.

When you choose Report or Slides (also when you reopen an empty conversation), the **Format** and **Template** chips appear in the prompt box. **Format** offers Web report, HWP and Word for a report, and Web slides and PowerPoint for slides. When you pick HWP, Word or PowerPoint, a **Start blank** button appears. The **Template** chip shows the name of the chosen template (or **Default**), opens the template gallery, and has × to start without a template. The project's default template is preselected. The addresses `/new/report?format=hwp` and `/new/slides?format=pptx` open with the format preselected. [Writing reports](../write/report) · [Writing slides](../write/slides)

:::tip When you ask for a document in Chat
If you ask for a document in Chat, for example "Write a report" or "Make a slide deck", **the request moves automatically to a new conversation for that task.** Agent conversations and requests about existing content such as "Summarize this" or "Review this" stay in Chat.
:::

### Composer features and where they apply

![Composer](/img/guide/composer.png)

| Feature | Description | Applies to |
|---|---|---|
| **+** | One menu holding Attach files or photos, Skills, Web search, Connectors and Agents. In a report or slides conversation that already has messages it also has Pick a template. On a phone it opens as a bottom sheet. | — |
| Attach files or photos | Uploads a file. You can drag and drop files or paste them. Pasting long text turns it into a **Pasted text** chip. | The whole conversation |
| 🎤 Dictate | Turns speech into text. Shown only when the administrator has connected speech transcription. | That input |
| Template chip · Pick a template | Selects the template for reports and slides. On the first screen use the **Template** chip above the prompt box; in a conversation that already has messages use Pick a template in the **+** menu. Not offered in Chat, Image or Audio/Video. | That conversation |
| Skills | Selects up to three skills to apply to the request. | That request |
| Web search: Auto · Always · Off | Chooses the web search mode. **Always** or **Off** shows as a chip in the composer. | That request. A new conversation resets it automatically |
| Connectors | Turns the connectors you have installed on and off. | Your whole account |
| Agents | Selects an agent and starts a new conversation with that agent. | — |
| Model name ▾ | Selects the model to use. | Choosing it on Home makes it your account default, while choosing it during a conversation applies it to that conversation |
| ↑ · ■ | Sends the request or stops generation. | — |

The items currently in effect appear as chips above the composer: the project, the format, the template, skills, attachments and the Auto state. Press × to remove one.

## Example requests and work in progress

- **Example requests**: Examples suited to the screen chosen with the chips above the prompt box (Chat, Report, Slides and so on) are shown. Selecting one fills the composer; it does not send. Pick an agent from **+** → **Agents** in the composer. [Agents and skills](../chat/agents)
- **In progress**: Shown while a long task such as video generation is running.

## Buttons at the top right

There are **Share** (on the conversation screen), **Install app** (installs a shortcut app in browsers that support it), **EN** (language switch) and a theme button. The theme cycles through system, light and dark.

## Mobile screen

Press **+** in the composer to open Attach files or photos, Skills, Web search, Connectors and Agents (and Pick a template in a report or slides conversation that already has messages) in a bottom sheet. The processing details of an answer are collapsed as "n processing details".

![Mobile home screen](/img/guide/mobile-home.png)
![Mobile conversation screen](/img/guide/mobile-chat.png)
