import { Download, Loader2, RotateCcw, TriangleAlert, X } from 'lucide-react'
import { Badge, Button, ButtonLink } from '@/components/ui'
import { fileUrl } from '@/lib/api'
import { cn, relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Artifact, AudioArtifact, ImageArtifact, Job, VideoArtifact } from '@/types'
import { useT } from '@/lib/useT'

const aspectClass: Record<string, string> = {
  '1:1': 'aspect-square',
  '16:9': 'aspect-video',
  '9:16': 'aspect-[9/16]',
  '4:3': 'aspect-[4/3]',
}

const audioKindLabel: Record<AudioArtifact['audioKind'], string> = {
  narration: '내레이션',
  music: '음악',
}

function isImage(a: Artifact): a is ImageArtifact {
  return a.kind === 'image'
}
function isAudio(a: Artifact): a is AudioArtifact {
  return a.kind === 'audio'
}
function isVideo(a: Artifact): a is VideoArtifact {
  return a.kind === 'video'
}
function isAsset(a: Artifact): a is ImageArtifact | AudioArtifact | VideoArtifact {
  return isImage(a) || isAudio(a) || isVideo(a)
}

/** Static amplitude envelope. Rendered as bars so it stays crisp at any width. */
function Waveform({ peaks }: { peaks: number[] }) {
  return (
    <div className="flex h-10 flex-1 items-center gap-[2px]" aria-hidden>
      {peaks.map((p, i) => (
        <span
          key={i}
          className="flex-1 rounded-full bg-accent/45"
          style={{ height: `${Math.max(8, p * 100)}%` }}
        />
      ))}
    </div>
  )
}

/**
 * The async-generation surface. A request becomes a card immediately, shows
 * stage + progress while the worker runs, and swaps to the result (grid or
 * player) on success. Failure states cost nothing, so the card says so rather
 * than talking about refunds.
 */
