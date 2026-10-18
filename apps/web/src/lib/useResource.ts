import { useCallback, useEffect, useRef, useState } from 'react'

/** How long a load may take before the screen says it is slow. */
const SLOW_MS = 10_000

/** Fetched resource in three states; failures are `error`, slow loads flagged past `SLOW_MS`. */
export interface Resource<T> {
  status: 'loading' | 'error' | 'ok'
  data: T | null
  error: unknown
  slow: boolean
  reload: () => void
}

/** Runs `load` on `deps` change or `reload`; late answers dropped. Only `deps` restart. */
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
