import clsx, { type ClassValue } from 'clsx'
import type { SessionMade } from '@/types'
import { currentLang, currentLocale, translate } from './i18n'

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs)
}

export function uid(prefix = 'id') {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`
}

export function formatTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

/**
 * Both helpers take null because the API does: a user who has never signed in
 * has no `lastActiveAt`, and one awaiting approval has no `cycleResetsAt`.
 * Rendering "Invalid Date" for those is worse than saying nothing.
 */
export function relativeTime(iso: string | null | undefined) {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const diff = Date.now() - then
  const min = Math.round(diff / 60_000)
  const en = currentLang() === 'en'
  if (min < 1) return en ? 'just now' : '방금'
  if (min < 60) return en ? `${min}m ago` : `${min}분 전`
  const hours = Math.round(min / 60)
  if (hours < 24) return en ? `${hours}h ago` : `${hours}시간 전`
  const days = Math.round(hours / 24)
  if (days < 7) return en ? `${days}d ago` : `${days}일 전`
  return new Date(iso).toLocaleDateString(currentLocale(), { month: 'short', day: 'numeric' })
}

export function formatDate(iso: string | null | undefined) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString(currentLocale(), {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

/** Date and time as printed in lists. Even the field order is locale-dependent. */
export function formatDateTime(iso: string | null | undefined) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(currentLocale())
}

/**
 * The line under a media session's title: what it made.
 *
 * A picture or clip surface runs no turn, so those rows have no last message to
 * show and sat blank beneath their own name. The name is already the prompt
 * somebody typed, so repeating it underneath would say one thing twice; this
 * says the other half — how many came back, how long, what shape — which is the
 * only thing that tells seven clips of one request apart.
 *
 * Written here rather than on the server because it is interface text and has
 * to read in whichever language is on, and assembled from counts and
 * measurements rather than picked from a list of finished sentences because
 * "이미지 4장" and "영상 4초 · 16:9" are one shape with different parts in it.
 *
 * A part that is not known is left out rather than defaulted. A shorter true
 * line beats "영상 0초", which reads as a clip that failed.
 */
export function madeLine(made: SessionMade | null | undefined, t: (s: string) => string) {
  if (!made || made.count < 1) return null
  const counted: Record<SessionMade['kind'], string> = {
    image: '이미지 {n}장',
    video: '영상 {n}개',
    narration: '내레이션 {n}개',
    music: '음악 {n}곡',
  }
  const bare: Record<SessionMade['kind'], string> = {
    image: '이미지',
    video: '영상',
    narration: '내레이션',
    music: '음악',
  }
  const head =
    made.count === 1
      ? t(bare[made.kind])
      : t(counted[made.kind]).replace('{n}', String(made.count))
  const seconds = made.seconds > 0 ? t('{n}초').replace('{n}', String(made.seconds)) : ''
  // 하나뿐이면 길이는 그 하나를 꾸미는 말이라 "영상 4초" 한 마디로 읽힌다.
  // 여러 개면 개수가 먼저 와서 붙일 자리가 없으므로 가운뎃점으로 나눈다.
  const parts = made.count === 1 && seconds ? [`${head} ${seconds}`] : [head, seconds]
  // A ratio reads the same in both languages, so it goes through untranslated.
  return [...parts, made.aspect].filter(Boolean).join(' · ')
}

/**
 * A saved row put back where it already was, or added on top when it is new.
 *
 * An edit has to land in place: a corrected row that jumps to the front of the
 * list reads as a second copy of itself.
 */
export function upsertById<T extends { id: string }>(rows: T[], row: T): T[] {
  return rows.some((r) => r.id === row.id)
    ? rows.map((r) => (r.id === row.id ? row : r))
    : [row, ...rows]
}

/** Buckets chats into today / last 7 days / older groups for the sidebar. */
export function groupByRecency<T extends { updatedAt: string }>(items: T[]) {
  const now = Date.now()
  const day = 86_400_000
  const lang = currentLang()
  const label = (ko: string) => translate(lang, ko)
  const groups: { label: string; items: T[] }[] = [
    { label: label('오늘'), items: [] },
    { label: label('지난 7일'), items: [] },
    { label: label('이전'), items: [] },
  ]
  for (const item of items) {
    const age = now - new Date(item.updatedAt).getTime()
    if (age < day) groups[0].items.push(item)
    else if (age < day * 7) groups[1].items.push(item)
    else groups[2].items.push(item)
  }
  return groups.filter((g) => g.items.length > 0)
}
