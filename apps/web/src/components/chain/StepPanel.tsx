import { Plus, Trash2, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { StepSettings } from '@/components/agents/PipelineEditor'
import { Button, Field, Input, Select, Textarea } from '@/components/ui'
import { ancestors, isCondition, START, type CanvasEdge } from '@/lib/pipelineGraph'
import { agentOutput } from '@/lib/userPipelines'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import type { Agent, ModelInfo, PipelineStep, Skill } from '@/types'
import type { ConditionOp, ConditionStep, GraphCatalogue, GraphProblem, GraphStep } from '@/types/pipelineGraph'
import { kindLabel } from './stepInfo'

/** Skills 항상 적용 at most (the server's limit on active skills). */
export const MAX_ACTIVE = 6

const OP_LABEL: Record<ConditionOp, string> = { gt: '보다 크면', eq: '같으면', empty: '비어 있으면', contains: '들어 있으면' }

const RULE_BRANCHES = [
  { key: 'yes', label: '예' },
  { key: 'no', label: '아니오' },
]

export interface PanelProps {
  id: string
  steps: GraphStep[]
  lines: CanvasEdge[]
  name: (id: string) => string
  /** The agent an agent step runs, with unsaved changes. */
  agentOf: (id: string) => Agent | null
  /** Whether this person may change that agent. */
  canEdit: (agent: Agent) => boolean
  skills: Skill[]
  models: ModelInfo[]
  cat: GraphCatalogue | null
  problems: GraphProblem[]
  onStep: (step: GraphStep) => void
  onAgent: (agentId: string, patch: Partial<Agent>) => void
  onConnect: (source: string, target: string, handle?: string | null) => void
  onRemove: (id: string) => void
  onClose: () => void
}

function Heading({ kind, title, onClose }: { kind: string; title: string; onClose: () => void }) {
  const t = useT()
  return (
    <div className="flex items-start gap-2">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-sm text-muted">{t('선택한 단계')} · {t(kind)}</span>
        <b className="truncate text-lg text-fg">{t(title)}</b>
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label={t('닫기')}
        className="grid size-8 place-items-center rounded-control text-muted hover:bg-elevated focus-visible:outline-2 focus-visible:outline-accent"
      >
        <X size={15} />
      </button>
    </div>
  )
}

/** 「다음 단계」: the keyboard's way to draw a line. */
function NextSelect({ props, handle, label }: { props: PanelProps; handle?: string | null; label: string }) {
  const t = useT()
  const { id, steps, lines, name } = props
  const current = lines.find((l) => l.source === id && (l.sourceHandle ?? null) === (handle ?? null))?.target ?? ''
  return (
    <Field label={t(label)}>
      <Select value={current} onChange={(e) => props.onConnect(id, e.target.value, handle)} data-testid="next-step">
        <option value="">{t('이어지지 않음')}</option>
        {steps
          .filter((s) => s.id !== id)
          .map((s) => (
            <option key={s.id} value={s.id}>
              {t(name(s.id))}
            </option>
          ))}
      </Select>
    </Field>
  )
}

function Flow({ props }: { props: PanelProps }) {
  const t = useT()
  const { id, lines, name } = props
  const from = lines.filter((l) => l.target === id).map((l) => l.source)
  const to = lines.filter((l) => l.source === id).map((l) => l.target)
  const agent = props.agentOf(id)
  const made = agent ? (agentOutput(agent) === 'document' ? '문서' : '답') : '결과'
  return (
    <div className="space-y-1.5 rounded-card bg-elevated/60 p-3 text-sm leading-relaxed" data-testid="step-flow">
      <div>
        <span className="text-muted">{t('받는 것')}</span> ·{' '}
        {from.length
          ? from.map((f) => (f === START ? t('대화의 요청과 첨부') : `${t(name(f))}${t('의 결과')}`)).join(', ')
          : t('없음 — 앞 단계와 이어지지 않았습니다')}
      </div>
      <div>
        <span className="text-muted">{t('넘기는 것')}</span> · {t(made)}
        {to.length ? ` → ${to.map((x) => t(name(x))).join(', ')}` : ` · ${t('마지막 단계')}`}
      </div>
    </div>
  )
}

function Problems({ problems }: { problems: GraphProblem[] }) {
  const t = useT()
  if (!problems.length) return null
  return (
    <ul className="space-y-1 rounded-card border border-warn/40 bg-warn/5 p-3 text-sm text-warn" role="alert">
      {problems.map((p) => (
        <li key={`${p.code}-${p.message}`}>{t(p.message)}</li>
      ))}
    </ul>
  )
}

function RemoveButton({ props }: { props: PanelProps }) {
  const t = useT()
  return (
    <Button type="button" size="sm" variant="ghost" className="self-start text-danger" onClick={() => props.onRemove(props.id)}>
      <Trash2 size={13} />
      {t('단계 빼기')}
    </Button>
  )
}

/** Skills of an agent with 항상 적용 / 필요할 때. */
function AgentSkills({ agent, editable, props }: { agent: Agent; editable: boolean; props: PanelProps }) {
  const t = useT()
  const active = agent.activeSkillIds ?? []
  const allowed = agent.skillIds
  const ids = [...active, ...(allowed ?? []).filter((x) => !active.includes(x))]
  const owned = props.skills.filter((s) => s.enabled && !ids.includes(s.id))
  const setMode = (skillId: string, always: boolean) => {
    const nextActive = always ? [...active.filter((x) => x !== skillId), skillId] : active.filter((x) => x !== skillId)
    // Active skills are in the allowlist too when there is one.
    const nextAllowed = allowed === null ? null : allowed.includes(skillId) ? allowed : [...allowed, skillId]
    props.onAgent(agent.id, { activeSkillIds: nextActive, skillIds: nextAllowed })
  }
  const remove = (skillId: string) =>
    props.onAgent(agent.id, {
      activeSkillIds: active.filter((x) => x !== skillId),
      skillIds: allowed === null ? null : allowed.filter((x) => x !== skillId),
    })
  const add = (skillId: string) => props.onAgent(agent.id, { skillIds: [...(allowed ?? []), skillId] })
  return (
    <div className="space-y-2" data-testid="agent-skills">
      <span className="text-sm text-muted">{t('스킬')}</span>
      {ids.length === 0 && (
        <p className="text-sm text-faint">
          {allowed === null ? t('따로 고르지 않았습니다. 대화에서 고른 스킬을 씁니다.') : t('붙은 스킬이 없습니다.')}
        </p>
      )}
      {ids.map((skillId) => {
        const skill = props.skills.find((s) => s.id === skillId)
        const on = active.includes(skillId)
        const label = skill?.name ?? t('찾을 수 없는 스킬')
        const full = !on && active.length >= MAX_ACTIVE
        return (
          <div key={skillId} className="space-y-1.5 rounded-card border border-line p-2.5" data-testid="agent-skill">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <b className="min-w-0 truncate text-sm">{t(label)}</b>
              <div className="flex items-center gap-1">
                <div role="radiogroup" aria-label={t('{name} 적용 방식').replace('{name}', label)} className="flex gap-0.5 rounded-control bg-elevated p-0.5">
                  {[
                    { always: true, text: '항상 적용' },
                    { always: false, text: '필요할 때' },
                  ].map((o) => (
                    <button
                      key={o.text}
                      type="button"
                      role="radio"
                      aria-checked={on === o.always}
                      disabled={!editable || (o.always && full)}
                      onClick={() => setMode(skillId, o.always)}
                      className={cn(
                        'h-7 rounded-[6px] px-2.5 text-sm focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-45',
                        on === o.always ? 'bg-panel font-medium text-fg shadow-sm' : 'text-muted',
                      )}
                    >
                      {t(o.text)}
                    </button>
                  ))}
                </div>
                {editable && (
                  <button
                    type="button"
                    aria-label={t('{name} 빼기').replace('{name}', label)}
                    onClick={() => remove(skillId)}
                    className="grid size-7 place-items-center rounded-control text-muted hover:bg-elevated focus-visible:outline-2 focus-visible:outline-accent"
                  >
                    <X size={13} />
                  </button>
                )}
              </div>
            </div>
            {full && <p className="text-xs text-faint">{t('항상 적용은 {n}개까지입니다.').replace('{n}', String(MAX_ACTIVE))}</p>}
          </div>
        )
      })}
      <p className="text-xs leading-relaxed text-muted">
        {t('항상 적용: 매 턴 지시에 넣습니다. 필요할 때: 모델이 설명을 보고 고릅니다.')}
      </p>
      {editable && (
        <div className="flex flex-wrap items-center gap-2">
          {owned.length > 0 && (
            <Select
              aria-label={t('+ 스킬 추가')}
              value=""
              onChange={(e) => e.target.value && add(e.target.value)}
              className="h-8 w-auto py-0 text-sm"
            >
              <option value="">{t('+ 스킬 추가')}</option>
              {owned.map((s) => (
                <option key={s.id} value={s.id}>
                  {t(s.name)}
                </option>
              ))}
            </Select>
          )}
          <Link to="/customize?tab=skills&view=explore" className="text-sm text-accent hover:underline">
            {t('스토어에서 찾기')}
          </Link>
        </div>
      )}
    </div>
  )
}

function AgentPanel({ step, props }: { step: PipelineStep; props: PanelProps }) {
  const t = useT()
  const agent = props.agentOf(step.id)
  if (!agent) {
    return (
      <>
        <Heading kind="에이전트" title={props.name(step.id)} onClose={props.onClose} />
        <p className="text-sm text-danger">{t('이 에이전트를 찾을 수 없습니다. 단계를 빼거나 다른 에이전트로 바꾸세요.')}</p>
        <RemoveButton props={props} />
      </>
    )
  }
  const editable = props.canEdit(agent)
  const chat = props.models.filter((m) => m.modality === 'chat')
  return (
    <>
      <Heading kind="에이전트" title={agent.name || props.name(step.id)} onClose={props.onClose} />
      <Problems problems={props.problems} />
      <Field label={t('이름')}>
        <Input
          value={agent.name}
          maxLength={80}
          disabled={!editable}
          onChange={(e) => props.onAgent(agent.id, { name: e.target.value })}
        />
      </Field>
      <Field label={t('지시')} hint={t('이 단계에서만 이 에이전트에게 전합니다.')}>
        <Textarea
          rows={4}
          maxLength={1000}
          value={step.settings.instruction ?? ''}
          onChange={(e) => props.onStep({ ...step, settings: { ...step.settings, instruction: e.target.value } })}
        />
      </Field>
      <Field label={t('모델')}>
        <Select value={agent.model} disabled={!editable} onChange={(e) => props.onAgent(agent.id, { model: e.target.value })}>
          <option value="">{t('기본 모델')}</option>
          {chat.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
          {agent.model && !chat.some((m) => m.id === agent.model) && <option value={agent.model}>{agent.model}</option>}
        </Select>
      </Field>
      <AgentSkills agent={agent} editable={editable} props={props} />
      {!editable && <p className="text-sm text-faint">{t('다른 사람이 공유한 에이전트라 지시만 이 단계에서 바꿀 수 있습니다.')}</p>}
      <Flow props={props} />
      <NextSelect props={props} label="다음 단계" />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link to={`/customize/agents/${agent.id}`} className="text-sm text-accent hover:underline">
          {t('에이전트 전체 설정 열기')}
        </Link>
        <RemoveButton props={props} />
      </div>
    </>
  )
}

function ConditionPanel({ step, props }: { step: ConditionStep; props: PanelProps }) {
  const t = useT()
  const set = (patch: Partial<ConditionStep>) => props.onStep({ ...step, ...patch })
  const before = ancestors(props.lines, step.id)
  const values = props.steps
    .filter((s) => before.has(s.id) && !isCondition(s))
    .flatMap((s) =>
      (props.cat?.conditionValues ?? [])
        .filter((v) => v.stepKind === s.kind)
        .map((v) => ({ value: `${s.id}|${v.key}`, label: `${props.name(s.id)} · ${v.label}` })),
    )
  const rule = step.rule ?? { step: '', value: '', op: 'gt' as ConditionOp, target: '' }
  const ruleKey = rule.step && rule.value ? `${rule.step}|${rule.value}` : ''
  const setMode = (mode: 'rule' | 'ai') =>
    set(
      mode === 'rule'
        ? { mode, branches: RULE_BRANCHES, rule }
        : { mode, branches: step.branches.length >= 2 ? step.branches : RULE_BRANCHES, question: step.question ?? '' },
    )
  const nextKey = () => {
    let n = step.branches.length + 1
    while (step.branches.some((b) => b.key === `b${n}`)) n++
    return `b${n}`
  }
  const modeButton = (mode: 'rule' | 'ai', title: string, note: string) => (
    <button
      type="button"
      role="radio"
      aria-checked={step.mode === mode}
      onClick={() => setMode(mode)}
      className={cn(
        'flex flex-col gap-0.5 rounded-card border p-2.5 text-left focus-visible:outline-2 focus-visible:outline-accent',
        step.mode === mode ? 'border-accent bg-accent-soft/40' : 'border-line',
      )}
    >
      <b className="text-sm">{t(title)}</b>
      <span className="text-xs leading-relaxed text-muted">{t(note)}</span>
    </button>
  )
  return (
    <>
      <Heading kind="조건" title={step.name || '조건'} onClose={props.onClose} />
      <Problems problems={props.problems} />
      <Field label={t('이름')}>
        <Input value={step.name} maxLength={80} onChange={(e) => set({ name: e.target.value })} placeholder={t('예: 실행 과제가 있나?')} />
      </Field>
      <div role="radiogroup" aria-label={t('판단 방식')} className="flex flex-col gap-1.5">
        <span className="text-sm text-muted">{t('판단 방식')}</span>
        {modeButton('rule', '규칙으로', '앞 단계 결과의 값을 비교합니다. 모델을 부르지 않아 늘 같은 갈래로 갑니다.')}
        {modeButton('ai', 'AI 판단으로', '질문을 적으면 판단 모델이 갈래를 고르고 이유를 실행 기록에 남깁니다.')}
      </div>
      {step.mode === 'rule' ? (
        <fieldset className="space-y-2 rounded-card border border-line p-2.5" data-testid="condition-rule">
          <legend className="px-1 text-sm text-muted">{t('규칙')}</legend>
          <Field label={t('값')}>
            <Select
              value={ruleKey}
              onChange={(e) => {
                const [s, v] = e.target.value.split('|')
                set({ rule: { ...rule, step: s ?? '', value: v ?? '' } })
              }}
            >
              <option value="">{values.length ? t('값 고르기') : t('앞 단계에서 읽을 값이 없습니다')}</option>
              {values.map((v) => (
                <option key={v.value} value={v.value}>
                  {t(v.label)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t('비교')}>
            <Select value={rule.op} onChange={(e) => set({ rule: { ...rule, op: e.target.value as ConditionOp } })}>
              {(Object.keys(OP_LABEL) as ConditionOp[]).map((op) => (
                <option key={op} value={op}>
                  {t(OP_LABEL[op])}
                </option>
              ))}
            </Select>
          </Field>
          {rule.op !== 'empty' && (
            <Field label={t('기준')}>
              <Input value={rule.target} maxLength={200} onChange={(e) => set({ rule: { ...rule, target: e.target.value } })} />
            </Field>
          )}
        </fieldset>
      ) : (
        <Field label={t('질문')} hint={t('판단 모델이 앞 단계 결과를 읽고 이 질문에 맞는 갈래를 고릅니다.')}>
          <Textarea rows={3} maxLength={500} value={step.question ?? ''} onChange={(e) => set({ question: e.target.value })} />
        </Field>
      )}
      <div className="space-y-2" data-testid="condition-branches">
        <span className="text-sm text-muted">{t('갈래')}</span>
        {step.branches.map((b) => (
          <div key={b.key} className="space-y-1.5 rounded-card border border-line p-2.5" data-testid="condition-branch">
            <div className="flex items-center gap-2">
              {step.mode === 'ai' ? (
                <Input
                  aria-label={t('갈래 이름')}
                  value={b.label}
                  maxLength={40}
                  className="h-8"
                  onChange={(e) => set({ branches: step.branches.map((x) => (x.key === b.key ? { ...x, label: e.target.value } : x)) })}
                />
              ) : (
                <b className="text-sm">{t(b.label)}</b>
              )}
              {step.mode === 'ai' && step.branches.length > 2 && (
                <button
                  type="button"
                  aria-label={t('{name} 갈래 빼기').replace('{name}', b.label)}
                  onClick={() => set({ branches: step.branches.filter((x) => x.key !== b.key) })}
                  className="grid size-7 shrink-0 place-items-center rounded-control text-muted hover:bg-elevated focus-visible:outline-2 focus-visible:outline-accent"
                >
                  <X size={13} />
                </button>
              )}
            </div>
            <NextSelect props={props} handle={b.key} label="이어지는 단계" />
          </div>
        ))}
        <Button
          type="button"
          size="sm"
          disabled={step.mode === 'rule'}
          onClick={() => set({ branches: [...step.branches, { key: nextKey(), label: t('갈래 {n}').replace('{n}', String(step.branches.length + 1)) }] })}
        >
          <Plus size={13} />
          {t('갈래 추가 (AI 판단에서 여러 갈래)')}
        </Button>
      </div>
      <p className="text-xs leading-relaxed text-muted">
        {t('앞 단계로 되돌아가는 선(반복)은 막습니다. 값이 없으면 「아니오」로 갑니다.')}
      </p>
      <Flow props={props} />
      <RemoveButton props={props} />
    </>
  )
}

function StartPanel({ props }: { props: PanelProps }) {
  const t = useT()
  return (
    <>
      <Heading kind="시작" title="요청 받기" onClose={props.onClose} />
      <p className="text-sm text-muted">{t('대화의 요청과 첨부를 받아 첫 단계로 넘깁니다.')}</p>
      <NextSelect props={props} label="첫 단계" />
    </>
  )
}

function OtherPanel({ step, props }: { step: PipelineStep; props: PanelProps }) {
  return (
    <>
      <Heading kind={kindLabel(step)} title={props.name(step.id)} onClose={props.onClose} />
      <Problems problems={props.problems} />
      <StepSettings step={step} cat={props.cat} onChange={(s) => props.onStep({ ...step, settings: { ...step.settings, ...s } })} />
      <Flow props={props} />
      <NextSelect props={props} label="다음 단계" />
      <RemoveButton props={props} />
    </>
  )
}

/** Right side: the selected step's settings. */
export function StepPanel(props: PanelProps) {
  const step = props.steps.find((s) => s.id === props.id)
  return (
    <div className="flex flex-col gap-4" data-testid="step-panel">
      {props.id === START ? (
        <StartPanel props={props} />
      ) : !step ? null : isCondition(step) ? (
        <ConditionPanel step={step} props={props} />
      ) : step.kind === 'agent' ? (
        <AgentPanel step={step} props={props} />
      ) : (
        <OtherPanel step={step} props={props} />
      )}
    </div>
  )
}
