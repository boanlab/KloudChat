---
title: Attaching files
---

# Attaching files

Attach files with 📎 in the composer, and the model answers from their content. You can attach several files at once, and drag and drop and paste are supported. The Images and Audio/Video screens do not offer attachments.

![A conversation answered from an attached document](/img/guide/chat-with-file.png)

## Supported formats

| Category | Formats | Conditions |
|---|---|---|
| Documents | PDF, Word (.docx), Hangul (.hwp, .hwpx), PowerPoint (.pptx), Excel (.xlsx) | The file must contain text. Scanned images are not recognised. |
| Text and code | .txt, .md, .csv, .tsv, .json, and most code files | Read as text. |
| Images | PNG, JPG, GIF, WebP | **4MB or smaller**. Sent only to models that support image recognition, marked 👁 in the list. |
| Audio and video | mp3, wav, mp4, and similar | **25MB or smaller**. Converted to text and read when the administrator has connected speech transcription. |

The maximum size for a single file is 200MB. Older Office formats (.doc, .ppt, .xls) are not supported. A file that could not be read appears as **Unread attachment** in the processing steps of the answer.

## How long an attachment is referenced

An attached file stays available to later requests in the same conversation. It appears in the processing steps as **Attachment from an earlier turn**.

- When a file is larger than can be processed at once, only the **parts relevant to your question** are selected and sent. Ask something such as "Tell me what Article 7 says" and the text around that article is sent.
- If the answer is not in the part that was sent, the model searches inside the file with the **Find in files** tool.
- The attachment entry in the processing steps shows how far the file was used, as used, truncated, or omitted.

The Reports and Slides screens do not carry over attachments from earlier requests, so attach the material for the document to that request directly. Files attached in another conversation are not referenced either. To use the same material in several conversations, register it in a [project](../personal/projects).

## Improving recognition accuracy

- State exactly what you are looking for. "Summarise this file" is less accurate than "List only the remote work application steps from this file".
- Material with many tables is recognised more accurately as Excel or CSV than as PDF.
- For an attached Hangul file (.hwpx), select **Open as document** on the file chip to convert it straight into an editable report. The conversion calls no model, so it uses no credits.

## Attaching images

Images are sent only to models that support image recognition. If you select a model without image recognition, the file is attached but the model cannot see its content. Images from earlier requests are not sent again, so attach the image again to ask more about it.
