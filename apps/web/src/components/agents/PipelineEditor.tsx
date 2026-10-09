import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Bot,
  Calculator,
  Copy,
  Download,
  ListTree,
  MessageCircleQuestion,
  Play,
  Plus,
  ScanSearch,
  Send,
  Settings2,
  ShieldCheck,
  Trash2,
  Undo2,
  Workflow,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Badge, Button, Card, ConfirmDialog, EmptyState, Field, Input, LoadingState, Modal, Select, Switch, Textarea } from '@/components/ui'
import { PipelineChain } from '@/components/agents/PipelineChain'
import { errorMessage, userPipelinesApi } from '@/lib/api'
import { useLeaveGuard } from '@/lib/useLeaveGuard'
import { REVIEW_LABEL, REVIEW_TONE, agentOutput } from '@/lib/userPipelines'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type {
  Agent,
  PipelineCatalogue,
  PipelineProblem,
  PipelineStep,
  PipelineStepKind,
  PipelineVerdict,
  UserPipeline,
} from '@/types'

const KIND_ICON: Record<PipelineStepKind, LucideIcon> = {
  agent: Bot,
  skill: Calculator,
  check: ShieldCheck,
  review: ScanSearch,
  human: MessageCircleQuestion,
  export: Download,
}

const STEP_KIND_LABEL: Record<PipelineStepKind, string> = {
  agent: '에이전트',
  skill: '스킬',
  check: '검사',
  review: '검토',
  human: '사람 확인',
  export: '내보내기',
}

const FORMAT_LABEL: Record<string, string> = { docx: 'Word', pdf: 'PDF', hwpx: '한글(HWPX)', md: '마크다운' }

let catalogue: Promise<PipelineCatalogue | null> | null = null
function loadCatalogue() {
  catalogue ??= userPipelinesApi.catalogue().catch(() => {
    catalogue = null
    return null
  })
  return catalogue
}

function useCatalogue() {
  const [value, setValue] = useState<PipelineCatalogue | null>(null)
  useEffect(() => {
    let current = true
    void loadCatalogue().then((c) => current && setValue(c))
    return () => {
      current = false
    }
  }, [])
  return value
}

/** A step's Korean name: the server's, else what the catalogue and agents say. */
function stepTitle(step: PipelineStep, agents: Agent[], cat: PipelineCatalogue | null): string {
  if (step.kind === 'agent') return agents.find((a) => a.id === step.ref)?.name ?? step.title ?? '에이전트'
  if (step.kind === 'skill') return cat?.skills.find((s) => s.ref === step.ref)?.name ?? step.title ?? step.ref
  if (step.kind === 'check') return cat?.checks.find((s) => s.ref === step.ref)?.name ?? step.title ?? step.ref
  if (step.kind === 'human') return cat?.human.find((s) => s.ref === step.ref)?.name ?? step.title ?? '사람 확인'
  return step.kind === 'review' ? '검토' : '내보내기'
}

/** The stored shape of a step, without the server's display fields. */
function bare(steps: PipelineStep[]): PipelineStep[] {
  return steps.map(({ title: _title, output: _output, ...step }) => step)
}

function newId(steps: PipelineStep[]) {
  let n = steps.length + 1
  while (steps.some((s) => s.id === `s${n}`)) n++
  return `s${n}`
}

/** A pipeline's steps as one line of chips: 데이터 분석 → 비교 보고서 → 근거 대조 … */
export function StepChips({ steps, className }: { steps: PipelineStep[]; className?: string }) {
  const t = useT()
  const agents = useStore((s) => s.agents)
  const cat = useCatalogue()
  if (steps.length === 0) return <p className={cn('text-sm text-faint', className)}>{t('단계가 없습니다.')}</p>
  return (
    <p className={cn('flex flex-wrap items-center gap-1 text-sm text-muted', className)} data-testid="step-chips">
      {steps.map((s, i) => {
        const Icon = KIND_ICON[s.kind] ?? Settings2
        return (
          <span key={s.id} className="flex items-center gap-1">
            {i > 0 && <span className="text-faint" aria-hidden>→</span>}
            <span className="flex items-center gap-1 rounded-control bg-elevated px-1.5 py-0.5">
              <Icon size={11} className="text-accent" aria-hidden />
              {t(stepTitle(s, agents, cat))}
            </span>
          </span>
        )
      })}
    </p>
  )
}

