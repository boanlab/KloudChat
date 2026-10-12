import { Check, ChevronRight, CircleAlert, CircleHelp, EllipsisVertical, Loader2, Minus, RotateCcw, X } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { Button, Dropdown, MenuItem, MenuLabel } from '@/components/ui'
import { ApiError, runsApi, type RunRow } from '@/lib/api'
import { FINDING_LABEL, RERUN_ERROR, TONE_LABEL, hasEvidence, notApplicable, stageLabel, stageReason, stageSummary, stageTone, type StageTone } from '@/lib/stages'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { PipelineRun, PipelineStage, StageEvidence } from '@/types'

/** Full runs fetched once per page load; a run that cannot be read is remembered as such. */
const fetched = new Map<string, Promise<RunRow | null>>()

function fullRun(sessionId: string, runId: string): Promise<RunRow | null> {
  const key = `${sessionId}:${runId}`
  let found = fetched.get(key)
  if (!found) {
    found = runsApi.get(sessionId, runId).catch(() => null)
    fetched.set(key, found)
  }
  return found
}

const TONE_ICON: Record<StageTone, ReactNode> = {
  done: <Check size={12} strokeWidth={2.5} />,
  findings: <CircleAlert size={12} strokeWidth={2.5} />,
  waiting: <CircleHelp size={12} strokeWidth={2.5} />,
  skipped: <Minus size={12} strokeWidth={2.5} />,
  failed: <X size={12} strokeWidth={2.5} />,
}

const TONE_CLASS: Record<StageTone, string> = {
  done: 'bg-success/12 text-success',
  findings: 'bg-warn/15 text-warn',
  waiting: 'bg-accent-soft text-accent',
  skipped: 'bg-elevated text-faint',
  failed: 'bg-danger/12 text-danger',
}

/** ✓ ! ? – ✕ in a dot, with its meaning for screen readers and on hover. */
export function StageIcon({ tone, size = 'md' }: { tone: StageTone; size?: 'sm' | 'md' }) {
  const t = useT()
  return (
    <span
      role="img"
      aria-label={t(TONE_LABEL[tone])}
      title={t(TONE_LABEL[tone])}
      className={cn(
        'grid shrink-0 place-items-center rounded-full',
        size === 'sm' ? 'size-4' : 'size-5',
        TONE_CLASS[tone],
      )}
    >
      {TONE_ICON[tone]}
    </span>
  )
}

