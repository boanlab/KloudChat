import {
  Brain,
  Check,
  ChevronRight,
  CircleMinus,
  Download,
  FileText,
  Loader2,
  TriangleAlert,
  Wrench,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn, fileSize } from '@/lib/utils'
import type { Step } from '@/types'
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

const icons = { thinking: Brain, tool: Wrench, artifact: FileText }
const kindLabel = { thinking: '추론', tool: '도구 사용', artifact: '산출물' }
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

function TraceBlock({ summary, note, text }: { summary: string; note?: string; text: string }) {
  return (
    <details className="group text-sm">
      <summary className="flex cursor-pointer list-none items-center gap-1 text-faint hover:text-fg">
        <ChevronRight size={12} className="shrink-0 transition-transform group-open:rotate-90" />
        {summary}
        {note && <span className="text-faint">· {note}</span>}
      </summary>
      <pre className="mt-1 max-h-80 overflow-auto whitespace-pre rounded-control border border-line bg-panel px-2.5 py-2 font-mono text-xs leading-relaxed text-fg">
        {text}
      </pre>
    </details>
  )
}

/** Download chips for the files a tool step made. */
function OutputFiles({ files }: { files: NonNullable<Step['outputs']> }) {
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
}: {
  steps: Step[]
  live: boolean
  /** The turn's end state, not a recoverable error in an individual tool call. */
  failed: boolean
  /** Epoch ms the turn started; drives the clock. */
  startedAt?: number
}) {
  const t = useT()
  const elapsed = useElapsed(startedAt, live)
  // The reader's toggle wins over the liveness default.
  const [manual, setManual] = useState<boolean | null>(null)
  if (steps.length === 0) return null

  const done = steps.filter((s) => s.status === 'done').length
  const current = steps.find((s) => s.status === 'running')
  const expanded = manual ?? live
  // Steps remaining, from the running step's own denominator.
  const left = current?.progress ? current.progress.total - current.progress.current : null
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
        {/* Expanded, the step itself is highlighted below, so the header names the kind of work. */}
        <span className="min-w-0 truncate text-fg">
          {expanded ? t(kindLabel[head.type]) : t(head.label)}
        </span>
        {!expanded && rest.length > 0 && (
          <span className="min-w-0 truncate text-faint">
            · {rest.slice(0, COLLAPSED_NAMES).map((s) => t(s.label)).join(' · ')}
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
              ? done === 1
                ? t('1단계 완료')
                : t('{n}단계 완료').replace('{n}', String(done))
              : steps.length === 1
                ? t('1단계')
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
