/**
 * How a cell's value is written for reading: the number formats a sheet most often holds
 * (`#,##0`, `0.0%`, `yyyy-mm-dd`, a currency sign), applied to the value the model carries.
 * A format this does not know shows the value as it is; the file keeps the real format.
 */

const EPOCH = Date.UTC(1899, 11, 30)

/** A date serial (days since 1899-12-30) as `yyyy-mm-dd`, with the other parts of a pattern filled in. */
function dateText(serial: number, pattern: string): string {
  const t = new Date(EPOCH + Math.round(serial * 86400000))
  const pad = (n: number, w = 2) => String(n).padStart(w, '0')
  return pattern
    .replace(/yyyy/gi, String(t.getUTCFullYear()))
    .replace(/yy/gi, pad(t.getUTCFullYear() % 100))
    .replace(/mm/g, pad(t.getUTCMonth() + 1))
    .replace(/dd/gi, pad(t.getUTCDate()))
    .replace(/hh/gi, pad(t.getUTCHours()))
    .replace(/ss/g, pad(t.getUTCSeconds()))
}

/** Decimals a pattern asks for: the zeros or `#`s after its point. */
function decimalsOf(pattern: string): number {
  const m = /\.([0#]+)/.exec(pattern)
  return m ? m[1].length : 0
}

/** The text a cell shows: `value` under the number format `fmt` (none or `General` shows it as is). */
export function formatValue(value: string | number | boolean | undefined, fmt?: string): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE'
  if (typeof value === 'string') return value
  if (!Number.isFinite(value)) return String(value)
  if (!fmt || fmt === 'General') return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(12)))
  // the first section of a pattern with several (`#,##0;-#,##0`), quoted text and colours dropped
  const pattern = fmt.split(';')[0].replace(/\[[^\]]*\]/g, '')
  if (/[ymd]/i.test(pattern.replace(/"[^"]*"/g, '')) && /y|d/i.test(pattern)) return dateText(value, pattern.replace(/"/g, ''))
  const percent = pattern.includes('%')
  const scaled = percent ? value * 100 : value
  const digits = decimalsOf(pattern)
  const grouped = pattern.includes(',')
  const body = Math.abs(scaled).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: grouped,
  })
  const quoted = [...pattern.matchAll(/"([^"]*)"|([₩$€¥£])/g)]
  const before = pattern.search(/[#0]/)
  const prefix = quoted.filter((m) => (m.index ?? 0) < before).map((m) => m[1] ?? m[2]).join('')
  const suffix = quoted.filter((m) => (m.index ?? 0) > before).map((m) => m[1] ?? m[2]).join('')
  const sign = scaled < 0 && Number(body.replace(/,/g, '')) !== 0 ? '-' : ''
  return `${sign}${prefix}${body}${percent ? '%' : ''}${suffix}`
}
