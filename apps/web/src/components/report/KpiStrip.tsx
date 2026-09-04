import { parse } from '@/components/report/StepList'

// `kpi` fence: `32% | 오탐 감소` per line. The exporters draw it as a table.
const isFigure = (text: string) => /\d/.test(text)

export function KpiStrip({ source }: { source: string }) {
  const pairs = parse(source, 4)
  if (!pairs.length) return null

  // Either order is accepted; the side with digits is the figure.
  const rows = pairs.map(([left, right]) =>
    isFigure(right) && !isFigure(left) ? ([right, left] as const) : ([left, right] as const),
  )

  // With no figures at all, show the pairs as a definition list.
  if (!rows.some(([value]) => isFigure(value))) {
    return (
      <dl className="my-5 grid gap-x-6 gap-y-2 border-y border-line py-4 sm:grid-cols-2">
        {rows.map(([name, note], i) => (
          <div key={i} className="flex min-w-0 items-baseline justify-between gap-3">
            <dt className="min-w-0 truncate text-base">{name}</dt>
            <dd className="shrink-0 text-sm text-muted">{note}</dd>
          </div>
        ))}
      </dl>
    )
  }

  return (
    <div className="my-5 flex flex-wrap justify-around gap-x-6 gap-y-4 border-y border-line py-4">
      {rows.map(([value, label], i) => (
        <div key={i} className="min-w-24 flex-1 text-center">
          <div className="text-2xl font-semibold leading-tight text-accent">{value}</div>
          <div className="mt-1 text-xs text-muted">{label}</div>
        </div>
      ))}
    </div>
  )
}
