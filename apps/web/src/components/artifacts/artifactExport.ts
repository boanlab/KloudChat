import { sectionText } from '@/components/report/SectionBody'
import { downloadArtifact, fileUrl } from '@/lib/api'
import type { Artifact, ChartArtifact, DeckArtifact, ReportArtifact, ReportSection, Slide } from '@/types'

/** Every file an artifact leaves as, the same set the server's `export_formats` offers. */
export type ExportFormat = 'pdf' | 'docx' | 'hwpx' | 'pptx' | 'md' | 'html' | 'source' | 'csv' | 'file'

export interface ExportChoice {
  format: ExportFormat
  /** Korean label, translated where it is shown. */
  label: string
  hint: string
}

/** Whether an HTML artifact is a deck: by its template kind, else by its markup (the server's rule). */
export function htmlIsDeck(content: string, templateKind?: string): boolean {
  return templateKind ? templateKind === 'deck' : content.includes('class="slide')
}

/** The download menu of an artifact, most used first. Empty when nothing can be taken away. */
export function exportChoices(artifact: Artifact, templateKind?: string): ExportChoice[] {
  switch (artifact.kind) {
    case 'report':
      return [
        { format: 'pdf', label: 'PDF', hint: 'PDF' },
        { format: 'docx', label: 'Word 문서', hint: 'DOCX' },
        { format: 'hwpx', label: '한글 문서', hint: 'HWPX' },
        { format: 'md', label: '마크다운 원문', hint: 'MD' },
      ]
    case 'deck':
      return [
        { format: 'pptx', label: 'PowerPoint', hint: 'PPTX' },
        { format: 'pdf', label: 'PDF (발표용)', hint: 'PDF' },
        { format: 'md', label: '텍스트 (노트 포함)', hint: 'MD' },
      ]
    case 'html':
      return htmlIsDeck(artifact.content, templateKind)
        ? [
            { format: 'pptx', label: 'PowerPoint', hint: 'PPTX' },
            { format: 'pdf', label: 'PDF', hint: 'PDF' },
            { format: 'html', label: '원본 HTML', hint: 'HTML' },
            { format: 'md', label: '텍스트', hint: 'MD' },
          ]
        : [
            { format: 'docx', label: 'Word 문서', hint: 'DOCX' },
            { format: 'pdf', label: 'PDF', hint: 'PDF' },
            { format: 'hwpx', label: '한글 문서', hint: 'HWPX' },
            { format: 'html', label: '원본 HTML', hint: 'HTML' },
            { format: 'md', label: '텍스트', hint: 'MD' },
          ]
    case 'code':
      return [{ format: 'source', label: '원본 파일', hint: (artifact.language ?? 'TXT').toUpperCase() }]
    case 'chart':
      // PNG and SVG are drawn from the chart on screen; the panel offers them.
      return [{ format: 'csv', label: '데이터', hint: 'CSV' }]
    case 'image':
    case 'audio':
    case 'video':
      return artifact.src ? [{ format: 'file', label: '원본 파일', hint: '' }] : []
  }
}

/** One thing to look at before a file leaves: 「근거 확인 필요 2장」. */
export interface ExportCheck {
  /** Korean label with `{n}`. */
  label: string
  count: number
  /** Report: the section; deck: the slide index. */
  target?: string | number
}

export function citationNumbers(sections: ReportSection[]): number[] {
  const found = new Set<number>()
  for (const section of sections) {
    for (const match of sectionText(section).matchAll(/(?:\[|［)\s*(\d+)\s*(?:\]|］)/g)) {
      found.add(Number(match[1]))
    }
  }
  return [...found].sort((left, right) => left - right)
}

export interface NumericEvidenceGap {
  section: ReportSection
  excerpts: string[]
}

/** Numeric claims that have no `[n]` marker in the same sentence. */
export function numericEvidenceGaps(sections: ReportSection[]): NumericEvidenceGap[] {
  const numericFact = /\d[\d,.]*\s*(?:%|％|원|명|건|개|배|년|월|일|시간|분|초|점|대|회|쪽|페이지|GB|MB|km|kg)/i
  const marker = /(?:\[|［)\s*\d+\s*(?:\]|］)/
  const gaps: NumericEvidenceGap[] = []
  for (const section of sections) {
    const excerpts = sectionText(section)
      .split(/(?<=[.!?。！？])\s+|\n+/)
      .map((sentence) => sentence.trim())
      .filter(
        (sentence) =>
          sentence.length > 0 &&
          numericFact.test(sentence) &&
          !marker.test(sentence) &&
          !/^\d+[.)]\s/.test(sentence),
      )
      .slice(0, 3)
    if (excerpts.length) gaps.push({ section, excerpts })
  }
  return gaps
}

