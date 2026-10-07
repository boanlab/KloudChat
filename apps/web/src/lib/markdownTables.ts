const GLUED_HEADER = /^([^|\n]*\S)[ \t]{0,8}(\|(?:[^|\n]{0,200}\|){2,40})[ \t]{0,8}$/
const SEPARATOR_ROW = /^[ \t]{0,8}\|?(?:[ \t]{0,8}:?-{2,}:?[ \t]{0,8}\|){1,40}[ \t]{0,8}(?::?-{2,}:?)?[ \t]{0,8}$/

/** A table header written on the same line as the sentence before it (「… 같습니다. | 주파수 |
 *  이득 |」) moves to its own line, so the table renders as a table. Mirrors the server's
 *  `richtext.detach_tables`, for documents stored before it. */
export function detachTables(text: string): string {
  const lines = text.split('\n')
  return lines
    .flatMap((line, index) => {
      const glued = SEPARATOR_ROW.test(lines[index + 1] ?? '') ? GLUED_HEADER.exec(line) : null
      return glued ? [glued[1].trimEnd(), '', glued[2]] : [line]
    })
    .join('\n')
}
