import { NotebookPen } from 'lucide-react'
import { useEffect, useState, type RefObject } from 'react'
import { errorMessage, shelfApi } from '@/lib/api'
import { withNames } from '@/lib/shelfNames'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** Longest selection kept as one note. */
const MAX_SELECTION = 20_000

/**
 * Text selected in a project conversation's transcript gets a 「메모로 저장」 button beside it;
 * the passage goes to the project's 메모함 as a note (origin `selection`).
 */
export function SelectionNote({
  projectId,
  sessionId,
  container,
}: {
  projectId: string
  sessionId: string
  container: RefObject<HTMLElement | null>
}) {
  const t = useT()
  const setToast = useStore((s) => s.setToast)
  const [picked, setPicked] = useState<{ text: string; top: number; left: number } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const read = () => {
      const selection = window.getSelection()
      const root = container.current
      if (!selection || selection.isCollapsed || !root || selection.rangeCount === 0) {
        setPicked(null)
        return
      }
      const range = selection.getRangeAt(0)
      if (!root.contains(range.commonAncestorContainer)) {
        setPicked(null)
        return
      }
      const text = selection.toString().trim()
      if (text.length < 2) {
        setPicked(null)
        return
      }
      const box = range.getBoundingClientRect()
      const frame = root.getBoundingClientRect()
      setPicked({
        text: text.slice(0, MAX_SELECTION),
        top: box.top - frame.top + root.scrollTop - 40,
        left: Math.min(Math.max(8, box.left - frame.left), frame.width - 150),
      })
    }
    const settle = () => window.setTimeout(read, 0)
    document.addEventListener('mouseup', settle)
    document.addEventListener('keyup', settle)
    document.addEventListener('touchend', settle)
    return () => {
      document.removeEventListener('mouseup', settle)
      document.removeEventListener('keyup', settle)
      document.removeEventListener('touchend', settle)
    }
  }, [container])

  if (!picked) return null
  const save = async () => {
    setBusy(true)
    try {
      await shelfApi.addNote(projectId, { body: picked.text, origin: 'selection', sessionId })
      setToast({ text: withNames(t('{notes}에 저장했습니다.'), t) })
      window.getSelection()?.removeAllRanges()
      setPicked(null)
    } catch (err) {
      setToast({ text: errorMessage(err, t('메모로 저장하지 못했습니다.')) })
    } finally {
      setBusy(false)
    }
  }
  return (
    <button
      type="button"
      data-testid="selection-note"
      disabled={busy}
      // Keeps the selection: a mousedown here would clear it before the click.
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => void save()}
      style={{ top: Math.max(4, picked.top), left: picked.left }}
      className="absolute z-20 inline-flex items-center gap-1.5 rounded-control border border-line bg-panel px-2.5 py-1.5 text-sm font-medium shadow-float hover:bg-elevated"
    >
      <NotebookPen size={13} className="text-accent" />
      {withNames(t('{notes}에 저장'), t)}
    </button>
  )
}
