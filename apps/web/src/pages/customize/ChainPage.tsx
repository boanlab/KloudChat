import {
  ReactFlowProvider,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from '@xyflow/react'
import { AlertTriangle, Bot, Plus } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { StepList } from '@/components/agents/PipelineEditor'
import { ChainCanvas } from '@/components/chain/ChainCanvas'
import { ChainContext, edgeFor, linesOf, nodeFor, nodesOf, type ChainView } from '@/components/chain/flow'
import { Palette } from '@/components/chain/Palette'
import { paletteGroups, usePaletteNotes, type PaletteItem } from '@/components/chain/paletteItems'
import { RunView } from '@/components/chain/RunView'
import { StepPanel } from '@/components/chain/StepPanel'
import { loadGraphCatalogue, problemText, savedGraph, stepAgent, stepName } from '@/components/chain/stepInfo'
import { HubTopBar } from '@/components/customize/DetailLayout'
import { PageBody } from '@/components/layout/AppShell'
import { Badge, Button, EmptyState, LoadingState, Modal } from '@/components/ui'
import { agentsApi, errorMessage, pipelineGraphApi } from '@/lib/api'
import { agentStartKind } from '@/lib/kinds'
import {
  START,
  canvasEdges,
  connect,
  freeSpot,
  fromCanvas,
  insertAfter,
  isCondition,
  isLinear,
  linearEdges,
  newStepId,
  tailFor,
  wouldCycle,
  type CanvasEdge,
} from '@/lib/pipelineGraph'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Agent, PipelineStep, PipelineVerdict } from '@/types'
import type { ConditionStep, GraphCatalogue, GraphProblem, GraphStep } from '@/types/pipelineGraph'

type View = 'edit' | 'runs' | 'list'
const VIEWS: { id: View; label: string }[] = [
  { id: 'edit', label: '편집' },
  { id: 'runs', label: '실행 기록' },
  { id: 'list', label: '목록으로 보기' },
]

const COL = 230
const ROW = 150

type NewStep = Omit<PipelineStep, 'id'> | Omit<ConditionStep, 'id'>

/** Unsaved canvas kept while a skill's agent draft is open elsewhere. */
interface Parked {
  steps: GraphStep[]
  lines: CanvasEdge[]
  places: Record<string, { x: number; y: number }>
  patches: Record<string, Partial<Agent>>
  /** The skill the draft was opened from, the drop point and the agents there were then. */
  skill?: { id: string; position: { x: number; y: number }; known: string[] }
}
const parkKey = (agentId: string) => `kc-chain-draft:${agentId}`

function writeParked(agentId: string, parked: Parked) {
  try {
    sessionStorage.setItem(parkKey(agentId), JSON.stringify(parked))
  } catch {
    // Not kept: the draft opens all the same.
  }
}

function readParked(agentId: string): Parked | null {
  try {
    const raw = sessionStorage.getItem(parkKey(agentId))
    return raw ? (JSON.parse(raw) as Parked) : null
  } catch {
    return null
  }
}

/** Server-only fields off a step before it is sent back. */
function bare(step: GraphStep): GraphStep {
  if (isCondition(step)) return step
  const { title: _title, output: _output, ...rest } = step
  return rest
}

