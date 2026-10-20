import { Bot, GitFork, Play, Settings2, type LucideIcon } from 'lucide-react'
import { KIND_ICON, STEP_KIND_LABEL, stepTitle } from '@/components/agents/PipelineEditor'
import { SELF } from '@/lib/process'
import { userPipelinesApi } from '@/lib/api'
import { isCondition, readConditionValues, START } from '@/lib/pipelineGraph'
import type { Agent, PipelineCatalogue, PipelineStep } from '@/types'
import type {
  GraphCatalogue,
  GraphProblem,
  GraphProblemCode,
  GraphStep,
  PipelineEdge,
  PipelineGraph,
  RunStepStatus,
} from '@/types/pipelineGraph'

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
    .then((c) => {
      const raw = c as unknown as Partial<GraphCatalogue>
      return {
        ...c,
        conditionValues: readConditionValues(c as unknown as Record<string, unknown>),
        conditionModes: raw.conditionModes ?? ['rule', 'ai'],
        ruleOps: raw.ruleOps ?? ['gt', 'eq', 'empty', 'contains'],
        maxBranches: raw.maxBranches ?? 4,
      }
    })
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


/** Korean words for the graph codes, when a problem comes without its sentence. */
const GRAPH_MESSAGE: Record<GraphProblemCode, string> = {
  cycle: '앞 단계로 되돌아가는 선이 있습니다. 반복은 둘 수 없습니다.',
  unreachable_step: '첫 단계에서 이어지지 않아 실행되지 않는 단계입니다.',
  branch_dead_end: '갈래 뒤에 이어질 단계가 없습니다.',
  condition_incomplete: '조건을 다 채우지 않았습니다.',
  parallel_next: '조건 단계가 아니면 다음 단계는 하나만 이을 수 있습니다.',
  card_before_branch: '확인 질문과 그 뒤 에이전트 사이에는 조건 단계를 둘 수 없습니다.',
  branch_surface: '갈래마다 마지막 에이전트가 만드는 것이 다릅니다. 모든 갈래가 문서를 쓰거나 모두 답을 해야 합니다.',
}

/** A problem's sentence: the server's, else ours for its code. */
export const problemText = (p: GraphProblem) => p.message || GRAPH_MESSAGE[p.code as GraphProblemCode] || p.code
