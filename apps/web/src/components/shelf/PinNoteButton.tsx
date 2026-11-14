import { NotebookPen } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui'
import { errorMessage, shelfApi } from '@/lib/api'
import { withNames } from '@/lib/shelfNames'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** An answer of a project conversation pinned to the project's 메모함; nothing elsewhere. */
export function PinNoteButton({ sessionId, messageId, content }: { sessionId: string; messageId: string; content: string }) {
  const t = useT()
  const projectId = useStore((s) => s.sessions.find((c) => c.id === sessionId)?.projectId ?? null)
  const setToast = useStore((s) => s.setToast)
  const [done, setDone] = useState(false)
  if (!projectId) return null
  const pin = async () => {
    try {
      await shelfApi.addNote(projectId, { body: content, origin: 'answer', sessionId, messageId })
      setDone(true)
      setToast({ text: withNames(t('{notes}에 저장했습니다.'), t) })
    } catch (err) {
      setToast({ text: errorMessage(err, t('메모로 저장하지 못했습니다.')) })
    }
  }
  const label = withNames(t('{notes}에 저장'), t)
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={label}
      title={label}
      aria-pressed={done}
      className={done ? 'text-accent' : undefined}
      onClick={() => void pin()}
    >
      <NotebookPen size={14} />
    </Button>
  )
}
