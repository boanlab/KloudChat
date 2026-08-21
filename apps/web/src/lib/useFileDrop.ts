import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Files dragged onto a region, and whether one is hovering it right now.
 *
 * Every upload in this product went through a hidden `<input type=file>` behind
 * a paperclip. Dragging a file onto the window did what a browser does with a
 * file nobody claimed: it navigated away from the app and opened the file. So
 * the interaction that every comparable product treats as the default was not
 * merely missing — it actively destroyed what was on screen.
 *
 * Two things are needed and only one is obvious. The obvious one is a drop
 * handler. The other is that `dragover` must be cancelled on the *window*, not
 * only on the target: the browser's default is what navigates, and a region
 * that only cancels over itself still loses the app when somebody misses it by
 * ten pixels.
 *
 * `over` is tracked with a counter rather than a boolean. `dragenter` and
 * `dragleave` both fire when the pointer crosses into a child element, so a
 * boolean flickers off every time the cursor passes over the text inside the
 * drop zone it is hovering.
 */
export function useFileDrop(onFiles: (files: File[]) => void, enabled = true) {
  const [over, setOver] = useState(false)
  const depth = useRef(0)
  const latest = useRef(onFiles)
  latest.current = onFiles

  // Files only. A dragged selection of text or a link is not an upload, and
  // lighting the drop zone for one promises something that will not happen.
  const hasFiles = (e: DragEvent | React.DragEvent) =>
    Array.from(e.dataTransfer?.types ?? []).includes('Files')

  useEffect(() => {
    if (!enabled) return
    // Cancelling here is what stops the browser from navigating to the file.
    // Without it a near miss replaces the app with the dropped document.
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

/**
 * Files out of a paste.
 *
 * The other half of the same gap: a screenshot on the clipboard had nowhere to
 * go. Returns a handler for the element that has focus while somebody presses
 * ⌘V — the composer's textarea — and does nothing when the clipboard holds only
 * text, so ordinary pasting is untouched.
 */
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
