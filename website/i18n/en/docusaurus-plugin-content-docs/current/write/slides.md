---
title: Writing slides
---

# Writing slides

On the home screen, select **Slides** and enter the presentation topic and the audience. The procedure is the same as for a report. It runs through clarifying questions, the **structure check**, the **Add pictures?** card, and then writing slide by slide.

Choose **Web slides** or **PowerPoint** with the **Format** chip above the prompt box (the same in a new conversation, one started from a project or an agent, or one reopened from history). When you pick PowerPoint, a **Start blank** button appears, and the turn makes a PowerPoint file and fills it. You edit the file in the [PowerPoint editor](pptx). Attach a PowerPoint form (`.pptx`, `.potx`), or keep one in the project's or agent's **Default form files**, and the deck is made on that form ([Writing on a form](pptx#writing-on-a-form)). A project form shows as a **Project form** chip beside the format chip; ✕ removes it for this conversation only. The deck's purpose pattern (see [Structure by purpose](#structure-by-purpose) below) is chosen automatically from the words of the request. Opening `/new/slides?format=pptx` preselects the format. What follows describes web slides.

![A finished slide deck](/img/guide/slides.png)

## Items in the structure check

- Slide titles and their order
- **How will this be used?**: presenting with narration, or handing over the material only

The look of web slides is not chosen on the structure card. After writing, choose it in the panel from editorial, minimal, poster, split, dark, steel, warm, pastel, forest, academic, and monochrome.

## Structure by purpose

The words of the request tell the deck's purpose, and the deck follows that purpose's flow and checks. There is no picker.

| Purpose | When the request says | Flow |
|---|---|---|
| Review defense | thesis review, defense, interim review | question → method → results → limits → next plans |
| Research or paper talk | paper talk, lab seminar, journal club, conference talk | paper → problem → method (the paper's figures and tables) → results → limits → discussion questions |
| Class project presentation | class presentation, group project, final presentation | course and team → question → research → analysis → conclusion → references |
| Investor deck | fundraising, IR deck, pitch deck, series A | problem → solution → market → model → traction → competition → team → financials → ask |
| Results report | quarterly results, KPI, against target | key numbers → against target → trend (charts from the material's values) → causes → next plans |
| Project retrospective | project results, completion report, retrospective, post-mortem | goals and period → outcome → against goals → went well, went badly → lessons → follow-ups |
| Product or service pitch | product or solution introduction, sales deck, customer meeting | customer's problem → features and benefits → how it works → cases → how to start |
| Proposal for approval | proposal, grant, business plan, budget request | situation → proposal → evidence → schedule and budget → request |
| Policy or rule change briefing | policy or rule change, information session, effective date | what changes → before and after → who and from when → what to do → contact |
| Training or onboarding | onboarding, internal training, workshop | goals → concepts → step by step → common mistakes → check questions |
| Lecture | lecture, class slides, week n | topic → concepts with examples → summary → next session or assignment |
| Options to recommendation | options review, recommendation, board report | recommendation → situation → options compared → criteria → risks → ask |
| Meeting deck | meeting slides, agenda, briefing, status update | what to decide today → agenda → progress → blockers → owners and dates |

- Each purpose has questions to ask before writing. A results report, for instance, asks for the period and the targets when the material lacks them, and leaves bracket blanks when there is no answer.
- Results reports, research talks, project retrospectives and meeting decks are made from your material alone; they search the web only when the request asks for it ("search", "latest").
- When two purposes match, the one higher in the table is taken, and the progress steps say 「구조: … 패턴」 with the other match. Web slides show the same line under their card.
- With a PowerPoint form attached, the look follows the form and the flow follows the purpose.

## Setting the number of slides

| Example input | What it does |
|---|---|
| "6 slides", "a 20-slide deck" | Writes the number of slides you set. The maximum is 50. |
| "a 20-minute presentation" | Calculates the minimum number of slides at one slide per two minutes. 20 minutes gives at least 10 slides. Under 4 minutes the time is ignored, and the count stays between 5 and 12. |
| Not specified | Writes 5 to 12 slides to suit the topic. |

If the deck runs to more than 6 slides, a table of contents is inserted as the second slide.

## Slide types

The model chooses the type that suits the content. It uses cover, table of contents, item list, table (comparison), metrics (key figures), chart (when the material contains figures), diagram (flow and structure), and closing. You can set them in your request, for example "use a table for the comparison" or "include a flow chart". The server draws diagrams as shapes, and a PPTX export carries them as editable PowerPoint shapes. A slide whose title names a structure, a relationship or a flow ("Overall structure", "Object relationships", "Request flow") gets a diagram even when the request never says "diagram", and a relationship is drawn as lines named with each relation rather than as a chain of arrows. After the deck is made, "turn slide 4 into a structure diagram" redraws that slide as one. A deck written in English gets English diagram labels and captions.

## Changing the look

The **Look** menu in the panel toolbar changes the look and the accent color. **Redo this slide** in the ⋯ menu writes the selected slide again. If the project or agent has a [design brief](../personal/projects.md#design-brief), the deck follows its colors and typefaces.

## Presentation mode

**Present** in the toolbar or the ⋯ menu opens full-screen presenter view. Move between slides with `←` and `→`, and exit with `Esc`. It provides a presentation timer (click it to reset to zero), speaker notes shown with the `N` key, and a slide list.

## Export

In **Download**, choose PowerPoint (PPTX), PDF (for presenting), or text (with notes). If there are slides at overflow risk, slides whose evidence needs checking, or unresolved review notes, it tells you before exporting. To fill and edit a received `.pptx` or a company form as it is, use [PowerPoint documents](pptx).

## Revising

If you ask for something like "make slide 2 more concise", only that slide is written again. A request that changes the structure, such as "extend it to 20 slides" or "add a case study slide", is written straight away with the new structure, without questions or an approval step. For details, see [Revising by request](revise). To edit sentences yourself or reorder slides, use the [Artifact editor](editor).
