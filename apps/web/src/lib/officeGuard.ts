/**
 * Hand edits in an open office editor (HWP, Word, PowerPoint) that are not saved yet. The
 * agent edits the saved file, so before a turn is sent the person saves or drops them (the
 * panel asks); leaving the document (closing the panel, opening another one, another page,
 * a reload, a sign-out for idleness) asks the same, or saves first where nobody is there to ask.
 */

/** Why the panel asks: before an agent turn, or before the document goes away. */
export type AskReason = 'send' | 'leave' | 'reload'

interface Guard {
  dirty: () => boolean
  /** Asks the editor itself for its unsaved state (one that is only polled lags behind). */
  refresh?: () => Promise<void>
  /** Saves or discards as the person chooses; false when they cancel (or the save failed). */
  ask: (reason: AskReason) => Promise<boolean>
  /** Saves without asking; false when it could not. */
  save: () => Promise<boolean>
}

const guards = new Map<string, Guard>()
/** Open documents by artifact id: what leaving would drop. */
const open = new Map<string, Guard>()

/** Registers the editor of `sessionId`'s document (`artifactId`); returns the unregister. */
export function guardEdits(sessionId: string | null | undefined, artifactId: string, guard: Guard): () => void {
  if (sessionId) guards.set(sessionId, guard)
  open.set(artifactId, guard)
  return () => {
    if (sessionId && guards.get(sessionId) === guard) guards.delete(sessionId)
    if (open.get(artifactId) === guard) open.delete(artifactId)
  }
}

/** An office editor is open beside `sessionId`: a send checks it first (`askAboutEdits`). */
export function hasOfficeEditor(sessionId: string | null | undefined): boolean {
  return !!sessionId && guards.has(sessionId)
}

export function hasUnsavedEdits(sessionId: string | null | undefined): boolean {
  return !!sessionId && !!guards.get(sessionId)?.dirty()
}

/** True to go on with the turn. */
export async function askAboutEdits(sessionId: string): Promise<boolean> {
  const guard = guards.get(sessionId)
  if (!guard) return true
  await guard.refresh?.().catch(() => undefined)
  return guard.dirty() ? guard.ask('send') : true
}

/** Some open office document holds unsaved edits (as last known; no editor is asked). */
export function officeDirty(): boolean {
  for (const guard of open.values()) if (guard.dirty()) return true
  return false
}

/**
 * Before the open documents go away: each with unsaved edits asks to save or drop them.
 * True to go on; false when the person stays (cancelled, or the save failed).
 */
export async function leaveOffice(reason: Exclude<AskReason, 'send'> = 'leave'): Promise<boolean> {
  for (const guard of [...open.values()]) {
    await guard.refresh?.().catch(() => undefined)
    if (guard.dirty() && !(await guard.ask(reason))) return false
  }
  return true
}

/** Saves every open document's unsaved edits without asking (nobody is there: idle sign-out). */
export async function saveOpenOffice(): Promise<void> {
  for (const guard of [...open.values()]) {
    await guard.refresh?.().catch(() => undefined)
    if (guard.dirty()) await guard.save().catch(() => false)
  }
}

/** Thrown by a step the person called off at the unsaved-edits question; nothing to report. */
export class LeaveCancelled extends Error {
  constructor() {
    super('left_unsaved_edits_open')
    this.name = 'LeaveCancelled'
  }
}
