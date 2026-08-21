import { useEffect, useRef, useState } from 'react'

/**
 * Whether this panel is too narrow to keep a rail beside the thing it is a
 * rail for — measured on the panel, never on the window.
 *
 * A `lg:` variant answers a question about the browser, and these panels are
 * not the browser. The same deck is 786px inside a gallery dialog on an 820px
 * tablet and 340px inside a column somebody dragged in on a 1440px desktop;
 * asking the window gets both backwards, hiding the rail where there is room
 * for it and keeping it where there is not. The floor is the width the panels
 * ask for themselves (`min-w-[460px]`), so the drawer appears exactly when the
 * panel is below the size it was drawn at.
 */
export function usePanelNarrow<T extends HTMLElement>(floor = 460) {
  const ref = useRef<T>(null)
  const [narrow, setNarrow] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width
      // A panel mid-unmount measures 0, and that is not "narrow" — reading it
      // would fold the rail away for the frame before it disappears.
      if (width > 0) setNarrow(width < floor)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [floor])
  return { ref, narrow }
}
