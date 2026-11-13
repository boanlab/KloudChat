/** 지식 서가 wire types (`routers/shelf.py`) and small helpers shared by its components. */
import type { FileRow } from './api'
import type { StudioKind } from './shelfNames'

export type SourceKind = 'file' | 'web' | 'text' | 'note' | 'video' | 'audio'

export interface ShelfSource extends FileRow {
  kind: SourceKind
  /** Per-file digest (strict-local), when one is current. */
  digest: { label: string; title: string; summary: string; outline: string[]; terms: string[] } | null
  guideState: 'none' | 'running' | 'done' | 'failed'
  readable: boolean
  createdAt: string
}

export interface ShelfListing {
  projectId: string
  sources: ShelfSource[]
  /** Internal data stays on strict-local models (administrator's rule). */
  strictLocal: boolean
  kinds: StudioKind[]
}

export interface SourceGuide {
  state: 'none' | 'running' | 'done' | 'failed'
  summary: string
  topics: string[]
  questions: string[]
  model: string
  error?: string
}

export interface Located {
  fileId: string
  name: string
  mime: string
  kind: SourceKind
  sourceUrl: string | null
  chars: number
  windowStart: number
  window: string
  start: number
  end: number
  focusStart: number
  focusEnd: number
  page: number | null
  seconds: number | null
  score: number
  matched: boolean
}

export interface StudioRequest {
  kind: StudioKind
  sourceIds: string[]
  options: {
    language?: 'auto' | 'ko' | 'en'
    format?: 'web' | 'hwp' | 'docx'
    count?: number
    length?: 'short' | 'standard' | 'long'
    focus?: string
    model?: string
  }
}

export interface SpeechPlan {
  mode: 'local' | 'outside' | 'none'
  model?: string
  credits?: number
  reason?: 'no_speech_model' | 'strict_local_no_tts'
}

export interface StudioEstimate {
  kind: StudioKind
  sources: number
  partial: string[]
  model: string
  modelLabel: string
  strict: boolean
  required: boolean
  inputTokens: number
  outputTokens: number
  credits: number
  maxCredits: number
  speech?: SpeechPlan
}

export interface NumberedSource {
  n: number
  fileId: string
  name: string
  kind: SourceKind
  chars: number
  read: number
}

export interface QuizQuestion {
  id: string
  type: 'mc' | 'short'
  question: string
  choices?: string[]
  answer: number | string
  explanation: string
  cite: number[]
}

export interface Flashcard {
  id: string
  front: string
  back: string
  cite: number[]
}

export interface StudioContent {
  language: 'ko' | 'en'
  sources: NumberedSource[]
  partial: string[]
  strict: boolean
  title?: string
  sections?: { id: string; heading: string; content: string }[]
  questions?: QuizQuestion[]
  cards?: Flashcard[]
  lines?: { speaker: 'A' | 'B'; text: string }[]
}

export interface StudioItem {
  id: string
  projectId: string
  kind: StudioKind
  title: string
  state: 'running' | 'done' | 'failed'
  error: string
  sourceIds: string[]
  options: StudioRequest['options']
  content: StudioContent | null
  artifactId: string | null
  sessionId: string | null
  audioFileId: string | null
  speech: { state: '' | 'idle' | 'running' | 'done' | 'failed'; done: number; total: number } | null
  model: string
  credits: number
  inputTokens: number
  outputTokens: number
  createdAt: string
  updatedAt: string
}

export interface Note {
  id: string
  projectId: string
  title: string
  body: string
  origin: 'manual' | 'answer' | 'selection'
  sessionId: string | null
  messageId: string | null
  fileId: string | null
  createdAt: string
  updatedAt: string
}

/** Seconds as 「1:02:05」 / 「2:10」. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const rest = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`
}

/** Polls `read` every `ms` while `busy(value)`; stops on unmount via the returned function. */
export function poll<T>(read: () => Promise<T>, busy: (value: T) => boolean, onValue: (value: T) => void, ms = 2500): () => void {
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | null = null
  const tick = async () => {
    if (stopped) return
    try {
      const value = await read()
      if (stopped) return
      onValue(value)
      if (!busy(value)) return
    } catch {
      /* the next tick tries again */
    }
    timer = setTimeout(() => void tick(), ms)
  }
  timer = setTimeout(() => void tick(), ms)
  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
  }
}

/** Saves `blob` under `name` (a browser download). */
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}

/** A studio document as markdown with its numbered sources listed under it, so the answer
 * body links each 「[n]」 to the source viewer as it does in a conversation. */
export function readableDocument(content: StudioContent): string {
  const body = (content.sections ?? [])
    .map((section) => `## ${section.heading}\n\n${section.content}`)
    .join('\n\n')
  const listed = content.sources.map((source) => `- [${source.n}] ${source.name}`).join('\n')
  return listed ? `${body}\n\n### 출처\n${listed}` : body
}

/** Short answers compared loosely: case, spaces and punctuation aside, either containing the other. */
export function sameAnswer(given: string, expected: string): boolean {
  const norm = (text: string) => text.toLowerCase().replace(/[\s.,·:;!?'"()[\]{}「」『』-]/g, '')
  const a = norm(given)
  const b = norm(expected)
  if (!a || !b) return false
  return a === b || (a.length >= 2 && b.includes(a) && a.length * 2 >= b.length) || a.includes(b)
}

/** `items` in a new random order. */
export function shuffled<T>(items: T[]): T[] {
  const out = [...items]
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}
