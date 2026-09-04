import { BarChart3, Download, Table2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { Badge, Button, Dropdown, MenuItem, MenuLabel } from '@/components/ui'
import { cn } from '@/lib/utils'
import type { ChartArtifact } from '@/types'
import {
  PanelControls,
  usePanelWidth,
  type PanelMode,
} from '@/components/artifacts/PanelControls'
import { useT } from '@/lib/useT'

/** Model-provided series points with an empty fallback. */
function pointsOf(one: ChartArtifact['series'][number]) {
  return Array.isArray(one?.points) ? one.points : []
}

/** The series, likewise: a chart with none is empty rather than fatal. */
function seriesOf(chart: ChartArtifact) {
  return Array.isArray(chart.series) ? chart.series : []
}

/** Every x across every series, in first-appearance order. */
function categories(chart: ChartArtifact): string[] {
  const out: string[] = []
  for (const one of seriesOf(chart)) {
    for (const point of pointsOf(one)) if (!out.includes(point.x)) out.push(point.x)
  }
  return out
}

/**
 * Snaps the tick interval to a round number.
 *
 * Dividing by `max / 4` produces steps like 0.5, which round on display into a
 * repeated axis such as "0 1 1 2 2" — indistinguishable, to the reader, from a
 * flat series.
 */
function niceStep(range: number, count: number): number {
  if (range <= 0) return 1
  const rough = range / count
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  for (const multiple of [1, 2, 2.5, 5, 10]) {
    if (magnitude * multiple >= rough) return magnitude * multiple
  }
  return magnitude * 10
}

/**
 * Dependency-free SVG rather than a charting library: the whole plot is on this
 * page. The underlying rows stay one tab away, so the numbers are checkable.
 */
function Plot({ chart, interactive = true }: { chart: ChartArtifact; interactive?: boolean }) {
  const t = useT()
  const [hover, setHover] = useState<string | null>(null)
  const keys = categories(chart)
  const series = seriesOf(chart)

  // Inside the SVG, not in HTML: an exported .png/.svg would otherwise carry
  // colours and no names.
  const legend = series.length > 1
  const W = 560
  const H = 280
  const padL = 56
  const padR = 14
  const padB = 46
  const padT = legend ? 34 : 14
  const plotW = W - padL - padR
  const plotH = H - padB - padT
  const band = keys.length > 0 ? plotW / keys.length : plotW
  const stacked = chart.chartType === 'stacked'

  const valueAt = (index: number, key: string) =>
    pointsOf(series[index]).find((p) => p.x === key)?.y

  // Stacked bars scale to the whole stack, grouped ones to the tallest bar.
  const totals = keys.map((key) =>
    series.reduce((sum, _, i) => sum + Math.max(valueAt(i, key) ?? 0, 0), 0),
  )
  const values = series.flatMap((one) => pointsOf(one).map((p) => p.y))
  const hi = Math.max(stacked ? Math.max(...totals, 0) : Math.max(...values, 0), 0)
  // Zero baseline: SVG does not draw a bar of negative height.
  const lo = Math.min(...values, 0)
  const step = niceStep(hi - lo || 1, 4)
  const top = Math.ceil(hi / step) * step || step
  const bottom = Math.floor(lo / step) * step

  const y = (v: number) => padT + plotH - ((v - bottom) / (top - bottom)) * plotH
  const centre = (i: number) => padL + band * (i + 0.5)
  const zero = y(0)

  const ticks: number[] = []
  for (let v = bottom; v <= top + step / 2; v += step) ticks.push(Number(v.toFixed(6)))
  // Enough decimals to tell two ticks apart, and no more.
  const decimals = Math.max(0, Math.min(4, -Math.floor(Math.log10(step))))
  const label = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })

  if (keys.length === 0) {
    return (
      <div className="grid h-40 place-items-center text-base text-faint">
        {t('그릴 수 있는 값이 없습니다')}
      </div>
    )
  }

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      role="img"
      aria-label={chart.caption || chart.title}
    >
      {ticks.map((v) => (
        <g key={v}>
          <line
            x1={padL}
            x2={W - padR}
            y1={y(v)}
            y2={y(v)}
            stroke="var(--border)"
            strokeWidth={v === 0 && bottom < 0 ? 1.5 : 1}
          />
          <text
            x={padL - 8}
            y={y(v) + 4}
            textAnchor="end"
            className="fill-[var(--faint)]"
            fontSize={10}
          >
            {label(v)}
          </text>
        </g>
      ))}

      {/* 축 이름. 데이터 옆 각주가 아니라 축에 붙어 있어야 읽힌다 */}
      {chart.yLabel && (
        <text
          transform={`translate(13 ${padT + plotH / 2}) rotate(-90)`}
          textAnchor="middle"
          className="fill-[var(--muted)]"
          fontSize={10}
        >
          {chart.yLabel}
        </text>
      )}
      {chart.xLabel && (
        <text
          x={padL + plotW / 2}
          y={H - 6}
          textAnchor="middle"
          className="fill-[var(--muted)]"
          fontSize={10}
        >
          {chart.xLabel}
        </text>
      )}

      {legend &&
        series.map((one, si) => {
          // Laid out by approximate width — SVG has no flexbox.
          const offset = series
            .slice(0, si)
            .reduce((x, prior) => x + prior.name.length * 7 + 26, padL)
          return (
            <g key={`legend-${one.name}`}>
              <rect x={offset} y={8} width={9} height={9} rx={2} fill={one.color} />
              <text
                x={offset + 14}
                y={16}
                className="fill-[var(--muted)]"
                fontSize={11}
              >
                {one.name}
              </text>
            </g>
          )
        })}

      {chart.chartType === 'line'
        ? series.map((one, si) => {
            const drawn = keys
              .map((key, i) => ({ i, v: valueAt(si, key) }))
              .filter((d): d is { i: number; v: number } => typeof d.v === 'number')
            return (
              <g key={one.name}>
                <polyline
                  fill="none"
                  stroke={one.color}
                  strokeWidth={2.5}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  points={drawn.map((d) => `${centre(d.i)},${y(d.v)}`).join(' ')}
                />
                {/* 점을 찍는다. 값이 하나뿐인 계열은 선이 그려지지 않아
                    그리지 않은 것과 구분이 안 됐다 */}
                {drawn.map((d) => (
                  <circle key={d.i} cx={centre(d.i)} cy={y(d.v)} r={3} fill={one.color} />
                ))}
              </g>
            )
          })
        : keys.map((key, i) => {
            let cursor = 0
            const width = stacked ? band * 0.5 : (band * 0.62) / series.length
            return series.map((one, si) => {
              const value = valueAt(si, key)
              if (typeof value !== 'number') return null
              const left = stacked
                ? centre(i) - width / 2
                : centre(i) - (width * series.length) / 2 + width * si
              const height = Math.abs(y(value) - zero)
              const topEdge = stacked
                ? y(cursor + Math.max(value, 0)) // 아래에서 위로 쌓는다
                : Math.min(y(value), zero)
              if (stacked) cursor += Math.max(value, 0)
              return (
                <rect
                  key={`${one.name}-${key}`}
                  x={left}
                  y={topEdge}
                  width={width}
                  height={Math.max(height, 1)}
                  rx={stacked ? 0 : 3}
                  fill={one.color}
                  opacity={hover && hover !== key ? 0.35 : 1}
                />
              )
            })
          })}

      {keys.map((key, i) => (
        <text
          key={key}
          x={centre(i)}
          y={padT + plotH + 16}
          textAnchor="middle"
          className={cn(hover === key ? 'fill-[var(--fg)]' : 'fill-[var(--muted)]')}
          fontSize={10}
        >
          {key}
        </text>
      ))}

      {/* 값 읽기. 숫자를 데이터 탭에만 두면 차트만 캡처해 간 사람은 읽을 수 없다 */}
      {interactive &&
        keys.map((key, i) => (
          <rect
            key={`hit-${key}`}
            x={padL + band * i}
            y={padT}
            width={band}
            height={plotH}
            fill="transparent"
            onMouseEnter={() => setHover(key)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      {interactive && hover && (
        <g pointerEvents="none">
          <line
            x1={centre(keys.indexOf(hover))}
            x2={centre(keys.indexOf(hover))}
            y1={padT}
            y2={padT + plotH}
            stroke="var(--border)"
            strokeWidth={1}
          />
          {series.map((one, si) => {
            const value = valueAt(si, hover)
            if (typeof value !== 'number') return null
            return (
              <text
                key={one.name}
                x={centre(keys.indexOf(hover)) < padL + plotW / 2 ? centre(keys.indexOf(hover)) + 10 : centre(keys.indexOf(hover)) - 10}
                y={padT + 12 + si * 14}
                textAnchor={centre(keys.indexOf(hover)) < padL + plotW / 2 ? 'start' : 'end'}
                className="fill-[var(--fg)]"
                fontSize={11}
                fontWeight={600}
              >
                {series.length > 1 ? `${one.name} ` : ''}
                {value.toLocaleString()}
              </text>
            )
          })}
        </g>
      )}
    </svg>
  )
}

/** Static, non-interactive render for cards and previews. */
export function ChartThumb({ chart }: { chart: ChartArtifact }) {
  return (
    <div className="pointer-events-none flex size-full flex-col justify-center bg-panel px-4 py-3">
      <Plot chart={chart} interactive={false} />
      <div className="mt-1 flex flex-wrap gap-2.5">
        {seriesOf(chart).map((s) => (
          <span key={s.name} className="flex items-center gap-1 text-2xs text-muted">
            <span className="size-2 rounded-sm" style={{ background: s.color }} />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  )
}


/** Hands `data` to the browser as a file. Same shape the report exports use. */
function save(data: BlobPart, mime: string, filename: string) {
  const url = URL.createObjectURL(new Blob([data], { type: mime }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

function stem(title: string) {
  return title.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'chart'
}

/**
 * The rendered SVG with theme variables resolved. `var(--border)` and friends
 * mean nothing once the file leaves the page, so they are substituted for their
 * computed values at save time.
 */
function svgSource(node: SVGSVGElement): string {
  const clone = node.cloneNode(true) as SVGSVGElement
  const computed = getComputedStyle(document.documentElement)
  const resolve = (value: string) =>
    value.replace(/var\((--[\w-]+)\)/g, (_, name) => computed.getPropertyValue(name).trim() || '#888')

  const CLASS_COLOURS: Record<string, string> = {
    'fill-[var(--faint)]': '#8b8b93',
    'fill-[var(--muted)]': '#6b6b73',
    'fill-[var(--fg)]': '#1a1a1a',
  }
  clone.querySelectorAll('*').forEach((node_) => {
    const el = node_ as SVGElement
    for (const attribute of ['stroke', 'fill']) {
      const current = el.getAttribute(attribute)
      if (current?.includes('var(')) el.setAttribute(attribute, resolve(current))
    }
    for (const [className, colour] of Object.entries(CLASS_COLOURS)) {
      if (el.classList.contains(className)) el.setAttribute('fill', colour)
    }
    el.removeAttribute('class')
  })
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
  rect.setAttribute('width', '100%')
  rect.setAttribute('height', '100%')
  rect.setAttribute('fill', '#ffffff')
  clone.insertBefore(rect, clone.firstChild)
  return new XMLSerializer().serializeToString(clone)
}

/** The table as CSV, with a BOM so Excel opens Korean without mangling it. */
function csvSource(chart: ChartArtifact): string {
  const escape = (v: string | number) => {
    const text = String(v)
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  const lines = [chart.table.columns.map(escape).join(',')]
  for (const row of chart.table.rows) lines.push(row.map(escape).join(','))
  return `\ufeff${lines.join('\n')}\n`
}

export function ChartPanel({
  chart,
  onClose,
  onModeChange,
}: {
  chart: ChartArtifact
  onClose?: () => void
  onModeChange?: (mode: PanelMode) => void
}) {
  const t = useT()
  const width = usePanelWidth(onModeChange)
  const [tab, setTab] = useState<'chart' | 'table'>('chart')
  const plot = useRef<HTMLDivElement>(null)

  const svg = () => plot.current?.querySelector('svg') ?? null

  const exportSvg = () => {
    const node = svg()
    if (node) save(svgSource(node), 'image/svg+xml', `${stem(chart.title)}.svg`)
  }

  // Rasterised through a canvas; the SVG is already the picture. 2× so it
  // stays sharp in a deck.
  const exportPng = () => {
    const node = svg()
    if (!node) return
    const source = svgSource(node)
    const image = new Image()
    image.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = image.width * 2
      canvas.height = image.height * 2
      const context = canvas.getContext('2d')
      if (!context) return
      context.fillStyle = '#ffffff'
      context.fillRect(0, 0, canvas.width, canvas.height)
      context.drawImage(image, 0, 0, canvas.width, canvas.height)
      canvas.toBlob((blob) => {
        if (blob) save(blob, 'image/png', `${stem(chart.title)}.png`)
      }, 'image/png')
    }
    image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`
  }

  const exportCsv = () =>
    save(csvSource(chart), 'text/csv;charset=utf-8', `${stem(chart.title)}.csv`)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <BarChart3 size={15} className="shrink-0 text-accent" />
        <p className="min-w-0 flex-1 truncate text-base font-medium">{chart.title}</p>
        <Badge>v{chart.version}</Badge>
        <Dropdown
          align="right"
          trigger={() => (
            <Button size="sm">
              <Download size={14} />
              {t('내보내기')}
            </Button>
          )}
        >
          <MenuLabel>{t('형식 선택')}</MenuLabel>
          <MenuItem hint="PNG" onClick={exportPng}>
            {t('이미지')}
          </MenuItem>
          <MenuItem hint="SVG" onClick={exportSvg}>
            {t('벡터')}
          </MenuItem>
          {/* CSV rather than XLSX: the menu offered 엑셀 and nothing behind it
              could write a workbook. A BOM makes Excel read the Korean. */}
          <MenuItem hint="CSV" onClick={exportCsv}>
            {t('데이터')}
          </MenuItem>
        </Dropdown>
        {/* 차트 패널만 닫는 버튼이 없었다. 세션에서 열면 대화를 떠나기 전에는
            치울 방법이 없었고, 그건 패널이 아니라 벽이다. */}
        <PanelControls mode={width.mode} onCycle={width.cycle} onClose={onClose} />
      </header>

      <div className="flex gap-1 border-b border-line px-3 py-1.5">
        {(
          [
            { id: 'chart', label: t('차트'), icon: BarChart3 },
            { id: 'table', label: t('데이터'), icon: Table2 },
          ] as const
        ).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              'flex items-center gap-1.5 rounded-control px-2 py-1 text-sm transition-colors',
              tab === t.id ? 'bg-elevated text-fg' : 'text-muted hover:text-fg',
            )}
          >
            <t.icon size={13} />
            {t.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4" ref={plot}>
        {/* Hidden rather than unmounted. The image exports serialise the live
            <svg>, so unmounting it on the data tab meant 이미지 and 벡터 did
            nothing at all from there — the menu opened, the item was clicked,
            and no file was ever produced. */}
        <div className={cn(tab === 'chart' ? '' : 'hidden')}>
            <Plot chart={chart} interactive={tab === 'chart'} />
            {chart.caption && <p className="mt-3 text-base text-muted">{chart.caption}</p>}
            {/* 축 이름은 축에 그려지므로 여기서는 출처만 */}
            {chart.sourceFile && (
              <p className="mt-2 text-xs text-faint">출처 {chart.sourceFile}</p>
            )}
        </div>
        <div className={cn(tab === 'table' ? '' : 'hidden')}>
          <div className="overflow-x-auto rounded-card border border-line">
            <table className="w-full border-collapse text-base">
              <thead className="bg-elevated">
                <tr>
                  {chart.table.columns.map((c) => (
                    <th key={c} className="border-b border-line px-3 py-2 text-left font-semibold">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {chart.table.rows.map((row, i) => (
                  <tr key={i} className="border-b border-line last:border-0">
                    {row.map((cell, j) => (
                      <td key={j} className={cn('px-3 py-2', j > 0 && 'tabular-nums')}>
                        {typeof cell === 'number' ? cell.toLocaleString() : cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
