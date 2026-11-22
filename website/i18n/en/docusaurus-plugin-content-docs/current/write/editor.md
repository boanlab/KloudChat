---
title: Artifact editor
---

# Artifact editor

You can edit web reports and web slides directly in the right-hand panel. Both panels have, in the header, the title, the version number (v), a **Not saved** mark, **Save**, **Download**, a share icon, a ⋯ menu, and width and close buttons. On a narrow screen, sharing moves into the ⋯ menu. At phone width the panel is read-only; ask for changes in the conversation ([Revising by request](revise)).

HWP, Word and PowerPoint artifacts open in their own editors instead of this one. See [HWP documents](hwp), [Word documents](docx) and [PowerPoint documents](pptx).

## Report panel

The **Web view** and **Page view** (actual A4 pages) buttons in the header switch how the report is shown. **Review** opens and closes the evidence panel, and **Edit** opens the editing ribbon.

### Editing while reading

- The **Edit the section** menu beside a section title: add a section before or after, duplicate this section, move up or down, delete this section, **Rewrite just this section**, and **Review** (a fact check of the section's claims against the evidence). The last remaining section cannot be deleted. Type an instruction into **Rewrite just this section** to have it done that way; leave it empty to write the section again from scratch.
- Selecting a sentence in the body shows **Fix this passage**. It quotes the selected passage and asks for the change you describe.
- The ⋯ menu: **Contents** (progress per section), **Edit the source**, **Version history**.

### Editing ribbon

**Edit** opens the tabs **Home · Insert · Layout · Review · View**. You type directly on the page and save with `Ctrl+S` (or **Save**).

| Tab | Features |
|---|---|
| Home | Undo and redo; bold, italic, strikethrough, code, link; bullet, numbered and task lists; indent and outdent; quotation; styles (body, section heading, subheading, small heading); find and replace; **Markdown source** (edit the whole document as Markdown) |
| Insert | Table (add and delete rows and columns), picture file, diagram (mermaid), code block, citation number, insert a blank, divider, page break, **Made pictures** |
| Layout | **Look** (editorial, magazine, minimal), **Accent**, **Page setup** (header, footer, page numbers, margins) |
| Review | **Document checks**, **Evidence** (the source panel), **Stage**, **Versions**, review notes (a note on the selected sentence) |
| View | **Outline**, **Markdown source** |

- **Made pictures** inserts a picture into a section you choose, either newly generated or one you already made. An inserted picture is part of the file, so it is carried along when you print and share. If the image feature is turned off, you cannot generate a new one.
- The look of a whole project goes in its [design brief](../personal/projects.md#design-brief).

### Download

**Download** offers PDF, Word document (DOCX), Hangul document (HWPX) and the Markdown source, and **Print** is in the same menu. If some items have no verified evidence, the menu shows **evidence to check before exporting**.

## Slides panel

The slide list is on the left, the selected slide in the middle with its speaker notes below, and a toolbar on top.

### Toolbar

Undo and redo, zoom, **Layout**, **Image**, **Table**, **Chart**, font, font size, bold, italic, underline, text color, clear formatting, **Look** and **Present**.

- **Layout**: choose the slide's shape from the basic layouts and the purpose patterns (grid and cards, list, flow and steps, compare and analyse, figures and charts, one sentence). Content that a new layout cannot show is kept, not deleted, and **Move to a new slide** brings it out.
- **Image**: **Upload image** (PNG, JPG, GIF, WebP, 5MB or less), **Add a picture** or replace it (image model), replacing an automatically generated diagram with a picture drawn by an image model (**Replace with a drawn picture**), show the whole image or fill the frame, size (small, medium, large), left or right placement, and remove the image.
- **Look**: choose from editorial, minimal, poster, split, dark, slate, warm, pastel, forest, paper and mono, change the accent color, set an accent for the selected slide only, or **Reset this slide's formatting**. **Format several slides** changes the accent color and font size (80 to 120%) of several slides at once.
- Click text on a slide to edit it directly. The text box under the slide is for going through the slide at once: the first line is the title, each following line is an item, and a line split by `|` is a table row. Table, figure and card slides have a data area for rows and columns, and chart slides let you change the type (bar, line), the unit and the series.
- Drag slides in the list to reorder them; the slide menu adds a slide before or after, duplicates, moves and deletes. Save with `Ctrl+S`.

### Review and the ⋯ menu

**Review** (in the ⋯ menu or the toolbar) opens the review pane. It holds **slides to check**, **slides that may be cut off**, the automatic checks and review request, stages, versions, and the **Review notes** per slide. The ⋯ menu also has **Present**, **Redo this slide**, **Fact-check this slide** and **Version history**.

### Download

**Download** offers PowerPoint (PPTX), PDF (for presenting), and text (with notes). The menu shows the counts of slides that may be cut off, slides that need their evidence checked, and unresolved review notes. **Present** enters [presenter view](slides#presentation-mode).

## Review

| Feature | Description |
|---|---|
| Automatic checks | Checks the document without calling a model. If there are items that must be fixed (P0: empty content, blocks that were not written, placeholders, unrendered Markdown, Chinese characters mixed into Korean sentences, figures with no evidence, arithmetic errors, the writer's plan or instructions left in place of the text) it shows **{n} to fix**; if there are only items worth a look (P1: filler phrases, sentences that start with an emoji, repeated lines, too many lines on one slide, lines that run past two rows) it shows **{n} to look at**. Each item has **Fix it**, and with more than one there is **Fix all**. |
| Ask for a review | Calls the model once to read the document and give a score out of 10 with up to six points to address. The score is for reference and does not restrict export. Arithmetic is not judged. |
| Fact check | Compares claims against the evidence: in a report, **Review** in the section menu; in slides, **Fact-check this slide**. A verdict with no source URL is lowered to **Needs review**. Opinions and definitions are not judged. |
| Evidence panel (report) | Shows the source list, citation checks, citation styles (APA, MLA, Chicago, IEEE), adding material directly, deleting unused material, and the research log (search terms, accepted, excluded). |
| Review notes | In a report, select a sentence and leave a note; in slides, add a note per slide and **Mark as done** or reopen it. The number of unresolved notes is shown. |

## Version management and conflict handling

The earlier version is kept every time you save, rewrite, or restore. In the ⋯ menu, **Version history** (in a report, **Review → Versions** in the ribbon) shows the content and changes of an earlier version, and you can roll back to it.

If the document was saved elsewhere in the meantime, your save is refused and you are told so. Choose **Download my edits** to keep your edits as a file, or **Load the latest version**.
