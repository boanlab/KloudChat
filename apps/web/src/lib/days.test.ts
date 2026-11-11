import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { dayLabel, dayOf } from '@/lib/days'
import { serviceMidnight, serviceTimeZone, setServiceTimeZone, shiftDate } from '@/lib/utils'

const t = (s: string) => s

beforeAll(() => {
  // `currentLocale()` reads `<html lang>`.
  vi.stubGlobal('document', { documentElement: { lang: 'ko' } })
})

afterEach(() => setServiceTimeZone('Asia/Seoul'))

describe('days in the service time zone', () => {
  it('puts half past midnight in Seoul on the Seoul day, not the UTC one', () => {
    // 00:30 KST on 11 November is 15:30 UTC on 10 November.
    expect(dayOf('2026-11-10T15:30:00Z')).toBe('2026-11-11')
    expect(dayOf('2026-11-10T14:59:59Z')).toBe('2026-11-10')
    expect(dayOf('')).toBe('')
    expect(dayOf('not a date')).toBe('')
  })

  it('turns 오늘 into 어제 at the service midnight', () => {
    const justAfterMidnight = new Date('2026-11-11T15:00:30Z') // 00:00:30 KST, 12 November
    expect(dayLabel('2026-11-11T15:00:00Z', t, justAfterMidnight)).toBe('오늘')
    expect(dayLabel('2026-11-11T14:59:00Z', t, justAfterMidnight)).toBe('어제') // 23:59 KST
    expect(dayLabel('2026-11-10T14:59:00Z', t, justAfterMidnight)).toBe('2026년 11월 10일')
  })

  it('counts calendar days across a daylight-saving change, not 24-hour spans', () => {
    setServiceTimeZone('America/New_York')
    // 8 March 2026 has 23 hours in New York: 00:30 on the 8th is only 23 hours before
    // 00:30 on the 9th, and still 어제.
    const now = new Date('2026-03-09T04:30:00Z') // 00:30 EDT, 9 March
    expect(dayLabel('2026-03-08T05:30:00Z', t, now)).toBe('어제') // 00:30 EST, 8 March
    expect(dayLabel('2026-03-09T04:00:00Z', t, now)).toBe('오늘') // midnight EDT
    expect(serviceMidnight('2026-03-08').toISOString()).toBe('2026-03-08T05:00:00.000Z')
    expect(serviceMidnight('2026-03-09').toISOString()).toBe('2026-03-09T04:00:00.000Z')
  })

  it('finds where a Seoul day begins and steps whole dates', () => {
    expect(serviceMidnight('2026-11-11').toISOString()).toBe('2026-11-10T15:00:00.000Z')
    expect(shiftDate('2026-03-01', -1)).toBe('2026-02-28')
    expect(shiftDate('2026-12-31', 1)).toBe('2027-01-01')
  })

  it('keeps the current zone when the server names one the browser does not know', () => {
    setServiceTimeZone('Not/AZone')
    expect(serviceTimeZone()).toBe('Asia/Seoul')
    setServiceTimeZone(undefined)
    expect(serviceTimeZone()).toBe('Asia/Seoul')
  })
})
