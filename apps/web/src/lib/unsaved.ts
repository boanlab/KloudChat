import { useEffect, useRef } from 'react'
import { create } from 'zustand'

/** Unsaved settings sections by key: tabbed screens and tab close ask first. */
export const useUnsaved = create<{ dirty: Record<string, boolean>; mark: (key: string, on: boolean) => void; clear: () => void }>(
  (set) => ({
    dirty: {},
    mark: (key, on) => set((s) => (Boolean(s.dirty[key]) === on ? s : { dirty: { ...s.dirty, [key]: on } })),
    clear: () => set({ dirty: {} }),
  }),
)

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
