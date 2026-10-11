import { useCallback, useEffect, useRef, useState } from 'react'

/** How long a load may take before the screen says it is slow. */
export const SLOW_MS = 10_000

/**
 * Something a screen fetches, in one of three states. A failed load is `error`, never an
 * endless 「불러오는 중…」; past `SLOW_MS` the loading state says so (`slow`).
 */
export interface Resource<T> {
  status: 'loading' | 'error' | 'ok'
  data: T | null
  error: unknown
  slow: boolean
  reload: () => void
}

/**
 * Runs `load` now and whenever `deps` change or `reload` is called; a late answer to a
 * superseded call is dropped. `load` may read the latest props: only `deps` start a new call.
 */
export function useResource<T>(load: () => Promise<T>, deps: readonly unknown[]): Resource<T> {
  const [state, setState] = useState<{ status: Resource<T>['status']; data: T | null; error: unknown }>({
    status: 'loading',
    data: null,
    error: null,
  })
  const [slow, setSlow] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const loader = useRef(load)
  loader.current = load

  useEffect(() => {
    let current = true
    setState((s) => ({ status: 'loading', data: s.data, error: null }))
    setSlow(false)
    const timer = window.setTimeout(() => current && setSlow(true), SLOW_MS)
    loader
      .current()
      .then((data) => current && setState({ status: 'ok', data, error: null }))
      .catch((error: unknown) => current && setState((s) => ({ status: 'error', data: s.data, error })))
      .finally(() => window.clearTimeout(timer))
    return () => {
      current = false
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt])

  const reload = useCallback(() => setAttempt((n) => n + 1), [])
  return { ...state, slow: slow && state.status === 'loading', reload }
}
