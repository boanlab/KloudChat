import { Maximize2, Minimize2, X } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui'
import { useT } from '@/lib/useT'

/**
 * The two things every artifact panel owes the reader: room, and a way out.
 *
 * Shared rather than per panel, so which of them a reader gets does not depend
 * on what the model happened to produce.
 */
export function PanelControls({
  wide,
  onToggleWide,
  onClose,
}: {
  wide: boolean
  /** Omitted where the host cannot grow — inside a fixed-width preview. */
  onToggleWide?: () => void
  onClose?: () => void
}) {
  const t = useT()
  return (
    <>
      {onToggleWide && (
        <Button
          variant="ghost"
          size="icon"
          aria-label={wide ? t('패널 좁히기') : t('넓게 보기')}
          title={wide ? t('원래 너비로 되돌립니다') : t('패널을 넓혀 크게 봅니다')}
          onClick={onToggleWide}
        >
          {wide ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
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
export function usePanelWidth(onWideChange?: (wide: boolean) => void) {
  const [wide, setWide] = useState(false)
  return {
    wide,
    toggle: onWideChange
      ? () => {
          setWide(!wide)
          onWideChange(!wide)
        }
      : undefined,
  }
}
