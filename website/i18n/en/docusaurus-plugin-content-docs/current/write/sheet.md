---
title: Sheets
---

# Sheets

Make and edit tables and calculations as an Excel file (`.xlsx`). A sheet editor opens beside the conversation, and what is written for your request appears in it at once. You can also edit the same file directly in the editor.

The sheet screen appears as **Sheet** among the screen chips on Home when an administrator has turned it on.

## Getting started

- On Home choose **Sheet** and describe the table you want, for example "Make a monthly sales table with a total row and a month-over-month change column as formulas".
- To start with nothing typed, press **Start with a blank sheet**. A conversation starts with a blank workbook open.
- An `.xlsx` file in a conversation or in the file box opens in the editor with **Open as a sheet**.
- Asking in chat for something "as an Excel file", "as xlsx" or "as a spreadsheet" also writes it in this conversation's sheet. The screen does not change; the sheet opens in the side panel.
- `.xls` files (Excel 97–2003 format) cannot be opened, and macros in a macro-enabled file are not handled. Save the file as `.xlsx` in Excel and upload that.

## Writing and editing by chat

- A whole workbook is written in one go: sheet names, headers, rows, formulas, number formats (`#,##0`, `0.0%`, dates), column widths and merges. The first row is a header, bold and frozen.
- **A formula goes in as a formula.** A value that follows from other cells, such as a total, a difference, a rate or price × quantity, is written as a formula (`=SUM(B2:B9)`), not a number typed in, so the result follows when you edit a cell.
- The totals and rates quoted in the answer are the values the server calculated from the formulas and saved in the file. The server calculates arithmetic, comparisons, cell and range references (across sheets too) and SUM, AVERAGE, MIN, MAX, COUNT, COUNTA, IF, IFERROR, ROUND, ROUNDUP, ROUNDDOWN, ABS, INT, MOD, SQRT, POWER, AND, OR, NOT, SUMIF, COUNTIF, SUMPRODUCT, CONCAT and LEN. A formula that uses any other function is left as it is and is calculated when you open it in Excel or the editor.
- **Numbers the material does not give are not invented.** A value you did not provide stays a bracket blank such as `[매출액]` in the cell, and the answer says what is missing.
- Requests such as "insert a row above row 5", "delete column C", "add another sheet" or "format column B with thousands separators" cover writing cells and ranges, formats, column widths, merges, inserting and deleting rows and columns, and adding, renaming and deleting sheets. When rows or columns are inserted or deleted, the cells below and to the right and the references inside formulas move with them.
- Ask "put this attached CSV or Excel file in a sheet" and the cells are copied as they are: no rounding, no change of units, empty cells stay empty. The server copies a big table from the file; the model does not retype it. A messy table with titles, totals and notes goes through the **Excel cleanup** skill first ([Skill recipes](../chat/skill-recipes)).
- On request it adds an Excel chart (bar, line or stacked bar) beside the table. The editor cannot draw charts, but the file keeps it and Excel shows it.
- Formulas that call other files or addresses or run something (`WEBSERVICE`, `HYPERLINK`, references to other workbooks and the like) are refused.
- While the sheet is being edited, the editor is locked; when the new version is saved, the editor loads it. The several changes of one call are all applied or none is.
- Cells you typed are not deleted or overwritten unless you ask.

## Editing by hand and saving

- Click a cell to edit its value or formula; the toolbar at the top does formatting, inserting and deleting rows and columns, adding and renaming sheets, column widths and merges.
- After an edit the header shows **Unsaved**. **Save**, or Ctrl+S, saves a new version. A save writes only the cells you changed, so charts, pictures, pivot tables and conditional formats that the editor cannot draw stay as they were.
- If you send a chat with unsaved edits, you first choose **Save and send**, **Discard and send** or **Cancel**. The chat always starts from the saved file.
- If another version was saved in the meantime, it is not saved over and you are told. Choose **Download my edits** to get your current edits as a file, or **Load the latest version**.
- See and restore versions under **Version history** in the ⋯ menu, or ask in chat, "undo what you just changed".

## Sharing

Share only this sheet by link with the share icon in the header. Sharing a conversation also opens its sheet. The reader sees a read-only table with a tab per sheet and downloads the `.xlsx` with **Download**. The table shows values (a formula shows its calculated value) and the downloaded file keeps the formulas. The table shows up to 500 rows and 60 columns; download a bigger sheet to see all of it. The audience options are the same as for other artifacts ([Sharing](share)).

## Download and limits

- **Download → Excel file** gives the editor's current content as `.xlsx`.
- A new workbook takes up to 50,000 cells and one edit up to 20,000. Add a bigger table in parts.
- Changing an existing chart, pivot tables, conditional formats and macros cannot be made or edited; asked for one, the chat says so.
- At phone width a sheet can be read and downloaded; editing and saving are on a wider screen.
