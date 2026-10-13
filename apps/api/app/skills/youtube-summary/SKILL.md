---
name: YouTube 영상 전사·요약
description: YouTube 주소를 주면 「YouTube 전사」 커넥터로 자막(없으면 음성 전사)을 받아, 그 전사문만으로 요약·핵심 정리·인용을 합니다. 전사문에 없는 내용은 쓰지 않습니다.
when_to_use: 사용자가 YouTube 링크를 주고 「이 영상 요약해 줘」「무슨 내용이야」「자막 뽑아 줘」「강의 핵심 정리」를 부탁할 때.
kinds: [chat]
required_tools: [mcp__youtube__transcript]
version: 1.1.0
---
Answer from the video's own transcript, never from the title or from what you know about the channel.

1. Call `mcp__youtube__transcript` once with the URL the person gave (`url`). Pass `language` only when they ask for a particular caption language (「한국어 자막으로」 → `ko`). A long video can take a few minutes when there are no captions and the audio is transcribed; say so in one line if the person is waiting.
2. If the tool returns an error or an empty transcript, say that the transcript could not be fetched and why in one sentence, and stop. Do not summarise from the title, description or general knowledge.
3. Head the note with the video it is about, from the lines above the transcript in the tool's result: 「- **영상**: 「<Title>」 (<Channel>, <Length>, <Published의 연도>년 게시)」 and 「- **링크**: <URL>」. Leave out a value the result does not give; never fill it from memory.
4. Summarise what was asked from the transcript only: by default a three-line gist, then the main points in order with their rough time (「앞부분」「중반」「끝부분」 when the transcript has no timestamps), then names, numbers and claims exactly as spoken. Quote short phrases in 「」 when the wording matters.
5. A transcript made from audio can mishear names and numbers. Mark a doubtful one 「(전사 불확실)」 rather than correcting it from your own knowledge.
6. When the person asks for the transcript itself, give it as is (cleaned of repeated filler only), not a summary.
7. Do not add outside facts, opinions or recommendations the video does not make; if the person asks whether a claim is true, say the video does not settle it and suggest searching.
