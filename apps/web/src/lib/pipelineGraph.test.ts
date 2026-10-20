import { describe, expect, it } from 'vitest'
import type { PipelineStep } from '@/types'
import type { ConditionStep, GraphStep, PipelineGraph } from '@/types/pipelineGraph'
import { START, canvasEdges, connect, fromCanvas, layout, newStepId, readConditionValues, readRun, wouldCycle } from '@/lib/pipelineGraph'

const agent = (id: string): PipelineStep => ({ id, kind: 'agent', ref: `a-${id}`, settings: {} })
const cond = (id: string): ConditionStep => ({
  kind: 'condition', id, name: '실행 과제가 있나?', mode: 'rule',
  rule: { step: 's1', value: 'action_items', op: 'gt', target: '0' },
  branches: [{ key: 'yes', label: '예' }, { key: 'no', label: '아니오' }],
})

describe('graph and steps', () => {
  it('a linear pipeline comes back as the same steps with no edges', () => {
    const graph: PipelineGraph = { steps: [agent('s1'), agent('s2'), agent('s3')], edges: null }
    const back = fromCanvas(graph.steps, canvasEdges(graph))
    expect(back).toEqual(graph)
  })

  it('a linear pipeline drawn on the canvas in a new order saves that order', () => {
    const steps = [agent('s1'), agent('s2'), agent('s3')]
    let lines = connect([], START, 's3')
    lines = connect(lines, 's3', 's1')
    lines = connect(lines, 's1', 's2')
    const back = fromCanvas(steps, lines)
    expect(back.steps.map((s) => s.id)).toEqual(['s3', 's1', 's2'])
    expect(back.edges).toBeNull()
  })

  it('a condition keeps its branches as edges, in branch order, and survives a round trip', () => {
    const steps: GraphStep[] = [agent('s1'), cond('c1'), agent('s2'), agent('s3'), agent('s4')]
    const graph: PipelineGraph = {
      steps,
      edges: [
        { from: 's1', to: 'c1' },
        { from: 'c1', to: 's2', branch: 'yes' },
        { from: 'c1', to: 's3', branch: 'no' },
        { from: 's2', to: 's4' },
      ],
    }
    const back = fromCanvas(steps, canvasEdges(graph))
    expect(back.steps.map((s) => s.id)).toEqual(['s1', 'c1', 's2', 's4', 's3'])
    expect(back.edges).toEqual([
      { from: 's1', to: 'c1' },
      { from: 'c1', to: 's2', branch: 'yes' },
      { from: 'c1', to: 's3', branch: 'no' },
      { from: 's2', to: 's4' },
    ])
    expect(fromCanvas(back.steps, canvasEdges(back))).toEqual(back)
  })

  it('a step left unconnected keeps its id and stays in the steps with explicit edges', () => {
    const steps = [agent('s1'), agent('s2'), agent('s3')]
    const lines = connect(connect([], START, 's1'), 's1', 's2')
    const back = fromCanvas(steps, lines)
    expect(back.steps.map((s) => s.id)).toEqual(['s1', 's2', 's3'])
    expect(back.edges).toEqual([{ from: 's1', to: 's2' }])
  })

  it('ids stay stable and a new id never repeats one in use', () => {
    expect(newStepId([{ id: 's1' }, { id: 's3' }])).toBe('s4')
    expect(newStepId([{ id: 's2' }, { id: 's3' }])).toBe('s4')
    expect(newStepId([])).toBe('s1')
  })

  it('a step leads to one next step: a new line from it replaces the old one', () => {
    const lines = connect(connect([], 's1', 's2'), 's1', 's3')
    expect(lines.map((l) => l.target)).toEqual(['s3'])
    const both = connect(connect([], 'c1', 's2', 'yes'), 'c1', 's3', 'no')
    expect(both).toHaveLength(2)
  })

  it('a line back to an earlier step is a loop', () => {
    const lines = connect(connect([], 's1', 's2'), 's2', 's3')
    expect(wouldCycle(lines, 's3', 's1')).toBe(true)
    expect(wouldCycle(lines, 's1', 's3')).toBe(false)
    expect(wouldCycle(lines, 's2', 's2')).toBe(true)
  })

  it('layout puts a condition branch on its own row', () => {
    const graph: PipelineGraph = {
      steps: [agent('s1'), cond('c1'), agent('s2'), agent('s3')],
      edges: [{ from: 's1', to: 'c1' }, { from: 'c1', to: 's2', branch: 'yes' }, { from: 'c1', to: 's3', branch: 'no' }],
    }
    const at = Object.fromEntries(layout(graph).map((p) => [p.id, p.position]))
    expect(at.s2.x).toBe(at.s3.x)
    expect(at.s3.y).toBeGreaterThan(at.s2.y)
    expect(at.c1.x).toBeGreaterThan(at.s1.x)
  })

  it('the step the start leads to is saved first, even when another line comes into it', () => {
    const steps: GraphStep[] = [agent('s1'), cond('c1'), agent('s2')]
    let lines = connect([], START, 'c1')
    lines = connect(lines, 'c1', 's1', 'yes')
    lines = connect(lines, 'c1', 's2', 'no')
    lines = connect(lines, 's2', 'c1')
    expect(fromCanvas(steps, lines).steps[0].id).toBe('c1')
  })

  it("a run record reads the server's snake_case, branch label included", () => {
    const run = readRun({
      id: 'r1', status: 'done', started_at: '2026-10-20T05:00:00Z', ended_at: null, session_id: 'x',
      steps: [{ step_id: 'c1', kind: 'condition', title: '과제가 있나?', status: 'done', started_at: null, ended_at: null,
        input_summary: '요청', output_summary: '', note: '과제 3개', branch_taken: 'yes', branch_label: '예' }],
    })
    expect(run.steps[0]).toMatchObject({ stepId: 'c1', kind: 'condition', title: '과제가 있나?', branchTaken: 'yes', branchLabel: '예' })
    expect(run.sessionId).toBe('x')
  })

  it('rule values keep the request source and a 사람 확인 ref', () => {
    const values = readConditionValues({ condition_values: [
      { step_kind: 'request', key: 'attachments', label: '첨부 수', type: 'number' },
      { step_kind: 'human', ref: 'gaps', key: 'gaps', label: '빈칸 수', type: 'number' },
    ] })
    expect(values).toEqual([
      { stepKind: 'request', key: 'attachments', label: '첨부 수', type: 'number' },
      { stepKind: 'human', key: 'gaps', label: '빈칸 수', type: 'number', ref: 'gaps' },
    ])
  })
})
