import { useEffect, useRef, useState } from 'react'
import { SlideChart } from '@/components/slides/SlideChart'
import { artifactsApi } from '@/lib/api'
import { diagramKey } from '@/lib/diagramKey'
import { rasterise } from '@/lib/mermaid'

/**
 * A `chart` fence in a report, drawn.
 *
 * The same component the deck draws its charts with, because the two surfaces
 * have to agree about what a chart looks like — one accent, one zero floor,
 * one set of gridlines — and the fastest way for them to disagree is to have
 * two of them.
 *
 *     ```chart
 *     bar | 건
 *     분기 | 처리 건수 | 반려 건수
 *     1분기 | 120 | 8
 *     ```
 *
 * Parsed here rather than on the server for the same reason the strip of
 * figures is: this is what a section body carries, and the browser is one of
 * the four readers of it.
 */
export function ChartBlock({
  source,
  owner,
}: {
  source: string
  /**
   * Which document this chart belongs to, so the picture drawn here can be
   * kept. `.hwpx` cannot draw a chart — OWPML has an element for one and
   * writing it blind is how a file stops opening — so it embeds what a reader
   * has already seen, exactly the way it embeds a mermaid diagram. Absent in
   * the transcript, where there is nothing to store onto.
   */
  owner?: { artifactId: string; sectionId: string; stored?: Record<string, string> }
}) {
  const chart = parse(source)
  const host = useRef<HTMLDivElement>(null)
  const [key, setKey] = useState('')

  useEffect(() => {
    if (!chart) return
    let live = true
    void diagramKey(source).then((digest) => live && setKey(digest))
    return () => {
      live = false
    }
  }, [source, chart])

  const stored = key ? owner?.stored?.[key] : undefined
  useEffect(() => {
    const node = host.current
    if (!node || !owner?.artifactId || !key || stored) return
    let live = true
    void (async () => {
      const svg = node.querySelector('svg')
      if (!svg) return
      const png = await rasterise(new XMLSerializer().serializeToString(svg))
      if (!png || !live) return
      await artifactsApi
        .storeDiagram(owner.artifactId, owner.sectionId, key, png)
        .catch(() => undefined)
    })()
    return () => {
      live = false
    }
  }, [key, stored, owner?.artifactId, owner?.sectionId])

  if (!chart) return null
  return (
    <div ref={host} className="my-5">
      <SlideChart chart={chart} accent="var(--accent, #5b5bd6)" scale={1.25} />
    </div>
  )
}

/**
 * The fence as numbers, or `null`.
 *
 * Kept in step with `report_export._chart_block`, which reads the same text on
 * the way into a file — including the rule that matters: a row with fewer
 * values than there are series is dropped rather than padded. Padding puts a
 * zero on the chart that nobody wrote, and a zero on a chart is a claim.
 */
export function parse(source: string): Parameters<typeof SlideChart>[0]['chart'] | null {
  const lines = source
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  if (lines.length < 3) return null

  const head = lines[0].split('|').map((cell) => cell.trim())
  const kind = head[0]?.toLowerCase() === 'line' ? 'line' : 'bar'
  const unit = head[1] ?? ''
  const names = lines[1]
    .split('|')
    .map((cell) => cell.trim())
    .slice(1, 3)
  if (!names.length) return null

  const categories: string[] = []
  const columns: number[][] = names.map(() => [])
  for (const line of lines.slice(2)) {
    const cells = line.split('|').map((cell) => cell.trim())
    if (cells.length < names.length + 1 || !cells[0]) continue
    const values = cells.slice(1, names.length + 1).map((cell) => Number(cell.replace(/,/g, '')))
    if (values.some((v) => !Number.isFinite(v))) continue
    categories.push(cells[0])
    values.forEach((value, i) => columns[i].push(value))
  }
  if (categories.length < 2) return null

  return {
    kind,
    unit,
    categories: categories.slice(0, 8),
    series: names.map((name, i) => ({ name, values: columns[i].slice(0, 8) })),
  }
}
