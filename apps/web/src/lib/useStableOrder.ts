import { useRef } from 'react'

/**
 * Holds a list in the order it was first shown: rows already on screen keep
 * their place, new rows go on top newest-first. `updatedAt` moves on every
 * write, so sorting live would reorder rows on a toggle.
 * Pass the unfiltered list and filter the result, or hidden rows re-rank as new.
 */
export function useStableOrder<T extends { id: string; updatedAt: string }>(items: T[]): T[] {
  const order = useRef<string[]>([])
  const byId = new Map(items.map((i) => [i.id, i]))

  // Deleted rows fall out; the rest keep their positions.
  const held = order.current.filter((id) => byId.has(id))
  const seen = new Set(held)
  const fresh = items
    .filter((i) => !seen.has(i.id))
    .sort((a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt))
    .map((i) => i.id)

  // Written during render; safe because idempotent (StrictMode's double render yields the same list).
  const next = [...fresh, ...held]
  order.current = next
  return next.map((id) => byId.get(id)!)
}
