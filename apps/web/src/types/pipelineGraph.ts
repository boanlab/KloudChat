import type { PipelineCatalogue, PipelineStep } from '@/types'

/* ── pipeline graph: steps, condition steps and the lines between them ─── */

/** What a rule compares. */
export type ConditionOp = 'gt' | 'eq' | 'empty' | 'contains'

/** A rule-mode condition: one value a preceding step produced, compared with `target`. */
export interface ConditionRule {
  /** Id of the preceding step whose value is read. */
  step: string
  /** `condition_values` key. */
  value: string
  op: ConditionOp
  target: string
}

/** One way out of a condition step; rule mode has exactly `yes` and `no`. */
export interface ConditionBranch {
  key: string
  label: string
}

/** A step that follows exactly one of its branches. */
export interface ConditionStep {
  kind: 'condition'
  id: string
  name: string
  mode: 'rule' | 'ai'
  rule?: ConditionRule
  /** AI mode: what the judging model is asked. */
  question?: string
  branches: ConditionBranch[]
}

/** Any step of a pipeline, as stored. */
export type GraphStep = PipelineStep | ConditionStep

/** A line from one step to the next; `branch` names the condition branch it leaves by. */
export interface PipelineEdge {
  from: string
  to: string
  branch?: string
}

/** A value a rule can read from a preceding step of `stepKind`. */
export interface ConditionValue {
  stepKind: string
  key: string
  label: string
  type: string
}

/** The catalogue with what rules can read. */
export type GraphCatalogue = PipelineCatalogue & { conditionValues: ConditionValue[] }

/** Problem codes the graph adds to a pipeline verdict. */
export type GraphProblemCode = 'branch_dead_end' | 'cycle' | 'condition_incomplete' | 'unreachable_step'

/** A pipeline problem; `step` is a 1-based position (0: the whole pipeline) or, for graph codes, a step id. */
export interface GraphProblem {
  step: number | string
  code: string
  message: string
}

export interface GraphVerdict {
  ok: boolean
  problems: GraphProblem[]
  warnings: GraphProblem[]
}

/** A pipeline's steps and lines; `edges` null: linear in `steps` order. */
export interface PipelineGraph {
  steps: GraphStep[]
  edges: PipelineEdge[] | null
}

/* ── runs ──────────────────────────────────────────────────────────────── */

export type RunStepStatus = 'waiting' | 'running' | 'done' | 'failed' | 'skipped'

/** What one step of a run received, did and handed on. */
export interface PipelineRunStep {
  stepId: string
  status: RunStepStatus
  startedAt: string | null
  endedAt: string | null
  inputSummary: string
  outputSummary: string
  note: string
  /** A condition step: the branch key it took. */
  branchTaken: string | null
}

/** One run of a user pipeline, readable by its owner. */
export interface PipelineRunRecord {
  id: string
  status: string
  startedAt: string | null
  endedAt: string | null
  sessionId: string | null
  steps: PipelineRunStep[]
}
