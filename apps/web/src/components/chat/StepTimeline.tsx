import {
  Brain,
  Check,
  ChevronRight,
  CircleMinus,
  FileText,
  Loader2,
  TriangleAlert,
  Wrench,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import type { Step } from '@/types'
import { useT } from '@/lib/useT'

/**
 * Seconds since the turn began, ticking.
 *
 * The header said 작업 중 and nothing else, which is true of a turn one second
 * old and of one that has been wedged for four minutes. A number that keeps
 * moving is what tells those apart without opening anything.
 */
function useElapsed(since: number | undefined, live: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!live || since === undefined) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [live, since])
  if (since === undefined) return null
  const seconds = Math.max(0, Math.floor((now - since) / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

const icons = { thinking: Brain, tool: Wrench, artifact: FileText }
/** What kind of work this is, for the header once the list is showing. */
const kindLabel = { thinking: '추론', tool: '도구 사용', artifact: '산출물' }
/** How many other steps the folded line names before it starts counting.
 *  Every turn now opens with lines saying what it was handed — memories,
 *  attachments, project knowledge — and unbudgeted the folded line would spend
 *  itself on those and never reach the work. */
const COLLAPSED_NAMES = 3

/** One checklist line. Finished work is struck through: the shape of the run
 *  stays readable without every line still being worth reading. */
function StepRow({ step, live }: { step: Step; live: boolean }) {
  const Icon = icons[step.type]
  const running = step.status === 'running'
  return (
    <div className="flex items-start gap-2.5 text-base">
      <span
        className={cn(
          'mt-[3px] grid size-3.5 shrink-0 place-items-center',
          step.status === 'error'
            ? 'text-danger'
            : running
              ? 'text-accent'
              : 'text-faint',
        )}
      >
        {running ? (
          live ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <CircleMinus size={13} />
          )
        ) : step.status === 'error' ? (
          <TriangleAlert size={13} />
        ) : (
          <Check size={13} />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span
          className={cn(
            step.status === 'done' && 'text-faint line-through decoration-faint/50',
            step.status === 'error' && 'text-danger',
            running && (live ? 'font-medium text-fg' : 'text-fg'),
          )}
        >
          {step.label}
          {step.progress && (
            <span className="ml-1 tabular-nums text-faint no-underline">
              ({step.progress.current}/{step.progress.total})
            </span>
          )}
          {running && live && <span className="animate-blink">…</span>}
        </span>
        {step.detail && (
          <span className="ml-1.5 text-faint no-underline">— {step.detail}</span>
        )}
      </span>
      <Icon size={12} className="mt-1 shrink-0 text-faint" />
    </div>
  )
}

/**
 * The turn's work log: a header naming the current step and what is left, over
 * a checklist that keeps finished steps visible. Open while live, collapsed
 * once the turn settles, reopenable either way.
 */
export function StepTimeline({
  steps,
  live,
  startedAt,
}: {
  steps: Step[]
  live: boolean
  /** Epoch milliseconds the turn started. Drives the running clock. */
  startedAt?: number
}) {
  const t = useT()
  const elapsed = useElapsed(startedAt, live)
  //: Reader's choice, and it wins over the liveness default — a card folded
  //: away must not spring open on the next step.
  const [manual, setManual] = useState<boolean | null>(null)
  if (steps.length === 0) return null

  const done = steps.filter((s) => s.status === 'done').length
  const failed = steps.some((s) => s.status === 'error')
  const current = steps.find((s) => s.status === 'running')
  const expanded = manual ?? live
  /**
   * Steps remaining, from the running step's own denominator. The step count
   * cannot supply it: it only ever reflects what has already arrived.
   */
  const left = current?.progress ? current.progress.total - current.progress.current : null
  // Header subject: the kind of work, then the step's own name.
  const head = current ?? steps.at(-1)!
  const HeadIcon = icons[head.type]
  const rest = steps.filter((s) => s !== head)

  return (
    <div className="animate-fade-up mb-3 overflow-hidden rounded-card border border-line bg-elevated/50">
      <button
        onClick={() => setManual(!expanded)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-elevated"
      >
        <ChevronRight
          size={13}
          className={cn('shrink-0 text-faint transition-transform', expanded && 'rotate-90')}
        />
        <span className="shrink-0 font-medium text-muted">
          {live ? t('작업 중') : failed ? t('중단됨') : t('작업 완료')}
        </span>
        <span className="shrink-0 text-line-strong">|</span>
        <HeadIcon size={13} className="shrink-0 text-accent" />
        {/* Collapsed: the step name is the status line. Expanded: that line is
            already the highlighted row below, so name the kind of work. */}
        <span className="min-w-0 truncate text-fg">
          {expanded ? t(kindLabel[head.type]) : head.label}
        </span>
        {/* The rest of the run, named rather than counted: "3단계" says a turn
            did something; the names say it searched the web. */}
        {!expanded && rest.length > 0 && (
          <span className="min-w-0 truncate text-faint">
            · {rest.slice(0, COLLAPSED_NAMES).map((s) => s.label).join(' · ')}
            {rest.length > COLLAPSED_NAMES &&
              ` · ${t('외 {n}개').replace('{n}', String(rest.length - COLLAPSED_NAMES))}`}
          </span>
        )}
        {live && elapsed && (
          <span className="ml-auto shrink-0 whitespace-nowrap text-faint tabular-nums">
            {elapsed}
          </span>
        )}
        <span
          className={cn(
            'shrink-0 whitespace-nowrap text-faint tabular-nums',
            !(live && elapsed) && 'ml-auto',
          )}
        >
          {live && left !== null && left > 0
            ? t('{n}개 남음').replace('{n}', String(left))
            : live
              ? t('{n}단계 완료').replace('{n}', String(done))
              : t('{n}단계').replace('{n}', String(steps.length))}
        </span>
      </button>
      {expanded && (
        <div className="space-y-1.5 border-t border-line px-3 py-2.5">
          {steps.map((s) => (
            <StepRow key={s.id} step={s} live={live} />
          ))}
        </div>
      )}
    </div>
  )
}

