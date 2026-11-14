/** CSV for spreadsheet export: RFC 4180 quoting, a BOM so Excel reads UTF-8 Korean. */

export type CsvCell = string | number | boolean | null | undefined

/** One cell. Text that a spreadsheet would run as a formula (=, +, -, @) is kept as text. */
export function csvCell(value: CsvCell): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : ''
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  let text = value
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function toCsv(rows: CsvCell[][]): string {
  return rows.map((row) => row.map(csvCell).join(',')).join('\r\n')
}

/** Saves `rows` as `name` through the browser. */
export function downloadCsv(name: string, rows: CsvCell[][]) {
  downloadBlob(name, new Blob([`﻿${toCsv(rows)}`], { type: 'text/csv;charset=utf-8' }))
}

export function downloadBlob(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  // After the click has handed the file over.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
