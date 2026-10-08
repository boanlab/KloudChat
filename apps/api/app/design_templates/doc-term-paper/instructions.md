Build this document with the **기말 리포트** template. The layout is fixed; write only the content.
The reader is the grader. They look for three things — what the claim is, what supports it,
and where the support came from.

Each section is one `layout` plus that section's body HTML fragment.

- `cover` — the cover. The title and subtitle, plus places for 과목·학번·제출일.
- `section` — one chapter. Set the chapter title with `<h2>` and continue with the body.

## Rules

- **The introduction poses the question.** State in the first paragraph what the paper asks.
  If it only lays out background without a question, the grader cannot tell what it is trying to do.
- **One chapter, one claim.** Make the chapter title the chapter's claim. Avoid titles like
  "배경", which hide what the chapter says.
- **A footnote at every citation.** Put the footnote right at each sentence that uses someone
  else's words or numbers. Gathered at the end of a paragraph, no one can tell which sentence
  came from where. Mark footnotes with `[^1]` and define them at the end of the section as
  `[^1]: 저자, 『책』, 쪽수.`.
- **Do not write what you have not verified.** Years, names and page numbers written from
  memory are wrong. Write only what you confirmed in the material; if you could not confirm it,
  drop the sentence.
- **The conclusion is not a summary.** Answer the question posed in the introduction, and
  state in one line what the paper could not cover.
- Gather references in the last section. The footnotes and the list must not disagree.
- Wrap anything that must be read **character for character** — commands, exception
  names, file paths — in `<code>`. The layout sets it in monospace; quotation marks
  instead leave no way to tell code from a quotation.
