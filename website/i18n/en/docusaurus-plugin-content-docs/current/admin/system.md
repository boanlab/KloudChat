---
title: System settings
---

# System settings

The **System** screen in the Manage menu is made up of seven tabs.

## Proxy

The model gateway (LiteLLM) address and master key (stored encrypted, never shown again), **Connection test** (confirms n models), **Refresh model list**, and revert to the environment variable values. If you see a list of models hidden for having no price information, enter the model prices in the gateway settings.

## Routing

The routing settings are described in [Policy and routing](policy#auto-routing).

## Features

- **Features to use**: Turn reports, slide decks, images, and audio/video on and off. Chat is always on. Disabling one removes it from the menu, and the server refuses new conversations for it as well. Images and video work in practice only if the gateway has those models. If you enable them without a model, the user screen shows a notice that generation is not possible.
- **Feature integration**: One feature server address, or an address per feature (web search · document import · code execution · deep research · speech transcription · material search). **Connection test** individually and for all of them. Setting a speech transcription address enables dictation and reading audio and video files. With a material search (indexing) address, agent material and conversation attachments are searched as vectors (word search only without it).

The safe search level and blocked domains for web search are set on the search server. For details, see the backend (KloudChat-LLM) documentation. By default, safe search is off.

## Shared templates

Register, edit, and delete the **starting points** shown in the **Start working** gallery for every user. A registered item has a screen, name, description, category, input fields, examples, required features, a hidden instruction, and a form file. The formats that define the shape of a result are built into the product, so they are not created on this screen.

## Branding

The service name, the contact address (the mail link on the waiting screen. The first administrator if left empty), and the logo (PNG, JPG, WebP, up to 2MB, no SVG, with revert to default). Changing the service name also changes the browser tab icon to the matching initials.

## Mail

The SMTP host, port, security, account, and sender address, the service address (for mail links, with **Use the current address**), and **Send a test**. Once it is set, a password reset link appears on the sign-in screen and you can turn on email verification.

## Sign-up

- **Sign-up method**: Use after approval / Use immediately / Not accepted. If the `SIGNUP_MODE` environment variable is set, it takes priority.
- **Email domains allowed to sign up**: Several, separated by commas. Subdomains have to be listed separately.
- **Email verification**: Not effective without a mail server.

## Management items outside the System tab

- The **Usage** screen: charts by period, by model, by screen, and by user, CSV export, and storage space (disk usage, orphan file cleanup, and automatic cleanup from the oldest when it fills up).
- **Design**: An administrator can **provide a design system to every user** (shared, read only).
- **Store**: Agents and skills published by an administrator account carry an **Official** badge.
- The trusted proxy range, the file size limit, and similar settings are configured through environment variables rather than the screen. See `docs/configuration.md` in the repository.
