import { Expand, Maximize2, Minimize2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui'
import { useT } from '@/lib/useT'

/** Panel width: `narrow` beside the chat, `wide` reading width, `full` document only. */
export type PanelMode = 'narrow' | 'wide' | 'full'

const NEXT: Record<PanelMode, PanelMode> = { narrow: 'wide', wide: 'full', full: 'narrow' }

export function nextMode(mode: PanelMode): PanelMode {
  return NEXT[mode]
}

/** Width-cycle and close buttons shared by every artifact panel. */
export function PanelControls({
  mode,
  onCycle,
  onClose,
}: {
  mode: PanelMode
  /** Omitted where the host cannot grow (fixed-width preview). */
  onCycle?: () => void
  onClose?: () => void
}) {
  const t = useT()
  // Labelled by what pressing it does, not by the current state.
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

/** Panel-local width mode, reported up so the host can resize. */
export function usePanelWidth(onModeChange?: (mode: PanelMode) => void) {
  // Opens wide; the parent starts narrow, so the initial mode is announced once.
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
