Build this document with the **설문 분석** template. The layout is fixed; write only the content.
The reader first asks "can I trust these numbers". An analysis that does not state its sample
does not get read.

Each section is one `layout` plus that section's body HTML fragment.

- `cover` — the cover. State the survey population, the period and the sample size.
- `section` — one chapter.

## Rules

- **Write the survey overview (조사 개요) first.** Whom, when and how it asked. State the number
  distributed, the number returned and the response rate. If the response rate is low, say so.
- **Show the sample as a table.** Put the breakdown by gender, age, affiliation and so on in a
  table. If the sample is skewed, state it in the body.
- **Do not mix descriptive statistics (기술통계) with interpretation.** Tables hold only numbers;
  interpretation goes in the paragraph below the table.
- **For cross-tabulations, state what was crossed with what.** Put the two variables in the
  table title; if you tested significance, give the test name and value. If you did not,
  write "차이가 보인다" instead of "차이가 있다".
- **Do not write correlation as causation.** A survey shows relationships; it does not
  establish causes.
- Always include a limitations section: sample skew, the limits of self-report, what was not asked.
- Wrap anything that must be read **character for character** — commands, exception
  names, file paths — in `<code>`. The layout sets it in monospace; quotation marks
  instead leave no way to tell code from a quotation.
