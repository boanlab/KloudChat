/**
 * A procedure, numbered, in order.
 *
 * The third of the three shapes a report reaches for when prose is the wrong
 * tool — after the table (values to read) and the strip of figures (one number
 * to remember). This one is a sequence, and prose hides sequence: "먼저 …, 그
 * 다음 …, 이후 …" is a paragraph a reader has to take apart before they can
 * follow it.
 *
 *     ```steps
 *     자료 수집 | 공개 데이터와 내부 로그를 모은다
 *     정제 | 중복과 결측을 걸러낸다
 *     분석 | 세 가지 기준으로 견준다
 *     ```
 *
 * Deliberately not a mermaid diagram, which is the other way to draw this. A
 * diagram is right when the flow branches — a decision, a loop, two paths that
 * rejoin — and wrong when it does not, because a straight chain of boxes is a
 * picture of a list. And a picture is where the text goes to die: in the
 * `.docx` somebody submits it cannot be searched, copied, corrected or reflowed
 * to another paper size. This stays text the whole way out.
 */
export function StepList({ source }: { source: string }) {
  const steps = parse(source, 8)
  if (!steps.length) return null
  return (
    <ol className="my-5 list-none space-y-0 border-l-2 border-accent pl-0">
      {steps.map(([name, detail], i) => (
        <li key={i} className="flex gap-3 border-b border-line py-2 pl-3 last:border-0">
          <span className="mt-px shrink-0 text-sm font-semibold tabular-nums text-accent">
            {i + 1}
          </span>
          <span className="min-w-0">
            <strong className="font-semibold">{name}</strong>
            {detail ? <span className="text-muted"> {detail}</span> : null}
          </span>
        </li>
      ))}
    </ol>
  )
}

/**
 * `[왼쪽, 오른쪽]` per line, split on the first `|`.
 *
 * Shared with the strip of figures above it, and kept in step with
 * `report_export._kpi_rows`, which reads the same two fences on the way into a
 * file — including the caps. A line with no separator is dropped: half of one
 * of these blocks is an item with nothing said about it.
 */
export function parse(source: string, limit = 4): [string, string][] {
  return source
    .split('\n')
    .map((line) => {
      const at = line.indexOf('|')
      if (at < 0) return null
      const pair: [string, string] = [line.slice(0, at).trim(), line.slice(at + 1).trim()]
      return pair[0] && pair[1] ? pair : null
    })
    .filter((pair): pair is [string, string] => pair !== null)
    .slice(0, limit)
}
