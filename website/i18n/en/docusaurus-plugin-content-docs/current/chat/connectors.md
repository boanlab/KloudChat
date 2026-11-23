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

## Getting one from the store

**Explore** lists the MCP servers other people shared. A server an administrator shared carries an **Official** badge; any other shows the name of whoever shared it. Press **Add** to bring it into My MCP servers. If the server asks for credentials (a key or token), a form opens before it is installed and each person enters their own values. The values are kept on the server only and are not shown again.

A server you get this way is not a copy: it is **a link to the shared server**. The address and tools follow the shared server, so the detail screen shows "Server shared by (name)" instead of an address. Turning tools on or off and fixing your own credentials stay yours. When the sharer stops sharing, it reads "Sharing ended, so it cannot be used" and you can only **Remove** it. A server an administrator hid from the store keeps working for people who already added it.

## Adding a server directly

If you already use an MCP server, connect it with **Add server directly**. Enter the name, the transport (http or sse), the endpoint address, the environment variables, and the authentication method. Environment variables and credentials are not shown again after saving. A server that runs a command (stdio) runs on the API server, so only an administrator can register one. A server you add as a non-administrator must be on the public internet: addresses that point inside the network, such as `localhost` or `127.0.0.1`, private ranges (`10.x`, `172.16-31.x`, `192.168.x`), the cloud metadata address and the deployment's own service names, are refused both when you save and when the server is called.

## Sharing your MCP server

For a server you added, choose **Not shared** or **Share with our organization** under **Share** in its detail screen. A shared server is listed in the store (**Explore**) and others use it. Only http and sse servers can be shared.

- Sharing does not hand over your environment variable values; nobody else uses them. Under **Credentials receivers enter themselves**, list the key name, the name shown on screen, a hint and whether it is secret (up to 8), so each receiver enters their own.
- When people use a shared server, the detail screen shows how many. Stopping the sharing first asks, "N people use it. If you stop sharing, their copy of this server stops working."
- A shared server can be deleted only after you stop sharing it.

A server an administrator added has more fields: shared environment variable values, the tool-address key, **Provide to everyone by default** and **Show in the store**.

## Tool permissions

Set each tool on or off in the MCP server detail screen. A **write** tool that changes data asks for confirmation before it runs. Content returned by an MCP server is treated as external input, so any instructions inside it are not executed.

The more tools you enable, the less likely the model is to choose the right one, so enable only the tools you actually use.
