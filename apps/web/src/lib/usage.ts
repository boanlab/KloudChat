/**
 * Token counters a turn carries: the writer's, and the planner's and reviewers' when they ran
 * (`outlineInputTokens`, `model:<id>:inputTokens`). The line under an answer shows their sum.
 */
export type UsageCounters = Record<string, number | boolean | undefined>

export function tokenTotals(usage: UsageCounters | undefined): { input: number; output: number } {
  let input = 0
  let output = 0
  for (const [key, value] of Object.entries(usage ?? {})) {
    if (typeof value !== 'number') continue
    if (/(?:^i|I)nputTokens$/.test(key)) input += value
    else if (/(?:^o|O)utputTokens$/.test(key)) output += value
  }
  return { input, output }
}

/** The numeric counters of a `usage` event, without its `type`. */
export function counters(event: Record<string, unknown>): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [key, value] of Object.entries(event)) {
    if (key !== 'type' && key !== 'credits' && typeof value === 'number') out[key] = value
  }
  return out
}
