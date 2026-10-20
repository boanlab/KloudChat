import { Handle, Position, type NodeProps } from '@xyflow/react'
import { createElement, useContext } from 'react'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { isCondition } from '@/lib/pipelineGraph'
import type { Agent, Skill } from '@/types'
import { ChainContext } from './flow'
import { RUN_FRAME, RUN_LABEL, RUN_TEXT, kindIcon, kindLabel, kindTone, problemText } from './stepInfo'

function useView() {
  const view = useContext(ChainContext)
  if (!view) throw new Error('ChainContext missing')
  return view
}

const handleClass = '!size-3 !border-2 !border-panel !bg-line-strong'

/** 「!」 on a step the verdict names. */
function ProblemBadge({ count }: { count: number }) {
  const t = useT()
  if (!count) return null
  return (
    <span
      className="absolute -top-2.5 -right-2.5 grid size-6 place-items-center rounded-full bg-warn text-sm font-bold text-panel"
      role="img"
      aria-label={t('확인할 점 {n}개').replace('{n}', String(count))}
      data-testid="node-problem"
    >
      !
    </span>
  )
}

function Frame({ id, selected, className, children }: { id: string; selected: boolean; className?: string; children: React.ReactNode }) {
  const view = useView()
  const problems = view.problems(id)
  const record = view.run?.(id)
  return (
    <div
      className={cn(
        'relative flex w-[168px] flex-col items-center justify-center gap-1 rounded-card border bg-panel px-2.5 py-2.5 text-center text-sm leading-snug',
        record ? RUN_FRAME[record.status] : problems.length ? 'border-warn' : 'border-line-strong',
        selected && 'ring-4 ring-accent/25 outline-2 outline-accent',
        className,
      )}
      data-testid="chain-node"
      data-step={id}
      data-status={record?.status}
    >
      {children}
      {!record && <ProblemBadge count={problems.length} />}
    </div>
  )
}

function Title({ id }: { id: string }) {
  const t = useT()
  const view = useView()
  const step = view.step(id) ?? null
  const record = view.run?.(id)
  const problems = view.problems(id)
  const note = view.note(id)
  return (
    <>
      {createElement(kindIcon(step), { size: 20, className: kindTone(step), 'aria-hidden': true })}
      <span className="min-w-0 max-w-full">
        <b className="block truncate text-fg" title={view.name(id)}>{t(view.name(id))}</b>
        {record ? (
          <span className={cn('block truncate', RUN_TEXT[record.status])}>
            {t(RUN_LABEL[record.status])}
            {record.outputSummary && record.status === 'done' ? ` · ${t(record.outputSummary)}` : ''}
          </span>
        ) : problems.length ? (
          <span className="block truncate text-warn" title={t(problemText(problems[0]))}>{t(problemText(problems[0]))}</span>
        ) : (
          <span className="block truncate text-muted">{t(kindLabel(step))}{note ? ` · ${t(note)}` : ''}</span>
        )}
      </span>
    </>
  )
}

/** An agent's skills under its node: filled = 항상 적용, dashed = 필요할 때. */
function SkillChips({ agent, skills }: { agent: Agent; skills: Skill[] }) {
  const t = useT()
  const active = agent.activeSkillIds ?? []
  const ids = [...active, ...(agent.skillIds ?? []).filter((id) => !active.includes(id))]
  const rows = ids.map((id) => skills.find((s) => s.id === id)).filter((s): s is Skill => !!s)
  if (!rows.length) return null
  return (
    <ul className="mt-1 flex w-[168px] flex-col items-center gap-1 border-t border-dashed border-line-strong pt-1.5" aria-label={t('붙은 스킬')}>
      {rows.slice(0, 4).map((s) => {
        const on = active.includes(s.id)
        return (
          <li
            key={s.id}
            className={cn(
              'max-w-full truncate rounded-full border px-2 py-0.5 text-xs',
              on ? 'border-accent/30 bg-accent-soft text-accent' : 'border-dashed border-accent/40 bg-panel text-muted',
            )}
            data-testid="skill-chip"
            data-pinned={on ? 'true' : 'false'}
            title={`${s.name} · ${on ? t('항상 적용') : t('필요할 때')}`}
          >
            {t(s.name)} · {on ? t('항상 적용') : t('필요할 때')}
          </li>
        )
      })}
      {rows.length > 4 && <li className="text-xs text-faint">+{rows.length - 4}</li>}
    </ul>
  )
}

export function StepNode({ id, selected }: NodeProps) {
  const view = useView()
  const agent = view.agent(id)
  return (
    <div className="flex flex-col items-center">
      <Frame id={id} selected={selected}>
        <Handle type="target" position={Position.Left} className={handleClass} />
        <Title id={id} />
        <Handle type="source" position={Position.Right} className={handleClass} />
      </Frame>
      {agent && !view.run && <SkillChips agent={agent} skills={view.skills} />}
    </div>
  )
}

export function ConditionNode({ id, selected }: NodeProps) {
  const t = useT()
  const view = useView()
  const step = view.step(id)
  const branches = isCondition(step) ? step.branches : []
  const taken = view.run?.(id)?.branchTaken
  return (
    <Frame id={id} selected={selected} className="border-warn/60 pr-5">
      <Handle type="target" position={Position.Left} className={handleClass} />
      <Title id={id} />
      {branches.map((b, i) => (
        <Handle
          key={b.key}
          id={b.key}
          type="source"
          position={Position.Right}
          className={handleClass}
          style={{ top: `${((i + 1) * 100) / (branches.length + 1)}%` }}
        >
          <span
            className={cn(
              'pointer-events-none absolute left-3 -translate-y-1/2 rounded-control border bg-panel px-1 text-xs whitespace-nowrap',
              taken === b.key ? 'border-accent text-accent' : 'border-line text-muted',
            )}
          >
            {t(b.label)}
          </span>
        </Handle>
      ))}
    </Frame>
  )
}

export function StartNode({ id, selected }: NodeProps) {
  const t = useT()
  return (
    <div
      className={cn(
        'relative flex items-center gap-2 rounded-full border border-success/50 bg-panel px-3 py-2 text-sm',
        selected && 'outline-2 outline-accent',
      )}
      data-testid="chain-node"
      data-step={id}
    >
      <Title id={id} />
      <span className="sr-only">{t('대화나 첨부로 시작합니다')}</span>
      <Handle type="source" position={Position.Right} className={handleClass} />
    </div>
  )
}
