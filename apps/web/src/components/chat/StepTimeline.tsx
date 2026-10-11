import {
  Brain,
  Check,
  ChevronRight,
  CircleMinus,
  Download,
  FileText,
  Loader2,
  TriangleAlert,
  Workflow,
  Wrench,
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { cn, fileSize } from '@/lib/utils'
import type { PipelineRun, SessionKind, Step } from '@/types'
import { StageList, TraceBlock } from './StageList'
import { doingPhrase, donePhrase, failedPhrase, runSeconds, stageLabel, stageTone, waitingPhrase } from '@/lib/stages'
import { useT } from '@/lib/useT'
import { downloadFile } from '@/lib/api'

/** m:ss since `since`, ticking while live. */
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

/** 「55초」, 「1분 5초」: how long a finished run took. */
function durationWords(seconds: number, t: (text: string) => string): string {
  const m = Math.floor(seconds / 60)
  const sec = seconds % 60
  if (m === 0) return t('{n}초').replace('{n}', String(sec))
  if (sec === 0) return t('{m}분').replace('{m}', String(m))
  return t('{m}분 {s}초').replace('{m}', String(m)).replace('{s}', String(sec))
}

/** How the turn stands, for its one-line header. */
type Phase = 'live' | 'waiting' | 'failed' | 'stopped' | 'done'

/** The header's outcome line: what is happening or what happened, in plain words. */
function headline(
  phase: Phase,
  kind: SessionKind | undefined,
  run: PipelineRun | undefined,
  awaiting: string | null | undefined,
  t: (text: string) => string,
): string {
  if (phase === 'live') return t(doingPhrase(kind, run))
  if (phase === 'waiting') return t(waitingPhrase(kind, awaiting ? { stage: awaiting } : null))
  if (phase === 'stopped') return t('멈췄습니다')
  if (phase === 'failed') {
    const where = run?.stages.find((s) => s.status === 'failed')
    const said = t(failedPhrase(kind))
    return where ? `${said} · ${t('「{stage}」에서 멈춤').replace('{stage}', t(stageLabel(where)))}` : said
  }
  const done = t(donePhrase(kind, run))
  const seconds = run ? runSeconds(run) : null
  return seconds ? `${done} · ${durationWords(seconds, t)}` : done
}

const icons = { thinking: Brain, tool: Wrench, artifact: FileText, pipeline: Workflow }
/** Steps the collapsed header names before counting the rest. */
const COLLAPSED_NAMES = 3

/** One checklist line; finished steps are struck through. */
function StepRow({ step, live }: { step: Step; live: boolean }) {
  const t = useT()
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
          {t(step.label)}
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
        {step.outputs && step.outputs.length > 0 && <OutputFiles files={step.outputs} />}
        {(step.code || step.stdout || step.stderr) && <RunTrace step={step} />}
      </span>
      <Icon size={12} className="mt-1 shrink-0 text-faint" />
    </div>
  )
}

/** 코드 보기 / 출력 보기: what a sandbox step ran and printed, collapsed by default. */
function RunTrace({ step }: { step: Step }) {
  const t = useT()
  const output = [step.stdout, step.stderr && `stderr:\n${step.stderr}`]
    .filter(Boolean)
    .join('\n\n')
  return (
    <span className="mt-1 block space-y-1 no-underline">
      {step.code && (
        <TraceBlock
          summary={t('코드 보기')}
          note={
            step.codeAttempt && step.codeAttempt > 1
              ? t('{n}번째 시도의 코드').replace('{n}', String(step.codeAttempt))
              : undefined
          }
          text={step.code}
        />
      )}
      {output && <TraceBlock summary={t('출력 보기')} text={output} />}
    </span>
  )
}

/** Download chips for the files a tool step made. */
export function OutputFiles({ files }: { files: NonNullable<Step['outputs']> }) {
  const t = useT()
  const [failed, setFailed] = useState(false)
  const take = async (id: string, name: string) => {
    setFailed(false)
    try {
      await downloadFile(id, name)
    } catch {
      setFailed(true)
    }
  }
  return (
    <span className="mt-1 flex flex-wrap items-center gap-1.5">
      {files.map((file) => (
        <button
          key={file.id}
          type="button"
          title={t('만든 파일을 내려받습니다')}
          onClick={() => void take(file.id, file.name)}
          className="flex max-w-full items-center gap-1.5 rounded-control border border-line bg-panel px-2 py-1 text-sm transition-colors hover:border-strong hover:bg-elevated"
        >
          <FileText size={12} className="shrink-0 text-faint" />
          <span className="truncate">{file.name}</span>
          <span className="shrink-0 text-faint">{fileSize(file.size)}</span>
          <Download size={12} className="shrink-0 text-faint" />
        </button>
      ))}
      {failed && <span className="text-sm text-danger">{t('파일을 내려받지 못했습니다.')}</span>}
    </span>
  )
}

