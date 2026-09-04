import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Files dragged onto a region, and whether one is hovering it. `dragover` and
 * `drop` are cancelled on the window, not only the target: the browser default
 * navigates to the dropped file. `over` is a depth counter because
 * `dragenter`/`dragleave` fire on every child crossing.
 */
export function useFileDrop(onFiles: (files: File[]) => void, enabled = true) {
  const [over, setOver] = useState(false)
  const depth = useRef(0)
  const latest = useRef(onFiles)
  latest.current = onFiles

  // Files only: dragged text or links are not uploads.
  const hasFiles = (e: DragEvent | React.DragEvent) =>
    Array.from(e.dataTransfer?.types ?? []).includes('Files')

  useEffect(() => {
    if (!enabled) return
    // Stops the browser navigating to a file dropped outside the target.
    const swallow = (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault()
    }
    window.addEventListener('dragover', swallow)
    window.addEventListener('drop', swallow)
    return () => {
      window.removeEventListener('dragover', swallow)
      window.removeEventListener('drop', swallow)
    }
  }, [enabled])

  const reset = useCallback(() => {
    depth.current = 0
    setOver(false)
  }, [])

  const handlers = enabled
    ? {
        onDragEnter: (e: React.DragEvent) => {
          if (!hasFiles(e)) return
          e.preventDefault()
          depth.current += 1
          setOver(true)
        },
        onDragOver: (e: React.DragEvent) => {
          if (!hasFiles(e)) return
          e.preventDefault()
          // Tells the cursor this is a copy, not a move or a refusal.
          e.dataTransfer.dropEffect = 'copy'
        },
        onDragLeave: (e: React.DragEvent) => {
          if (!hasFiles(e)) return
          depth.current = Math.max(0, depth.current - 1)
          if (depth.current === 0) setOver(false)
        },
        onDrop: (e: React.DragEvent) => {
          if (!hasFiles(e)) return
          e.preventDefault()
          reset()
          const files = Array.from(e.dataTransfer.files)
          if (files.length) latest.current(files)
        },
      }
    : {}

  return { over, handlers }
}

/** Paste handler that hands over pasted files; does nothing when the clipboard holds only text. */
export function usePasteFiles(onFiles: (files: File[]) => void) {
  const latest = useRef(onFiles)
  latest.current = onFiles
  return useCallback((e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData?.files ?? [])
    if (!files.length) return
    e.preventDefault()
    latest.current(files)
  }, [])
}
