import { Expand, Maximize2, Minimize2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui'
import { useT } from '@/lib/useT'

/**
 * How much of the window the document is asking for.
 *
 * Three positions rather than two, because a document surface has three
 * honest answers. `narrow` is a document beside a conversation. `wide` — where
 * a document opens — is a reading width with the transcript still to hand.
 * `full` is the document alone: the conversation has said what it had to say
 * and the person is reading or writing now.
 */
export type PanelMode = 'narrow' | 'wide' | 'full'

/** The order the button walks, and back to the start. */
const NEXT: Record<PanelMode, PanelMode> = { narrow: 'wide', wide: 'full', full: 'narrow' }

export function nextMode(mode: PanelMode): PanelMode {
  return NEXT[mode]
}

/**
 * The two things every artifact panel owes the reader: room, and a way out.
 *
 * Shared rather than per panel, so which of them a reader gets does not depend
 * on what the model happened to produce.
 */
export function PanelControls({
  mode,
  onCycle,
  onClose,
}: {
  mode: PanelMode
  /** Omitted where the host cannot grow — inside a fixed-width preview. */
  onCycle?: () => void
  onClose?: () => void
}) {
  const t = useT()
  // Named for what pressing it does, not for where it is. A control whose name
  // is its current state leaves the reader to work out the rest.
  const label =
    mode === 'narrow' ? t('넓게 보기') : mode === 'wide' ? t('문서만 보기') : t('패널 좁히기')
  const hint =
    mode === 'narrow'
      ? t('패널을 넓혀 크게 봅니다')
      : mode === 'wide'
        ? t('대화를 접고 문서만 봅니다')
        : t('원래 너비로 되돌립니다')
  return (
    <>
      {onCycle && (
        <Button variant="ghost" size="icon" aria-label={label} title={hint} onClick={onCycle}>
          {mode === 'narrow' ? (
            <Maximize2 size={15} />
          ) : mode === 'wide' ? (
            <Expand size={15} />
          ) : (
            <Minimize2 size={15} />
          )}
        </Button>
      )}
      {onClose && (
        <Button
          variant="ghost"
          size="icon"
          aria-label={t('닫기')}
          title={t('패널을 닫습니다')}
          onClick={onClose}
        >
          <X size={15} />
        </Button>
      )}
    </>
  )
}

/**
 * Panel-local width state, reported up so the host can actually grow.
 *
 * The panel owns whether it *wants* room; only the host knows whether there is
 * any — the same deck sits in a resizable side panel on one screen and in a
 * fixed-width preview dialog on another.
 */
export function usePanelWidth(onModeChange?: (mode: PanelMode) => void) {
  // Wide to begin with, for the reason `ReportPanel` states at length: a
  // document column beside a transcript is not a reading width, and a deck's
  // stage is not a viewing one. The parent holds the split and starts it
  // narrow, so the opening position is announced once rather than assumed.
  const [mode, setMode] = useState<PanelMode>('wide')
  useEffect(() => {
    onModeChange?.('wide')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return {
    mode,
    cycle: onModeChange
      ? () => {
          const next = nextMode(mode)
          setMode(next)
          onModeChange(next)
        }
      : undefined,
  }
}
