import type { ModelInfo, SessionKind } from '@/types'

/** Overlay the account's saved surface picks on the browser's settled models (`reconcileDefaults` runs only on catalogue load). */
export function withAccountPicks(
  current: Record<SessionKind, string>,
  available: Pick<ModelInfo, 'id' | 'kinds'>[],
  account: Partial<Record<SessionKind, string>> | undefined,
): Record<SessionKind, string> {
  let next = current
  for (const [kind, id] of Object.entries(account ?? {}) as [SessionKind, string][]) {
    if (!id || current[kind] === id || !(kind in current)) continue
    if (!available.some((m) => m.id === id && m.kinds.includes(kind))) continue
    next = { ...next, [kind]: id }
  }
  return next
}
