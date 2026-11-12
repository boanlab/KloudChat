/**
 * Hand edits in an open office editor (HWP, Word) that are not saved yet. The agent edits the
 * saved file, so before a turn is sent the person saves or drops them (the panel asks).
 */
interface Guard {
  dirty: () => boolean
  /** Saves or discards as the person chooses; false when they cancel the send. */
  ask: () => Promise<boolean>
}

const guards = new Map<string, Guard>()

/** Registers the editor of `sessionId`'s document; returns the unregister. */
export function guardEdits(sessionId: string, guard: Guard): () => void {
  guards.set(sessionId, guard)
  return () => {
    if (guards.get(sessionId) === guard) guards.delete(sessionId)
  }
}

export function hasUnsavedEdits(sessionId: string | null | undefined): boolean {
  return !!sessionId && !!guards.get(sessionId)?.dirty()
}

/** True to go on with the turn. */
export function askAboutEdits(sessionId: string): Promise<boolean> {
  const guard = guards.get(sessionId)
  return guard && guard.dirty() ? guard.ask() : Promise.resolve(true)
}
