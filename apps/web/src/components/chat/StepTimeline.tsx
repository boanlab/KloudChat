import {
  Brain,
  Check,
  ChevronRight,
  FileText,
  Loader2,
  TriangleAlert,
  Wrench,
} from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import type { Step } from '@/types'
import { useT } from '@/lib/useT'

const icons = { thinking: Brain, tool: Wrench, artifact: FileText }

function StepRow({ step, live }: { step: Step; live: boolean }) {
  const Icon = icons[step.type]
  return (
    <div className="flex items-start gap-2.5 text-[13px]">
      <span
        className={cn(
          'mt-0.5 grid size-4 shrink-0 place-items-center',
          step.status === 'error' ? 'text-danger' : step.status === 'done' ? 'text-success' : 'text-accent',
        )}
      >
        {step.status === 'running' ? (
          <Loader2 size={13} className="animate-spin" />
        ) : step.status === 'error' ? (
          <TriangleAlert size={13} />
        ) : (
          <Check size={13} />
        )}
      </span>
      <Icon size={13} className="mt-0.5 shrink-0 text-faint" />
      <span className="min-w-0 flex-1">
        <span className={cn(step.status === 'running' && live ? 'text-fg' : 'text-muted')}>
          {step.label}
          {step.progress && (
            <span className="ml-1 tabular-nums text-faint">
              ({step.progress.current}/{step.progress.total})
            </span>
          )}
          {step.status === 'running' && live && <span className="animate-blink">…</span>}
        </span>
        {step.detail && <span className="ml-1.5 text-faint">— {step.detail}</span>}
      </span>
    </div>
  )
}

/**
 * While the turn is live, every step is visible — the running one reads as the
 * current status line ("reading document…"). Once the turn settles it collapses to a
 * single summary row that can be expanded again.
 */
export function StepTimeline({ steps, live }: { steps: Step[]; live: boolean }) {
  const t = useT()
  const [expanded, setExpanded] = useState(false)
  if (steps.length === 0) return null

  if (live) {
    return (
      <div className="mb-3 space-y-1.5 rounded-xl border border-line bg-elevated/50 px-3 py-2.5">
        {steps.map((s) => (
          <StepRow key={s.id} step={s} live />
        ))}
      </div>
    )
  }

  return (
    <div className="mb-3 overflow-hidden rounded-xl border border-line bg-elevated/50">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] text-muted transition-colors hover:text-fg"
      >
        <ChevronRight
          size={13}
          className={cn('shrink-0 transition-transform', expanded && 'rotate-90')}
        />
        <Check size={13} className="shrink-0 text-success" />
        {/* `shrink-0` and `whitespace-nowrap`: in the narrow transcript column
            beside an open report panel, flex was shrinking these to a few pixels
            and the summary wrapped one character per line. */}
        <span className="shrink-0 whitespace-nowrap">
            {t('{n}단계 완료').replace('{n}', String(steps.length))}
          </span>
        <span className="min-w-0 truncate text-faint">
          · {steps.map((s) => s.label).join(' · ')}
        </span>
      </button>
      {expanded && (
        <div className="space-y-1.5 border-t border-line px-3 py-2.5">
          {steps.map((s) => (
            <StepRow key={s.id} step={s} live={false} />
          ))}
        </div>
      )}
    </div>
  )
}
