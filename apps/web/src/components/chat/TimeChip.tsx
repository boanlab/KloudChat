import { Play } from 'lucide-react'
import { useEffect, useState } from 'react'
import { MediaDialog } from '@/components/files/MediaPlayer'
import type { MediaInfo } from '@/lib/api'
import { clock, frameAt, frameSrc, mediaInfo } from '@/lib/media'
import { useT } from '@/lib/useT'

/**
 * A moment of a video an answer points to (「[12:30]」, or a cited passage 「[3]」): the time
 * with that moment's picture; a click plays the video from there in a dialog.
 */
export function TimeChip({ fileId, seconds, number }: { fileId: string; seconds: number; number?: string }) {
  const t = useT()
  const [info, setInfo] = useState<MediaInfo | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    let live = true
    mediaInfo(fileId).then((found) => live && setInfo(found)).catch(() => undefined)
    return () => {
      live = false
    }
  }, [fileId])
  const frame = info ? frameAt(info, seconds) : null
  const thumb = info && frame !== null ? frameSrc(info, frame) : ''
  const label = t('{name} {time}부터 재생').replace('{name}', info?.name ?? t('영상')).replace('{time}', clock(seconds))
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={label}
        aria-label={label}
        data-testid="time-chip"
        data-file={fileId}
        data-seconds={seconds}
        className="mx-0.5 inline-flex items-center gap-1 rounded border border-line-strong py-px pr-1.5 pl-px align-baseline text-[0.78em] leading-snug text-accent hover:bg-elevated"
      >
        {thumb ? (
          <img src={thumb} alt="" className="h-[1.35em] w-[2.4em] rounded-sm object-cover" />
        ) : (
          <Play size={11} className="ml-1" />
        )}
        {number && <span className="text-faint">{number}</span>}
        <span className="font-mono">{clock(seconds)}</span>
      </button>
      {open && (
        <MediaDialog fileId={fileId} name={info?.name ?? t('영상')} start={seconds} onClose={() => setOpen(false)} />
      )}
    </>
  )
}
