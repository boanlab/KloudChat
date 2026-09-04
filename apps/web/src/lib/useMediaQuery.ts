import { useEffect, useState } from 'react'

/** Subscribes to a media query. Used to switch layout modes, not to hide content. */
export function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)

  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches)
    setMatches(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])

  return matches
}

/** Below 1024px the sidebar and the artifact panel become overlays. */
export const useNarrowLayout = () => useMediaQuery('(max-width: 1023px)')
