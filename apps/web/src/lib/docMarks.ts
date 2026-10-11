import { useEffect, useState } from 'react'
import { artifactsApi } from '@/lib/api'
import type { DocMarks, ReportArtifact } from '@/types'

/** Stored marks, re-located live against the body; documents saved without marks keep the placeholder scan. */
export function useDocMarks(report: ReportArtifact, writing: boolean): DocMarks | null {
  const [live, setLive] = useState<{ version: number; marks: DocMarks } | null>(null)
  const stored = report.marks ?? null
  useEffect(() => {
    if (!stored || writing) return
    let current = true
    artifactsApi.marks(report.id).then(
      (marks) => { if (current) setLive({ version: report.version, marks }) },
      () => undefined,
    )
    return () => { current = false }
  }, [report.id, report.version, stored, writing])
  return live?.version === report.version ? live.marks : stored
}

/** The section's text with its `nth` occurrence of `text` replaced; null when it is not there. */
export function fillBlank(content: string, text: string, nth: number, value: string): string | null {
  let at = -1
  for (let i = 0; i <= nth; i++) {
    at = content.indexOf(text, at + 1)
    if (at < 0) return null
  }
  return content.slice(0, at) + value + content.slice(at + text.length)
}

/** Flagged sentence as rendered, whitespace-free: source spans lose table pipes and list, heading and emphasis marks. */
export function findingProbe(text: string): string {
  let line = text.trim()
  if (line.startsWith('|')) line = line.split('|').join(' ')
  line = line.replace(/\*\*|__|`/g, '').replace(/^(?:[-*+]|#{1,6}|\d+[.)])\s+/, '')
  return line.replace(/\s+/g, '').slice(0, 40)
}
