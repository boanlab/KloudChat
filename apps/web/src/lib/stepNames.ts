/** Names of a folded step list: each kind once in first-done order, repeats counted, at most `limit` kinds then 「외 n개」. */
export function collapsedStepNames(
  labels: string[],
  t: (text: string) => string,
  limit: number,
): string {
  const counts = new Map<string, number>()
  for (const label of labels) {
    const name = t(label)
    counts.set(name, (counts.get(name) ?? 0) + 1)
  }
  const names = [...counts].map(([name, n]) =>
    n > 1 ? `${name} ${t('{n}회').replace('{n}', String(n))}` : name,
  )
  const shown = names.slice(0, limit).join(' · ')
  return names.length > limit
    ? `${shown} · ${t('외 {n}개').replace('{n}', String(names.length - limit))}`
    : shown
}
