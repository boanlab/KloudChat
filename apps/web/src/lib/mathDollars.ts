/**
 * Escapes a `$` that opens no inline math, so 「$4 vs $38」 stays a price. Math opens on `$` with no
 * space after and closes on a later `$` with no space before and no digit after; `$$` and `\$` are untouched.
 */
export function escapeCurrency(text: string): string {
  const out: string[] = []
  let i = 0
  while (i < text.length) {
    const ch = text[i]
    if (ch === '\\') {
      out.push(text.slice(i, i + 2))
      i += 2
      continue
    }
    if (ch !== '$') {
      out.push(ch)
      i += 1
      continue
    }
    if (text[i + 1] === '$') {
      // display math: up to its closing `$$`, as written
      const end = text.indexOf('$$', i + 2)
      const stop = end < 0 ? text.length : end + 2
      out.push(text.slice(i, stop))
      i = stop
      continue
    }
    const close = inlineClose(text, i)
    if (close < 0) {
      out.push('\\$')
      i += 1
      continue
    }
    out.push(text.slice(i, close + 1))
    i = close + 1
  }
  return out.join('')
}

/** Where the inline math opened at `open` closes, on its own line; -1 when it does not. */
function inlineClose(text: string, open: number): number {
  const first = text[open + 1]
  if (first === undefined || /\s/.test(first)) return -1
  for (let j = open + 1; j < text.length; j += 1) {
    const ch = text[j]
    if (ch === '\n') return -1
    if (ch === '\\') {
      j += 1
      continue
    }
    if (ch !== '$') continue
    if (text[j + 1] === '$') return -1
    if (/\s/.test(text[j - 1]) || /\d/.test(text[j + 1] ?? '')) return -1
    return j
  }
  return -1
}
