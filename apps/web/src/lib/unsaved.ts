import { useEffect, useRef, useState } from 'react'
import { create } from 'zustand'

/** Unsaved forms by key: in-app links, tabbed screens and tab close ask first. */
export const useUnsaved = create<{ dirty: Record<string, boolean>; mark: (key: string, on: boolean) => void; clear: () => void }>(
  (set) => ({
    dirty: {},
    mark: (key, on) => set((s) => (Boolean(s.dirty[key]) === on ? s : { dirty: { ...s.dirty, [key]: on } })),
    clear: () => set({ dirty: {} }),
  }),
)

/** Office documents mark themselves `office:<id>`; their own guard asks about them. */
const isOffice = (key: string) => key.startsWith('office:')


/** Unsaved forms other than office documents, right now. */
export const formsUnsaved = () =>
  Object.entries(useUnsaved.getState().dirty).some(([key, on]) => on && !isOffice(key))

/** Drops the unsaved marks of forms other than office documents (the person chose to leave). */
export function dropFormMarks() {
  const { dirty, mark } = useUnsaved.getState()
  for (const key of Object.keys(dirty)) if (!isOffice(key)) mark(key, false)
}

/**
 * Marks `key` as holding unsaved changes while `dirty` holds; unmarked when it unmounts. A tab
 * close or reload asks first, unless `still` (read as the page goes) says the changes were
 * saved or dropped meanwhile: a state update lands after the reload that follows the answer.
 */
export function useMarkUnsaved(key: string, dirty: boolean, still?: () => boolean) {
  const mark = useUnsaved((s) => s.mark)
  const latest = useRef(still)
  useEffect(() => {
    latest.current = still
  })
  useEffect(() => {
    mark(key, dirty)
    if (!dirty) return
    const protect = (event: BeforeUnloadEvent) => {
      if (latest.current && !latest.current()) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', protect)
    return () => {
      window.removeEventListener('beforeunload', protect)
      mark(key, false)
    }
  }, [key, dirty, mark])
}

/** Setter for `key`'s unsaved mark, for a form that keeps its own `dirty` ref for late fetches. */
export function useUnsavedMark(key: string): (on: boolean) => void {
  const [dirty, setDirty] = useState(false)
  useMarkUnsaved(key, dirty)
  return setDirty
}