/** Cheap overflow estimate for the whole deck; the selected slide is measured in pixels. */
export function overflowRisk(slide: Slide): boolean {
  const titleLoad = Math.ceil((slide.title?.length ?? 0) / 34)
  const bulletLoad = (slide.bullets ?? []).reduce((sum, row) => sum + Math.max(1, Math.ceil(row.length / 38)), 0)
  const tableLoad = (slide.rows ?? []).length * 1.35
  const pairLoad = Math.max(slide.metrics?.length ?? 0, slide.bands?.length ?? 0, slide.tiles?.length ?? 0, slide.timeline?.length ?? 0, slide.steps?.length ?? 0, slide.cards?.length ?? 0, slide.items?.length ?? 0) * 1.6
  const columnLoad = Math.max(0, ...(slide.columns ?? []).map((column) => column.items.length)) * 1.2
  const chartLoad = (slide.chart?.categories.length ?? 0) * 0.8 + (slide.chart?.series.length ?? 0) * 0.8
  const scale = slide.textScale ?? 1
  return (titleLoad * 1.5 + bulletLoad + tableLoad + pairLoad + columnLoad + chartLoad) * scale > 10
}

/** Citations with no source behind them plus numeric claims with no citation. */
export function reportEvidenceWarnings(report: ReportArtifact): number {
  const known = new Set(report.sources.map((source) => source.ordinal))
  return (
    citationNumbers(report.sections).filter((number) => !known.has(number)).length +
    numericEvidenceGaps(report.sections).reduce((sum, gap) => sum + gap.excerpts.length, 0)
  )
}

/** Slides whose fact check found an unsupported claim, by index. */
export function weakSlideIndexes(deck: DeckArtifact): number[] {
  return deck.slides
    .map((s, i) => (s.factCheck?.claims.some((c) => c.verdict !== 'supported') ? i : -1))
    .filter((i) => i >= 0)
}

/** What the ribbon's export menu asks about first, without the panel open. */
export function exportChecks(artifact: Artifact): ExportCheck[] {
  if (artifact.kind === 'report') {
    const count = reportEvidenceWarnings(artifact)
    return count ? [{ label: '내보내기 전 근거 확인 {count}건', count }] : []
  }
  if (artifact.kind === 'deck') {
    const checks: ExportCheck[] = []
    const overflow = artifact.slides.map((s, i) => (overflowRisk(s) ? i : -1)).filter((i) => i >= 0)
    if (overflow.length) checks.push({ label: '잘림 위험 {n}장 확인', count: overflow.length, target: overflow[0] })
    const weak = weakSlideIndexes(artifact)
    if (weak.length) checks.push({ label: '근거 확인 필요 {n}장', count: weak.length, target: weak[0] })
    const open = (artifact.reviewComments ?? []).filter((c) => c.status === 'open').length
    if (open) checks.push({ label: '미해결 검토 메모 {n}개', count: open })
    return checks
  }
  return []
}

/** The table as CSV; the BOM makes Excel decode Korean as UTF-8. */
export function csvSource(chart: ChartArtifact): string {
  const escape = (v: string | number) => {
    const text = String(v)
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  const lines = [chart.table.columns.map(escape).join(',')]
  for (const row of chart.table.rows) lines.push(row.map(escape).join(','))
  return `\ufeff${lines.join('\n')}\n`
}


/** A file name stem a download can carry. */
function stem(title: string): string {
  return title.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'download'
}

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}

/** Takes an artifact away as `format`, by the same endpoint the panel's menu uses. */
export async function takeArtifact(artifact: Artifact, format: ExportFormat): Promise<void> {
  const title = artifact.title || 'document'
  if (format === 'csv' && artifact.kind === 'chart') {
    saveBlob(new Blob([csvSource(artifact)], { type: 'text/csv;charset=utf-8' }), `${stem(title)}.csv`)
    return
  }
  if (format === 'file') {
    const src = 'src' in artifact ? artifact.src : null
    if (!src) return
    const res = await fetch(fileUrl(src) ?? src, { credentials: 'include' })
    if (!res.ok) throw new Error(String(res.status))
    const ext = src.split('?')[0].split('.').pop()
    saveBlob(await res.blob(), ext && ext.length <= 5 && !ext.includes('/') ? `${stem(title)}.${ext}` : stem(title))
    return
  }
  if (format === 'csv') return
  await downloadArtifact(artifact.id, format, title)
}
