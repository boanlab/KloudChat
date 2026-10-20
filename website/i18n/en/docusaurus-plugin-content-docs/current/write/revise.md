---
title: Revising by request
---

# Revising by request

Type what you want changed into the input box at the bottom of a finished report or slide deck. It analyzes the request and decides for itself which part to change and how. For sentence-level edits or reordering slides, the [Artifact editor](editor) is faster.

| Example request | How it is handled |
|---|---|
| "Cut section 2 in half", "add a schedule table to the conclusion" | Writes only that part again. The rest is kept. |
| "Drop the table in section 2 and put a 3-line summary at the top" | Writes again only the parts each ask points at. The summary goes at the top of the first section as a 「핵심」 box with exactly the lines asked, without repeating the title or author line. |
| "Remove table 2 and figure 1" | Takes out the numbered tables and figures as they are, without rewriting. Sentences that pointed at them are tidied, and the numbers of later tables and figures, and the references to them, close up. Rewriting that section in the same request puts no new table or figure in their place. A number that is not in the document is reported as not found. |
| "Make the whole thing more concise", "change the tone to polite form" | Writes every part again. |
| "Extend it to 20 slides", "add 3 slides", "merge 2 and 3", "change the order" | Proposes the structure again, waits for your approval, and then writes. Parts that are kept reuse their existing titles. |
| A question about the document, such as "how many slides are there?" | Not answered on this screen and nothing is changed. The reply asks you to name what to change, and to ask about the content in Chat with the downloaded file attached. |
| "Write it again", "start over", "with a different topic" | Keeps the existing document and writes a new one. |

When the revision is finished, the message says what was done for each ask (for example, "removed table 2 from section 2, put a 3-line summary at the top, rewrote section 1") and the panel updates at once. An ask that was not found or not applied is never reported as fixed; the message names it. On slides, an asked layout (such as "make slide 3 a table") or picture removal that does not show on the new slide is not reported as fixed; the message names what was not applied. "Remove the picture" also removes a diagram the deck drew itself and says which slide lost what; when there is no picture to remove, it says nothing was changed. When the deck has charts but no picture, it names the slides with charts and asks which to remove. "Remove the chart on slide 4" takes the chart off and, on a slide left empty, keeps the chart's values as bullets. A layout asked by name ("as a numbered list", "as bullets", "into a table") is drawn in that layout, or the message says it was not. An ask only for another shape ("make slide 4 a numbered list", "make slide 3 a table") moves the slide's own points and figures into that shape and keeps its title; when the points do not split into table columns, the slide stays as it was and the message says so. Shortening a section to a set number of sentences ("cut section 2 to two sentences") is checked for that count and for the section's figures and names; it is written once more if they miss, and the message says what still differs. Figures still in the section's tables or lists are not called dropped, and a figure or date that neither the section nor the material states is pointed out. Cutting a slide to fewer items ("make slide 9 one item") joins its old items when none came back, and the message names figures the cut dropped, as it does for a deck cut to fewer slides. When a rewritten slide no longer shows a figure it showed before, it is rewritten once more, and if the figure is still missing the message names it. An ask typed in English gets its reply in English. Each revision saves the previous body text as a version.

Even for a partial revision, it refers to the original request and the attached material again. To reflect new material, attach it and then ask for the revision.
