/**
 * Column sorting shared by every data table: what a cell sorts by, how two values compare,
 * and the order a heading cycles through (first direction → the other → unsorted).
 *
 * Cells are often formatted text ("2.2 GB", "1.9 MB/s / 3.1 KB/s", "45%", "320ms",
 * "1,234", "—"), so `sortValue` reads the first quantity in a text and scales it by its unit:
 * "2.2 GB" sorts above "499 MB", "1.9 MB/s" above "3.1 KB/s", "1.2s" above "900ms".
 */

export type SortDir = 'asc' | 'desc'
export type SortState = { key: string; dir: SortDir } | null
export type SortValue = number | string | null

const BYTE_UNITS: Record<string, number> = {
  b: 1,
  kb: 1024,
  kib: 1024,
  mb: 1024 ** 2,
  mib: 1024 ** 2,
  gb: 1024 ** 3,
  gib: 1024 ** 3,
  tb: 1024 ** 4,
  tib: 1024 ** 4,
  pb: 1024 ** 5,
}
const TIME_UNITS: Record<string, number> = {
  ms: 0.001,
  s: 1,
  sec: 1,
  초: 1,
  m: 60,
  min: 60,
  분: 60,
  h: 3600,
  시간: 3600,
  d: 86_400,
  일: 86_400,
}
/** No value: a dash, an empty cell or "n/a". Sorted last whichever way the column runs. */
const EMPTY = /^\s*(?:[-–—]|n\/a|없음|)\s*$/i

// A leading quantity, after at most one short word ("CPU 12%"): a number with optional
// thousands separators and fraction, then an optional unit that ends the word. Bounded
// repeats keep the scan linear on any input.
const QUANTITY =
  /^(?:\p{L}{1,6}\s{1,2})?([+-]?(?:\d{1,3}(?:,\d{3}){1,6}(?:\.\d{1,12})?|\d{1,18}(?:\.\d{1,12})?|\.\d{1,12}))\s{0,2}(%|[a-zA-Z]{1,3}(?:\/s)?|초|분|시간|일)?(?![\p{L}\d])/u

const AGO = /(?:전|ago)\s*$/i
const JUST_NOW = /^(?:방금|just now)$/i

const ISO_DATE = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/

/** What a cell sorts by: a number when the text reads as a quantity, a date as its time, else the text. */
export function sortValue(input: unknown): SortValue {
  if (input === null || input === undefined || input === false) return null
  if (typeof input === 'number') return Number.isFinite(input) ? input : null
  if (input instanceof Date) return Number.isNaN(input.getTime()) ? null : input.getTime()
  const text = String(input).trim()
  if (EMPTY.test(text)) return null
  if (JUST_NOW.test(text)) return 0
  if (ISO_DATE.test(text)) {
    const at = Date.parse(text)
    if (!Number.isNaN(at)) return at
  }
  const m = QUANTITY.exec(text)
  if (!m) return text
  const n = Number(m[1].replace(/,/g, ''))
  if (!Number.isFinite(n)) return text
  const raw = m[2] ?? ''
  const unit = raw.toLowerCase()
  const rate = unit.endsWith('/s')
  const base = rate ? unit.slice(0, -2) : unit
  let value = n
  if (base in BYTE_UNITS) value = n * BYTE_UNITS[base]
  else if (raw === 'M') value = n * 1e6
  else if (raw === 'k' || raw === 'K') value = n * 1e3
  else if (!rate && base in TIME_UNITS) value = n * TIME_UNITS[base]
  // "3분 전" / "2h ago": further back is earlier, so ascending runs oldest first.
  return AGO.test(text) ? -value : value
}

const collators = new Map<string, Intl.Collator>()
function collator(locale?: string): Intl.Collator {
  const key = locale ?? ''
  let c = collators.get(key)
  if (!c) {
    c = new Intl.Collator(locale, { numeric: true, sensitivity: 'base' })
    collators.set(key, c)
  }
  return c
}

/** Two values in `dir`; empty values always last, numbers before text. */
export function compareValues(a: SortValue, b: SortValue, dir: SortDir, locale?: string): number {
  if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1
  const sign = dir === 'asc' ? 1 : -1
  if (typeof a === 'number' && typeof b === 'number') return (a - b) * sign
  if (typeof a === 'number') return -1
  if (typeof b === 'number') return 1
  return collator(locale).compare(a, b) * sign
}

/** Rows in the column's order; stable, so equal values keep the order they came in. */
export function sortRows<R>(
  rows: readonly R[],
  value: (row: R) => unknown,
  dir: SortDir,
  locale?: string,
): R[] {
  const keyed = rows.map((row, i) => ({ row, i, v: sortValue(value(row)) }))
  keyed.sort((x, y) => compareValues(x.v, y.v, dir, locale) || x.i - y.i)
  return keyed.map((k) => k.row)
}

/** A heading clicked: a new column opens in its first direction, then the other, then unsorted. */
export function nextSort(state: SortState, key: string, first: SortDir = 'asc'): SortState {
  if (!state || state.key !== key) return { key, dir: first }
  if (state.dir === first) return { key, dir: first === 'asc' ? 'desc' : 'asc' }
  return null
}

/** `aria-sort` for a heading. */
export function ariaSort(state: SortState, key: string): 'ascending' | 'descending' | 'none' {
  if (!state || state.key !== key) return 'none'
  return state.dir === 'asc' ? 'ascending' : 'descending'
}

/** `name` / `-name` as kept in an address or storage; '' is unsorted. */
export function encodeSort(state: SortState): string {
  return state ? `${state.dir === 'desc' ? '-' : ''}${state.key}` : ''
}

export function decodeSort(text: string | null | undefined, keys?: readonly string[]): SortState {
  if (!text) return null
  const desc = text.startsWith('-')
  const key = desc ? text.slice(1) : text
  if (!key || (keys && !keys.includes(key))) return null
  return { key, dir: desc ? 'desc' : 'asc' }
}
