---
title: MCP servers
---

# MCP servers

An MCP server is an external tool that the model can call. Install one from **Customize** → **MCP servers**, and turn it on or off from **+** → **MCP servers** in the composer. The on or off state applies to your **whole account**.

![The MCP servers screen](/img/guide/connectors.png)

## Built-in MCP servers

MCP servers the administrator marks **Built in** are installed for everyone and appear under **My MCP servers** with a Built in badge. If you do not use one, switch it off, or switch off single tools. A built-in MCP server cannot be removed; one you switched off comes back as it was when you switch it on again.

| MCP server | What it does |
|---|---|
| Time | Looks up the current time and date. Available in Chat, Reports, and Slides. |
| YouTube transcription | Fetches a video's captions from its address, or transcribes the audio when there are none. Available in Chat and Reports. |
| Deep research | Searches and reads the web over several rounds and sums up what it found. It can take minutes. Available in Chat and Reports. |

The catalogue shows only the entries the administrator made visible in the MCP servers tab. An MCP server hidden from the list keeps working for anyone who already installed it.

## Adding a server directly

If you already use an MCP server, connect it with **Add server directly**. Enter the name, the transport (stdio, http, or sse), the run command or endpoint address, the environment variables, and the authentication method. Environment variables and credentials are not shown again after saving. A stdio server runs on the API server, so you can register only a command an administrator allows, exactly as allowed, and without environment variables. An http or sse server you add yourself as a non-administrator must be on the public internet: addresses that point inside the network, such as `localhost` or `127.0.0.1`, private ranges (`10.x`, `172.16-31.x`, `192.168.x`), the cloud metadata address and the deployment's own service names, are refused both when you save and when the server is called. Ask an administrator to add an in-house MCP server to the MCP server catalogue.

## Tool permissions

Set each tool on or off in the MCP server detail screen. A **write** tool that changes data asks for confirmation before it runs. Content returned by an MCP server is treated as external input, so any instructions inside it are not executed.

The more tools you enable, the less likely the model is to choose the right one, so enable only the tools you actually use.
