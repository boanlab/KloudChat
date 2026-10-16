---
title: API and coding tool integration
---

# API and coding tool integration

You can call the models your organization runs from your own code or from a coding tool. The account menu has two screens, **API integration** and **AI agent integration**.

## API integration

![API integration](/img/guide/api-setup.png)

1. Issue a key under Settings → **API keys**.
2. Follow the examples shown on the screen. Examples are provided for the OpenAI SDK, streaming, the LiteLLM SDK (`openai/` prefix), curl, and embeddings.
3. For the model name, use **the id shown in the model list** exactly as it appears.

```bash
# Linux / macOS, OpenAI-compatible
export OPENAI_BASE_URL="https://<service-address>/llm/v1"
export OPENAI_API_KEY="<your-api-key>"
```

```powershell
# Windows PowerShell
$env:OPENAI_BASE_URL = "https://<service-address>/llm/v1"
$env:OPENAI_API_KEY = "<your-api-key>"
```

Requests sent with a key reach the usage screen and this month's credits within a few minutes, and appear as **API keys** in the by-screen breakdown. Requests to free models are counted as requests too. Any allowed-model restriction set on the account applies to the key in the same way.

## Reasoning effort

Models that reason let you set how much they think. Send `reasoning_effort` in the OpenAI format, `reasoning.effort` in the Responses API, or `thinking` in the Anthropic format.

```bash
curl "$OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "z-ai/glm-5.3-flash", "reasoning_effort": "low",
       "messages": [{"role": "user", "content": "..."}]}'
```

Each model takes its own levels. Check the `reasoning` field on each model in `GET /llm/v1/models`.

```json
{"id": "z-ai/glm-5.3-flash", "reasoning": {"efforts": ["low", "high", "max"], "default": "max", "mandatory": true}}
```

| Field | Meaning |
|---|---|
| `efforts` | The levels it takes, lowest first |
| `default` | The level used when you send none |
| `mandatory` | When `true`, reasoning cannot be turned off. A request to turn it off (`none`, or `thinking` set to `disabled`) is sent at the lowest level instead. |

A model whose `reasoning` is `null` takes no level, and any level sent is ignored. A level not in the list is handled as a nearby one. Higher levels use more output tokens and credits.

## AI agent integration

![AI agent integration](/img/guide/agent-setup.png)

This screen explains how to connect coding agents such as Claude Code and Codex to organization models. Issue a key, choose a model, then copy the commands from the tab for your operating system (Linux · macOS · Windows).

| Tool | Environment variables |
|---|---|
| Claude Code | `ANTHROPIC_BASE_URL=https://<service-address>/llm`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` |
| Codex · OpenAI-compatible | `OPENAI_BASE_URL=https://<service-address>/llm/v1`, `OPENAI_API_KEY`, `OPENAI_MODEL` |

Note that the Anthropic-format address has no `/v1` while the OpenAI-format address includes it.

:::tip Environment variable scope
`export` and `$env:` apply only to the current terminal session. To keep them, add them to `~/.bashrc` or `~/.zshrc`. If another project uses a separate OpenAI key, use separate terminals so that the values do not conflict.
:::

## Key management

- The key value is shown only once, when it is issued. Issue a new key if you lose it.
- Discard keys you no longer use under Settings → API keys.
- Do not type a key into the conversation box or into a document.
