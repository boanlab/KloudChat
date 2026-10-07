/**
 * Slide chart as SVG. Same arithmetic as the `.pptx` and `.pdf` renderers:
 * zero floor, four gridlines, the deck's accent.
 */
export function SlideChart({
  chart,
  accent,
  scale,
  ink = '#444',
}: {
  chart: NonNullable<import('@/types').Slide['chart']>
  accent: string
  /** Preview scale factor. */
  scale: number
  /** Label colour for the pie and horizontal-bar drawers; the look's ink on a pattern slide. */
  ink?: string
}) {
  const width = 400
  const height = 150
  const pad = { left: 34, right: 4, top: 8, bottom: 20 }
  const plot = {
    w: width - pad.left - pad.right,
    h: height - pad.top - pad.bottom,
  }

  if (chart.kind === 'pie' || chart.kind === 'donut') return <PieChart chart={chart} accent={accent} scale={scale} ink={ink} />
  if (chart.kind === 'hbar') return <HBarChart chart={chart} accent={accent} scale={scale} ink={ink} />
  const stacked = chart.kind === 'stacked'

  const values = stacked
    ? chart.categories.map((_, i) => chart.series.reduce((sum, s) => sum + Math.max(0, s.values[i] ?? 0), 0))
    : chart.series.flatMap((s) => s.values)
  const ceiling = Math.max(...values, 0) * 1.15
  if (!(ceiling > 0) || chart.categories.length === 0) return null

  const step = plot.w / chart.categories.length
  const y = (value: number) => pad.top + plot.h - plot.h * (value / ceiling)
  const colours = [accent, mix(accent, '#ffffff', 0.55)]

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      style={{ width: '100%', height: `${height * scale}px`, marginTop: `${10 * scale}px` }}
      role="img"
    >
      {[0, 1, 2, 3, 4].map((tick) => {
        const at = pad.top + (plot.h * (4 - tick)) / 4
        return (
          <g key={tick}>
            <line x1={pad.left} y1={at} x2={width - pad.right} y2={at} stroke="#e5e5e5" strokeWidth={0.5} />
            <text x={pad.left - 5} y={at + 3} textAnchor="end" fontSize={7} fill="#888">
              {tickLabel((ceiling * tick) / 4)}
            </text>
          </g>
        )
      })}

      {chart.kind === 'line'
        ? chart.series.map((series, s) => (
            <polyline
              key={s}
              fill="none"
              stroke={colours[s % colours.length]}
              strokeWidth={1.6}
              points={series.values
                .map((v, i) => `${pad.left + step * (i + 0.5)},${y(v)}`)
                .join(' ')}
            />
          ))
        : stacked
          ? chart.categories.map((_, i) => {
              // Series pile up in the slot, first at the bottom.
              const bases = runningSums(chart.series.map((series) => Math.max(0, series.values[i] ?? 0)))
              return chart.series.map((_series, s) => {
                const base = bases[s]
                const top = bases[s + 1]
                return (
                  <rect
                    key={`${s}-${i}`}
                    x={pad.left + step * (i + 0.5) - (step * 0.5) / 2}
                    y={y(top)}
                    width={step * 0.5}
                    height={y(base) - y(top)}
                    fill={palette(accent, chart.series.length)[s]}
                  />
                )
              })
            })
        : chart.series.map((series, s) => {
            // Series share the slot side by side.
            const span = (step * 0.6) / chart.series.length
            return series.values.map((v, i) => (
              <rect
                key={`${s}-${i}`}
                x={pad.left + step * (i + 0.5) - (step * 0.6) / 2 + span * s}
                y={y(v)}
                width={span}
                height={pad.top + plot.h - y(v)}
                fill={colours[s % colours.length]}
              />
            ))
          })}

      {chart.categories.map((label, i) => (
        <text
          key={i}
          x={pad.left + step * (i + 0.5)}
          y={height - 6}
          textAnchor="middle"
          fontSize={7.5}
          fill="#666"
        >
          {label}
        </text>
      ))}
      {chart.unit && (
        <text x={0} y={pad.top + 2} fontSize={7} fill="#888">
          {chart.unit}
        </text>
      )}
      {stacked && (
        <Legend
          x={width - pad.right}
          y={pad.top + 2}
          align="end"
          entries={chart.series.map((series, s) => [series.name, palette(accent, chart.series.length)[s]])}
        />
      )}
    </svg>
  )
}

