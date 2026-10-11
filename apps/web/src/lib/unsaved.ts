import { useEffect } from 'react'
import { create } from 'zustand'

/**
 * Settings sections with changes not saved yet, by key: a screen with tabs asks before it
 * leaves them (관리 › 시스템), and closing the tab asks the browser's own question.
 */
export const useUnsaved = create<{ dirty: Record<string, boolean>; mark: (key: string, on: boolean) => void; clear: () => void }>(
  (set) => ({
    dirty: {},
    mark: (key, on) => set((s) => (Boolean(s.dirty[key]) === on ? s : { dirty: { ...s.dirty, [key]: on } })),
    clear: () => set({ dirty: {} }),
  }),
)

/** Marks `key` as holding unsaved changes while `dirty` holds; unmarked when it unmounts. */
export function useMarkUnsaved(key: string, dirty: boolean) {
  const mark = useUnsaved((s) => s.mark)
  useEffect(() => {
    mark(key, dirty)
    if (!dirty) return
    const protect = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', protect)
    return () => {
      window.removeEventListener('beforeunload', protect)
      mark(key, false)
    }
  }, [key, dirty, mark])
}
