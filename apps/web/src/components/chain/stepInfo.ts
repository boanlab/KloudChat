import { Bot, GitFork, Play, Settings2, type LucideIcon } from 'lucide-react'
import { KIND_ICON, STEP_KIND_LABEL, stepTitle } from '@/components/agents/PipelineEditor'
import { SELF } from '@/lib/process'
import { userPipelinesApi } from '@/lib/api'
import { isCondition, readConditionValues, START } from '@/lib/pipelineGraph'
import type { Agent, PipelineCatalogue, PipelineStep } from '@/types'
import type { GraphCatalogue, GraphStep, PipelineEdge, PipelineGraph, RunStepStatus } from '@/types/pipelineGraph'

/** Step kind's Korean name, a condition and the start included. */
export function kindLabel(step: GraphStep | null): string {
  if (!step) return '시작'
  if (isCondition(step)) return '조건'
  return STEP_KIND_LABEL[step.kind] ?? step.kind
}

export function kindIcon(step: GraphStep | null): LucideIcon {
  if (!step) return Play
  if (isCondition(step)) return GitFork
  if (step.kind === 'agent') return Bot
  return KIND_ICON[step.kind] ?? Settings2
}

/** Icon colour per kind, from the app's tokens. */
export function kindTone(step: GraphStep | null): string {
  if (!step) return 'text-success'
  if (isCondition(step)) return 'text-warn'
  if (step.kind === 'check' || step.kind === 'review') return 'text-success'
  if (step.kind === 'human') return 'text-warn'
  return 'text-accent'
}

/** Agent a step runs: `@self` is the chain's own agent. */
export function stepAgent(step: GraphStep | null | undefined, self: Agent, agents: Agent[]): Agent | null {
  if (!step || isCondition(step) || step.kind !== 'agent') return null
  return step.ref === SELF ? self : (agents.find((a) => a.id === step.ref) ?? null)
}

/** Step's shown name. */
export function stepName(step: GraphStep | null | undefined, self: Agent, agents: Agent[], cat: PipelineCatalogue | null): string {
  if (!step) return '시작'
  if (isCondition(step)) return step.name || '조건'
  const agent = stepAgent(step, self, agents)
  if (agent) return agent.name || '이 에이전트'
  return stepTitle(step as PipelineStep, agents, cat)
}

export const isStart = (id: string) => id === START

export const RUN_LABEL: Record<RunStepStatus, string> = {
  done: '끝',
  running: '쓰는 중',
  waiting: '기다리는 중',
  failed: '실패',
  skipped: '건너뜀',
}

/** Node frame per run status. */
export const RUN_FRAME: Record<RunStepStatus, string> = {
  done: 'border-success bg-success/5',
  running: 'border-accent bg-accent-soft/40',
  waiting: 'border-line border-dashed',
  failed: 'border-danger bg-danger/5',
  skipped: 'border-line border-dashed opacity-60',
}

export const RUN_TEXT: Record<RunStepStatus, string> = {
  done: 'text-success',
  running: 'text-accent',
  waiting: 'text-faint',
  failed: 'text-danger',
  skipped: 'text-faint',
}

/** The catalogue with what a rule can read, fetched once per page; a failure is not kept. */
let catalogue: Promise<GraphCatalogue | null> | null = null
export function loadGraphCatalogue() {
  catalogue ??= userPipelinesApi
    .catalogue()
    .then((c) => ({ ...c, conditionValues: readConditionValues(c as unknown as Record<string, unknown>) }))
    .catch(() => {
      catalogue = null
      return null
    })
  return catalogue
}

/** The agent's saved graph: its 직접 구성, else its own step alone. */
export function savedGraph(agent: Agent): PipelineGraph {
  const p = agent.process
  const steps = p?.mode === 'custom' ? (p.steps ?? []) : []
  const edges = (p as { edges?: PipelineEdge[] | null } | null | undefined)?.edges ?? null
  if (steps.length) return { steps: steps as GraphStep[], edges }
  return { steps: [{ id: 's1', kind: 'agent', ref: SELF, settings: {} }], edges: null }
}

