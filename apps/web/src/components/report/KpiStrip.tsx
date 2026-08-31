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
export function KpiStrip({ source }: { source: string }) {
  const figures = parse(source, 4)
  if (!figures.length) return null
  return (
    <div className="my-5 flex flex-wrap justify-around gap-x-6 gap-y-4 border-y border-line py-4">
      {figures.map(([value, label], i) => (
        <div key={i} className="min-w-24 flex-1 text-center">
          <div className="text-2xl font-semibold leading-tight text-accent">{value}</div>
          <div className="mt-1 text-xs text-muted">{label}</div>
        </div>
      ))}
    </div>
  )
}
