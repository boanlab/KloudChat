---
title: Output editor
---

# Output editor

You can edit reports and slides directly in the right-hand panel. The features are grouped by the tabs in the ribbon at the top.

## Report panel

The ribbon consists of **Home · Edit · Insert · Layout · Review · View · File**.

### Edit

- **Edit document**: edit directly on the page. It supports bold, italic, underline, strikethrough, text colour, highlighter, line spacing, lists and indentation, quotations, table insertion with row and column operations and merging, page breaks, source citation insertion, find and replace, and undo.
- **Source**: edit the whole Markdown. Save with `Ctrl+Enter`.
- The menu beside a section title adds a section before or after, duplicates, moves up or down, deletes, runs **Rewrite this section only**, and runs a review. The last remaining section cannot be deleted. If you type an instruction into **Rewrite this section only**, only that section is written again.
- Selecting a sentence in the body shows **Fix this part**. It quotes the selected sentence and asks for a revision.

### Insert

In **Add a picture**, set the section to insert into, how the picture is produced (generate a new one or choose an existing one), and the caption. An inserted picture is part of the file, so it is carried along when you print and share. If the image feature is turned off, you cannot generate a new one.

### Layout

It provides page setup (header, footer, page numbers), document design (editorial, magazine, minimal), accent colour, and format changes.

### View

Switch between **Web view** and **Page view** (actual A4 pages), and check progress and the word count in the contents pane. Wide view, document only, and close panel are chosen from the buttons at the top right of the panel, not from the ribbon.

### File

Exports to PDF, Word (DOCX), Hangul (HWPX), the Markdown source, and print.

## Slides panel

The ribbon consists of **Home · Edit · Insert · Review · View · Slide Show · File**.

### Home

Choose the slide design from editorial, minimal, poster, split, dark, steel, warm, pastel, forest, academic, and monochrome, change the accent colour, and run **Rebuild this slide**.

### Edit

- Click text on a slide to edit it directly. You can set bold, italic, size, and colour.
- In bulk text box editing, the first line becomes the title and each line becomes an item, and `|` separates the rows of a table.
- It provides speaker notes, table data editing (add, move, and delete rows and columns), and chart editing (bar and line, units, series).
- In the picture tool, upload an image (PNG, JPG, GIF, WebP, 5MB or less) and set the display mode, the size, and the left or right placement.
- When the content overflows, it supports auto fit, splitting the slide, changing the layout, adjusting the font size, and undo. Save with `Ctrl+S`.
- The slide menu adds a slide before or after, duplicates, moves, deletes, and sets the layout and the font size. You can select several slides to change the accent colour and font size in bulk, or to reset the formatting.

### Insert

It provides adding and replacing pictures, and replacing an automatically generated diagram with a picture drawn by an image model.

### View and Slide Show

It provides the slide list pane and slide navigation. For presentation mode, see [Writing slides](slides#presentation-mode).

### File

Exports to PowerPoint (PPTX), PDF, and text (with notes), and supports copying the script.

## Review

| Feature | Description |
|---|---|
| Automatic checks | Detects **Must fix** (empty content, blocks that were not written, placeholders, figures with no evidence, arithmetic errors) and **Worth a look** (padding expressions, sentences that start with an emoji, repeated lines, lines longer than two rows), and corrects them with **Fix** or **Fix all**. For slides it reports **n slides at overflow risk** separately. |
| Get a review | The model reads the document once and gives a score out of 10 with up to six points to address. The score is for reference and does not restrict export. |
| Fact check | Compares the claims in a section or a slide against the evidence. A verdict with no source address is lowered to **Needs checking**. Opinions and definitions are excluded from judgement. |
| Evidence panel (report) | Provides the source list, citation checks, citation styles (APA, MLA, Chicago, IEEE), adding material directly, deleting unused material, and the research log (search terms, accepted, excluded). |
| Review notes (slides) | Add notes per slide and mark them resolved or reopened. Unresolved notes are shown with a badge. |
| Version history | See below. |

## Version management and conflict handling

A snapshot is saved every time you save, rewrite, or restore. In the ribbon, open **Review → Version history** to see the content of an earlier version, compare the additions, deletions, and edits, and then roll back.

If the same document was edited in another window, it asks you to choose between **Load the latest version** and **Copy my edits**.

## Editing limits on documents with a format applied

Reports and slides with a format applied support block-level rewriting only, and Markdown source editing is not provided. The export formats are PDF, DOCX/PPTX, HWPX, HTML, and text.
