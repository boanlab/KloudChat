/** The service time zone and calendar-day arithmetic in it. No imports: `api.ts` takes it. */

/**
 * Service time zone (API `TIMEZONE`, sent with `/auth/config`): every date on screen and every
 * day boundary is in it, whatever the browser's zone, as refills and usage days are.
 */
let serviceZone = 'Asia/Seoul'

export function serviceTimeZone(): string {
  return serviceZone
}

/** Takes the server's zone; a name this browser does not know leaves the current one. */
export function setServiceTimeZone(zone: string | null | undefined) {
  if (!zone) return
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: zone })
    serviceZone = zone
  } catch {
    // RangeError: unknown zone.
  }
}

/** Wall-clock fields of an instant in the service zone. */
function zoneParts(ms: number) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: serviceZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    second: 'numeric',
  }).formatToParts(ms)
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0)
  return {
    year: get('year'),
    month: get('month'),
    day: get('day'),
    hour: get('hour'),
    minute: get('minute'),
    second: get('second'),
  }
}

const pad = (n: number) => String(n).padStart(2, '0')

/** Calendar day of an instant in the service zone, `YYYY-MM-DD`; '' when unreadable. */
export function serviceDate(at: string | number | Date | null | undefined): string {
  if (at === null || at === undefined || at === '') return ''
  const ms = new Date(at).getTime()
  if (Number.isNaN(ms)) return ''
  const { year, month, day } = zoneParts(ms)
  return `${year}-${pad(month)}-${pad(day)}`
}

/** `YYYY-MM-DD` moved by whole calendar days (no 24-hour arithmetic on instants). */
export function shiftDate(date: string, days: number): string {
  const [y, m, d] = date.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10)
}

/** The instant a service-zone calendar day (`YYYY-MM-DD`) begins. */
export function serviceMidnight(date: string): Date {
  const [y, m, d] = date.split('-').map(Number)
  const wall = Date.UTC(y, m - 1, d)
  // Offset of the zone at an instant: its wall clock read as UTC, minus the instant.
  const offset = (ms: number) => {
    const p = zoneParts(ms)
    const whole = Math.floor(ms / 1000) * 1000
    return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - whole
  }
  // Twice: the offset at the guess can differ from the one at midnight across a DST change.
  const guess = wall - offset(wall)
  return new Date(wall - offset(guess))
}
