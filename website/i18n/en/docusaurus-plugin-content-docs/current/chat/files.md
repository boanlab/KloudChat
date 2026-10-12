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

The maximum size for a single file is 200MB. Older Office formats (.doc, .ppt, .xls) are not supported. A file that could not be read appears as **Unread attachment** in the processing steps of the answer.

## How long an attachment is referenced

An attached file stays available to later requests in the same conversation. It appears in the processing steps as **Attachment from an earlier turn**.

- When a file is too large to process at once, only the **parts relevant to your question** are selected and sent. Ask something such as "Tell me what Article 7 says" and the text around that article is sent.
- If the answer is not in the part that was sent, the model searches inside the file with the **Find in files** tool.
- The processing steps mark each attachment as used, truncated, or omitted.

The Reports and Slides screens do not carry over attachments from earlier requests, so attach the material for the document to that request directly. Files attached in another conversation are not referenced either. To use the same material in several conversations, register it in a [project](../personal/projects). Where the administrator has turned on **Internal material stays strict-local**, a request with attachments is answered by the strict-local twin of the same model. [Privacy](../privacy)

## Improving recognition accuracy

- State exactly what you are looking for. "Summarize this file" is less accurate than "List only the remote work application steps from this file".
- Attach table-heavy material as Excel or CSV rather than PDF. It is read far more accurately that way.
- For an attached Hangul file (.hwpx), select **Open as document** on the file chip to convert it straight into an editable report. The conversion calls no model, so it uses no credits.

## Scanned PDFs and document photos

When the administrator has connected text from scans (OCR), the text of PDF pages without a text layer and of document photos is read inside the organization after upload.

- The file chip and the project and agent material lists show progress, such as **Reading text, page 3 of 10**. Ask before it finishes and the answer draws only on the pages read so far, and says it cannot know the rest.
- The OCR engine reads first; pages it is unsure of, and pages with a table, are read again by a strict-local model that reads pictures. Page pictures go nowhere else.
- Where the two readings differ in words or numbers, the file shows **n pages to check**. Compare important values such as amounts and dates with the original.
- A photo with hardly any text is treated as a picture only.
- The administrator sets how many pages of one document are read. Past that, the file shows **first n pages**.

## Attaching images

Images are sent only to strict-local models that support image recognition. The personal data check reads text only, so images never go to a model that may send data outside the organization. If you select any other model, the file is attached but the model cannot see its content. Images from earlier requests are not sent again, so attach the image again to ask more about it.
