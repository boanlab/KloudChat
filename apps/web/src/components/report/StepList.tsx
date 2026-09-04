/**
 * `steps` fence: a numbered procedure, kept as text through every export.
 *
 *     ```steps
 *     자료 수집 | 공개 데이터와 내부 로그를 모은다
 *     정제 | 중복과 결측을 걸러낸다
 *     ```
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

/** `[left, right]` per line, split on the first `|`; mirrors `report_export._kpi_rows`. Lines without a separator are dropped. */
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
