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

/** The sidebar's coarser buckets: 오늘 · 어제 · 지난 7일 · 지난 30일 · 이전. */
export function recencyBucket(iso: string | null | undefined, t: (s: string) => string): string {
  const d = iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return t('날짜 없음')
  // Calendar days apart, by local date parts: a record at exactly midnight is today,
  // and a daylight-saving switch does not shift the boundary.
  const today = new Date()
  const startOfToday = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())
  const startOfThat = Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())
  const days = Math.round((startOfToday - startOfThat) / 86_400_000)
  if (days <= 0) return t('오늘')
  if (days === 1) return t('어제')
  if (days <= 7) return t('지난 7일')
  if (days <= 30) return t('지난 30일')
  return t('이전')
}
