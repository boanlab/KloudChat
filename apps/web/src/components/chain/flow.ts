import { MarkerType, type Edge, type Node } from '@xyflow/react'
import { createContext } from 'react'
import { START, edgeId, layout, type CanvasEdge } from '@/lib/pipelineGraph'
import type { Agent, Skill } from '@/types'
import type { GraphProblem, GraphStep, PipelineGraph, PipelineRunStep } from '@/types/pipelineGraph'

/** Drag payload type for palette items. */
export const DRAG_TYPE = 'application/x-kloudchat-step'

/** React Flow node for a step (or the start). */
export function nodeFor(id: string, step: GraphStep | undefined, position: { x: number; y: number }): Node {
  return {
    id,
    type: id === START ? 'start' : step?.kind === 'condition' ? 'condition' : 'step',
    position,
    data: {},
    deletable: id !== START,
  }
}

export function edgeFor(line: CanvasEdge): Edge {
  return {
    id: line.id || edgeId(line.source, line.target, line.sourceHandle),
    source: line.source,
    target: line.target,
    sourceHandle: line.sourceHandle ?? null,
    type: 'smoothstep',
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
  }
}

export const linesOf = (edges: Edge[]): CanvasEdge[] =>
  edges.map((e) => ({ id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle ?? null }))

/** Nodes of a stored graph, laid out. */
export function nodesOf(graph: PipelineGraph): Node[] {
  const byId = new Map(graph.steps.map((s) => [s.id, s]))
  return layout(graph).map((p) => nodeFor(p.id, byId.get(p.id), p.position))
}

/** What a node shows besides its place: read by id so the node list only changes with the steps. */
export interface ChainView {
  step: (id: string) => GraphStep | undefined
  name: (id: string) => string
  /** The agent an agent step runs, with unsaved changes. */
  agent: (id: string) => Agent | null
  /** A short line under the name. */
  note: (id: string) => string
  skills: Skill[]
  problems: (id: string) => GraphProblem[]
  /** Run view: the step's record. */
  run?: (id: string) => PipelineRunStep | undefined
}

export const ChainContext = createContext<ChainView | null>(null)

