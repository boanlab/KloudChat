import { Loader2, RotateCcw, TriangleAlert, X } from 'lucide-react'
import { Button } from '@/components/ui'
import { cn, relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Job } from '@/types'
import { useT } from '@/lib/useT'

/** Progress, failure and retry for a running media job; a finished one renders nothing (the turn shows the clip). */
export function JobCard({ job }: { job: Job }) {
  const t = useT()
  const { cancelJob, retryJob } = useStore()

  if (job.status === 'running' || job.status === 'queued') {
    return (
      <div className="animate-fade-up rounded-panel border border-line bg-panel p-4">
        <div className="flex items-center gap-2.5">
          <Loader2 size={15} className="shrink-0 animate-spin text-accent" />
          <span className="flex-1 text-base font-medium">{t(job.stage)}</span>
          <span className="text-xs tabular-nums text-faint">{job.progress}%</span>
          <Button variant="ghost" size="icon" aria-label={t('취소')} onClick={() => void cancelJob(job.id)}>
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

  return null
}
