import { ChevronDown, RotateCcw } from 'lucide-react'
import { Button, Dropdown, MenuItem, MenuLabel } from '@/components/ui'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'

/** Retry a failed turn on the same model, or on another one for this turn only. */
export function RetryActions({
  sessionId,
  messageId,
  prompt,
  kind,
}: {
  sessionId: string
  /** The failed question's row; the retry replaces it in place. */
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
