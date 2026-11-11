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