/** Code and what it printed, collapsed; shared with tool steps. */
export function TraceBlock({ summary, note, text }: { summary: string; note?: string; text: string }) {
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

/** One stage's 근거: computed values, findings before → after, changed text, code. */
function Evidence({ evidence }: { evidence: StageEvidence }) {
  const t = useT()
  const before = evidence.findings?.before ?? []
  const after = evidence.findings?.after ?? []
  const finding = (code: string) => t(FINDING_LABEL[code] ?? code)
  return (
    <div className="space-y-2.5 text-sm">
      {evidence.computed && evidence.computed.length > 0 && (
        <div>
          <p className="mb-1 font-medium text-muted">{t('정한 값')}</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            {evidence.computed.map((row, i) => (
              <div key={`${i}-${row.name}`} className="contents">
                <dt className="text-muted">{row.name}</dt>
                <dd className="font-medium tabular-nums text-fg">{row.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      {evidence.computedBlock && <TraceBlock summary={t('계산 기록 보기')} text={evidence.computedBlock} />}
      {(before.length > 0 || after.length > 0) && (
        <div>
          <p className="mb-1 font-medium text-muted">
            {t('검사 결과 {before}건 → {after}건')
              .replace('{before}', String(before.length))
              .replace('{after}', String(after.length))}
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            {before.map((code, i) => (
              <span
                key={`b${i}`}
                className={cn(
                  'rounded-control border border-line px-1.5 py-0.5 text-xs',
                  // Fixed by the stage: struck through; still there: kept plain.
                  !after.includes(code) && 'text-faint line-through',
                )}
              >
                {finding(code)}
              </span>
            ))}
            {after.length > 0 && <span className="text-faint" aria-hidden>→</span>}
            {after.map((code, i) => (
              <span key={`a${i}`} className="rounded-control border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-xs text-warn">
                {finding(code)}
              </span>
            ))}
            {before.length > 0 && after.length === 0 && (
              <span className="text-xs text-success">{t('모두 고침')}</span>
            )}
          </div>
        </div>
      )}
      {evidence.changes && evidence.changes.length > 0 && (
        <div>
          <p className="mb-1 font-medium text-muted">{t('바꾼 곳 {n}개').replace('{n}', String(evidence.changes.length))}</p>
          <ul className="space-y-1">
            {evidence.changes.map((change, i) => (
              <li key={i} className="rounded-control border border-line bg-panel px-2 py-1">
                {change.heading && <span className="mr-1.5 text-xs text-faint">{change.heading}</span>}
                <span className="sr-only">{t('바꾸기 전')}</span>
                <del className="text-danger decoration-danger/60">{change.old}</del>
                <span className="mx-1.5 text-faint" aria-hidden>→</span>
                <span className="sr-only">{t('바꾼 뒤')}</span>
                <ins className="text-success no-underline">{change.new}</ins>
              </li>
            ))}
          </ul>
        </div>
      )}
      {evidence.code?.map((run, i) => (
        <div key={i} className="space-y-1">
          <TraceBlock summary={t('코드 보기')} text={run.code} />
          {(run.stdout || run.stderr) && (
            <TraceBlock
              summary={t('출력 보기')}
              text={[run.stdout, run.stderr && `stderr:\n${run.stderr}`].filter(Boolean).join('\n\n')}
            />
          )}
        </div>
      ))}
      {evidence.questions && evidence.questions.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-muted">
          {evidence.questions.map((q) => <li key={q.id}>{q.label || q.question}</li>)}
        </ul>
      )}
    </div>
  )
}

/** How long a stage took, in seconds; nothing for one that took no measurable time (「0ms」). */
function seconds(ms?: number) {
  if (ms === undefined || ms < 1) return null
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`
}

function StageRow({
  stage,
  sessionId,
  runId,
  canRerun,
  busy,
  compact,
  waitingSlot,
  loadEvidence,
}: {
  stage: PipelineStage
  sessionId: string
  runId: string
  canRerun: boolean
  busy: boolean
  compact: boolean
  waitingSlot?: ReactNode
  /** The stored record's evidence, read when 「보기」 is first opened. */
  loadEvidence: () => Promise<StageEvidence | undefined>
}) {
  const t = useT()
  const rerunStage = useStore((s) => s.rerunStage)
  const [open, setOpen] = useState(false)
  const [fetchedEvidence, setEvidence] = useState<StageEvidence | undefined>()
  const evidence = stage.evidence ?? fetchedEvidence
  const [loading, setLoading] = useState(false)
  const [missing, setMissing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const panelId = useId()

  const shown = evidence ? { ...stage, evidence } : stage
  // Read with the stored evidence once it is fetched: a check that left findings turns to 「!」.
  const tone = stageTone(shown)
  const viewable = hasEvidence(shown) || !!stage.reason || (!stage.evidence && !missing)
  const toggle = async () => {
    const next = !open
    setOpen(next)
    if (!next || evidence) return
    setLoading(true)
    const found = await loadEvidence()
    setLoading(false)
    if (found) setEvidence(found)
    else setMissing(true)
  }
  const rerun = async () => {
    setConfirming(false)
    setError(null)
    try {
      await rerunStage(sessionId, runId, { name: stage.name, label: stage.label })
    } catch (err) {
      const code = err instanceof ApiError ? err.detail : ''
      setError(t(RERUN_ERROR[code] ?? '다시 만들지 못했습니다. 잠시 뒤 다시 시도해 주세요.'))
    }
  }
  // The server's reason is internal English: the row says it in Korean; the raw text is in 「보기」.
  const why = tone === 'skipped' || tone === 'failed' || stage.status === 'stopped' || (tone === 'waiting' && !stage.summary)
    ? t(stageReason(stage))
    : ''
  const line = [stageSummary(stage), why].filter(Boolean).join(' · ')

  return (
    <li className="py-1" data-stage={stage.name} data-stage-status={stage.status}>
      <div className="flex min-w-0 items-start gap-2">
        <span className="mt-px"><StageIcon tone={tone} size={compact ? 'sm' : 'md'} /></span>
        <div className="min-w-0 flex-1">
          <p className={cn('flex min-w-0 flex-wrap items-baseline gap-x-1.5', compact ? 'text-sm' : 'text-base')}>
            <span className={cn('font-medium', tone === 'skipped' ? 'text-faint' : 'text-fg')}>{t(stageLabel(stage))}</span>
            {(stage.attempt ?? 1) > 1 && (
              <span className="text-xs text-faint">{t('{n}번째').replace('{n}', String(stage.attempt))}</span>
            )}
            {line && (
              <span
                title={stage.reason || undefined}
                className={cn('min-w-0 [overflow-wrap:anywhere]', tone === 'failed' ? 'text-danger' : tone === 'skipped' ? 'text-faint' : 'text-muted')}
              >
                {line}
              </span>
            )}
          </p>
        </div>
        {!compact && tone !== 'skipped' && seconds(stage.ms) && (
          <span className="mt-0.5 shrink-0 text-xs tabular-nums text-faint max-sm:hidden">{seconds(stage.ms)}</span>
        )}
        {viewable && (tone !== 'skipped' || !!stage.reason) && (
          <button
            type="button"
            onClick={() => void toggle()}
            aria-expanded={open}
            aria-controls={panelId}
            aria-label={t('{stage} 근거 보기').replace('{stage}', t(stageLabel(stage)))}
            className="shrink-0 rounded-control px-1.5 py-0.5 text-sm text-accent hover:bg-elevated focus-visible:outline-2 focus-visible:outline-accent"
          >
            {open ? t('접기') : t('보기')}
          </button>
        )}
        {canRerun && stage.rerunnable && (
          <Dropdown
            align="right"
            trigger={() => (
              <Button
                variant="ghost"
                size="icon"
                disabled={busy}
                aria-label={t('{stage} 단계 메뉴').replace('{stage}', t(stageLabel(stage)))}
                className="-my-1 shrink-0"
              >
                <EllipsisVertical size={14} />
              </Button>
            )}
          >
            <MenuLabel>{t(stageLabel(stage))}</MenuLabel>
            <MenuItem icon={<RotateCcw size={13} />} onClick={() => setConfirming(true)}>
              {t('이 단계부터 다시')}
            </MenuItem>
          </Dropdown>
        )}
      </div>
      {confirming && (
        <div role="group" aria-label={t('이 단계부터 다시')} className="mt-1.5 ml-7 max-sm:ml-0 rounded-control border border-line bg-panel px-3 py-2 text-sm">
          <p className="text-fg">
            {t('「{stage}」 단계부터 다시 만듭니다. 이 작업 뒤에 직접 고친 내용은 새 판에 들어가지 않고, 지금 판은 버전 기록에 남습니다.').replace('{stage}', t(stageLabel(stage)))}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant="primary" autoFocus onClick={() => void rerun()}>
              <RotateCcw size={13} />
              {t('다시 만들기')}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>{t('취소')}</Button>
          </div>
        </div>
      )}
      {error && <p role="alert" className="mt-1 ml-7 max-sm:ml-0 text-sm text-danger">{error}</p>}
      {open && (
        <div id={panelId} className="mt-1.5 ml-7 max-sm:ml-0 rounded-control border border-line bg-panel/60 px-3 py-2">
          {loading ? (
            <p className="flex items-center gap-1.5 text-sm text-faint"><Loader2 size={12} className="animate-spin" />{t('불러오는 중…')}</p>
          ) : evidence && hasEvidence(shown) ? (
            <Evidence evidence={evidence} />
          ) : !stage.reason ? (
            <p className="text-sm text-faint">{t('이 단계는 따로 남긴 근거가 없습니다.')}</p>
          ) : null}
          {!loading && stage.reason && (
            <p className="mt-1.5 text-xs text-faint">
              {t('기록된 사유')}: <code className="font-mono [overflow-wrap:anywhere]">{stage.reason}</code>
            </p>
          )}
        </div>
      )}
      {waitingSlot && tone === 'waiting' && <div className="mt-2">{waitingSlot}</div>}
    </li>
  )
}

/** A run's stages, one row each; `compact` = chat form (no rerun menu). Skipped stages fold behind one line. */
export function StageList({
  sessionId,
  run,
  live,
  compact = false,
  rerunnable = false,
  waitingSlot,
}: {
  sessionId: string
  run: PipelineRun
  live: boolean
  compact?: boolean
  /** The surface can rerun (report, slides): rows offer 「이 단계부터 다시」. */
  rerunnable?: boolean
  /** The question card, shown under the waiting stage. */
  waitingSlot?: ReactNode
}) {
  const t = useT()
  const busy = useStore((s) => !!s.running[sessionId])
  const [showSkipped, setShowSkipped] = useState(false)
  const stages = run.stages
  const skipped = stages.filter((s) => stageTone(s) === 'skipped')
  const shown = showSkipped ? stages : stages.filter((s) => stageTone(s) !== 'skipped')
  if (stages.length === 0) return null
  const loadEvidence = (stage: PipelineStage) => async () => {
    if (live || !run.id) return undefined
    const full = await fullRun(sessionId, run.id)
    return full?.stages.find((s) => s.id === stage.id || s.name === stage.name)?.evidence
  }
  return (
    <div data-testid="stage-list" data-run={run.id}>
      <ol aria-label={t('작업 단계')} className={cn('divide-y divide-line/60', compact && 'divide-y-0')}>
        {shown.map((stage) => (
          <StageRow
            key={stage.id || stage.name}
            stage={stage}
            sessionId={sessionId}
            runId={run.id}
            canRerun={rerunnable && !compact && !live}
            busy={busy}
            compact={compact}
            waitingSlot={waitingSlot}
            loadEvidence={loadEvidence(stage)}
          />
        ))}
      </ol>
      {skipped.length > 0 && (
        <button
          type="button"
          onClick={() => setShowSkipped((v) => !v)}
          aria-expanded={showSkipped}
          className="mt-1 flex items-center gap-1 rounded-control px-1 text-sm text-faint hover:text-fg"
        >
          <ChevronRight size={12} className={cn('transition-transform', showSkipped && 'rotate-90')} />
          {/* Most skipped rows were simply not needed; a stop or a cancel leaves them unrun. */}
          {skipped.every(notApplicable)
            ? showSkipped
              ? t('해당 없는 단계 숨기기')
              : t('해당 없는 단계 {n}개').replace('{n}', String(skipped.length))
            : showSkipped
              ? t('실행하지 않은 단계 숨기기')
              : t('실행하지 않은 단계 {n}개').replace('{n}', String(skipped.length))}
        </button>
      )}
    </div>
  )
}