type Chart = NonNullable<import('@/types').Slide['chart']>

/** Shades of the accent for categories or series: the accent, then alternately lighter and darker. */
function palette(accent: string, count: number): string[] {
  const steps: [string, number][] = [
    ['#ffffff', 0], ['#ffffff', 0.45], ['#111827', 0.4], ['#ffffff', 0.7], ['#111827', 0.65], ['#ffffff', 0.85],
    ['#111827', 0.2], ['#ffffff', 0.3],
  ]
  return Array.from({ length: Math.max(count, 1) }, (_, i) => {
    const [toward, amount] = steps[i % steps.length]
    return amount ? mix(accent, toward, amount) : accent
  })
}

/** A row of colour keys; `align: end` right-aligns it at `x`. */
function Legend({ x, y, entries, align = 'start' }: { x: number; y: number; entries: [string, string][]; align?: 'start' | 'end' }) {
  const widths = entries.map(([name]) => 12 + textWidth(name, 7) + 8)
  const offsets = runningSums(widths)
  const origin = align === 'end' ? x - offsets[offsets.length - 1] : x
  return (
    <g>
      {entries.map(([name, colour], i) => {
        const left = origin + offsets[i]
        return (
          <g key={i}>
            <rect x={left} y={y - 5} width={7} height={7} fill={colour} />
            <text x={left + 10} y={y + 1} fontSize={7} fill="#666">{name}</text>
          </g>
        )
      })}
    </g>
  )
}

/** Pie or donut of the first series, legend on the right with each share. */
function PieChart({ chart, accent, scale, ink }: { chart: Chart; accent: string; scale: number; ink: string }) {
  const width = 400
  const height = 150
  const values = (chart.series[0]?.values ?? []).map((v) => Math.max(0, v || 0))
  const total = values.reduce((sum, v) => sum + v, 0)
  if (!(total > 0) || chart.categories.length === 0) return null
  const colours = palette(accent, values.length)
  const cx = 120
  const cy = height / 2
  const r = 62
  const hole = chart.kind === 'donut' ? r * 0.58 : 0
  // Each slice starts where the ones before it end, from twelve o'clock.
  const starts = runningSums(values).map((sum) => -Math.PI / 2 + (sum / total) * Math.PI * 2)
  const point = (radius: number, a: number) => `${cx + radius * Math.cos(a)} ${cy + radius * Math.sin(a)}`
  const slices = values.map((v, i) => {
    const sweep = (v / total) * Math.PI * 2
    const start = starts[i]
    const end = starts[i + 1]
    if (v <= 0) return null
    // A whole circle cannot be one arc; two halves.
    if (sweep >= Math.PI * 2 - 1e-6) {
      return hole
        ? <circle key={i} cx={cx} cy={cy} r={(r + hole) / 2} fill="none" stroke={colours[i]} strokeWidth={r - hole} />
        : <circle key={i} cx={cx} cy={cy} r={r} fill={colours[i]} />
    }
    const large = sweep > Math.PI ? 1 : 0
    const d = hole
      ? `M ${point(r, start)} A ${r} ${r} 0 ${large} 1 ${point(r, end)} L ${point(hole, end)} A ${hole} ${hole} 0 ${large} 0 ${point(hole, start)} Z`
      : `M ${cx} ${cy} L ${point(r, start)} A ${r} ${r} 0 ${large} 1 ${point(r, end)} Z`
    return <path key={i} d={d} fill={colours[i]} stroke="#fff" strokeWidth={0.8} />
  })
  const rowHeight = Math.min(18, (height - 16) / values.length)
  const top = cy - (rowHeight * values.length) / 2 + rowHeight / 2
  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: `${height * scale}px`, marginTop: `${10 * scale}px` }} role="img">
      {slices}
      {hole > 0 && (
        <>
          <text x={cx} y={cy - 1} textAnchor="middle" fontSize={16} fontWeight={700} fill={accent}>
            {tickLabel(total)}
          </text>
          {chart.unit && <text x={cx} y={cy + 11} textAnchor="middle" fontSize={7.5} fill="#888">{chart.unit}</text>}
        </>
      )}
      {chart.categories.map((label, i) => (
        <g key={i}>
          <rect x={220} y={top + rowHeight * i - 4.5} width={8} height={8} fill={colours[i]} />
          <text x={233} y={top + rowHeight * i + 2.5} fontSize={8.5} fill={ink}>{label}</text>
          <text x={340} y={top + rowHeight * i + 2.5} textAnchor="end" fontSize={8.5} fontWeight={700} fill={ink}>
            {`${Math.round(((values[i] ?? 0) / total) * 1000) / 10}%`}
          </text>
        </g>
      ))}
    </svg>
  )
}

