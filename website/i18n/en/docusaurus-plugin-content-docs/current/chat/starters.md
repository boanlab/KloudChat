---
title: Templates and themes
---

# Templates and themes

A template is the structure of an artifact; a theme is its look. Work you do often belongs to [agents](agents.md). Neither is a Customize tab: you pick each where it is used.

| Feature | Role | Where to pick it | Scope |
|---|---|---|---|
| **Template** | The structure of the artifact (structure rules, typesetting, check items). There are built-in templates and your own. | **+** → **Pick a template** in the composer; **Template & theme** › **Template** in the agent settings | That conversation (clear it with × on the chip), or the conversations that agent opens |
| **Theme** | A setting that bundles colors, typefaces, and writing style. | **Template & theme** › **Theme** in the agent settings, **Edit › Theme** in the report or slides panel, the project settings | That agent's or project's artifacts |

## Templates

A template defines the structure rules and the typesetting of reports and slides. Select one and the **What this template checks** list appears. You edit a document in a template by rewriting it one block at a time, and you can export it as PDF, DOCX/PPTX, HWPX, HTML, or text. Create your own document template with **New template** in the template picker or the agent settings ([Agents and skills](agents.md)). Images, audio and video have no templates.

The built-in templates in the picker are six documents (internal draft 간이기안문, plan 추진 계획(안), result report 결과 보고서, bullet-style briefing 개조식 보고서, comparison report 비교·분석 보고서, minutes 회의록) and the **Briefing** deck. They write straight into the institution's format and show that format's check results when done. Conversations and agents that use a hidden template keep writing and exporting with it.

## Themes

Manage them in **Settings** → **Themes** (also opened by **Manage themes** in the agent settings). Themes other people shared appear under **Shared themes** on the same tab.

![The theme screen](/img/guide/designs.png)

- Set the name, the accent color, the body color, the secondary color, the typeface (sans-serif or serif), the writing style rules, the image style, and the rules to apply alongside them.
- **Import from a document** extracts a draft from an existing hwpx, docx, or pdf file, or from a web address. It calls the model once, and you can review the content before saving.
- The writing style applies to conversations, reports, and slides. Colors and typefaces apply to slides and report covers. The image style applies to images. Nothing applies to audio and video.
- Theme settings are copied into an artifact when it is generated. Changing the theme later does not change artifacts that already exist.
- An administrator can distribute a read-only shared theme with **Provide to all users**.
- Of the built-in themes, the pickers show **Default** (기본) and **Document serif** (문서용 명조).
