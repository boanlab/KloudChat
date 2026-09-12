---
title: Connectors
---

# Connectors

A connector is an external tool (an MCP server) that the model can call. Install one from the account menu → **Connectors**, and turn it on or off with 🔌 in the composer. The on or off state applies to your **whole account**.

![The Connectors screen](/img/guide/connectors.png)

## Built-in connectors

| Connector | What it does |
|---|---|
| Time | Looks up the current time and date. Available in Chat, Reports, and Slides. |
| YouTube transcript | Fetches the subtitles of a video. If there are no subtitles, it transcribes the audio. |
| Deep research | Investigates by repeating searches and page reads. It takes several minutes to tens of minutes, and it is available only when the administrator has connected a deep research server. |

## Adding a server directly

If you already use an MCP server, connect it with **Add server directly**. Enter the name, the transport (stdio, http, or sse), the run command or endpoint address, the environment variables, and the authentication method. Environment variables and credentials are not shown again after saving.

## Tool permissions

Set each tool on or off in the connector detail screen. A **write** tool that changes data asks for confirmation before it runs. Content returned by a connector is treated as external input, so any instructions inside it are not executed.

The more tools you enable, the less likely the model is to choose the right one, so enable only the tools you actually use.