/** Horizontal bars, one row per category; series share the row. */
function HBarChart({ chart, accent, scale, ink }: { chart: Chart; accent: string; scale: number; ink: string }) {
  const width = 400
  const height = 150
  const values = chart.series.flatMap((s) => s.values)
  const ceiling = Math.max(...values, 0) * 1.12
  if (!(ceiling > 0) || chart.categories.length === 0) return null
  const labelWidth = Math.min(130, Math.max(40, ...chart.categories.map((c) => textWidth(c, 8))) + 8)
  const legend = chart.series.length > 1
  const pad = { left: labelWidth, right: 34, top: legend ? 14 : 4, bottom: 4 }
  const plotW = width - pad.left - pad.right
  const row = (height - pad.top - pad.bottom) / chart.categories.length
  const band = Math.min(row * 0.62, 22)
  const colours = palette(accent, chart.series.length)
  const span = band / chart.series.length
  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: `${height * scale}px`, marginTop: `${10 * scale}px` }} role="img">
      <line x1={pad.left} y1={pad.top} x2={pad.left} y2={height - pad.bottom} stroke="#d4d4d4" strokeWidth={0.6} />
      {chart.categories.map((label, i) => {
        const middle = pad.top + row * (i + 0.5)
        return (
          <g key={i}>
            <text x={pad.left - 6} y={middle + 3} textAnchor="end" fontSize={8} fill={ink}>{label}</text>
            {chart.series.map((series, s) => {
              const v = Math.max(0, series.values[i] ?? 0)
              const w = plotW * (v / ceiling)
              const top = middle - band / 2 + span * s
              return (
                <g key={s}>
                  <rect x={pad.left} y={top} width={w} height={span} fill={colours[s]} />
                  <text x={pad.left + w + 3} y={top + span / 2 + 2.5} fontSize={7} fill="#666">
                    {tickLabel(v)}{chart.unit && s === 0 && i === 0 ? ` ${chart.unit}` : ''}
                  </text>
                </g>
              )
            })}
          </g>
        )
      })}
      {legend && (
        <Legend x={width - pad.right} y={6} align="end" entries={chart.series.map((series, s) => [series.name, colours[s]])} />
      )}
    </svg>
  )
}

/** Approximate width of `text` at `size` viewBox units: Hangul one em, others about half. */
function textWidth(text: string, size: number): number {
  let ems = 0
  for (const char of text ?? '') {
    const code = char.codePointAt(0) ?? 0
    ems += (code >= 0xac00 && code <= 0xd7a3) || (code >= 0x3000 && code <= 0x9fff) ? 1 : char === ' ' ? 0.3 : 0.56
  }
  return ems * size
}

function tickLabel(value: number): string {
  return Math.abs(value) >= 10
    ? Math.round(value).toLocaleString()
    : String(Math.round(value * 10) / 10)
}

/** Mixes `from` toward `toward` by `amount`. */
function mix(from: string, toward: string, amount: number): string {
  const parse = (hex: string) => {
    const clean = hex.replace('#', '')
    const full = clean.length === 3 ? clean.replace(/./g, (c) => c + c) : clean
    const n = parseInt(full.slice(0, 6), 16)
    return Number.isNaN(n) ? [91, 91, 214] : [(n >> 16) & 255, (n >> 8) & 255, n & 255]
  }
  const a = parse(from)
  const b = parse(toward)
  const hex = (n: number) => Math.round(n).toString(16).padStart(2, '0')
  return `#${a.map((v, i) => hex(v + (b[i] - v) * amount)).join('')}`
}

/** Running sums: `[0, a, a + b, …]`, one longer than `values`. */
function runningSums(values: number[]): number[] {
  return values.reduce<number[]>((sums, value) => [...sums, sums[sums.length - 1] + value], [0])
}