export function JobCard({ job }: { job: Job }) {
  const t = useT()
  const { artifacts, cancelJob, openArtifact, models, retryJob } = useStore()
  // Scoped to this job, not the session — a session can hold many generations.
  const assets = artifacts.filter((a) => isAsset(a) && a.jobId === job.id)
  const first = assets[0]
  const model = models.find((m) => m.id === (first && 'model' in first ? first.model : ''))

  if (job.status === 'running' || job.status === 'queued') {
    return (
      <div className="animate-fade-up rounded-panel border border-line bg-panel p-4">
        <div className="flex items-center gap-2.5">
          <Loader2 size={15} className="shrink-0 animate-spin text-accent" />
          <span className="flex-1 text-base font-medium">{t(job.stage)}</span>
          <span className="text-xs tabular-nums text-faint">{job.progress}%</span>
          <Button variant="ghost" size="icon" aria-label={t('취소')} onClick={() => cancelJob(job.id)}>
            <X size={14} />
          </Button>
        </div>
        <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-elevated">
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-500 ease-out"
            style={{ width: `${job.progress}%` }}
          />
        </div>
        <p className="mt-2 text-xs text-faint">
          {t('예상 {n} 크레딧 · 완료 시에만 차감됩니다').replace('{n}', job.creditsEstimated.toLocaleString())}
        </p>
      </div>
    )
  }

  if (job.status === 'failed' || job.status === 'canceled') {
    const failed = job.status === 'failed'
    return (
      <div
        className={cn(
          'animate-fade-up rounded-panel border p-4',
          failed ? 'border-danger/30 bg-danger/5' : 'border-line bg-panel',
        )}
      >
        <div className="flex items-start gap-2.5">
          <TriangleAlert
            size={15}
            className={cn('mt-0.5 shrink-0', failed ? 'text-danger' : 'text-faint')}
          />
          <div className="min-w-0 flex-1">
            <p className={cn('text-base font-medium', failed && 'text-danger')}>
              {failed ? t('생성 실패') : t('취소됨')}
            </p>
            {job.error && <p className="mt-0.5 text-base text-muted">{t(job.error)}</p>}
            <p className="mt-1.5 text-xs text-faint">
              {t('크레딧이 차감되지 않았습니다')} · {relativeTime(job.createdAt)}
            </p>
          </div>
          <Button size="sm" onClick={() => void retryJob(job)}>
            <RotateCcw size={13} />
            {t('다시 시도')}
          </Button>
        </div>
      </div>
    )
  }

  // succeeded
  const videos = assets.filter(isVideo)
  const audios = assets.filter(isAudio)
  const images = assets.filter(isImage)

  return (
    <div className="animate-fade-up space-y-2">
      {audios.map((a) => (
        <div
          key={a.id}
          className="flex items-center gap-3 rounded-panel border border-line bg-panel px-3.5 py-3"
        >
          <div className="min-w-0 flex-1">
            {/* 브라우저 기본 플레이어. `waveform` 은 비어 있는 채로 저장되므로
                자체 파형 UI 로는 이미 결제된 클립을 들을 수 없다. */}
            <audio controls src={fileUrl(a.src)} className="w-full" preload="metadata">
              <track kind="captions" />
            </audio>
            {a.waveform.length > 0 && <Waveform peaks={a.waveform} />}
            <p className="mt-1 truncate text-xs text-faint">
              {t(audioKindLabel[a.audioKind])} · {t('{n}초').replace('{n}', String(a.durationSec))} ·{' '}
                {model?.label ?? a.model}
            </p>
          </div>
          <Badge>{t('{n} 크레딧').replace('{n}', job.creditsUsed.toLocaleString())}</Badge>
          <ButtonLink
            variant="ghost"
            size="icon"
            aria-label={t('다운로드')}
            href={fileUrl(a.src)}
            download={a.title}
          >
            <Download size={15} />
          </ButtonLink>
        </div>
      ))}

      {videos.map((v) => (
        <div key={v.id} className="overflow-hidden rounded-panel border border-line bg-panel">
          {/* `posterSrc` is written empty — nothing makes thumbnails — so the
              <img> here drew a broken image, and the ▶ over it was decoration. */}
          <video
            controls
            src={fileUrl(v.src)}
            poster={fileUrl(v.posterSrc)}
            preload="metadata"
            playsInline
            className={cn('w-full bg-black', aspectClass[v.aspect] ?? 'aspect-video')}
          />
          <div className="flex items-center gap-2 px-3.5 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate text-base font-medium">{v.title}</p>
              <p className="text-xs text-faint">
                {t('{n}초').replace('{n}', String(v.durationSec))} · {v.aspect} · {model?.label ?? v.model}
              </p>
            </div>
            <Badge>{t('{n} 크레딧').replace('{n}', job.creditsUsed.toLocaleString())}</Badge>
            <ButtonLink
              variant="ghost"
              size="icon"
              aria-label={t('다운로드')}
              href={fileUrl(v.src)}
              download={v.title}
            >
              <Download size={15} />
            </ButtonLink>
          </div>
        </div>
      ))}

      {images.length > 0 && (
        <>
          <div
            className={cn(
              'grid gap-2',
              images.length === 1 ? 'grid-cols-1' : images.length <= 4 ? 'grid-cols-2' : 'grid-cols-3',
            )}
          >
            {images.map((img) => (
              <button
                key={img.id}
                onClick={() => openArtifact(img.id)}
                className={cn(
                  'group relative overflow-hidden rounded-card border border-line bg-elevated',
                  aspectClass[img.aspect] ?? 'aspect-square',
                )}
              >
                <img
                  src={fileUrl(img.src)}
                  alt={img.prompt}
                  className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                />
              </button>
            ))}
          </div>
          <p className="text-xs text-faint">
            {t('{n}장').replace('{n}', String(images.length))} ·{' '}
            {t('{n} 크레딧 차감됨').replace('{n}', job.creditsUsed.toLocaleString())}
          </p>
        </>
      )}
    </div>
  )
}
