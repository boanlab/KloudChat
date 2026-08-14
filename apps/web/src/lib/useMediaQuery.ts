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

/**
 * Below this width the sidebar and the artifact panel cannot both sit beside the
 * conversation — three columns in 820px leaves the middle one unusable. They
 * become overlays instead.
 */
export const useNarrowLayout = () => useMediaQuery('(max-width: 1023px)')
