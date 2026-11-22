---
title: Writing reports
---

# Writing reports

On the home screen, select **Report** and enter the topic of the report you want to write. If you have supporting material, attach it from **+** → **Attach files or photos** in the composer. When you attach material, KloudChat fills the figures in from that material instead of estimating them. The document is written in the language you asked in: an English memo pasted on its own gives an English document, and a Korean ask over English material ("아래 메모로 회의록을 만들어줘") a Korean one; the reply when it is done is in the document's language. An agent with an output language set writes in that language. Where a search server is connected, it searches the web before writing and cites the sources. A request about domestic research trends or one that names KCI also finds KCI, DBpia and RISS papers through the same search and cites their authors, year, journal and DOI.

## Choosing the format and form

On Home and in any empty report conversation (new, started from a project or an agent, or reopened from history), a row of chips appears above the prompt box.

- **Format** chip: choose **Web report**, **HWP** or **Word**. A web report is an HTML document you edit in the panel. HWP and Word write that file in its [HWP](hwp) or [Word](docx) editor beside the conversation. When you pick HWP or Word, a **Start blank** button appears. Opening `/new/report?format=hwp` preselects the format.
- **Form file**: attach a company or organization Word form (`.docx`, `.dotx`) and the report is written as a Word file on that form ([Word documents](docx#writing-on-a-form)). When the project or agent has **Default form files**, a **Project form** chip appears beside the format chip; ✕ removes it for this conversation only.
- You do not pick a structure. The document format that fits the purpose in the request, such as "meeting minutes", "agenda" or "results report", is chosen, and it brings the questions to ask before writing and the checks after.

## Step 1: Confirming the structure

KloudChat does not write the document right away. It proposes a **structure** first. If it lacks information it needs for writing, a **Before we start** card appears ahead of the structure and asks clarifying questions. Answer them, or select **Proceed with the material available**, and it moves on to the structure step.

![Structure confirmation card](/img/guide/proposal-card.png)

- You can edit section titles, change their order, and add or delete sections. After editing, **Back to the proposal** restores the original.
- On the structure card, **Format** lets you switch between Web report, HWP and Word. The change is applied when you press **Generate as it is**. For a web report, choose the look after writing, from **Edit** → **Layout → Look**.
- Press **Generate as it is**, or **Generate with my changes** if you edited anything, and writing begins.
- If you type a sentence into the input box below the card, it proposes the structure again with that sentence as a condition. You can only approve it with the buttons.
- If the topic needs diagrams or pictures, an **Add pictures?** card follows. It tells you the number of pictures and the estimated credits. Diagrams (flowcharts and structure charts) are written as a flow by the text model and drawn as shapes by the server, so they do not consume image credits. Pictures are drawn by the image model.

## Step 2: Writing

It writes section by section in order, and shows the first section in the right-hand panel as soon as it is complete. A section that fails is marked as failed, and writing continues for the remaining sections.

![A finished report](/img/guide/report.png)

A report in HWP or Word appears as a finished file in the editor beside the conversation, and you keep editing it there. What follows describes a web report.

The default look is an official document: body text justified, a navy accent, and plain statements with their basis instead of strong adjectives. Numbers stay in the sentences and tables that use them; there is no separate strip of key figures at the end. Change the look and the accent colour from **Edit** → **Layout → Look** and **Accent**, and the look of a whole project in its [design brief](../personal/projects.md#design-brief).

## Step 3: Revising

Two methods are provided.

- **Revise by request**: type into the input box at the bottom of the document, for example "Cut section 2 in half" or "Add one more section". [Revising by request](revise)
- **Edit directly**: use the **Edit** ribbon and the **Edit the section** menu in the panel to edit sentences, reorder sections, insert pictures, and get a review. [Artifact editor](editor)

## Items you can specify in your request

| Example input | What it does |
|---|---|
| "In four sections" | Sets the number of sections. The range is 3 to 12. |
| "Review report", "weekly report", "meeting minutes", "incident report" | Applies a structure that suits the document type. Only documents that call for a judgment include summary and recommendation sections. |
| "Results report", "bullet-style briefing", "comparison report", "meeting agenda" | Writes in that document format's head fields and outline, asks for missing information before writing, and checks the format afterwards. |

## Export

Open **Download** in the panel header and choose PDF, Word document (DOCX), Hangul document (HWPX), the Markdown source, or print. If any item has evidence that is not confirmed, it tells you before exporting. PDF reproduces the chosen look as it is, and DOCX puts an easily editable structure first.
