import { useCallback, useContext, useEffect, useRef, useState } from 'react'
import { UNSAFE_NavigationContext } from 'react-router-dom'

type Go = (...args: unknown[]) => void

/** Routes the navigator's push/replace through `hold` until the returned function puts them back. */
function intercept(navigator: unknown, hold: (go: () => void) => void) {
  const nav = navigator as { push: Go; replace: Go }
  const push = nav.push
  const replace = nav.replace
  nav.push = (...args) => hold(() => push(...args))
  nav.replace = (...args) => hold(() => replace(...args))
  return () => {
    nav.push = push
    nav.replace = replace
  }
}

/**
 * Asks before leaving while `active`: in-app navigation waits for `leave()`, and closing or
 * reloading the tab gets the browser's own prompt. `BrowserRouter` has no `useBlocker`, so the
 * router's navigator is held for as long as the guard is on.
 */
export function useLeaveGuard(active: boolean) {
  const { navigator } = useContext(UNSAFE_NavigationContext)
  const [held, setHeld] = useState<(() => void) | null>(null)
  const restore = useRef<(() => void) | null>(null)

  useEffect(() => {
    if (!active) return
    const unhold = intercept(navigator, (go) => setHeld(() => go))
    const protect = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', protect)
    const off = () => {
      unhold()
      window.removeEventListener('beforeunload', protect)
    }
    restore.current = off
    return () => {
      off()
      restore.current = null
    }
  }, [active, navigator])

  /** Lets the next navigation through, for a leave the page itself decided on. */
  const release = useCallback(() => restore.current?.(), [])
  const stay = useCallback(() => setHeld(null), [])
  const leave = useCallback(() => {
    const go = held
    setHeld(null)
    restore.current?.()
    go?.()
  }, [held])
  return { pending: held !== null, stay, leave, release }
}
