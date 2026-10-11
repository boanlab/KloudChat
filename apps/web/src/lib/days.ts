/** Calendar-day helpers for grouping conversations. */

/** Local calendar day of a timestamp, as `YYYY-M-D`. */
export function dayOf(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`
}

/** 오늘, 어제, or the full date. */
export function dayLabel(iso: string | null | undefined, t: (s: string) => string): string {
  const d = iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return t('날짜 없음')
  const today = new Date()
  const same = (a: Date, b: Date) => dayOf(a.toISOString()) === dayOf(b.toISOString())
  if (same(d, today)) return t('오늘')
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (same(d, yesterday)) return t('어제')
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
}
