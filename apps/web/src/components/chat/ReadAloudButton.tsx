import { Loader2, Pause, Play, Square, Volume2 } from 'lucide-react'
import { useEffect } from 'react'
import { Button } from '@/components/ui'
import { pauseReading, readingKey, readPieces, resumeReading, startReading, stopReading, useReadState, type ReadUnit } from '@/lib/readAloud'
import { useSpeechModel, voiceFor } from '@/lib/speechModel'
import { useVoiceMode } from '@/lib/voiceSession'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'

/** 「읽어 주기」: reads a written text aloud with the self-hosted voice; the text on screen stays as it is.
 * `units` is read when the button is pressed, so it always reads what is shown then. `onAt` follows the
 * reading (scroll to the section, show the slide). Hidden while a voice conversation is on (it reads the answers
 * itself) and where no self-hosted voice exists. */
export function ReadAloudButton({
  id,
  units,
  onAt,
  onEnd,
  className,
  compact = false,
}: {
  /** One per readable thing: the button that owns the reading is the one that shows 멈춤. */
  id: string
  units: () => ReadUnit[]
  onAt?: (at: string) => void
  /** The reading ended or was stopped. */
  onEnd?: () => void
  className?: string
  /** Icon only, with the progress as a number (a narrow header). */
  compact?: boolean
}) {
  const t = useT()
  const model = useSpeechModel()
  const voiceMode = useVoiceMode()
  const reading = useReadState()
  const mine = reading.key === id
  const active = mine && reading.status !== 'idle'

  // A voice conversation starting, or this button going away, ends its reading.
  useEffect(() => {
    if (voiceMode && mine) stopReading()
  }, [voiceMode, mine])
  useEffect(
    () => () => {
      if (readingKey() === id) stopReading()
    },
    [id],
  )

  if (!model || voiceMode) return null

  const begin = () => {
    const { pieces, cut } = readPieces(units())
    if (!pieces.length) return
    startReading(id, pieces, { voice: voiceFor(model), cut, onAt, onEnd })
  }

  const status =
    mine && reading.problem === 'limit'
      ? t('읽기 요청이 많습니다. 잠시 뒤 다시 눌러 주세요.')
      : mine && reading.problem === 'failed'
        ? t('음성을 만들지 못했습니다.')
        : active && reading.status === 'paused'
          ? t('잠시 멈춤')
          : active && reading.cut
            ? t('앞부분만 읽습니다')
            : ''
  const progress = active && reading.total > 1 ? `${reading.index + 1}/${reading.total}` : ''

  return (
    <span className={cn('inline-flex flex-wrap items-center gap-0.5', className)} data-testid="read-aloud">
      <Button
        variant="ghost"
        size="icon"
        aria-label={active ? t('멈춤') : t('읽어 주기')}
        aria-pressed={active}
        title={active ? t('읽기를 멈춥니다') : t('소리 내어 읽어 줍니다. 화면의 글은 그대로입니다.')}
        onClick={() => (active ? stopReading() : begin())}
      >
        {active ? (
          reading.status === 'loading' ? <Loader2 size={14} className="animate-spin" /> : <Square size={13} />
        ) : (
          <Volume2 size={14} />
        )}
      </Button>
      {active && (reading.status === 'playing' || reading.status === 'paused') && (
        <Button
          variant="ghost"
          size="icon"
          aria-label={reading.status === 'paused' ? t('이어 읽기') : t('잠시 멈춤')}
          onClick={() => (reading.status === 'paused' ? resumeReading() : pauseReading())}
        >
          {reading.status === 'paused' ? <Play size={14} /> : <Pause size={14} />}
        </Button>
      )}
      {(progress || status) && (
        <span
          role="status"
          aria-live="polite"
          className={cn('px-1 text-xs tabular-nums', mine && reading.problem ? 'text-danger' : 'text-muted')}
          data-testid="read-aloud-status"
        >
          {[progress, !compact && active && reading.label ? reading.label : '', status].filter(Boolean).join(' · ')}
        </span>
      )}
    </span>
  )
}