export function ChainPage() {
  const t = useT()
  const { agentId = '' } = useParams()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const agents = useStore((s) => s.agents)
  const skills = useStore((s) => s.skills)
  const models = useStore((s) => s.models)
  const me = useStore((s) => s.user?.id)
  const ensureWorkspace = useStore((s) => s.ensureWorkspace)
  const workspaceLoading = useStore((s) => s.workspaceLoading)
  const newSession = useStore((s) => s.newSession)
  const setToast = useStore((s) => s.setToast)
  const view: View = (VIEWS.find((v) => v.id === params.get('view'))?.id ?? 'edit') as View
  const agent = agents.find((a) => a.id === agentId)

  useEffect(() => {
    void ensureWorkspace()
  }, [ensureWorkspace])

  const [cat, setCat] = useState<GraphCatalogue | null>(null)
  const [catFailed, setCatFailed] = useState(false)
  const [catTry, setCatTry] = useState(0)
  useEffect(() => {
    let live = true
    void loadGraphCatalogue().then((c) => {
      if (!live) return
      setCat(c)
      setCatFailed(!c)
    })
    return () => {
      live = false
    }
  }, [catTry])
  const [sheet, setSheet] = useState(false)

  const [steps, setSteps] = useState<GraphStep[]>([])
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [patches, setPatches] = useState<Record<string, Partial<Agent>>>({})
  const [selected, setSelected] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [verdict, setVerdict] = useState<{ problems: GraphProblem[]; warnings: GraphProblem[] } | null>(null)
  const loaded = useRef('')

  // Load once per agent: a parked draft wins over the saved graph.
  useEffect(() => {
    if (!agent || loaded.current === agent.id) return
    loaded.current = agent.id
    const parked = readParked(agent.id)
    if (parked) {
      let { steps: kept, lines: keptLines } = parked
      const places = { ...parked.places }
      // Back from a skill's draft: the agent it made (`?added=<id>`, else the new one with that skill) becomes the node.
      const skill = parked.skill
      const made =
        skill &&
        (agents.find((a) => a.id === params.get('added')) ??
          agents.find((a) => !skill.known.includes(a.id) && ((a.activeSkillIds ?? []).includes(skill.id) || (a.skillIds ?? []).includes(skill.id))))
      if (skill && made) {
        const id = newStepId(kept)
        kept = [...kept, { id, kind: 'agent', ref: made.id, settings: {} }]
        if (kept.length === 1) keptLines = connect(keptLines, START, id)
        places[id] = skill.position
        writeParked(agent.id, { ...parked, steps: kept, lines: keptLines, places, skill: undefined })
        setSelected(id)
      }
      setSteps(kept)
      setEdges(keptLines.map(edgeFor))
      const byId = new Map(kept.map((s) => [s.id, s]))
      setNodes(Object.entries(places).map(([id, pos]) => nodeFor(id, byId.get(id), pos)))
      setPatches(parked.patches)
      setDirty(true)
      if (params.has('added')) {
        const next = new URLSearchParams(params)
        next.delete('added')
        setParams(next, { replace: true })
      }
      return
    }
    const graph = savedGraph(agent)
    setSteps(graph.steps)
    setEdges(canvasEdges(graph).map(edgeFor))
    setNodes(nodesOf(graph))
    // Once per agent; the address is read as it was on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent])

  const lines = useMemo(() => linesOf(edges), [edges])
  const agentOf = useCallback(
    (step: GraphStep | undefined): Agent | null => {
      if (!agent) return null
      const found = stepAgent(step, agent, agents)
      return found ? { ...found, ...patches[found.id] } : null
    },
    [agent, agents, patches],
  )
  const merged = useMemo(() => agents.map((a) => ({ ...a, ...patches[a.id] })), [agents, patches])
  const nameOf = useCallback(
    (id: string) => {
      if (id === START) return '요청 받기'
      if (!agent) return ''
      return stepName(steps.find((s) => s.id === id), { ...agent, ...patches[agent.id] }, merged, cat)
    },
    [agent, steps, merged, cat, patches],
  )

  // The order the server is sent, for 1-based problem positions.
  const graph = useMemo(() => fromCanvas(steps, lines), [steps, lines])
  useEffect(() => {
    if (!agent) return
    let live = true
    const timer = window.setTimeout(() => {
      void pipelineGraphApi
        .validate({ stages: graph.steps.map(bare), edges: graph.edges, agentId: agent.id })
        .then((v) => live && setVerdict({ problems: v.problems ?? [], warnings: v.warnings ?? [] }))
        .catch(() => live && setVerdict(null))
    }, 400)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [agent, graph])

  const stepIdOf = useCallback(
    (p: GraphProblem) => p.stepId || (p.step > 0 ? (graph.steps[p.step - 1]?.id ?? '') : ''),
    [graph],
  )
  const problemsOf = useCallback(
    (id: string) => [...(verdict?.problems ?? []), ...(verdict?.warnings ?? [])].filter((p) => stepIdOf(p) === id),
    [verdict, stepIdOf],
  )
  const overall = (verdict?.problems ?? []).filter((p) => !stepIdOf(p))
  const flagged = graph.steps.filter((s) => (verdict?.problems ?? []).some((p) => stepIdOf(p) === s.id))

  const viewCtx: ChainView = useMemo(
    () => ({
      step: (id) => steps.find((s) => s.id === id),
      name: nameOf,
      agent: (id) => agentOf(steps.find((s) => s.id === id)),
      note: (id) => {
        if (id === START) return '대화·첨부로'
        const step = steps.find((s) => s.id === id)
        if (isCondition(step)) return step.mode === 'ai' ? 'AI 판단' : '규칙'
        const a = agentOf(step)
        if (a) return models.find((m) => m.id === a.model)?.label ?? (a.model || '기본 모델')
        return ''
      },
      skills,
      problems: problemsOf,
    }),
    [steps, nameOf, agentOf, models, skills, problemsOf],
  )

  const groups = useMemo(
    () => (agent ? paletteGroups({ ...agent, ...patches[agent.id] }, merged, skills.filter((s) => s.enabled), cat, me) : []),
    [agent, patches, merged, skills, cat, me],
  )
  const paletteNotes = usePaletteNotes(agent?.id ?? '', groups, steps, lines, selected)

  /* ── changes ──────────────────────────────────────────────────────── */

  const changed = () => {
    setDirty(true)
    setError(null)
  }
  const placeOf = (id: string) => nodes.find((n) => n.id === id)?.position

  /** Adds a step after `after` (the selected step, else the last), or loose at `at`. */
  const addStep = (make: NewStep, at?: { x: number; y: number }) => {
    const id = newStepId(steps)
    const step = { ...make, id } as GraphStep
    let nextLines = lines
    let position = at
    if (!at) {
      const tail = tailFor(steps, lines, selected)
      const put = insertAfter([...steps, step], lines, tail, step)
      nextLines = put.lines
      const base = placeOf(tail) ?? { x: 0, y: 0 }
      const want = { x: base.x + COL, y: base.y + put.lane * ROW }
      // What followed moves right; the new step takes the first place no node covers.
      const moved = put.pushed ? nodes.map((n) => (n.position.x >= want.x ? { ...n, position: { ...n.position, x: n.position.x + COL } } : n)) : nodes
      position = freeSpot(moved.map((n) => n.position), want)
      if (put.pushed) setNodes(moved)
    } else if (steps.length === 0) {
      nextLines = connect(nextLines, START, id)
    }
    setSteps((s) => [...s, step])
    setEdges(nextLines.map(edgeFor))
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })), { ...nodeFor(id, step, position ?? { x: 0, y: 0 }), selected: true }])
    setSelected(id)
    changed()
  }

  const removeStep = (id: string) => {
    if (id === START) return
    const step = steps.find((s) => s.id === id)
    const into = lines.filter((l) => l.target === id)
    const out = lines.filter((l) => l.source === id)
    let rest = lines.filter((l) => l.source !== id && l.target !== id)
    // A step in a chain: its neighbours join up.
    if (!isCondition(step) && out.length === 1) for (const l of into) rest = connect(rest, l.source, out[0].target, l.sourceHandle)
    setSteps((s) => s.filter((x) => x.id !== id))
    setEdges(rest.map(edgeFor))
    setNodes((ns) => ns.filter((n) => n.id !== id))
    if (selected === id) setSelected(null)
    changed()
  }

  const link = (source: string, target: string, handle?: string | null) => {
    const rest = lines.filter((l) => !(l.source === source && (l.sourceHandle ?? null) === (handle ?? null)))
    if (!target) {
      setEdges(rest.map(edgeFor))
      return changed()
    }
    if (target === START || wouldCycle(rest, source, target)) {
      setToast({ text: t('앞 단계로 되돌아가는 선(반복)은 이을 수 없습니다.') })
      return
    }
    setEdges(connect(rest, source, target, handle).map(edgeFor))
    changed()
  }

  const onNodesChange = (changes: NodeChange[]) => {
    const removed = changes.filter((c) => c.type === 'remove').map((c) => c.id)
    if (removed.length) {
      removed.forEach(removeStep)
      return
    }
    if (changes.some((c) => c.type === 'position' && c.dragging)) setDirty(true)
    setNodes((ns) => applyNodeChanges(changes, ns))
  }
  const onEdgesChange = (changes: EdgeChange[]) => {
    if (changes.some((c) => c.type === 'remove')) changed()
    setEdges((es) => applyEdgeChanges(changes, es))
  }
  const valid = (c: Connection | Edge) =>
    !!c.source && !!c.target && c.target !== START && !wouldCycle(lines.filter((l) => !(l.source === c.source && (l.sourceHandle ?? null) === (c.sourceHandle ?? null))), c.source, c.target)

  const patchStep = (next: GraphStep) => {
    setSteps((s) => s.map((x) => (x.id === next.id ? next : x)))
    if (isCondition(next)) {
      // Lines from branches that are gone go too.
      setEdges((es) => es.filter((e) => e.source !== next.id || !e.sourceHandle || next.branches.some((b) => b.key === e.sourceHandle)))
    }
    changed()
  }
  const patchAgent = (id: string, patch: Partial<Agent>) => {
    setPatches((p) => ({ ...p, [id]: { ...p[id], ...patch } }))
    changed()
  }

  /* ── a skill dropped: its agent draft, then back here with the new agent ── */

  const park = (skill?: Parked['skill']) => {
    const parked: Parked = {
      steps,
      lines,
      places: Object.fromEntries(nodes.map((n) => [n.id, n.position])),
      patches,
      skill,
    }
    writeParked(agentId, parked)
  }
  const openSkillDraft = (skillId: string, position?: { x: number; y: number }) => {
    const tail = graph.steps.at(-1)?.id
    const at = position ?? { x: (placeOf(tail ?? START)?.x ?? 0) + COL, y: placeOf(tail ?? START)?.y ?? 0 }
    park({ id: skillId, position: at, known: agents.map((a) => a.id) })
    const back = `/customize/agents/${agentId}/chain`
    navigate(`/customize/agents/new?from=skill:${encodeURIComponent(skillId)}&return=${encodeURIComponent(back)}`)
  }

  const pick = (item: PaletteItem, at?: { x: number; y: number }) => {
    if ('start' in item.make) {
      setSelected(START)
      setNodes((ns) => ns.map((n) => ({ ...n, selected: n.id === START })))
      return
    }
    if ('skill' in item.make) return openSkillDraft(item.make.skill, at)
    addStep(structuredClone(item.make.step), at)
  }

  /* ── save, try ─────────────────────────────────────────────────────── */

  const save = async (): Promise<boolean> => {
    if (!agent) return false
    setSaving(true)
    setError(null)
    try {
      for (const [id, patch] of Object.entries(patches)) {
        const target = agents.find((a) => a.id === id)
        if (!target || target.ownerId !== me) continue
        await agentsApi.update(id, patch as Parameters<typeof agentsApi.update>[1])
      }
      const out = await pipelineGraphApi.save(agent.id, {
        base: agent.process?.base ?? null,
        stages: graph.steps.map(bare),
        edges: graph.edges,
      })
      const { agent: fields, ...process } = out
      useStore.setState((s) => ({
        agents: s.agents.map((a) => {
          const next = { ...a, ...patches[a.id] }
          return a.id === agent.id
            ? ({ ...next, ...(fields as Partial<Agent>), process: { ...process, steps: process.steps ?? graph.steps, edges: graph.edges } } as Agent)
            : next
        }),
      }))
      setPatches({})
      setDirty(false)
      try {
        sessionStorage.removeItem(parkKey(agent.id))
      } catch {
        // Nothing kept.
      }
      setToast({ text: t('저장했습니다.') })
      return true
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
      return false
    } finally {
      setSaving(false)
    }
  }

  const tryRun = async () => {
    if (!agent) return
    if (dirty && !(await save())) return
    try {
      const id = await newSession(agentStartKind(agent), { agentId: agent.id })
      navigate(`/s/${id}`)
    } catch (err) {
      setError(errorMessage(err, t('대화를 열지 못했습니다.')))
    }
  }

  const setView = (next: View) => {
    const p = new URLSearchParams(params)
    if (next === 'edit') p.delete('view')
    else p.set('view', next)
    setParams(p, { replace: true })
  }

  if (!agent) {
    return (
      <>
        <HubTopBar tab="agents" tabLabel="에이전트" title="" />
        <PageBody>
          {workspaceLoading || agents.length === 0 ? (
            <LoadingState label={t('에이전트를 불러오는 중…')} />
          ) : (
            <EmptyState icon={<Bot size={18} />} title={t('이 에이전트를 찾을 수 없습니다')} description={t('지워졌거나 볼 수 없는 에이전트입니다.')} />
          )}
        </PageBody>
      </>
    )
  }
  const mine = agent.ownerId === me && !agent.catalogKey
  const max = cat?.maxSteps ?? 12
  const paletteBody = cat ? (
    <Palette
      groups={groups}
      notes={paletteNotes}
      onPick={(item) => {
        setSheet(false)
        pick(item)
      }}
      full={steps.length >= max}
    />
  ) : catFailed ? (
    <p className="text-sm text-danger" role="alert">
      {t('단계 목록을 불러오지 못했습니다.')}{' '}
      <button type="button" className="underline" onClick={() => setCatTry((n) => n + 1)}>
        {t('다시 시도')}
      </button>
    </p>
  ) : (
    <LoadingState label={t('단계 목록을 불러오는 중…')} />
  )
  const linearNow = !steps.some(isCondition) && isLinear(graph.steps, graph.edges ?? linearEdges(graph.steps))

  return (
    <>
      <HubTopBar tab="agents" tabLabel={agent.name} to={`/customize/agents/${agent.id}`} title={t('파이프라인')} />
      <div className="min-h-0 flex-1 overflow-y-auto" data-testid="chain-page">
        <header className="flex flex-wrap items-center gap-x-4 gap-y-3 border-b border-line bg-panel px-4 py-3 sm:px-5">
          <div className="flex min-w-0 flex-[1_1_240px] items-center gap-2">
            <h1 className="truncate text-lg font-semibold">{agent.name}</h1>
            <Badge>{agent.visibility === 'org' ? t('조직 공개') : t('나만 보기')}</Badge>
            {dirty && <Badge tone="warn">{t('저장 안 됨')}</Badge>}
          </div>
          <div role="tablist" aria-label={t('보기')} className="flex gap-1 rounded-card bg-elevated p-1">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={view === v.id}
                onClick={() => setView(v.id)}
                className={cn(
                  'rounded-control px-3.5 py-1.5 text-base whitespace-nowrap focus-visible:outline-2 focus-visible:outline-accent max-sm:px-2.5',
                  view === v.id ? 'bg-panel font-medium text-fg shadow-sm' : 'text-muted hover:text-fg',
                )}
              >
                {t(v.label)}
              </button>
            ))}
          </div>
          {mine && (
            <div className="flex gap-2">
              <Button type="button" onClick={() => void tryRun()} disabled={saving}>
                {t('시험 실행')}
              </Button>
              <Button type="button" variant="primary" onClick={() => void save()} disabled={saving || !dirty}>
                {saving ? t('저장하는 중…') : t('저장')}
              </Button>
            </div>
          )}
        </header>
        {error && (
          <p role="alert" className="mx-4 mt-3 rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger sm:mx-5">
            {error}
          </p>
        )}
        {!mine ? (
          <PageBody>
            <EmptyState icon={<Bot size={18} />} title={t('이 에이전트의 순서는 만든 사람만 고칩니다')} />
          </PageBody>
        ) : view === 'runs' ? (
          <ChainContext.Provider value={viewCtx}>
            <RunView agent={agent} onEditStep={(id) => {
              setSelected(id)
              setNodes((ns) => ns.map((n) => ({ ...n, selected: n.id === id })))
              setView('edit')
            }} />
          </ChainContext.Provider>
        ) : view === 'list' ? (
          <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-5" data-testid="chain-list">
            {linearNow ? (
              <StepList
                steps={graph.steps as PipelineStep[]}
                onChange={(next) => {
                  const g = { steps: next as GraphStep[], edges: null }
                  setSteps(next as GraphStep[])
                  setEdges(canvasEdges(g).map(edgeFor))
                  setNodes(nodesOf(g))
                  changed()
                }}
                verdict={verdict ? ({ ok: !verdict.problems.length, surface: '', problems: verdict.problems, warnings: verdict.warnings } as PipelineVerdict) : null}
                selfName={agent.name}
              />
            ) : (
              <div className="space-y-3">
                <p className="text-sm text-muted">{t('갈래가 있는 순서는 목록에서 볼 수만 있습니다. 고치려면 편집 탭의 캔버스를 쓰세요.')}</p>
                <ol className="divide-y divide-line rounded-card border border-line">
                  {graph.steps.map((s, i) => (
                    <li key={s.id} className="flex items-center gap-2 px-3 py-2 text-base" data-testid="pipeline-step">
                      <span className="w-5 text-right text-sm tabular-nums text-faint">{i + 1}</span>
                      <span className="min-w-0 flex-1 truncate">{t(nameOf(s.id))}</span>
                      {isCondition(s) && (
                        <span className="text-sm text-muted">
                          {s.branches
                            .map((b) => `${t(b.label)} → ${t(nameOf(lines.find((l) => l.source === s.id && l.sourceHandle === b.key)?.target ?? '') || '—')}`)
                            .join(' · ')}
                        </span>
                      )}
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        ) : (
          <ChainContext.Provider value={viewCtx}>
            <div className="flex flex-wrap" data-testid="chain-editor">
              <aside aria-label={t('단계 목록')} className="flex-[1_1_240px] border-line bg-panel p-4 max-lg:hidden lg:max-w-[280px] lg:border-r">
                {paletteBody}
              </aside>
              <main className="flex min-w-0 flex-[999_1_480px] flex-col gap-3 p-4 max-lg:order-1">
                {(flagged.length > 0 || overall.length > 0) && (
                  <div role="status" className="flex items-start gap-2.5 rounded-card border border-warn/40 bg-warn/5 px-3.5 py-2.5 text-sm text-fg" data-testid="chain-banner">
                    <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warn" aria-hidden />
                    <div className="space-y-0.5">
                      {flagged.length > 0 && (
                        <p>
                          {t('확인할 단계가 {n}개 있습니다').replace('{n}', String(flagged.length))}:{' '}
                          {flagged.map((s, i) => (
                            <span key={s.id}>
                              {i > 0 && ', '}
                              <button type="button" className="font-semibold underline-offset-2 hover:underline" onClick={() => {
                                setSelected(s.id)
                                setNodes((ns) => ns.map((n) => ({ ...n, selected: n.id === s.id })))
                              }}>
                                {t(nameOf(s.id))}
                              </button>
                            </span>
                          ))}
                          {flagged.length === 1 && problemsOf(flagged[0].id)[0] && ` — ${t(problemText(problemsOf(flagged[0].id)[0]))}`}
                        </p>
                      )}
                      {overall.map((p) => (
                        <p key={`${p.code}-${p.message}`}>{t(problemText(p))}</p>
                      ))}
                    </div>
                  </div>
                )}
                <ReactFlowProvider>
                  <ChainCanvas
                    label="파이프라인 캔버스"
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onConnect={(c) => c.source && c.target && link(c.source, c.target, c.sourceHandle)}
                    isValidConnection={valid}
                    onSelect={(id) => setSelected(id)}
                    onDropItem={(key, at) => {
                      const item = groups.flatMap((g) => g.items).find((i) => i.key === key)
                      if (item) pick(item, at)
                    }}
                    footer={
                      <>
                      <Button type="button" size="sm" variant="primary" className="lg:hidden" onClick={() => setSheet(true)}>
                        <Plus size={13} />
                        {t('단계 추가')}
                      </Button>
                      <span className="text-sm text-muted">
                        {t('{n}단계 · 저장하면 목록으로 보기에도 같은 순서로 보입니다.').replace('{n}', String(steps.length))}
                      </span>
                      </>
                    }
                  />
                </ReactFlowProvider>
                <p className="text-sm text-muted">
                  {t('단계를 끌어 놓고, 오른쪽 점에서 다음 단계로 선을 이으세요. 왼쪽 단계를 누르면 고른 단계 뒤에 붙습니다. 에이전트 아래 칩은 붙은 스킬입니다.')}
                </p>
              </main>
              <aside
                aria-label={t('선택한 단계')}
                className="flex-[1_1_300px] border-line bg-panel p-4 lg:max-w-[360px] lg:border-l max-lg:order-2 max-lg:border-t"
              >
                {selected && (selected === START || steps.some((s) => s.id === selected)) ? (
                  <StepPanel
                    id={selected}
                    steps={steps}
                    lines={lines}
                    name={nameOf}
                    agentOf={(id) => agentOf(steps.find((s) => s.id === id))}
                    canEdit={(a) => a.ownerId === me && !a.sealed}
                    skills={skills}
                    models={models}
                    cat={cat}
                    problems={problemsOf(selected)}
                    onStep={patchStep}
                    onAgent={patchAgent}
                    onConnect={link}
                    onRemove={removeStep}
                    onClose={() => {
                      setSelected(null)
                      setNodes((ns) => ns.map((n) => ({ ...n, selected: false })))
                    }}
                  />
                ) : (
                  <p className="text-sm text-muted">{t('단계를 고르면 여기서 설정합니다.')}</p>
                )}
              </aside>
            </div>
          </ChainContext.Provider>
        )}
      </div>
      {/* Phone: the step list as a sheet over the canvas. */}
      <Modal open={sheet} onClose={() => setSheet(false)} title={t('단계 추가')} width="max-w-md">
        <div data-testid="palette-sheet">{paletteBody}</div>
      </Modal>
    </>
  )
}
