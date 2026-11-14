import { isMediaFile, mediaApi, mediaSeconds, type MediaInfo } from '@/lib/api'

/** What a file row says about its audio/video being read (`services/video_jobs.py`). */
export interface MediaFields {
  id: string
  mediaState?: 'queued' | 'running' | 'done' | 'failed' | null
  mediaStage?: string | null
  mediaDone?: number
  mediaTotal?: number
  mediaDuration?: number
}

export const isWatching = (file: MediaFields) =>
  file.mediaState === 'queued' || file.mediaState === 'running'

/** 「12:30」, 「1:02:03」 past an hour. */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  const h = Math.floor(whole / 3600)
  const m = Math.floor((whole % 3600) / 60)
  const s = String(whole % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

/** Seconds of 「12:30」, 「00:12:30」 or 「1:02:03」; null for anything else. */
export function secondsOf(stamp: string): number | null {
  const parts = stamp.split(':').map(Number)
  if (parts.length < 2 || parts.length > 3 || parts.some((n) => !Number.isInteger(n) || n < 0)) return null
  if (parts.slice(1).some((n) => n > 59)) return null
  return parts.reduce((total, n) => total * 60 + n, 0)
}

/** One short line on a video's reading, or null; failures shown by the file's own error. */
export function mediaNote(
  file: MediaFields,
  t: (key: string) => string,
): { text: string; tone: 'busy' | 'warn' | 'quiet' } | null {
  if (file.mediaState === 'queued') return { text: t('영상 읽기 대기 중'), tone: 'busy' }
  if (file.mediaState === 'running') {
    const stage = file.mediaStage
    if (stage === 'reading' && file.mediaTotal) {
      return {
        text: t('영상의 말과 화면 옮기는 중 {done}/{total}')
          .replace('{done}', String(file.mediaDone ?? 0))
          .replace('{total}', String(file.mediaTotal)),
        tone: 'busy',
      }
    }
    if (stage === 'frames') return { text: t('영상의 장면 고르는 중'), tone: 'busy' }
    if (stage === 'writing') return { text: t('영상 내용 정리하는 중'), tone: 'busy' }
    return { text: t('영상 여는 중'), tone: 'busy' }
  }
  if (file.mediaState === 'done') {
    return {
      text: t('영상 읽음 · {length}').replace('{length}', clock(file.mediaDuration ?? 0)),
      tone: 'quiet',
    }
  }
  return null
}

/** A file's media info, asked once per file while the page lives (signed URLs last hours). */
const known = new Map<string, Promise<MediaInfo>>()

export function mediaInfo(fileId: string, fresh = false): Promise<MediaInfo> {
  let found = fresh ? undefined : known.get(fileId)
  if (!found) {
    found = mediaApi.get(fileId)
    known.set(fileId, found)
    // a failure is asked again next time
    found.catch(() => known.delete(fileId))
  }
  return found
}

/** The keyframe shown at `seconds`: the last one at or before it. */
export function frameAt(info: MediaInfo, seconds: number): number | null {
  let best: number | null = null
  for (const frame of info.frames) {
    if (frame.t <= seconds + 0.5) best = frame.n
    else break
  }
  return best ?? info.frames[0]?.n ?? null
}

export const frameSrc = (info: MediaInfo, n: number, small = true) =>
  info.frameUrl ? info.frameUrl.replace('{n}', small ? `${n}s` : String(n)) : ''

/** The app's own link to a video moment (`knowledge.video_link`): the file box opens its player. */
export const VIDEO_LINK = /^\/files\?open=([A-Za-z0-9-]{1,64})(?:&t=(\d{1,6}))?$/

export const videoHref = (fileId: string, seconds: number) => `/files?open=${fileId}&t=${Math.floor(seconds)}`

/** A video the answer may point into: its id and file name. */
export interface VideoRef {
  id: string
  name: string
}

// 「[12:30]」, 「[00:12:30]」, 「[1:02:03]」 written as text (not already a link or a citation).
const STAMP = /(?<!\[)\[((?:\d{1,2}:)?\d{1,2}:[0-5]\d)\](?![(\]])/g

/**
 * Time marks the model wrote as text, turned into links to the video they belong to: the one
 * named just before the mark, else the one the answer already links to, else the only video
 * the conversation has. Code is left alone; a mark with no clear video stays text.
 */
export function linkTimestamps(text: string, videos: VideoRef[]): string {
  if (!text.includes(':]') && !/\d:\d\d\]/.test(text)) return text
  const linked = [...new Set([...text.matchAll(/\/files\?open=([A-Za-z0-9-]{1,64})/g)].map((m) => m[1]))]
  const fallback = linked.length === 1 ? linked[0] : videos.length === 1 ? videos[0].id : null
  return text
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(STAMP, (whole, stamp: string, at: number) => {
            const seconds = secondsOf(stamp)
            if (seconds === null) return whole
            const before = part.slice(Math.max(0, at - 120), at)
            const named = videos
              .filter((v) => before.includes(v.name))
              .sort((a, b) => before.lastIndexOf(b.name) - before.lastIndexOf(a.name))[0]
            const id = named?.id ?? fallback
            return id ? `[${stamp}](${videoHref(id, seconds)})` : whole
          }),
    )
    .join('')
}

/** Before a long audio/video goes up: what reading it will take, or why it cannot be read. */
export async function mediaPreflight(
  file: File,
  t: (key: string) => string,
): Promise<{ note: string | null; refusal: string | null; seconds?: number }> {
  if (!isMediaFile(file)) return { note: null, refusal: null }
  const seconds = await mediaSeconds(file)
  if (!seconds) return { note: null, refusal: null }
  const estimate = await mediaApi.estimate(seconds).catch(() => null)
  if (!estimate?.enabled) return { note: null, refusal: null, seconds }
  if (!estimate.allowed) {
    return {
      note: null,
      refusal: t('영상이 너무 깁니다. {n}분 이하로 나눠 올려 주세요.').replace('{n}', String(estimate.maxMinutes)),
      seconds,
    }
  }
  if (estimate.credits > estimate.creditsLeft) {
    return { note: null, refusal: t('크레딧이 모자라 이 영상을 읽을 수 없습니다.'), seconds }
  }
  // a short clip needs no word; a lecture says what it will take
  if (seconds < LONG_MEDIA_SEC) return { note: null, refusal: null, seconds }
  const note = (estimate.credits
    ? t('{name}: {length} 영상 · 올린 뒤 약 {minutes}분 동안 말과 화면을 읽습니다 · 약 {credits} 크레딧')
    : t('{name}: {length} 영상 · 올린 뒤 약 {minutes}분 동안 말과 화면을 읽습니다 · 창을 닫아도 됩니다'))
    .replace('{name}', file.name)
    .replace('{length}', clock(seconds))
    .replace('{minutes}', String(estimate.minutes))
    .replace('{credits}', estimate.credits.toLocaleString())
  return { note, refusal: null, seconds }
}

/** Past this, the estimate is shown before the upload. */
const LONG_MEDIA_SEC = 10 * 60
