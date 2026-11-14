import type { ModelInfo, SessionKind } from '@/types'

/**
 * The account's surface picks over the models this browser settled on: a pick saved on another
 * device, or one that arrived after the catalogue, still shows in 설정 › 환경설정 and starts new
 * conversations (`reconcileDefaults` runs only when the catalogue loads).
 */
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
