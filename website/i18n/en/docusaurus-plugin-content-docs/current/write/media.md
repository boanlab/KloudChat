---
title: Images and audio/video
---

# Images and audio/video

The **Image** and **Audio/Video** tabs appear on the home screen only when the administrator has enabled them. Most of these features use external generation services, so they consume credits, and the estimated credits are shown before you send. Attaching files is not supported.

## Images

Set the image options below the input box.

| Chip | Choices |
|---|---|
| Ratio | 16:9 · 9:16 · 4:3 · 1:1 (only the ones the model supports) |
| Style | **Picture**: Auto · photo · illustration · infographic · watercolor<br/>**Exact figure**: structure diagram · flow diagram · concept diagram · chart<br/>Prompt as written |
| Text | Auto · Korean · English · none |

One picture is made at a time. The **Picture** group is drawn by the picture model; the **Exact figure** group is drawn as shapes or a chart without it, so its text is exact. **Prompt as written** sends the text to the picture model unchanged.

Pick the picture model from the model name at the right of the composer. The list comes from the models registered in the model gateway; the default setup's external picture models are Seedream 5.0 Flash, Recraft V4.1, Grok Imagine Image 2.0, GPT-5.4 Image 2 and Gemini 3 Pro Image. When the administrator connects a self-hosted picture model, strict-local picture models appear as well. Pictures in a project conversation, a conversation with attachments, or a strict-local conversation are drawn by strict-local models only. [Privacy](../privacy)

The result card provides enlarged view, download, **Generate again**, **Fix the prompt**, and **View the prompt sent** (the final sentence that was actually sent). A chart is drawn as code by the text model. You can use **View the drawing code** and **Redraw with this code**. If you choose a model that does not support ratio settings, a message tells you that the image is generated as a square.

**Structure, flow and concept diagrams** do not use the image model. The text model writes the description as a flowchart, and the server draws it with the same shapes as the diagrams in reports and slides and keeps it as a picture. If there are too many steps or the content cannot be put as a flowchart, the message "This figure cannot be drawn. Try again with fewer steps." appears and no picture is made.

Images, audio and video have no templates. **+** → Pick a template in the composer is offered only for reports and slides. The colors and the image style of the project theme are applied to the request as well.

## Audio

Generates narration (with a voice choice) or music at 15, 30, 60, or 120 seconds. The result comes with a player and a download, and narration also shows the script. Audio is charged per generation.

## Video

Set the ratio, the resolution (720p or 1080p), whether there is sound, and the length (4 to 10 seconds). Video generation takes a long time, so the card shows the progress. You can still see the finished result after refreshing the screen or connecting from another device. You can cancel a generation. Credits are deducted only when it completes. If you choose a combination that the model does not price, a message saying "This model does not produce this combination" appears and sending is blocked. The theme does not apply to audio and video.
