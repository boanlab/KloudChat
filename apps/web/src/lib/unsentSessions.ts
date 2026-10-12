/**
 * Conversations opened but not yet sent: they live in the browser until the first send (or the
 * first attached file) creates them on the server under the same id. Kept per tab so a reload
 * finds the agent and project it was opened with.
 */
import type { Session } from '@/types'

const PREFIX = 'kchat.unsent.'

/** 32 hex digits, the server's own id shape: the row keeps this id once created. */
export function unsentId(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

function key(userId: string | undefined): string {
  return `${PREFIX}${userId ?? 'anon'}`
}

function read(userId: string | undefined): Record<string, Session> {
  try {
    const raw = sessionStorage.getItem(key(userId))
    const parsed: unknown = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, Session>) : {}
  } catch {
    return {}
  }
}

function write(userId: string | undefined, rows: Record<string, Session>): void {
  try {
    if (Object.keys(rows).length) sessionStorage.setItem(key(userId), JSON.stringify(rows))
    else sessionStorage.removeItem(key(userId))
  } catch {
    // Storage off or full: the conversation still opens; a reload loses it.
  }
}

export function keepUnsent(userId: string | undefined, row: Session): void {
  write(userId, { ...read(userId), [row.id]: { ...row, messages: [] } })
}

export function findUnsent(userId: string | undefined, id: string): Session | null {
  return read(userId)[id] ?? null
}

export function forgetUnsent(userId: string | undefined, id: string): void {
  const rows = read(userId)
  if (!(id in rows)) return
  delete rows[id]
  write(userId, rows)
}

export function purgeUnsent(userId: string | undefined): void {
  write(userId, {})
}
