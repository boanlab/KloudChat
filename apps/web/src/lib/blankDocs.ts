/** 「빈 문서로 시작」 and then leaving without doing anything drops the document and its empty conversation.
 *  The server decides what counts as untouched (`discard-if-blank`); this only asks when the screen is left. */
import { artifactsApi } from '@/lib/api'
import { trackBlank } from '@/lib/blankPending'
import { officeDirty } from '@/lib/officeGuard'
import { useStore } from '@/store/useStore'

/** Blank documents made in this tab and not yet asked about: conversation id → document id. */
const blanks = new Map<string, string>()

/** A blank document was just made in `sessionId`. */
export function rememberBlank(sessionId: string, artifactId: string) {
  blanks.set(sessionId, artifactId)
}

/** The conversation id of a `/s/:id` address. */
export function sessionOfPath(path: string): string | null {
  return /^\/s\/([^/?#]+)/.exec(path)?.[1] ?? null
}

/** Takes the conversation and its document off every list now; the returned call puts them back. */
function hide(sessionId: string): () => void {
  const before = useStore.getState()
  const row = before.sessions.find((c) => c.id === sessionId)
  const made = before.artifacts.filter((a) => a.sessionId === sessionId)
  useStore.setState((s) => ({
    sessions: s.sessions.filter((c) => c.id !== sessionId),
    artifacts: s.artifacts.filter((a) => a.sessionId !== sessionId),
    goneSessions: { ...s.goneSessions, [sessionId]: 'deleted' },
    activeSessionId: s.activeSessionId === sessionId ? null : s.activeSessionId,
    projects: s.projects.map((p) => ({ ...p, sessionIds: p.sessionIds.filter((x) => x !== sessionId) })),
  }))
  return () =>
    useStore.setState((s) => {
      const { [sessionId]: _gone, ...rest } = s.goneSessions
      return {
        goneSessions: rest,
        sessions: row && !s.sessions.some((c) => c.id === sessionId) ? [row, ...s.sessions] : s.sessions,
        artifacts: [...made.filter((a) => !s.artifacts.some((x) => x.id === a.id)), ...s.artifacts],
      }
    })
}

/** The screen of `sessionId` was left: its blank document, if untouched, goes. */
export function leaveBlank(sessionId: string, opts: { quiet?: boolean } = {}) {
  const artifactId = blanks.get(sessionId)
  if (!artifactId) return
  blanks.delete(sessionId)
  // hand edits still open are the person's: nothing to drop
  if (officeDirty()) return
  const restore = opts.quiet ? () => undefined : hide(sessionId)
  void trackBlank(
    artifactsApi
      .discardIfBlank(artifactId)
      .then((answer) => {
        if (!answer.discarded) restore()
      })
      .catch(restore),
  )
}

/** The page is going away (tab closed, phone backgrounded): the same, with nothing left to redraw. */
export function leaveAllBlanks() {
  for (const sessionId of [...blanks.keys()]) leaveBlank(sessionId, { quiet: true })
}
