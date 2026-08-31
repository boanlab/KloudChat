import { parse } from '@/components/report/StepList'

/**
 * A row of figures — the number large, what it counts under it.
 *
 * The thing a report needs that a diagram is not. A finding like "오탐이 32%
 * 줄었다" is the sentence a reader takes away, and buried mid-paragraph it is
 * read at the same weight as everything around it. Set as a strip it is read
 * first, which is the whole point of putting it there.
 *
 * Written as a fence rather than as markup because a section body is Markdown:
 * a raw `<div>` in one is text to every reader of it, and turning on raw HTML
 * to make a KPI block work would turn it on for everything else the model
 * writes too.
 *
 *     ```kpi
 *     32% | 오탐 감소
 *     1.4초 | 평균 응답
 *     ```
 *
 * Not rasterised, unlike a mermaid diagram. A diagram has to become a picture
 * because nothing outside a browser can draw one; a strip of figures is text,
 * and the three exporters draw it as a real table. That matters on the way
 * out: the person who receives the `.docx` can correct a number, search for
 * it, and print it sharp.
 */
/** Anything with a digit in it. A figure is what this block is for. */
const isFigure = (text: string) => /\d/.test(text)

export function KpiStrip({ source }: { source: string }) {
  const pairs = parse(source, 4)
  if (!pairs.length) return null

  // Written the other way round is still written. `32% | 오탐 감소` is the
  // shape the prompt shows and `오탐 감소 | 32%` is the shape people and
  // models write anyway; reading the digits rather than the position costs
  // nothing and stops a strip coming out with its labels set at 24px.
  const rows = pairs.map(([left, right]) =>
    isFigure(right) && !isFigure(left) ? ([right, left] as const) : ([left, right] as const),
  )

  // No figures anywhere. It happens for an honest reason: a writer told not to
  // invent numbers puts "확인 필요" where the number would go, which is the
  // right thing to write and the wrong thing to set in accent at 24px — four
  // sentences in the figure slot, wrapping, reading as a broken component.
  // Shown as the pairs they are instead. Nothing is dropped: what the writer
  // meant is still on the page, and the exporters read the same fence either
  // way.
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
