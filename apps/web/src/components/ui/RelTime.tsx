import { formatDateTime, relativeTime } from '@/lib/utils'

/** 「3분 전」 on screen, the exact date and time on hover and for assistive tech. */
export function RelTime({ iso, className }: { iso: string | null | undefined; className?: string }) {
  if (!iso) return <span className={className}>{relativeTime(iso)}</span>
  const exact = formatDateTime(iso)
  return (
    <time dateTime={iso} title={exact} className={className}>
      {relativeTime(iso)}
    </time>
  )
}
