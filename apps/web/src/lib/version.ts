/** This bundle's name, and whether the server now serves another one. */

export const BUILD_VERSION: string = import.meta.env.VITE_KCHAT_VERSION ?? 'dev'

/** Checked on return to the tab and on this period. */
const EVERY_MS = 10 * 60_000

/** The served build's name; null when the server did not say (dev server, offline). */
async function servedVersion(): Promise<string | null> {
  try {
    const res = await fetch('/version.json', { cache: 'no-store' })
    if (!res.ok) return null
    const body = (await res.json()) as { version?: unknown }
    return typeof body.version === 'string' ? body.version : null
  } catch {
    return null
  }
}

/** Calls `onNewer` once the server serves a build other than this one; returns the unsubscribe. */
export function watchForNewVersion(onNewer: () => void): () => void {
  let found = false
  let checking = false
  const check = async () => {
    if (found || checking || document.visibilityState === 'hidden') return
    checking = true
    const served = await servedVersion()
    checking = false
    if (served && served !== BUILD_VERSION && !found) {
      found = true
      onNewer()
    }
  }
  const onVisible = () => void check()
  window.addEventListener('focus', onVisible)
  document.addEventListener('visibilitychange', onVisible)
  const timer = window.setInterval(onVisible, EVERY_MS)
  return () => {
    window.removeEventListener('focus', onVisible)
    document.removeEventListener('visibilitychange', onVisible)
    window.clearInterval(timer)
  }
}
