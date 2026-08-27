import { ChevronDown, RotateCcw } from 'lucide-react'
import { Button, Dropdown, MenuItem, MenuLabel } from '@/components/ui'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'

/**
 * What to do about a turn that failed.
 *
 * Two offers, because a failure has two shapes. Sometimes the model was fine
 * and something transient was not, and asking again is the whole fix. Sometimes
 * the model is the problem — it does not serve this surface, it is out of
 * capacity, it accepted the request and never answered — and asking the same
 * model again is guaranteed to fail the same way.
 *
 * The second case had no path at all. Somebody had to open the picker, change
 * the conversation's model, retype or re-find the question, ask it, and then
 * remember to change the model back. Choosing one here runs this turn on that
 * model and leaves the rest of the conversation alone.
 */
export function RetryActions({
  sessionId,
  messageId,
  prompt,
  kind,
}: {
  sessionId: string
  /** The question's row, so the retry runs it again in place rather than asking twice. */
  messageId: string
  prompt: string
  kind: SessionKind
}) {
  const t = useT()
  const send = useStore((s) => s.send)
  const models = useStore((s) => s.models)
  const streaming = useStore((s) => !!s.running[sessionId])
  const sessions = useStore((s) => s.sessions)
  const currentId = sessions.find((s) => s.id === sessionId)?.model

  // The one that just failed is left in the list rather than hidden: 다시 시도
  // beside it already means "this model again", and a name disappearing from a
  // menu is how a list stops being trustworthy.
  const usable = models.filter((m) => m.kinds.includes(kind))

  return (
    <span className="flex shrink-0 items-center gap-1.5">
      <Button
        size="sm"
        disabled={streaming}
        onClick={() => void send(sessionId, kind, prompt, { retryOf: messageId })}
      >
        <RotateCcw size={13} />
        {t('다시 시도')}
      </Button>
      {usable.length > 1 && (
        <Dropdown
          align="right"
          className="max-h-80 min-w-64"
          trigger={() => (
            <Button size="sm" variant="ghost" disabled={streaming}>
              {t('다른 모델로')}
              <ChevronDown size={13} />
            </Button>
          )}
        >
          <MenuLabel>{t('이 요청만 다른 모델로')}</MenuLabel>
          {usable.map((m) => (
            <MenuItem
              key={m.id}
              onClick={() => void send(sessionId, kind, prompt, { model: m.id, retryOf: messageId })}
              hint={m.id === currentId ? t('현재 모델') : undefined}
            >
              {m.label}
            </MenuItem>
          ))}
        </Dropdown>
      )}
    </span>
  )
}
