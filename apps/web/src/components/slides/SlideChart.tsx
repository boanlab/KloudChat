/**
 * Slide chart as SVG. Same arithmetic as the `.pptx` and `.pdf` renderers:
 * zero floor, four gridlines, the deck's accent.
 */
export function SlideChart({
  chart,
  accent,
  scale,
}: {
  chart: NonNullable<import('@/types').Slide['chart']>
  accent: string
  /** Preview scale factor. */
  scale: number
}) {
  const width = 400
  const height = 150
  const pad = { left: 34, right: 4, top: 8, bottom: 20 }
  const plot = {
    w: width - pad.left - pad.right,
    h: height - pad.top - pad.bottom,
  }

  const values = chart.series.flatMap((s) => s.values)
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
    </svg>
  )
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
