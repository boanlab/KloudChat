/** Uncaught browser errors, reported to the server log: message, cut stack, page path, version. */

import { ApiError, clientErrorsApi } from './api'
import { BUILD_VERSION } from './version'

type Kind = 'error' | 'unhandledrejection' | 'boundary'

/** At most this many reports per minute from one page, and each distinct error once. */
const PER_MINUTE = 5
const sent = new Set<string>()
let recent: number[] = []

/** Noise every browser raises that says nothing about the app. */
const IGNORED = /ResizeObserver loop|^Script error\.?$/

/** A token pasted into an error text (a JWT, a bearer header) never leaves the page. */
const scrub = (text: string) =>
  text.replace(/eyJ[\w-]{4,}\.[\w-]{4,}\.[\w-]+/g, '[token]').replace(/Bearer \S+/gi, 'Bearer [token]')

function describe(reason: unknown): { message: string; stack: string; requestId: string } {
  if (reason instanceof Error) {
    return {
      message: reason.message,
      stack: reason.stack ?? '',
      requestId: reason instanceof ApiError ? reason.requestId ?? '' : '',
    }
  }
  return { message: typeof reason === 'string' ? reason : String(reason), stack: '', requestId: '' }
}

export function reportClientError(kind: Kind, reason: unknown) {
  const { message, stack, requestId } = describe(reason)
  if (!message || IGNORED.test(message)) return
  // An API refusal already sits in the server's own log under its request id.
  if (reason instanceof ApiError && reason.status < 500) return
  const key = `${kind}|${message}|${stack.split('\n', 2)[1] ?? ''}`
  const now = Date.now()
  recent = recent.filter((at) => now - at < 60_000)
  if (sent.has(key) || recent.length >= PER_MINUTE) return
  sent.add(key)
  recent.push(now)
  void clientErrorsApi
    .report({
      kind,
      message: scrub(message).slice(0, 500),
      stack: scrub(stack).slice(0, 3000),
      // The path only: a query can carry a share token.
      route: window.location.pathname.slice(0, 300),
      version: BUILD_VERSION.slice(0, 64),
      requestId: requestId.slice(0, 64),
    })
    .catch(() => undefined)
}

/** Window-level catches; the error boundary reports its own. */
export function reportUncaughtErrors() {
  window.addEventListener('error', (event) => reportClientError('error', event.error ?? event.message))
  window.addEventListener('unhandledrejection', (event) => reportClientError('unhandledrejection', event.reason))
}
