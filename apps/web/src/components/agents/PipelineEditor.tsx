import {
  ArrowDown,
  ArrowUp,
  Bot,
  Calculator,
  Download,
  ListTree,
  MessageCircleQuestion,
  Plus,
  ScanSearch,
  Settings2,
  ShieldCheck,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Badge, Button, Card, EmptyState, Field, Input, Modal, Select, Switch, Textarea } from '@/components/ui'
import { errorMessage, userPipelinesApi } from '@/lib/api'
import { agentOutput } from '@/lib/userPipelines'
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
import { SELF } from '@/lib/process'

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
 * The steps of an agent's 직접 구성 or of a 묶음, in order, each with its settings. Controlled:
 * the caller saves. `selfName` offers the agent's own step (`@self`) in 「+ 단계 추가」.
 */
export function StepList({
  steps,
  onChange,
  verdict,
  selfName,
}: {
  steps: PipelineStep[]
  onChange: (next: PipelineStep[]) => void
  /** The server's verdict on this order; none for a bundle, which is checked on save. */
  verdict?: PipelineVerdict | null
  selfName?: string
}) {
  const t = useT()
  const agents = useStore((s) => s.agents)
  const cat = useCatalogue()
  const [openStep, setOpenStep] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)
  const problemsAt = (n: number) => (verdict?.problems ?? []).filter((p) => p.step === n)
  const warningsAt = (n: number) => (verdict?.warnings ?? []).filter((p) => p.step === n)
  const overall = (verdict?.problems ?? []).filter((p) => p.step === 0)
  const max = cat?.maxSteps ?? 12

  const move = (i: number, by: number) => {
    const next = [...steps]
    const [row] = next.splice(i, 1)
    next.splice(i + by, 0, row)
    onChange(next)
  }
  const patch = (id: string, settings: PipelineStep['settings']) =>
    onChange(steps.map((s) => (s.id === id ? { ...s, settings: { ...s.settings, ...settings } } : s)))
  const add = (step: Omit<PipelineStep, 'id'>) => {
    onChange([...steps, { ...step, id: newId(steps) }])
    setPicking(false)
  }

  return (
    <div className="space-y-3" data-testid="step-list">
      <ol className="divide-y divide-line rounded-card border border-line" aria-label={t('단계')}>
        {steps.length === 0 && <li className="px-3 py-4 text-center text-sm text-faint">{t('단계가 없습니다.')}</li>}
        {steps.map((step, i) => {
          const Icon = KIND_ICON[step.kind] ?? Settings2
          const problems = problemsAt(i + 1)
          const warnings = warningsAt(i + 1)
          const title = step.kind === 'agent' && step.ref === SELF ? selfName || t('이 에이전트') : t(stepTitle(step, agents, cat))
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
                  <IconButton label={t('빼기')} onClick={() => onChange(steps.filter((s) => s.id !== step.id))} icon={X} />
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
        <Button type="button" size="sm" onClick={() => setPicking(true)} disabled={steps.length >= max}>
          <Plus size={13} />
          {t('단계 추가')}
        </Button>
        <span className="text-sm text-faint">
          {t('{n} / {max}단계').replace('{n}', String(steps.length)).replace('{max}', String(max))}
        </span>
      </div>
      <StepPicker open={picking} onClose={() => setPicking(false)} cat={cat} onPick={add} selfName={selfName} />
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
  selfName,
}: {
  open: boolean
  onClose: () => void
  cat: PipelineCatalogue | null
  onPick: (step: Omit<PipelineStep, 'id'>) => void
  selfName?: string
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
      items: [
        ...(selfName !== undefined
          ? [{ key: SELF, name: selfName || '이 에이전트', note: '이 에이전트가 기본 과정대로 씁니다', step: { kind: 'agent' as const, ref: SELF, settings: {} } }]
          : []),
        ...runnable.map((a) => ({
        key: a.id,
        name: a.name,
        note: agentOutput(a) === 'document' ? '문서를 씁니다' : '답을 합니다',
        step: { kind: 'agent' as const, ref: a.id, settings: {} },
      })),
      ],
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

/** What a review request is: a person's 묶음, an agent's 직접 구성, or an order made before. */
function reviewKind(p: UserPipeline): string {
  if (p.kind === 'bundle') return '묶음'
  if (p.agentId) return '에이전트의 직접 구성'
  return '예전에 만든 순서'
}

/** 「진행 과정 검토」: the administrator's queue of 직접 구성 and 묶음 asking to be shared. */
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
    <section className="space-y-2 pt-8" aria-label={t('진행 과정 검토')}>
      <h2 className="text-lg font-semibold">{t('진행 과정 검토')}</h2>
      <p className="text-sm text-muted">
        {t('파일로 내보내거나, 다른 모델이 검토하거나, 다른 에이전트를 부르는 직접 구성과 묶음은 승인한 뒤에야 다른 사람이 씁니다.')}
      </p>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {rows !== null && rows.length === 0 ? (
        <EmptyState icon={<ListTree size={18} />} title={t('검토를 기다리는 진행 과정이 없습니다')} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {(rows ?? []).map((p) => (
            <Card key={p.id} className="space-y-2 p-4" data-testid="pipeline-approval">
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 text-base font-semibold">{t(p.name)}</p>
                <Badge>{t(reviewKind(p))}</Badge>
              </div>
              <p className="text-sm text-faint">{p.ownerName}</p>
              <ol className="list-decimal space-y-0.5 pl-5 text-sm text-muted">
                {p.stages.map((s) => (
                  <li key={s.id}>
                    {t(STEP_KIND_LABEL[s.kind])} · {s.kind === 'agent' && s.ref === SELF ? t('이 에이전트') : t(s.title ?? s.ref)}
                    {s.kind === 'review' && s.settings.model ? ` (${s.settings.model})` : ''}
                    {s.kind === 'export' && s.settings.formats?.length ? ` (${s.settings.formats.join(', ')})` : ''}
                  </li>
                ))}
              </ol>
              <div className="flex flex-wrap gap-2">
                <Button type="button"
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
                <Button type="button"
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
            <Button type="button" onClick={() => setRejecting(null)}>{t('취소')}</Button>
            <Button type="button"
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
