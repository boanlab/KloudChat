import { useEffect, useRef } from 'react'
import { filesApi, type FileRow } from '@/lib/api'

/** What a file row says about its scanned pages being read (`services/ocr_jobs.py`). */
export interface OcrFields {
  id: string
  ocrState?: FileRow['ocrState']
  ocrTotal?: number
  ocrDone?: number
  ocrReview?: number
}

export const isReading = (file: OcrFields) =>
  file.ocrState === 'queued' || file.ocrState === 'running'

/**
 * One short line for a file's reading, or null when there is nothing to say:
 * 「글자 읽는 중 3/10쪽」, 「OCR로 읽음 · 확인 필요 2쪽」. A failure is told by the file's
 * own error.
 */
export function ocrNote(
  file: OcrFields,
  t: (key: string) => string,
): { text: string; tone: 'busy' | 'warn' | 'quiet' } | null {
  const total = file.ocrTotal ?? 0
  const done = file.ocrDone ?? 0
  if (isReading(file)) {
    return {
      text: t('글자 읽는 중 {done}/{total}쪽')
        .replace('{done}', String(done))
        .replace('{total}', String(total)),
      tone: 'busy',
    }
  }
  if (file.ocrState !== 'done') return null
  const parts = [t('OCR로 읽음')]
  if (done < total) parts.push(t('앞 {n}쪽까지').replace('{n}', String(done)))
  if (file.ocrReview) {
    parts.push(t('확인 필요 {n}쪽').replace('{n}', String(file.ocrReview)))
    return { text: parts.join(' · '), tone: 'warn' }
  }
  return { text: parts.join(' · '), tone: 'quiet' }
}

/** How often a screen asks after files still being read. */
const POLL_MS = 3000

/**
 * Follows the rows still being read: asks the server for each every few seconds and hands
 * back the fresh row, until none is left reading. Stops when the screen goes away.
 */
export function useOcrProgress(rows: OcrFields[], onRow: (row: FileRow) => void) {
  const onRowRef = useRef(onRow)
  useEffect(() => {
    onRowRef.current = onRow
  })
  const reading = rows.filter(isReading).map((row) => row.id)
  const key = reading.join(',')
  useEffect(() => {
    if (!key) return
    const ids = key.split(',')
    let live = true
    const timer = window.setInterval(() => {
      for (const id of ids) {
        void filesApi
          .get(id)
          .then((row) => {
            if (live) onRowRef.current(row)
          })
          .catch(() => undefined)
      }
    }, POLL_MS)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [key])
}
