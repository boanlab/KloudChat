Build this document with the **장애 보고** template. The layout is fixed; write only the
content. There are two kinds of readers — those who need to know what happened, and those
who must decide what to change so it does not happen again.

Each section is one `layout` plus that section's body HTML fragment.

- `cover` — the cover. State the times of occurrence, detection and recovery, and the scope of impact.
- `section` — one chapter.

## Rules

- **Summary first.** What was affected, for how long, and whom — within three sentences.
- **The chronological record as a table.** 시각·일어난 일·한 일. Do not mix guesses with
  facts; write down what was not known at the time as not known. State the time zone for times.
- **For the cause, look at the system.** "누가 잘못 눌렀다" is an event, not a cause. Go down
  to why that operation was possible, why it was not blocked, and why it was noticed late.
- **Do not write people's names.** Write roles. A report that keeps names makes the next
  person write less honestly.
- **Recurrence prevention as a table, with owners and deadlines.** An item with no owner is
  the same as one decided not to be done.
- If detection was late, that is an item too. Knowing quickly matters as much as fixing.
- Wrap anything that must be read **character for character** — commands, exception
  names, file paths — in `<code>`. The layout sets it in monospace; quotation marks
  instead leave no way to tell code from a quotation.
