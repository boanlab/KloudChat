import clsx, { type ClassValue } from 'clsx'
import type {
  Artifact,
  AudioArtifact,
  ImageArtifact,
  VideoArtifact,
} from '@/types'
import { currentLang, currentLocale } from './i18n'

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

/** Null-tolerant: a never-signed-in user has no `lastActiveAt`. */
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

/** The service's own day (the API's `TIMEZONE`): a refill or a limit that turns at its
 * midnight, stored as 15:00 UTC, is the 1st there, not the 31st in a UTC browser. */
const SERVICE_TIME_ZONE = 'Asia/Seoul'

export function formatDate(iso: string | null | undefined) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString(currentLocale(), {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    timeZone: SERVICE_TIME_ZONE,
  })
}

/** Date and time as printed in lists. Even the field order is locale-dependent. */
export function formatDateTime(iso: string | null | undefined) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(currentLocale())
}

/** Image, audio and video artifacts render inline in the turn; reports and decks are offered as chips. */
export function isMedia(a: Artifact): a is ImageArtifact | AudioArtifact | VideoArtifact {
  return a.kind === 'image' || a.kind === 'audio' || a.kind === 'video'
}

/** Byte count in binary units; one decimal below 10. */
export function fileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}

/**
 * Readable name for a `User-Agent` string, or `''` when unrecognised. Order
 * matters: Edge before Chrome, Chrome before Safari, since each UA claims the others.
 */
export function browserName(ua: string): string {
  if (!ua) return ''
  const browser =
    /Edg\//.test(ua) ? 'Edge'
    : /OPR\/|Opera/.test(ua) ? 'Opera'
    : /SamsungBrowser/.test(ua) ? 'Samsung Internet'
    : /Firefox\//.test(ua) ? 'Firefox'
    : /Chrome\//.test(ua) ? 'Chrome'
    : /Safari\//.test(ua) ? 'Safari'
    : /curl\//.test(ua) ? 'curl'
    : /python-requests|httpx|axios|Go-http/i.test(ua) ? '스크립트'
    : ''
  const platform =
    /iPhone|iPad/.test(ua) ? 'iOS'
    : /Android/.test(ua) ? 'Android'
    : /Mac OS X|Macintosh/.test(ua) ? 'macOS'
    : /Windows/.test(ua) ? 'Windows'
    : /Linux/.test(ua) ? 'Linux'
    : ''
  return [browser, platform].filter(Boolean).join(' · ')
}

/** A model's token price as a picker shows it: credits per 1,000 tokens, in and out. */
export function tokenRate(model: { inputCreditCost?: number; creditCost?: number }, t: (s: string) => string) {
  const input = model.inputCreditCost ?? 0
  const output = model.creditCost ?? 0
  if (!input && !output) return t('크레딧 없음')
  return t('1천 토큰당 입력 {in} · 출력 {out}크레딧').replace('{in}', String(input)).replace('{out}', String(output))
}
