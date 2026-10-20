import type {
  ConditionStep,
  ConditionValue,
  GraphStep,
  PipelineEdge,
  PipelineGraph,
  PipelineRunRecord,
  PipelineRunStep,
  RunStepStatus,
} from '@/types/pipelineGraph'

/** The canvas's start node: where a run begins; never stored. */
export const START = '__start'

/** A line on the canvas; `sourceHandle` is the condition branch it leaves by. */
export interface CanvasEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
}

export interface CanvasPlace {
  id: string
  position: { x: number; y: number }
}

const COL = 230
const ROW = 150

export const isCondition = (step: GraphStep | undefined): step is ConditionStep => step?.kind === 'condition'

export const edgeId = (source: string, target: string, handle?: string | null) => `${source}->${target}:${handle ?? ''}`

/** Lines of a linear pipeline: each step to the next. */
export function linearEdges(steps: GraphStep[]): PipelineEdge[] {
  return steps.slice(1).map((s, i) => ({ from: steps[i].id, to: s.id }))
}

/** The stored lines, or the linear ones when there are none. */
export function edgesOf(graph: PipelineGraph): PipelineEdge[] {
  return graph.edges ?? linearEdges(graph.steps)
}

/** Next free `s<n>` id. */
export function newStepId(steps: { id: string }[]): string {
  let n = steps.length + 1
  while (steps.some((s) => s.id === `s${n}`)) n++
  return `s${n}`
}

/** Step ids in reading order: the entry first, each chain followed depth-first, branches in their order;
 *  steps on a cycle or unconnected keep their place at the end. */
export function readingOrder(steps: GraphStep[], edges: PipelineEdge[], entry?: string | null): string[] {
  const ids = new Set(steps.map((s) => s.id))
  const index = new Map(steps.map((s, i) => [s.id, i]))
  const live = edges.filter((e) => ids.has(e.from) && ids.has(e.to))
  const indeg = new Map(steps.map((s) => [s.id, 0]))
  for (const e of live) indeg.set(e.to, (indeg.get(e.to) ?? 0) + 1)
  const branchRank = (e: PipelineEdge) => {
    const from = steps[index.get(e.from) ?? -1]
    return isCondition(from) ? from.branches.findIndex((b) => b.key === e.branch) : 0
  }
  const out = (id: string) =>
    live.filter((e) => e.from === id).sort((a, b) => branchRank(a) - branchRank(b) || (index.get(a.to)! - index.get(b.to)!))
  const ready = steps.filter((s) => indeg.get(s.id) === 0).map((s) => s.id)
  if (entry && ready.includes(entry)) ready.splice(0, 0, ...ready.splice(ready.indexOf(entry), 1))
  const order: string[] = []
  while (ready.length) {
    const id = ready.shift()!
    order.push(id)
    const freed: string[] = []
    for (const e of out(id)) {
      indeg.set(e.to, indeg.get(e.to)! - 1)
      if (indeg.get(e.to) === 0) freed.push(e.to)
    }
    ready.unshift(...freed)
  }
  for (const s of steps) if (!order.includes(s.id)) order.push(s.id)
  return order
}

/** True when the lines are exactly each step to the next, in `steps` order, with no condition. */
export function isLinear(steps: GraphStep[], edges: PipelineEdge[]): boolean {
  if (steps.some(isCondition)) return false
  const want = linearEdges(steps)
  if (edges.length !== want.length) return false
  return want.every((w) => edges.some((e) => e.from === w.from && e.to === w.to && !e.branch))
}

/** Canvas lines → stored graph: steps in reading order; `edges` null when that order is the whole story. */
export function fromCanvas(steps: GraphStep[], lines: CanvasEdge[]): PipelineGraph {
  const entry = lines.find((l) => l.source === START)?.target ?? null
  const edges: PipelineEdge[] = lines
    .filter((l) => l.source !== START && l.target !== START)
    .map((l) => (l.sourceHandle ? { from: l.source, to: l.target, branch: l.sourceHandle } : { from: l.source, to: l.target }))
  const byId = new Map(steps.map((s) => [s.id, s]))
  const ordered = readingOrder(steps, edges, entry).map((id) => byId.get(id)!)
  const pos = new Map(ordered.map((s, i) => [s.id, i]))
  const branchRank = (e: PipelineEdge) => {
    const from = byId.get(e.from)
    return isCondition(from) ? from.branches.findIndex((b) => b.key === e.branch) : 0
  }
  edges.sort((a, b) => pos.get(a.from)! - pos.get(b.from)! || branchRank(a) - branchRank(b))
  const linear = isLinear(ordered, edges) && (ordered.length === 0 || !entry || entry === ordered[0].id)
  return { steps: ordered, edges: linear ? null : edges }
}

/** Stored graph → canvas lines, the start line to the first step included. */
export function canvasEdges(graph: PipelineGraph): CanvasEdge[] {
  const lines = edgesOf(graph).map((e) => ({ id: edgeId(e.from, e.to, e.branch), source: e.from, target: e.to, sourceHandle: e.branch ?? null }))
  const first = graph.steps[0]
  return first ? [{ id: edgeId(START, first.id), source: START, target: first.id, sourceHandle: null }, ...lines] : lines
}

