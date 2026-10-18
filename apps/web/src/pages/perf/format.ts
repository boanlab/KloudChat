import type { PerfRange } from '@/lib/api'
import { currentLocale } from '@/lib/i18n'

export const RANGES: PerfRange[] = ['1h', '24h', '7d', '30d']

/** Dashboard path; detail screens sit under it. */
export const PERF_HOME = '/admin/performance'

/** Fixed categorical order (validated light/dark pairs); a series keeps its slot by name. */
export const SERIES = [
  'light-dark(#2a78d6, #3987e5)',
  'light-dark(#eb6834, #d95926)',
  'light-dark(#1baf7a, #199e70)',
  'light-dark(#eda100, #c98500)',
  'light-dark(#e87ba4, #d55181)',
  'light-dark(#4a3aa7, #9085e9)',
]

export type Unit = string

export function num(v: number, digits = 1): string {
  return v.toLocaleString(currentLocale(), { maximumFractionDigits: digits })
}

export function bytes(v: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let x = v
  while (Math.abs(x) >= 1024 && i < units.length - 1) {
    x /= 1024
    i += 1
  }
  return `${num(x, x >= 100 ? 0 : 1)} ${units[i]}`
}

export function fmt(v: number | null | undefined, unit: Unit): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–'
  if (unit === '%') return `${num(v, v >= 10 ? 0 : 1)}%`
  if (unit === 'bytes') return bytes(v)
  if (unit === 'bps') return `${bytes(v)}/s`
  if (unit === 's') return v >= 10 ? `${num(v, 0)}s` : `${num(v, 2)}s`
  if (unit === 'ms') return `${num(v, 0)}ms`
  return num(v, v >= 10 ? 0 : 1)
}

export function clock(ms: number, range: PerfRange): string {
  const d = new Date(ms)
  const opts: Intl.DateTimeFormatOptions =
    range === '1h' || range === '24h'
      ? { hour: '2-digit', minute: '2-digit' }
      : { month: 'numeric', day: 'numeric', hour: '2-digit' }
  return d.toLocaleString(currentLocale(), opts)
}

/** Node part of a subject: `model · node` → `node`; a bare node name stays as is. */
export function nodeOf(subject: string): string {
  const at = subject.lastIndexOf(' · ')
  return at < 0 ? subject : subject.slice(at + 3)
}
