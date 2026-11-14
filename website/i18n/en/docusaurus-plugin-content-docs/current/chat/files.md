---
title: Attaching files
---

# Attaching files

Attach files from **+** → **Attach files or photos** in the composer, and the model answers from their content. You can attach several files at once, and you can drag and drop them or paste them in. The Images and Audio/Video screens do not offer attachments.

![A conversation answered from an attached document](/img/guide/chat-with-file.png)

## Supported formats

| Category | Formats | Conditions |
|---|---|---|
| Documents | PDF, Word (.docx), Hangul (.hwp, .hwpx), PowerPoint (.pptx), Excel (.xlsx) | A scanned PDF is read page by page when the administrator has connected text from scans (OCR). |
| Text and code | .txt, .md, .csv, .tsv, .json, and most code files | Read as text. |
| Images | PNG, JPG, GIF, WebP, TIFF, BMP | The text of a document photo is read with OCR. The picture itself goes only to **strict-local** models that support image recognition (marked 👁), as PNG, JPG, GIF or WebP of **4MB or smaller**. |
| Audio and video | mp3, wav, mp4, and similar | **25MB or smaller**. Converted to text and read when the administrator has connected speech transcription. |

The maximum size for a single file is 200MB (an administrator can change it). A larger file is stopped before it uploads, with the limit named. Older Office formats (.doc, .ppt, .xls) are not supported. A file that could not be read appears as **Unread attachment** in the processing steps of the answer.

## How long an attachment is referenced

An attached file stays available to later requests in the same conversation. It appears in the processing steps as **Attachment from an earlier turn**.

- When a file is too large to process at once, only the **parts relevant to your question** are selected and sent. Ask something such as "Tell me what Article 7 says" and the text around that article is sent.
- If the answer is not in the part that was sent, the model searches inside the file with the **Find in files** tool.
- The processing steps mark each attachment as used, truncated, or omitted.

The Reports and Slides screens do not carry over attachments from earlier requests, so attach the material for the document to that request directly. Files attached in another conversation are not referenced on their own either. Attach a file you already uploaded again with **+** → **Choose from Files**, and to use the same material in many conversations all the time, register it in a [project](../personal/projects). Where the administrator has turned on **Internal material stays strict-local**, a request with attachments is answered by the strict-local twin of the same model. [Privacy](../privacy)

## Files

**Files** in the left menu holds every file of your account, under three tabs.

- **Uploaded**: files you attached to conversations or uploaded to projects
- **Made in conversations**: files made during a conversation, such as code-run outputs (CSV and the like), images and charts
- **Working documents**: the HWP, Word and PowerPoint documents and the web reports and web slides you made in conversations or edited in an editor

Each row shows the size, when it came, and which conversation or project it belongs to. Find files by name, download them, and delete one or a selection. Deleting a project file takes it out of the project too.

To use the same material again in a new chat, report or deck, pick it under **+** → **Choose from Files** in the input box. Nothing is uploaded again, so there is no wait.

### Working documents

The **Working documents** tab is where you find the documents you were working on again. Each document is one row, however many versions it has, and the one you edited last comes first.

- Each row shows the format (HWP document, Word document, PowerPoint document, web report, web slides), the number of versions (v3 and so on), when it was last edited, and the project and conversation it belongs to.
- Show one format with the format chips, find documents by title, and sort them **Recently edited**, **Least recently edited** or **By title**.
- Click the title or **Open in its editor** to open the same editor as from the chat, beside the document's conversation. An HWP, Word or PowerPoint document with no conversation opens beside a new chat and joins it. A web report or web slides with no conversation opens in the editing panel of the **Artifacts** screen.
- **Go to the conversation** takes you to the conversation that made it.
- **Download** saves the current version in its own format: HWP, Word and PowerPoint documents as the file itself, a web report as Markdown (.md), web slides as PowerPoint (.pptx). For other formats, use Download (export) on the document's screen.
- **Rename** changes the title, and you can delete one document or a selection. Deleting a document removes its version history too; the conversation that made it stays.

A working document is not a copy: it is the document itself. What you change in the editor shows here at once, and it is the same document as on the **Artifacts** screen. Opening an HWP, Word or PowerPoint file from the file box with **Edit** puts its copy under Working documents, and the uploaded file stays under **Uploaded**.

When you delete a conversation you can tick **Also delete the conversation's files**. Left unticked, its files stay in Files. Project files are never deleted with a conversation.

## Improving recognition accuracy

- State exactly what you are looking for. "Summarize this file" is less accurate than "List only the remote work application steps from this file".
- Attach table-heavy material as Excel or CSV rather than PDF. It is read far more accurately that way.
- An attached HWP (.hwp, .hwpx), Word (.docx) or PowerPoint (.pptx) file opens as that same file in its editor with **Open as HWP**, **Open in Word editor** or **Open in PowerPoint editor** on the file chip (the format is kept). The same file in the file box opens as a copy with **Edit**. To get a web report or web slides as an HWP, Word or PowerPoint file, use **Download** (export) on that screen.

## Scanned PDFs and document photos

When the administrator has connected text from scans (OCR), the text of PDF pages without a text layer and of document photos is read inside the organization after upload.

- The file chip and the project and agent material lists show progress, such as **Reading text, page 3 of 10**. Ask before it finishes and the answer draws only on the pages read so far, and says it cannot know the rest.
- The OCR engine reads first; pages it is unsure of, and pages with a table, are read again by a strict-local model that reads pictures. Page pictures go nowhere else.
- Where the two readings differ in words or numbers, the file shows **n pages to check**. Compare important values such as amounts and dates with the original.
- A photo with hardly any text is treated as a picture only.
- The administrator sets how many pages of one document are read. Past that, the file shows **first n pages**.

## Attaching images

Images are sent only to strict-local models that support image recognition. The personal data check reads text only, so images never go to a model that may send data outside the organization. If you select any other model, the file is attached but the model cannot see its content. Images from earlier requests are not sent again, so attach the image again to ask more about it.
