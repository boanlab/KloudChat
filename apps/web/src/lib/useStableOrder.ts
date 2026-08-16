import { useRef } from 'react'

/**
 * Holds a list in the order it was first shown in.
 *
 * These lists sort newest-first so that something just created is on the first
 * page rather than at position sixty, where it is indistinguishable from a save
 * that failed. But `updatedAt` moves on *every* write, and flipping a switch is
 * a write — so turning a skill off sent its card to the top of the screen,
 * under the cursor that had just left it. Toggling the row back then moved it
 * again. Nothing about "is this on" is a reason to re-rank anything.
 *
 * So the ranking is decided once per row: a row already on screen keeps its
 * place for as long as the screen is open, and only rows that were not there
 * before are ranked — newest first, at the top. Editing is stable; creating
 * still lands where it can be seen.
 *
 * Pass the *unfiltered* list and filter the result. Filtering first would drop
 * the hidden rows out of the remembered order, and switching a tab back would
 * re-rank them as if they were new.
 */
export function useStableOrder<T extends { id: string; updatedAt: string }>(items: T[]): T[] {
  const order = useRef<string[]>([])
  const byId = new Map(items.map((i) => [i.id, i]))

  // Deleted rows fall out; the rest hold the positions they already had.
  const held = order.current.filter((id) => byId.has(id))
  const seen = new Set(held)
  const fresh = items
    .filter((i) => !seen.has(i.id))
    .sort((a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt))
    .map((i) => i.id)

  // Written during render, which is safe here only because it is idempotent:
  // running it twice on the same input leaves `fresh` empty and `held` as it
  // was, so StrictMode's double render produces the same list.
  const next = [...fresh, ...held]
  order.current = next
  return next.map((id) => byId.get(id)!)
}
