/** Unsent composer text mirrored to this browser's storage, keyed by account and conversation.
 *
 * Storage may be unavailable (private window, blocked site data) or full; every call
 * swallows that, and the composer's in-memory copy carries on alone.
 */

const PREFIX = 'kc.draft.'

export function draftStorageKey(userId: string | undefined, key: string): string {
  return `${PREFIX}${userId ?? 'anon'}.${key}`
}

export function readStoredDraft(userId: string | undefined, key: string): string | null {
  try {
    return localStorage.getItem(draftStorageKey(userId, key))
  } catch {
    return null
  }
}

export function writeStoredDraft(userId: string | undefined, key: string, value: string): void {
  try {
    if (value.trim()) localStorage.setItem(draftStorageKey(userId, key), value)
    else localStorage.removeItem(draftStorageKey(userId, key))
  } catch {
    // Storage unavailable or full: the draft still lives in memory.
  }
}

/** Drops one conversation's draft, e.g. when the conversation is deleted. */
export function forgetStoredDraft(userId: string | undefined, key: string): void {
  try {
    localStorage.removeItem(draftStorageKey(userId, key))
  } catch {
    // Nothing to forget where nothing could be stored.
  }
}

/** Drops every draft of one account: a sign-out must not leave half-typed text behind. */
export function purgeStoredDrafts(userId: string | undefined): void {
  try {
    const own = `${PREFIX}${userId ?? 'anon'}.`
    const doomed: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const name = localStorage.key(i)
      if (name?.startsWith(own)) doomed.push(name)
    }
    doomed.forEach((name) => localStorage.removeItem(name))
  } catch {
    // Storage unavailable: there is nothing stored to purge.
  }
}
