---
title: Writing reports
---

# Writing reports

On the home screen, select **Report** and enter the topic of the report you want to write. If you have supporting material, attach it from **+** → **Attach files or photos** in the composer. When you attach material, KloudChat fills the figures in from that material instead of estimating them. The document is written in the language you asked in: an English memo pasted on its own gives an English document, and a Korean ask over English material ("아래 메모로 회의록을 만들어줘") a Korean one; the reply when it is done is in the document's language. An agent with an output language set writes in that language. Where a search server is connected, it searches the web before writing and cites the sources. A request about domestic research trends or one that names KCI also finds KCI, DBpia and RISS papers through the same search and cites their authors, year, journal and DOI.

## Step 1: Confirming the structure

KloudChat does not write the document right away. It proposes a **structure** first. If it lacks information it needs for writing, a **Before we start** card appears ahead of the structure and asks clarifying questions. Answer them, or select **Proceed with the material available**, and it moves on to the structure step.

![Structure confirmation card](/img/guide/proposal-card.png)

- You can edit section titles, change their order, and add or delete sections. After editing, **Back to the proposal** restores the original.
- The structure card covers content only. Choose the document theme after writing, from **Edit → Theme** in the ribbon.
- Press **Generate as it is**, or **Generate with my changes** if you edited anything, and writing begins.
- If you type a sentence into the input box below the card, it proposes the structure again with that sentence as a condition. You can only approve it with the buttons.
- If the topic needs diagrams or pictures, an **Add pictures?** card follows. It tells you the number of pictures and the estimated credits. Diagrams (flowcharts and structure charts) are written as a flow by the text model and drawn as shapes by the server, so they do not consume image credits. Pictures are drawn by the image model.

## Step 2: Writing

It writes section by section in order, and shows the first section in the right-hand panel as soon as it is complete. A section that fails is marked as failed, and writing continues for the remaining sections.

![A finished report](/img/guide/report.png)

The default look is an official document: body text justified, a navy accent, and plain statements with their basis instead of strong adjectives. Numbers stay in the sentences and tables that use them; there is no separate strip of key figures at the end. Change the colour and theme from **Edit → Theme** on the ribbon.

## Step 3: Revising

Two methods are provided.

- **Revise by request**: type into the input box at the bottom of the document, for example "Cut section 2 in half" or "Add one more section". [Revising by request](revise)
- **Edit directly**: use the ribbon in the panel to edit sentences, reorder sections, insert pictures, and get a review. [Artifact editor](editor)

## Items you can specify in your request

| Example input | What it does |
|---|---|
| "In four sections" | Sets the number of sections. The range is 3 to 12. |
| "Review report", "weekly report", "meeting minutes", "incident report" | Applies a structure that suits the document type. Only documents that call for a judgment include summary and recommendation sections. |
| Selecting a template from **+** → Pick a template | Applies the structure rules and typesetting of that template. |

## Export

In the ribbon, open **File → Export** and choose PDF, Word (DOCX), Hangul (HWPX), the Markdown source, or print. If any item has evidence that is not confirmed, it tells you before exporting. PDF reproduces the theme as it is, and DOCX puts an easily editable structure first.
