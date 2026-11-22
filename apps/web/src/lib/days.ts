/** Calendar-day helpers for grouping conversations, in the service time zone. */
import { formatDate, serviceDate, shiftDate } from './utils'

/** Service-zone calendar day of a timestamp, as `YYYY-MM-DD`. */
export function dayOf(iso: string | null | undefined): string {
  return serviceDate(iso)
}

/** 오늘, 어제, or the full date; compared as calendar dates, never as 24-hour spans. */
export function dayLabel(
  iso: string | null | undefined,
  t: (s: string) => string,
  now: Date = new Date(),
): string {
  const day = dayOf(iso)
  if (!day) return t('날짜 없음')
  const today = serviceDate(now)
  if (day === today) return t('오늘')
  if (day === shiftDate(today, -1)) return t('어제')
  return formatDate(iso)
}

export type DayBucket = 'today' | 'yesterday' | 'week' | 'older'

/** 오늘 / 어제 / 이번 주 (within the last seven days) / 이전, by service-zone calendar days. */
export function dayBucket(iso: string | null | undefined, now: Date = new Date()): DayBucket {
  const day = dayOf(iso)
  const today = serviceDate(now)
  // Undated rows are old as far as the list is concerned.
  if (!day) return 'older'
  if (day >= today) return 'today'
  if (day === shiftDate(today, -1)) return 'yesterday'
  if (day >= shiftDate(today, -7)) return 'week'
  return 'older'
}