/** Places left to right by depth; a condition's later branches drop a row. */
export function layout(graph: PipelineGraph): CanvasPlace[] {
  const edges = edgesOf(graph)
  const order = readingOrder(graph.steps, edges, graph.steps[0]?.id)
  const byId = new Map(graph.steps.map((s) => [s.id, s]))
  const depth = new Map<string, number>()
  const row = new Map<string, number>()
  const taken = new Set<string>()
  for (const id of order) {
    const into = edges.filter((e) => e.to === id && depth.has(e.from))
    let d = 1
    let r = 0
    if (into.length) {
      d = Math.max(...into.map((e) => depth.get(e.from)! + 1))
      const first = into[0]
      const from = byId.get(first.from)
      const lane = isCondition(from) ? Math.max(0, from.branches.findIndex((b) => b.key === first.branch)) : 0
      r = row.get(first.from)! + lane
    } else if (depth.size) {
      // Unconnected: below what is placed.
      r = Math.max(...row.values()) + 1
    }
    while (taken.has(`${d}:${r}`)) r++
    taken.add(`${d}:${r}`)
    depth.set(id, d)
    row.set(id, r)
  }
  return [
    { id: START, position: { x: 0, y: 0 } },
    ...graph.steps.map((s) => ({ id: s.id, position: { x: depth.get(s.id)! * COL, y: row.get(s.id)! * ROW } })),
  ]
}

/** True when a line source → target would close a loop. */
export function wouldCycle(lines: { source: string; target: string }[], source: string, target: string): boolean {
  if (source === target) return true
  const seen = new Set<string>()
  const walk = [target]
  while (walk.length) {
    const at = walk.pop()!
    if (at === source) return true
    if (seen.has(at)) continue
    seen.add(at)
    for (const l of lines) if (l.source === at) walk.push(l.target)
  }
  return false
}

/** Adds a line; a step (or a condition branch) leads to one next step, so its old line goes. */
export function connect(lines: CanvasEdge[], source: string, target: string, handle?: string | null): CanvasEdge[] {
  const rest = lines.filter((l) => !(l.source === source && (l.sourceHandle ?? null) === (handle ?? null)))
  return [...rest, { id: edgeId(source, target, handle), source, target, sourceHandle: handle ?? null }]
}

/** Steps a step can read from: every step with a path to it. */
export function ancestors(lines: { source: string; target: string }[], id: string): Set<string> {
  const found = new Set<string>()
  const walk = [id]
  while (walk.length) {
    const at = walk.pop()!
    for (const l of lines) {
      if (l.target === at && !found.has(l.source)) {
        found.add(l.source)
        walk.push(l.source)
      }
    }
  }
  found.delete(START)
  return found
}

type Raw = Record<string, unknown>

/** A field in either casing (`started_at` or `startedAt`). */
function field(row: Raw, snake: string): unknown {
  const camel = snake.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase())
  return row[camel] ?? row[snake]
}
const text = (v: unknown) => (typeof v === 'string' ? v : '')
const textOrNull = (v: unknown) => (typeof v === 'string' && v ? v : null)

const RUN_STEP_STATUS: RunStepStatus[] = ['waiting', 'running', 'done', 'failed', 'skipped']

/** A run row as the server sends it, read into `PipelineRunRecord`. */
export function readRun(row: Raw): PipelineRunRecord {
  const steps = Array.isArray(row.steps) ? (row.steps as Raw[]) : []
  return {
    id: text(row.id),
    status: text(row.status),
    startedAt: textOrNull(field(row, 'started_at')),
    endedAt: textOrNull(field(row, 'ended_at')),
    sessionId: textOrNull(field(row, 'session_id')),
    steps: steps.map((s): PipelineRunStep => {
      const status = text(s.status) as RunStepStatus
      return {
        stepId: text(field(s, 'step_id')),
        status: RUN_STEP_STATUS.includes(status) ? status : 'waiting',
        startedAt: textOrNull(field(s, 'started_at')),
        endedAt: textOrNull(field(s, 'ended_at')),
        inputSummary: text(field(s, 'input_summary')),
        outputSummary: text(field(s, 'output_summary')),
        note: text(s.note),
        branchTaken: textOrNull(field(s, 'branch_taken')),
      }
    }),
  }
}

/** A run still moving: polled. */
export const runIsLive = (run: PipelineRunRecord) =>
  run.status === 'running' || run.status === 'waiting' || run.steps.some((s) => s.status === 'running')

/** The catalogue's rule values in either casing. */
export function readConditionValues(cat: Raw | null): ConditionValue[] {
  const rows = cat ? field(cat, 'condition_values') : null
  if (!Array.isArray(rows)) return []
  return (rows as Raw[]).map((r) => ({
    stepKind: text(field(r, 'step_kind')),
    key: text(r.key),
    label: text(r.label),
    type: text(r.type),
  }))
}