/** The turn's step log: open while live, collapsed once settled, toggleable either way. */
export function StepTimeline({
  steps,
  live,
  failed,
  startedAt,
  sessionId,
  run,
  rerunnable,
  waitingSlot,
  kind,
  stopped,
  awaiting,
}: {
  steps: Step[]
  live: boolean
  /** The turn's end state, not a recoverable error in an individual tool call. */
  failed: boolean
  /** The reader stopped the turn: said as a stop, not a failure. */
  stopped?: boolean
  /** The surface the turn ran on; the header says what it made. */
  kind?: SessionKind
  /** The card this turn waits on (`outline`, `clarify`, `figures`), while it still waits. */
  awaiting?: string | null
  /** Epoch ms the turn started; drives the clock. */
  startedAt?: number
  sessionId?: string
  /** The pipeline run's stage records, listed apart from the tool steps. */
  run?: PipelineRun
  /** Report and slides: the rows offer 「이 단계부터 다시」 and show in full. */
  rerunnable?: boolean
  /** The question card, in the waiting stage's place. */
  waitingSlot?: ReactNode
}) {
  const t = useT()
  const elapsed = useElapsed(startedAt, live)
  // The reader's toggle wins over the liveness default.
  const [manual, setManual] = useState<boolean | null>(null)
  const stages = run && sessionId ? run.stages : []
  if (steps.length === 0 && stages.length === 0) return null
  // A pipeline turn reads as one list: its tool steps first, then the stages, under one header.
  if (stages.length > 0) {
    return (
      <StageGroup
        sessionId={sessionId!}
        run={run!}
        steps={steps}
        live={live}
        rerunnable={rerunnable}
        waitingSlot={waitingSlot}
        kind={kind}
        awaiting={awaiting}
        phase={phaseOf(live, failed, stopped, awaiting)}
        startedAt={startedAt}
      />
    )
  }

  const phase = phaseOf(live, failed, stopped, awaiting)
  const current = steps.find((s) => s.status === 'running')
  const expanded = manual ?? live
  const head = current ?? steps.at(-1)!
  const HeadIcon = icons[head.type]

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
        <HeadIcon size={13} className="shrink-0 text-accent" />
        <span className={cn('min-w-0 truncate font-medium', phase === 'failed' ? 'text-danger' : 'text-fg')}>
          {headline(phase, kind, undefined, awaiting, t)}
        </span>
        {/* Folded while live, the step under way is named beside the headline. */}
        {live && current && !expanded && (
          <span className="min-w-0 truncate text-faint">· {t(current.label)}</span>
        )}
        {/* Collapsed, what it did is named after the outcome. */}
        {!expanded && !live && (
          <span className="min-w-0 truncate text-faint">
            · {steps.slice(0, COLLAPSED_NAMES).map((s) => t(s.label)).join(' · ')}
            {steps.length > COLLAPSED_NAMES &&
              ` · ${t('외 {n}개').replace('{n}', String(steps.length - COLLAPSED_NAMES))}`}
          </span>
        )}
        {live && elapsed && (
          <span className="ml-auto shrink-0 whitespace-nowrap text-faint tabular-nums">
            {elapsed}
          </span>
        )}
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

/** Which phase a turn's header speaks for. */
function phaseOf(
  live: boolean,
  failed: boolean,
  stopped: boolean | undefined,
  awaiting: string | null | undefined,
): Phase {
  if (live) return 'live'
  if (awaiting) return 'waiting'
  if (stopped) return 'stopped'
  // A check that failed inside a finished run is flagged, not the turn's failure.
  if (failed) return 'failed'
  return 'done'
}

/** 「확인할 것 1개」: checks that left something to look at; the rest live in the list. */
function Flagged({ run }: { run: PipelineRun }) {
  const t = useT()
  const flagged = run.stages.filter((s) => s.status !== 'stopped' &&
    (stageTone(s) === 'findings' || stageTone(s) === 'failed')).length
  if (flagged === 0) return null
  return <span className="text-warn">{t('확인할 것 {n}개').replace('{n}', String(flagged))}</span>
}

/** A turn whose only visible work is its stages: the stage list under its own header. */
function StageGroup({
  sessionId,
  run,
  steps,
  live,
  rerunnable,
  waitingSlot,
  kind,
  awaiting,
  phase,
  startedAt,
}: {
  sessionId: string
  run: PipelineRun
  /** The turn's tool steps, listed above the stages. */
  steps: Step[]
  live: boolean
  rerunnable?: boolean
  waitingSlot?: ReactNode
  kind?: SessionKind
  awaiting?: string | null
  phase: Phase
  startedAt?: number
}) {
  const t = useT()
  const elapsed = useElapsed(startedAt, live)
  // A waiting card must be seen, not folded away.
  const [manual, setManual] = useState<boolean | null>(null)
  const expanded = manual ?? (live || run.status === 'waiting')
  const current = steps.find((s) => s.status === 'running')
  return (
    <div className="animate-fade-up mb-3 overflow-hidden rounded-card border border-line bg-elevated/50" data-category="pipeline">
      <button
        onClick={() => setManual(!expanded)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-elevated"
      >
        <ChevronRight size={13} className={cn('shrink-0 text-faint transition-transform', expanded && 'rotate-90')} />
        <Workflow size={13} className="shrink-0 text-accent" />
        <span className={cn('min-w-0 truncate font-medium', phase === 'failed' ? 'text-danger' : 'text-fg')}>
          {headline(phase, kind, run, awaiting, t)}
        </span>
        {live && current && !expanded && (
          <span className="min-w-0 truncate text-faint">· {t(current.label)}</span>
        )}
        <span className="ml-auto shrink-0 whitespace-nowrap text-sm text-faint tabular-nums">
          {live ? elapsed : phase === 'done' && <Flagged run={run} />}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-line px-3 py-2">
          {steps.length > 0 && (
            <div className="mb-1.5 space-y-1.5 border-b border-line/60 pb-2">
              {steps.map((s) => <StepRow key={s.id} step={s} live={live} />)}
            </div>
          )}
          <StageList
            sessionId={sessionId}
            run={run}
            live={live}
            compact={!rerunnable}
            rerunnable={rerunnable}
            waitingSlot={waitingSlot}
          />
        </div>
      )}
    </div>
  )
}
