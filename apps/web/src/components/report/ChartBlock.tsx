import { useEffect, useRef, useState } from 'react'
import { SlideChart } from '@/components/slides/SlideChart'
import { artifactsApi } from '@/lib/api'
import { diagramKey } from '@/lib/diagramKey'
import { rasterise } from '@/lib/mermaid'

/**
 * A `chart` fence in a report, drawn with the deck's chart component.
 *
 *     ```chart
 *     bar | 건
 *     분기 | 처리 건수 | 반려 건수
 *     1분기 | 120 | 8
 *     ```
 */
export function ChartBlock({
  source,
  owner,
}: {
  source: string
  /** Document to store the rendered picture on (the `.hwpx` export embeds it); absent in the transcript. */
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

/** Parses the fence; mirrors `report_export._chart_block`. Short rows are dropped, never padded. */
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
