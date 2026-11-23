---
title: Images and audio/video
---

# Images and audio/video

The **Image** and **Audio/Video** tabs appear on the home screen only when the administrator has enabled them. Most of these features use external generation services, so they consume credits, and the estimated credits are shown before you send. There is no attach menu; a picture is attached only through **Edit image**, described below.

## Images

Set the image options below the input box.

| Chip | Choices |
|---|---|
| Ratio | 16:9 · 9:16 · 4:3 · 1:1 (only the ones the model supports) |
| Style | **Image**: Auto · photo · illustration · infographic · watercolor<br/>**Exact figure**: structure diagram · flow diagram · concept diagram · chart<br/>Prompt as written |
| Text | Auto · Korean · English · none |

One picture is made at a time. The **Image** group is drawn by the image model; the **Exact figure** group is drawn as shapes or a chart (an outside image model draws the structure, flow and concept ones, and the text is checked). **Prompt as written** sends the text to the image model unchanged.

Pick the image model from the model name at the right of the composer. The list comes from the models registered in the model gateway; the default setup's external image models are Seedream 5.0 Flash, Recraft V4.1, Grok Imagine Image 2.0, GPT-5.4 Image 2 and Gemini 3 Pro Image. When the administrator connects a self-hosted image model, strict-local image models appear as well. Pictures in a project conversation, a conversation with attachments, or a strict-local conversation are drawn by strict-local models only. [Privacy](../privacy)

The result card provides enlarged view, download, **Generate again**, **Fix the prompt**, and **View the prompt sent** (the final sentence that was actually sent). A chart is drawn as code by the text model. You can use **View the drawing code** and **Redraw with this code**. If you choose a model that does not support ratio settings, a message tells you that the image is generated as a square.

### Editing an image

On a picture you made, a picture attached in a conversation, or a picture in the file box, **Edit image** puts that picture in the composer. Write what to change and send it: a new version is made and the original stays as it was. On the result, **Before & after** lays the original over the new one, and **Versions** opens the earlier ones.

Only models that take a picture and change it can edit; the model list marks them **Edits**. In the default setup they are Gemini 3 Pro Image, GPT-5.4 Image 2, Seedream 5.0 Flash and Grok Imagine Image 2.0. With a model that only draws (Recraft V4.1, strict-local drawing models such as Z-Image and Qwen-Image) the button is off and says why, and **Switch to a model that edits** changes it. The original picture goes to the external model as an attachment would, except that a picture in a strict-local conversation (Super Agent strict-local included), a picture a strict-local model drew, and an uploaded picture when the administrator keeps internal material on strict-local models never leave. When the administrator has connected a self-hosted model that edits (Qwen-Image-Edit 2511), that model edits them whatever image model is picked; without one they cannot be edited. That model only edits, so it is not in the list of models that draw new pictures. Credits are charged as for making a picture. There is no editing of a selected region only.

**Structure, flow and concept diagrams** are drawn in one of two ways, depending on the image model you chose. With a model that runs only inside your institution (strict-local), or when no image model is set, the text model writes the description as a flowchart, and the server draws it with the same shapes as the diagrams in reports and slides and keeps it as a picture. If there are too many steps or the content cannot be put as a flowchart, the message "This figure cannot be drawn. Try again with fewer steps." appears and no picture is made. With an image model outside the institution, the text model first lists the labels and layout from your request, and the image model draws the figure itself in the style of a paper figure. The text in the picture is then compared with the label list; if something is wrong, the figure is drawn once more and the better one is kept. Credits are charged for the pictures actually drawn.

Images, audio and video have no forms. The **Format** chip above the prompt box is offered only for reports and slides. The colors and style written in the project's or agent's [design brief](../personal/projects.md#design-brief) are applied to the image request as well.

## Audio

Choose the **Type**, narration (with a choice of six **voices**) or music, and generate it at 15, 30, 60, or 120 seconds. The result comes with a player and a download, and narration also shows the script. Audio is charged per generation.

## Video

Set the ratio (16:9, 9:16, 1:1), the resolution (720p or 1080p), whether there is sound, and the length (4, 6, 8 or 10 seconds). Video generation takes a long time, so the card shows the progress. You can still see the finished result after refreshing the screen or connecting from another device. You can cancel a generation. Credits are deducted only when it completes. If you choose a combination that the model does not price, a message saying "This model does not produce this combination" appears and sending is blocked. The design brief does not apply to audio and video.