/**
 * The agent editor's 「워크플로」 tab: which flow the agent runs, read-only. The choice is applied with
 * the agent's own 「저장」; editing happens on the 「워크플로」 page.
 */
export function AgentFlowSummary({
  agent,
  choice,
  onChoose,
}: {
  agent: Agent
  /** The flow picked here and not yet saved; `undefined` keeps the bound one. */
  choice: string | null | undefined
  onChoose: (pipelineId: string | null) => void
}) {
  const t = useT()
  const user = useStore((s) => s.user)
  const [mine, setMine] = useState<UserPipeline[] | null>(null)
  const owned = agent.ownerId === user?.id
  const output = agentOutput(agent)

  useEffect(() => {
    let current = true
    void userPipelinesApi
      .list()
      .then((rows) => current && setMine(rows))
      .catch(() => current && setMine([]))
    return () => {
      current = false
    }
  }, [agent.id])

  const bound = mine?.find((p) => p.boundAgents.some((a) => a.id === agent.id)) ?? null
  const pickedId = choice === undefined ? bound?.id ?? null : choice
  const picked = (pickedId && mine?.find((p) => p.id === pickedId)) || null
  const fits = (mine ?? []).filter((p) => !p.surface || p.surface === (output === 'document' ? 'report' : 'chat'))
  const editTo = picked ? `/pipelines/${picked.id}` : `/pipelines?tab=shipped&agent=${agent.id}`

  return (
    <div className="space-y-4" data-testid="agent-flow-summary">
      {owned && output && (
        <Field label={t('연결할 워크플로')} hint={t('에이전트를 저장하면 이 워크플로로 일합니다.')}>
          <Select
            aria-label={t('연결할 워크플로')}
            value={pickedId ?? ''}
            disabled={mine === null}
            onChange={(e) => onChoose(e.target.value || null)}
          >
            <option value="">{t('기본 워크플로')}</option>
            {fits.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </Field>
      )}
      <div className="space-y-2 rounded-card border border-line p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="min-w-0 truncate text-base font-medium">{picked ? picked.name : t('기본 워크플로')}</p>
          {picked ? (
            <Badge tone={REVIEW_TONE[picked.review]}>{t(REVIEW_LABEL[picked.review])}</Badge>
          ) : (
            <Badge>{t('잠김')}</Badge>
          )}
        </div>
        {picked ? <StepChips steps={picked.stages} /> : <PipelineChain agentId={agent.id} />}
        {pickedId !== (bound?.id ?? null) && mine !== null && (
          <p className="text-sm text-warn">{t('저장하면 이 워크플로로 바뀝니다.')}</p>
        )}
      </div>
      <Link
        to={editTo}
        className="inline-flex items-center gap-1 text-base font-medium text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent"
      >
        {t('워크플로 편집')}
        <ArrowRight size={14} aria-hidden />
      </Link>
    </div>
  )
}

/** A list-based editor for one of the person's pipelines: steps in order, each with its settings. */
export function PipelineEditor({ pipeline, onSaved }: { pipeline: UserPipeline; onSaved?: (row: UserPipeline) => void }) {
  const t = useT()
  const navigate = useNavigate()
  const agents = useStore((s) => s.agents)
  const loadSessions = useStore((s) => s.loadSessions)
  const cat = useCatalogue()
  const [saved, setSaved] = useState(pipeline)
  const [name, setName] = useState(pipeline.name)
  const [steps, setSteps] = useState<PipelineStep[]>(() => bare(pipeline.stages))
  const [verdict, setVerdict] = useState<PipelineVerdict | null>(
    pipeline.problems ? { ok: pipeline.problems.length === 0, surface: pipeline.surface ?? '', problems: pipeline.problems, warnings: pipeline.warnings ?? [] } : null,
  )
  const [openStep, setOpenStep] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const dirty = name !== saved.name || JSON.stringify(steps) !== JSON.stringify(bare(saved.stages))
  const guard = useLeaveGuard(dirty)

  // Live validation: the server holds every step to its contract.
  useEffect(() => {
    let current = true
    const timer = window.setTimeout(() => {
      void userPipelinesApi
        .validate(steps, saved.id)
        .then((v) => current && setVerdict(v))
        .catch(() => undefined)
    }, 250)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [steps, saved.id])

  const problemsAt = (n: number) => (verdict?.problems ?? []).filter((p) => p.step === n)
  const warningsAt = (n: number) => (verdict?.warnings ?? []).filter((p) => p.step === n)
  const overall = (verdict?.problems ?? []).filter((p) => p.step === 0)
  const max = cat?.maxSteps ?? 12

  const move = (i: number, by: number) =>
    setSteps((rows) => {
      const next = [...rows]
      const [row] = next.splice(i, 1)
      next.splice(i + by, 0, row)
      return next
    })
  const patch = (id: string, settings: PipelineStep['settings']) =>
    setSteps((rows) => rows.map((s) => (s.id === id ? { ...s, settings: { ...s.settings, ...settings } } : s)))
  const add = (step: Omit<PipelineStep, 'id'>) => {
    setSteps((rows) => [...rows, { ...step, id: newId(rows) }])
    setPicking(false)
  }

  const run = async (work: () => Promise<UserPipeline | void>, fallback: string) => {
    setBusy(true)
    setError(null)
    try {
      const out = await work()
      if (out) {
        setSaved(out)
        setName(out.name)
        setSteps(bare(out.stages))
        onSaved?.(out)
      }
    } catch (err) {
      setError(errorMessage(err, t(fallback)))
    } finally {
      setBusy(false)
    }
  }

  const save = () => run(() => userPipelinesApi.update(saved.id, { name, description: saved.description, stages: steps }), '저장하지 못했습니다. 단계를 확인해 주세요.')

  const remove = async () => {
    setBusy(true)
    setError(null)
    try {
      await userPipelinesApi.remove(saved.id)
      guard.release()
      navigate('/pipelines')
    } catch (err) {
      setError(errorMessage(err, t('삭제하지 못했습니다.')))
      setBusy(false)
    }
  }

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const made = await userPipelinesApi.start(saved.id)
      await loadSessions()
      navigate(`/s/${made.sessionId}`)
    } catch (err) {
      setError(errorMessage(err, t('이 워크플로로 시작하지 못했습니다.')))
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3" data-testid="pipeline-editor">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1 basis-64">
          <Field label={t('워크플로 이름')}>
            <Input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>
        <Badge tone={REVIEW_TONE[saved.review]}>{t(REVIEW_LABEL[saved.review])}</Badge>
        {verdict?.surface && (
          <Badge>{verdict.surface === 'report' ? t('문서를 만듭니다') : t('답을 만듭니다')}</Badge>
        )}
      </div>
      {saved.boundAgents.length > 0 && (
        <p className="text-sm text-muted">
          {t('연결된 에이전트')}: {saved.boundAgents.map((a) => t(a.name)).join(', ')}
        </p>
      )}
      {saved.review === 'rejected' && saved.reviewNote && (
        <p className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
          {t('반려 사유')}: {saved.reviewNote}
        </p>
      )}

      <ol className="divide-y divide-line rounded-card border border-line" aria-label={t('단계')}>
        {steps.length === 0 && <li className="px-3 py-4 text-center text-sm text-faint">{t('단계가 없습니다.')}</li>}
        {steps.map((step, i) => {
          const Icon = KIND_ICON[step.kind] ?? Settings2
          const problems = problemsAt(i + 1)
          const warnings = warningsAt(i + 1)
          const title = t(stepTitle(step, agents, cat))
          const isOpen = openStep === step.id
          return (
            <li
              key={step.id}
              className={cn('px-2 py-1.5 sm:px-3', problems.length > 0 && 'bg-danger/5')}
              data-testid="pipeline-step"
            >
              <div className="flex flex-wrap items-center gap-x-2">
                <span className="flex min-w-0 flex-1 basis-24 items-center gap-2">
                  <span className="w-5 shrink-0 text-right text-sm tabular-nums text-faint">{i + 1}</span>
                  <Icon size={14} className="shrink-0 text-accent" aria-hidden />
                  <span className="min-w-0 flex-1 truncate text-base font-medium text-fg" title={title}>
                    {title}
                  </span>
                  <Badge className="max-sm:hidden">{t(STEP_KIND_LABEL[step.kind])}</Badge>
                </span>
                <span className="flex shrink-0 items-center max-sm:ml-auto">
                  <IconButton label={t('위로')} disabled={i === 0} onClick={() => move(i, -1)} icon={ArrowUp} />
                  <IconButton label={t('아래로')} disabled={i === steps.length - 1} onClick={() => move(i, 1)} icon={ArrowDown} />
                  <IconButton
                    label={t('설정')}
                    pressed={isOpen}
                    onClick={() => setOpenStep(isOpen ? null : step.id)}
                    icon={Settings2}
                  />
                  <IconButton
                    label={t('빼기')}
                    onClick={() => setSteps((rows) => rows.filter((s) => s.id !== step.id))}
                    icon={X}
                  />
                </span>
              </div>
              <StepSummary step={step} cat={cat} />
              {[...problems, ...warnings].map((p) => (
                <ProblemLine key={`${p.code}-${p.message}`} problem={p} warn={!problems.includes(p)} />
              ))}
              {isOpen && <StepSettings step={step} cat={cat} onChange={(s) => patch(step.id, s)} />}
            </li>
          )
        })}
      </ol>
      {overall.map((p) => (
        <ProblemLine key={p.code} problem={p} />
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => setPicking(true)} disabled={steps.length >= max}>
          <Plus size={13} />
          {t('단계 추가')}
        </Button>
        <span className="text-sm text-faint">
          {t('{n} / {max}단계').replace('{n}', String(steps.length)).replace('{max}', String(max))}
        </span>
      </div>

      {error && (
        <p role="alert" className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {saved.review === 'approved' && (
        <p className="text-sm text-faint">{t('고쳐서 저장하면 공유가 풀리고 다시 승인을 받아야 합니다.')}</p>
      )}

      {/* One set of actions for the flow, pinned to the bottom of the page. */}
      <div
        className="sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center gap-2 border-t border-line bg-bg/95 px-4 pt-3 max-sm:gap-1 max-sm:px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur sm:-mx-6 sm:px-6"
        role="toolbar"
        aria-label={t('워크플로 작업')}
        data-testid="pipeline-actions"
      >
        <Button variant="primary" size="sm" disabled={busy || !dirty || !verdict?.ok} onClick={() => void save()}>
          {t('저장')}
        </Button>
        {(saved.review === '' || saved.review === 'rejected') && (
          <Button
            size="sm"
            disabled={busy || dirty || !verdict?.ok}
            title={dirty ? t('먼저 저장해 주세요.') : undefined}
            onClick={() => void run(() => userPipelinesApi.submit(saved.id), '공유를 요청하지 못했습니다.')}
          >
            <Send size={13} className="max-sm:hidden" />
            {t('공유 요청')}
          </Button>
        )}
        {(saved.review === 'pending' || saved.review === 'approved') && (
          <Button size="sm" disabled={busy} onClick={() => void run(() => userPipelinesApi.withdraw(saved.id), '공유를 거두지 못했습니다.')}>
            <Undo2 size={13} className="max-sm:hidden" />
            {saved.review === 'pending' ? t('요청 취소') : t('공유 그만두기')}
          </Button>
        )}
        <Button
          size="sm"
          disabled={busy || dirty || !verdict?.ok}
          title={dirty ? t('먼저 저장해 주세요.') : undefined}
          onClick={() => void start()}
        >
          <Play size={13} className="max-sm:hidden" />
          {t('이 워크플로로 시작')}
        </Button>
        {dirty && <span className="text-sm text-warn max-sm:hidden">{t('저장하지 않은 변경이 있습니다.')}</span>}
        <Button size="sm" variant="danger" className="ml-auto" disabled={busy} onClick={() => setConfirmDelete(true)}>
          <Trash2 size={13} />
          {/* Icon alone on a phone, so the bar stays one row. */}
          <span className="max-sm:sr-only">{t('삭제')}</span>
        </Button>
      </div>

      <StepPicker open={picking} onClose={() => setPicking(false)} cat={cat} onPick={add} />
      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => void remove()}
        title={t('{name} 삭제').replace('{name}', saved.name)}
        description={t('이 워크플로를 쓰던 에이전트는 기본 워크플로로 돌아갑니다.')}
      />
      <ConfirmDialog
        open={guard.pending}
        onClose={guard.stay}
        onConfirm={guard.leave}
        title={t('저장하지 않고 나갈까요?')}
        description={t('고친 단계와 이름이 사라집니다.')}
        confirmLabel={t('나가기')}
      />
    </div>
  )
}

function IconButton({
  label,
  icon: Icon,
  onClick,
  disabled,
  pressed,
}: {
  label: string
  icon: LucideIcon
  onClick: () => void
  disabled?: boolean
  pressed?: boolean
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'grid size-8 place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg',
        'focus-visible:outline-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-35',
        pressed && 'bg-elevated text-fg',
      )}
    >
      <Icon size={14} />
    </button>
  )
}

function ProblemLine({ problem, warn = false }: { problem: PipelineProblem; warn?: boolean }) {
  const t = useT()
  return (
    <p className={cn('ml-7 mt-0.5 text-sm', warn ? 'text-warn' : 'text-danger')} role={warn ? undefined : 'alert'}>
      {t(problem.message)}
    </p>
  )
}

function StepSummary({ step, cat }: { step: PipelineStep; cat: PipelineCatalogue | null }) {
  const t = useT()
  const s = step.settings
  let text = ''
  if (step.kind === 'agent' && s.instruction) text = s.instruction
  if (step.kind === 'review') {
    const model = cat?.reviewModels.find((m) => m.id === s.model)?.label ?? s.model ?? ''
    text = [model ? `${t('모델')} ${model}` : t('기본 검토 모델'), s.repair ? t('한 번 고침') : t('지적만')].join(' · ')
  }
  if (step.kind === 'export') text = (s.formats ?? []).map((f) => FORMAT_LABEL[f] ?? f).join(' · ')
  if (!text) return null
  return <p className="ml-7 truncate text-sm text-muted">{t(text)}</p>
}

function StepSettings({
  step,
  cat,
  onChange,
}: {
  step: PipelineStep
  cat: PipelineCatalogue | null
  onChange: (s: PipelineStep['settings']) => void
}) {
  const t = useT()
  const s = step.settings
  const describe =
    step.kind === 'skill'
      ? cat?.skills.find((x) => x.ref === step.ref)?.description
      : step.kind === 'check'
        ? cat?.checks.find((x) => x.ref === step.ref)?.description
        : step.kind === 'human'
          ? cat?.human.find((x) => x.ref === step.ref)?.description
          : ''
  return (
    <div className="mt-2 mb-1 ml-7 space-y-3 rounded-control border border-line bg-elevated/40 p-3" data-testid="step-settings">
      {describe && <p className="text-sm text-muted">{t(describe)}</p>}
      {step.kind === 'agent' && (
        <Field label={t('이 단계에서 할 일')} hint={t('요청에 덧붙여 이 에이전트에게만 전합니다.')}>
          <Textarea
            rows={2}
            maxLength={1000}
            value={s.instruction ?? ''}
            onChange={(e) => onChange({ instruction: e.target.value })}
          />
        </Field>
      )}
      {step.kind === 'review' && (
        <>
          <Field label={t('검토 모델')} hint={t('글을 쓴 모델과 다른 모델을 고르면 더 독립적으로 봅니다.')}>
            <Select value={s.model ?? ''} onChange={(e) => onChange({ model: e.target.value })}>
              <option value="">{t('기본 검토 모델')}</option>
              {(cat?.reviewModels ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t('살펴볼 점')}>
            <Input maxLength={500} value={s.focus ?? ''} onChange={(e) => onChange({ focus: e.target.value })} />
          </Field>
          <div className="flex items-center justify-between gap-2">
            <span className="text-base">{t('지적한 곳을 한 번 고치기')}</span>
            <Switch label={t('지적한 곳을 한 번 고치기')} checked={!!s.repair} onChange={(v) => onChange({ repair: v })} />
          </div>
        </>
      )}
      {step.kind === 'export' && (
        <fieldset className="flex flex-wrap gap-3">
          <legend className="mb-1 text-base font-medium">{t('파일 형식')}</legend>
          {(cat?.exportFormats ?? ['docx', 'pdf', 'hwpx', 'md']).map((f) => {
            const on = (s.formats ?? []).includes(f)
            return (
              <label key={f} className="flex items-center gap-1.5 text-base">
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() =>
                    onChange({ formats: on ? (s.formats ?? []).filter((x) => x !== f) : [...(s.formats ?? []), f] })
                  }
                />
                {t(FORMAT_LABEL[f] ?? f)}
              </label>
            )
          })}
        </fieldset>
      )}
    </div>
  )
}

/** 「+ 단계 추가」: every kind of step, grouped. */
function StepPicker({
  open,
  onClose,
  cat,
  onPick,
}: {
  open: boolean
  onClose: () => void
  cat: PipelineCatalogue | null
  onPick: (step: Omit<PipelineStep, 'id'>) => void
}) {
  const t = useT()
  const agents = useStore((s) => s.agents)
  const user = useStore((s) => s.user)
  const runnable = useMemo(
    () => agents.filter((a) => agentOutput(a) && (a.ownerId === user?.id || a.visibility === 'org')),
    [agents, user?.id],
  )
  const groups: { label: string; icon: LucideIcon; items: { key: string; name: string; note: string; step: Omit<PipelineStep, 'id'> }[] }[] = [
    {
      label: '에이전트',
      icon: Bot,
      items: runnable.map((a) => ({
        key: a.id,
        name: a.name,
        note: agentOutput(a) === 'document' ? '문서를 씁니다' : '답을 합니다',
        step: { kind: 'agent', ref: a.id, settings: {} },
      })),
    },
    {
      label: '스킬',
      icon: Calculator,
      items: (cat?.skills ?? []).map((s) => ({ key: s.ref, name: s.name, note: s.summary, step: { kind: 'skill', ref: s.ref, settings: {} } })),
    },
    {
      label: '검사',
      icon: ShieldCheck,
      items: (cat?.checks ?? []).map((s) => ({ key: s.ref, name: s.name, note: s.description, step: { kind: 'check', ref: s.ref, settings: {} } })),
    },
    {
      label: '검토',
      icon: ScanSearch,
      items: [{ key: 'review', name: '검토', note: '다른 모델이 문서와 자료를 읽고 고칠 곳을 짚습니다.', step: { kind: 'review', ref: '', settings: { model: '', repair: false } } }],
    },
    {
      label: '사람 확인',
      icon: ListTree,
      items: (cat?.human ?? []).map((s) => ({ key: s.ref, name: s.name, note: s.description, step: { kind: 'human', ref: s.ref, settings: {} } })),
    },
    {
      label: '내보내기',
      icon: Download,
      items: [{ key: 'export', name: '내보내기', note: '저장한 문서를 파일로 건넵니다.', step: { kind: 'export', ref: '', settings: { formats: ['docx'] } } }],
    },
  ]
  return (
    <Modal open={open} onClose={onClose} title={t('단계 추가')} width="max-w-xl">
      <div className="space-y-4">
        {groups.map((g) => (
          <section key={g.label} aria-label={t(g.label)}>
            <h3 className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-muted">
              <g.icon size={13} className="text-accent" aria-hidden />
              {t(g.label)}
            </h3>
            {g.items.length === 0 ? (
              <p className="text-sm text-faint">{t('넣을 수 있는 것이 없습니다.')}</p>
            ) : (
              <ul className="grid gap-1.5 sm:grid-cols-2">
                {g.items.map((item) => (
                  <li key={item.key}>
                    <button
                      type="button"
                      onClick={() => onPick(item.step)}
                      className="w-full rounded-control border border-line px-3 py-2 text-left hover:border-accent hover:bg-elevated focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      <span className="block text-base font-medium text-fg">{t(item.name)}</span>
                      <span className="line-clamp-2 block text-sm text-muted">{t(item.note)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </div>
    </Modal>
  )
}

/** The store's shared pipelines: bring one into your own, or start a conversation with it. */
export function PipelineStore({ onImported }: { onImported?: () => void } = {}) {
  const t = useT()
  const navigate = useNavigate()
  const loadSessions = useStore((s) => s.loadSessions)
  const setNotice = useStore((s) => s.setNotice)
  const [rows, setRows] = useState<UserPipeline[] | null>(null)
  const reload = useCallback(() => userPipelinesApi.store().then(setRows).catch(() => setRows([])), [])
  useEffect(() => {
    void reload()
  }, [reload])
  if (rows === null) return <LoadingState />
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={<Workflow size={18} />}
        title={t('공유된 워크플로가 없습니다')}
        description={t('내 워크플로를 조직에 공유 요청하면 관리자 승인 뒤 여기에 올라옵니다.')}
      />
    )
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2" aria-label={t('공유된 워크플로')}>
      {rows.map((p) => (
        <Card key={p.id} className="space-y-2 p-4" data-testid="shared-pipeline">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-base font-semibold">{p.name}</p>
              <p className="text-sm text-faint">
                {p.ownerName} · {t('{n}번 가져감').replace('{n}', String(p.installs))}
              </p>
            </div>
            {p.installed && <Badge tone="success">{t('가져옴')}</Badge>}
          </div>
          {p.description && <p className="text-sm text-muted">{p.description}</p>}
          <StepChips steps={p.stages} />
          <div className="flex flex-wrap gap-2">
            {!p.mine && (
              <Button
                size="sm"
                onClick={async () => {
                  try {
                    await userPipelinesApi.create({ fromId: p.id })
                    setNotice(t('내 워크플로로 가져왔습니다.'))
                    void reload()
                    onImported?.()
                  } catch (err) {
                    setNotice(errorMessage(err, t('워크플로를 가져오지 못했습니다.')))
                  }
                }}
              >
                <Copy size={13} />
                {t('가져오기')}
              </Button>
            )}
            <Button
              size="sm"
              onClick={async () => {
                try {
                  const made = await userPipelinesApi.start(p.id)
                  await loadSessions()
                  navigate(`/s/${made.sessionId}`)
                } catch (err) {
                  setNotice(errorMessage(err, t('이 워크플로로 시작하지 못했습니다.')))
                }
              }}
            >
              <Play size={13} />
              {t('이 워크플로로 시작')}
            </Button>
          </div>
        </Card>
      ))}
    </div>
  )
}

/** The administrator's queue of pipelines asking to be shared. */
export function PipelineApprovals() {
  const t = useT()
  const [rows, setRows] = useState<UserPipeline[] | null>(null)
  const [rejecting, setRejecting] = useState<UserPipeline | null>(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const reload = useCallback(() => userPipelinesApi.adminList().then(setRows).catch(() => setRows([])), [])
  useEffect(() => {
    void reload()
  }, [reload])
  return (
    <section className="space-y-2 pt-8" aria-label={t('워크플로 공유 승인')}>
      <h2 className="text-lg font-semibold">{t('워크플로 공유 승인')}</h2>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {rows !== null && rows.length === 0 ? (
        <EmptyState icon={<ListTree size={18} />} title={t('승인을 기다리는 워크플로가 없습니다')} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {(rows ?? []).map((p) => (
            <Card key={p.id} className="space-y-2 p-4" data-testid="pipeline-approval">
              <p className="text-base font-semibold">{p.name}</p>
              <p className="text-sm text-faint">{p.ownerName}</p>
              <ol className="list-decimal space-y-0.5 pl-5 text-sm text-muted">
                {p.stages.map((s) => (
                  <li key={s.id}>
                    {t(STEP_KIND_LABEL[s.kind])} · {t(s.title ?? s.ref)}
                    {s.kind === 'review' && s.settings.model ? ` (${s.settings.model})` : ''}
                  </li>
                ))}
              </ol>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="primary"
                  onClick={async () => {
                    try {
                      await userPipelinesApi.approve(p.id)
                      void reload()
                    } catch (err) {
                      setError(errorMessage(err, t('승인하지 못했습니다.')))
                    }
                  }}
                >
                  {t('승인')}
                </Button>
                <Button
                  size="sm"
                  onClick={() => {
                    setNote('')
                    setRejecting(p)
                  }}
                >
                  {t('반려')}
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      <Modal
        open={!!rejecting}
        onClose={() => setRejecting(null)}
        title={t('반려 사유')}
        footer={
          <>
            <Button onClick={() => setRejecting(null)}>{t('취소')}</Button>
            <Button
              variant="primary"
              disabled={!note.trim()}
              onClick={async () => {
                if (!rejecting) return
                try {
                  await userPipelinesApi.reject(rejecting.id, note)
                  setRejecting(null)
                  void reload()
                } catch (err) {
                  setError(errorMessage(err, t('반려하지 못했습니다.')))
                }
              }}
            >
              {t('반려')}
            </Button>
          </>
        }
      >
        <Field label={t('반려 사유')} hint={t('만든 사람에게 그대로 보입니다.')}>
          <Textarea rows={3} maxLength={400} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </Modal>
    </section>
  )
}
