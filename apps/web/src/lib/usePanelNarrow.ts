import { useEffect, useRef, useState } from 'react'

/**
 * Whether this panel is narrower than `floor`, measured on the panel itself
 * rather than the window. The default floor is the panels' own `min-w-[460px]`.
 */
export function usePanelNarrow<T extends HTMLElement>(floor = 460) {
  const ref = useRef<T>(null)
  const [narrow, setNarrow] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width
      // A panel mid-unmount measures 0; that is not "narrow".
      if (width > 0) setNarrow(width < floor)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [floor])
  return { ref, narrow }
}
