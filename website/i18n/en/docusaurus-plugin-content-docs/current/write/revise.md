---
title: Revising by request
---

# Revising by request

Type what you want changed into the input box at the bottom of a finished report or slide deck. It analyzes the request and decides for itself which part to change and how. For sentence-level edits or reordering slides, the [Artifact editor](editor) is faster.

| Example request | How it is handled |
|---|---|
| "Cut section 2 in half", "add a schedule table to the conclusion" | Writes only that part again. The rest is kept. |
| "Drop the table in section 2 and put a 3-line summary at the top" | Writes again only the parts each ask points at. The summary goes at the top of the first section as a 「핵심」 box with exactly the lines asked, without repeating the title or author line. |
| "Remove table 2 and figure 1" | Takes out the numbered tables and figures as they are, without rewriting. Sentences that pointed at them are tidied, and the numbers of later tables and figures, and the references to them, close up. Rewriting that section in the same request puts no new table or figure in their place. A number that is not in the document is reported as not found. A document written in English numbers its tables and figures "Table 2" and "Figure 1", and its sentences point at them in English ("See Table 2 for …"). |
| "Make the whole thing more concise", "change the tone to polite form" | Writes every part again. |
| "Extend it to 20 slides", "add 3 slides", "merge 2 and 3", "change the order" | Plans the structure again and writes it at once, with no questions or approval step: the open document is the material. Parts that are kept reuse their existing titles and content. |
| A question about the document or the conversation, such as "how many slides did I ask for?" or "where does the figure on slide 4 come from?" | Answered from the document and the conversation so far only; the document is not changed. |
| "Write it again", "start over", "with a different topic" | Keeps the existing document and writes a new one. |

When the revision is finished, the message says what was done for each ask (for example, "removed table 2 from section 2, put a 3-line summary at the top, rewrote section 1") and the panel updates at once. An ask that was not found or not applied is never reported as fixed; the message names it. If you ask in English, the reply is in English. The earlier text is kept as a version every time you revise.

## How particular requests are handled

- **Removing pictures and charts**: "Remove the picture" also removes a diagram the deck drew itself and says which slide lost what. When there is no picture to remove it says nothing was changed, and when there are only charts it names the slides with charts and asks what to remove. "Remove the chart on slide 4" removes the chart and leaves its figures as bullets.
- **Changing only the shape**: "Make slide 4 a numbered list" or "make slide 3 a table" carries the slide's items, figures and title over unchanged. If the content does not split into table cells, the slide is left as it is and the message says so.
- **Merging and splitting items**: "Merge the items on slide 2 into one" or "split the items on slide 3 in two" changes only that slide and does not rebuild the deck. On a slide with a chart, the chart stays and only the items beside it change.
- **Shortening**: when you fix a sentence count ("cut section 2 to two sentences"), the result is checked for that count and for the section's figures and names, and written once more if it misses. If it still does not fit, the original section is kept and the message says how many sentences it has. Figures, dates and boxes lost in shortening, and tables or charts that disappeared, are named in the message. A date the section did not contain is not added.
- **While a card is open**: even with a question card or a proposed structure open, a question about the document, or an edit that names its slide number ("make slide 3 a numbered list"), is handled as that request, not as the card's answer, and the card stays open. A request that changes the structure ("go ahead without slide 4") is taken as a note on the proposal.

A partial revision also looks again at the original request and the attached material. To bring in new material, attach it when you ask for the revision.
