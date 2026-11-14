import { Loader2, Play, RotateCcw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button, LoadingState, Modal } from '@/components/ui'
import { errorMessage, mediaApi, type MediaInfo } from '@/lib/api'
import { clock, frameAt, frameSrc, mediaInfo, mediaNote } from '@/lib/media'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

/** How often a player asks after a video still being read. */
const POLL_MS = 4000

/**
 * A video (or recording) with what was read from it: the player, streamed from the server with
 * seeking (never downloaded whole), and the time-coded outline — each stretch's picture, what
 * was on screen and what was said; a click on one plays from there.
 */
export function MediaPlayer({ fileId, start, audio = false }: { fileId: string; start?: number; audio?: boolean }) {
  const t = useT()
  const [info, setInfo] = useState<MediaInfo | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [now, setNow] = useState(start ?? 0)
  const player = useRef<HTMLVideoElement & HTMLAudioElement>(null)
  const seekTo = useRef<number | null>(start ?? null)

  useEffect(() => {
    let live = true
    let timer: number | undefined
    const load = (fresh: boolean) =>
      mediaInfo(fileId, fresh)
        .then((found) => {
          if (!live) return
          setInfo(found)
          if (found.state === 'queued' || found.state === 'running') {
            timer = window.setTimeout(() => void load(true), POLL_MS)
          }
        })
        .catch((err) => live && setFailed(errorMessage(err, t('영상을 불러오지 못했습니다.'))))
    void load(false)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [fileId, t])

  // a new moment asked for while open (another chip): play from there
  useEffect(() => {
    if (start === undefined) return
    seekTo.current = start
    const el = player.current
    if (el && el.readyState >= 1) {
      el.currentTime = start
      seekTo.current = null
      void el.play().catch(() => undefined)
    }
  }, [start])

  const play = (seconds: number) => {
    const el = player.current
    if (!el) return
    el.currentTime = seconds
    void el.play().catch(() => undefined)
  }

  const retry = () => {
    void mediaApi.retry(fileId).then(() => mediaInfo(fileId, true)).then(setInfo).catch(() => undefined)
  }

  if (failed) return <p className="py-8 text-center text-base text-danger">{failed}</p>
  if (!info) return <LoadingState />
  const busy = info.state === 'queued' || info.state === 'running'
  const note = mediaNote({ id: fileId, mediaState: info.state, mediaStage: info.stage, mediaDone: info.done, mediaTotal: info.total, mediaDuration: info.duration }, t)
  const current = info.segments.findLastIndex((segment) => segment.start <= now + 0.25)
  const Tag = audio || !info.hasVideo ? 'audio' : 'video'
  return (
    <div className="flex w-full flex-col gap-3" data-testid="media-player">
      {info.streamUrl && (
        <Tag
          ref={player}
          src={info.streamUrl}
          controls
          preload="metadata"
          poster={Tag !== 'video' ? undefined
            // the very moment it opens at, else the nearest kept frame
            : start && info.posterUrl ? info.posterUrl.replace('{t}', String(Math.floor(start)))
              : info.frames.length ? frameSrc(info, frameAt(info, start ?? 0) ?? 0, false) : undefined}
          data-testid="media-element"
          className={Tag === 'video' ? 'max-h-[52vh] w-full rounded-control bg-black' : 'w-full'}
          onLoadedMetadata={(e) => {
            if (seekTo.current !== null) {
              e.currentTarget.currentTime = seekTo.current
              seekTo.current = null
              void e.currentTarget.play().catch(() => undefined)
            }
          }}
          onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)}
        />
      )}
      {busy && note && (
        <p role="status" data-testid="media-status" className="flex items-center gap-1.5 text-sm text-muted">
          <Loader2 size={13} className="animate-spin" /> {note.text}
        </p>
      )}
      {info.state === 'failed' && (
        <p role="status" data-testid="media-status" className="flex flex-wrap items-center gap-2 text-sm text-warn">
          {info.error ?? t('영상을 읽지 못했습니다.')}
          <Button size="sm" variant="secondary" onClick={retry}>
            <RotateCcw size={13} /> {t('다시 읽기')}
          </Button>
        </p>
      )}
      {info.segments.length > 0 && (
        <ol data-testid="media-outline" aria-label={t('영상 내용')} className="max-h-[36vh] space-y-1 overflow-y-auto pr-1">
          {info.segments.map((segment, i) => (
            <li key={`${segment.start}-${i}`}>
              <button
                onClick={() => play(segment.start)}
                data-start={Math.floor(segment.start)}
                aria-current={i === current ? 'true' : undefined}
                className={cn(
                  'flex w-full items-start gap-2.5 rounded-control px-2 py-1.5 text-left hover:bg-elevated',
                  i === current && 'bg-elevated',
                )}
              >
                {segment.frame !== null && info.frameUrl ? (
                  <img src={frameSrc(info, segment.frame)} alt="" loading="lazy" className="mt-0.5 h-12 w-20 shrink-0 rounded object-cover" />
                ) : (
                  <span className="mt-0.5 grid h-12 w-20 shrink-0 place-items-center rounded bg-elevated text-faint"><Play size={14} /></span>
                )}
                <span className="min-w-0 flex-1 text-sm">
                  <span className="font-mono text-xs text-accent">{clock(segment.start)}</span>
                  {segment.shown && <span className="block truncate text-muted">{segment.shown}</span>}
                  {segment.said && <span className="line-clamp-2 block text-fg">{segment.said}</span>}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

/** The player in a dialog, from a chat's time chip or a project's file list. */
export function MediaDialog({ fileId, name, start, onClose }: { fileId: string; name: string; start?: number; onClose: () => void }) {
  return (
    <Modal open onClose={onClose} title={name} width="max-w-4xl">
      <MediaPlayer fileId={fileId} start={start} />
    </Modal>
  )
}
