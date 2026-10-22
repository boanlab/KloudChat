---
title: Connectors
---

# Connectors

A connector is an external tool (an MCP server) that the model can call. Install one from **Customize** → **Connectors**, and turn it on or off from **+** → **Connectors** in the composer. The on or off state applies to your **whole account**.

![The Connectors screen](/img/guide/connectors.png)

## Built-in connectors

Connectors the administrator marks **Built in** are installed for everyone and appear under **My connectors** with a Built in badge. If you do not use one, switch it off, or switch off single tools. A built-in connector cannot be removed; one you switched off comes back as it was when you switch it on again.

| Connector | What it does |
|---|---|
| Time | Looks up the current time and date. Available in Chat, Reports, and Slides. |
| YouTube transcription | Fetches a video's captions from its address, or transcribes the audio when there are none. Available in Chat and Reports. |
| Deep research | Searches and reads the web over several rounds and sums up what it found. It can take minutes. Available in Chat and Reports. |

The catalogue shows only the entries the administrator made visible in the connector catalogue. A connector hidden from the list keeps working for anyone who already installed it.

## Adding a server directly

If you already use an MCP server, connect it with **Add server directly**. Enter the name, the transport (stdio, http, or sse), the run command or endpoint address, the environment variables, and the authentication method. Environment variables and credentials are not shown again after saving. A stdio server runs on the API server, so you can register only a command an administrator allows, exactly as allowed, and without environment variables.

## Tool permissions

Set each tool on or off in the connector detail screen. A **write** tool that changes data asks for confirmation before it runs. Content returned by a connector is treated as external input, so any instructions inside it are not executed.

The more tools you enable, the less likely the model is to choose the right one, so enable only the tools you actually use.
