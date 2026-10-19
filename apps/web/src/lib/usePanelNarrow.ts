import { useLayoutEffect, useRef, useState } from 'react'

/** Panel narrower than `floor`, measured on the panel; default matches `min-w-[460px]`. */
export function usePanelNarrow<T extends HTMLElement>(floor = 460) {
  const ref = useRef<T>(null)
  const [narrow, setNarrow] = useState(false)
  // Measured before paint too: a first frame at full width would overflow a phone.
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const first = el.getBoundingClientRect().width
    if (first > 0) setNarrow(first < floor)
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
