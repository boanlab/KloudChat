import { ReactFlowProvider, applyNodeChanges, type Node } from '@xyflow/react'
import { History } from 'lucide-react'
import { useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, EmptyState, LoadingState } from '@/components/ui'
import { pipelineGraphApi } from '@/lib/api'
import { START, canvasEdges, isCondition, readRun, runIsOpen } from '@/lib/pipelineGraph'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import type { Agent } from '@/types'
import type { PipelineRunRecord, PipelineRunStep, RunStepStatus } from '@/types/pipelineGraph'
import { ChainCanvas } from './ChainCanvas'
import { ChainContext, edgeFor, nodesOf } from './flow'
import { RUN_LABEL, RUN_TEXT, kindLabel, savedGraph } from './stepInfo'

const RUN_STATUS: Record<string, string> = {
  running: '진행 중',
  waiting: '기다리는 중',
  done: '완료',
  failed: '실패',
  stopped: '멈춤',
  cancelled: '취소됨',
}

/** 한 일 when the record says nothing more: what its status means. */
function didOf(record: PipelineRunStep | undefined): string {
  if (!record) return ''
  if (record.status === 'done') return record.outputSummary ? `마쳤습니다 · ${record.outputSummary}` : '마쳤습니다'
  if (record.status === 'skipped') return '건너뛰었습니다'
  if (record.status === 'waiting') return '기다리는 중입니다'
  return ''
}

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''

/** 실행 기록: the runs of this agent's pipeline, each step's status on the canvas and what it took and gave. */
export function RunView({ agent, onEditStep }: { agent: Agent; onEditStep: (id: string) => void }) {
  const t = useT()
  const parent = useContext(ChainContext)
  const pipelineId = agent.process?.pipelineId ?? agent.pipelineId ?? null
  const [runs, setRuns] = useState<PipelineRunRecord[] | null>(pipelineId ? null : [])
  const [runId, setRunId] = useState<string | null>(null)
  const [fetched, setFetched] = useState<PipelineRunRecord | null>(null)
  const run = fetched?.id === runId ? fetched : null
  const [failed, setFailed] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const graph = useMemo(() => savedGraph(agent), [agent])
  const [nodes, setNodes] = useState<Node[]>(() => nodesOf(graph))
  const edges = useMemo(() => canvasEdges(graph).map(edgeFor), [graph])

  const loadRuns = useCallback(async () => {
    if (!pipelineId) return
    try {
      const rows = (await pipelineGraphApi.runs(pipelineId)).map(readRun)
      setRuns(rows)
      setFailed(false)
      setRunId((id) => id ?? rows[0]?.id ?? null)
    } catch {
      setFailed(true)
      setRuns((r) => r ?? [])
    }
  }, [pipelineId])
  useEffect(() => {
    void loadRuns()
  }, [loadRuns])

  // Runs are stored when they end or stop to wait, so one read each; 새로 고침 and coming back read again.
  const [reads, setReads] = useState(0)
  useEffect(() => {
    if (!runId) return
    let live = true
    pipelineGraphApi
      .run(runId)
      .then((row) => live && setFetched(readRun(row)))
      .catch(() => live && setFailed(true))
    return () => {
      live = false
    }
  }, [runId, reads])
  useEffect(() => {
    const again = () => {
      if (document.visibilityState !== 'visible') return
      void loadRuns()
      setReads((n) => n + 1)
    }
    document.addEventListener('visibilitychange', again)
    return () => document.removeEventListener('visibilitychange', again)
  }, [loadRuns])

  const recordOf = useCallback((id: string) => run?.steps.find((s) => s.stepId === id), [run])
  const ctx = useMemo(
    () =>
      parent && {
        ...parent,
        step: (id: string) => graph.steps.find((s) => s.id === id),
        problems: () => [],
        name: (id: string) => parent.name(id) || recordOf(id)?.title || '',
        run: (id: string) => (id === START ? undefined : recordOf(id) ?? (run ? { stepId: id, kind: '', title: '', status: (runIsOpen(run) ? 'waiting' : 'skipped') as RunStepStatus, startedAt: null, endedAt: null, inputSummary: '', outputSummary: '', note: '', branchTaken: null, branchLabel: '' } : undefined)),
      },
    [parent, graph, recordOf, run],
  )

  if (runs === null) return <LoadingState label={t('실행 기록을 불러오는 중…')} />
  const counts = (status: RunStepStatus) => run?.steps.filter((s) => s.status === status).length ?? 0
  const step = graph.steps.find((s) => s.id === picked)
  const record = picked ? recordOf(picked) : undefined
  const index = step ? graph.steps.indexOf(step) + 1 : 0
  const shownName = (id: string) => parent?.name(id) || recordOf(id)?.title || ''
  const next = edges.filter((e) => e.source === picked && (!record?.branchTaken || !e.sourceHandle || e.sourceHandle === record.branchTaken)).map((e) => shownName(e.target))
  const branch = record?.branchLabel || (isCondition(step) ? step.branches.find((b) => b.key === record?.branchTaken)?.label : undefined)

  return (
    <div className="flex flex-wrap" data-testid="chain-runs">
      <aside aria-label={t('실행 목록')} className="flex flex-[1_1_240px] flex-col gap-2 border-line bg-panel p-4 lg:max-w-[280px] lg:border-r max-lg:border-b">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-muted">{t('실행 기록')}</h2>
          <Button type="button" size="sm" variant="ghost" onClick={() => {
            void loadRuns()
            setReads((n) => n + 1)
          }}>
            {t('새로 고침')}
          </Button>
        </div>
        {failed && (
          <p className="text-sm text-danger" role="alert">
            {t('실행 기록을 불러오지 못했습니다.')}{' '}
            <button type="button" className="underline" onClick={() => void loadRuns()}>
              {t('다시 시도')}
            </button>
          </p>
        )}
        {runs.length === 0 && !failed && <p className="text-sm text-faint">{t('아직 실행 기록이 없습니다. 시험 실행으로 대화를 열면 여기에 남습니다.')}</p>}
        <ul className="flex flex-col gap-1.5">
          {runs.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                aria-pressed={r.id === runId}
                onClick={() => setRunId(r.id)}
                className={cn(
                  'flex w-full flex-col gap-0.5 rounded-control border px-3 py-2 text-left text-sm focus-visible:outline-2 focus-visible:outline-accent',
                  r.id === runId ? 'border-accent bg-accent-soft/40' : 'border-line hover:bg-elevated',
                )}
                data-testid="run-row"
              >
                <b>
                  {r.number ? `#${r.number} · ` : ''}
                  {t(RUN_STATUS[r.status] ?? r.status)}
                </b>
                <span className="text-muted">
                  {when(r.startedAt)}
                  {r.requestText ? ` · ${r.requestText}` : ''}
                  {r.attachments ? ` · ${t('첨부 {n}개').replace('{n}', String(r.attachments))}` : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <p className="text-xs leading-relaxed text-muted">{t('최근 30번까지 보입니다. 실행마다 대화 하나로 남고, 아티팩트는 기존 아티팩트 화면에서 열립니다.')}</p>
      </aside>
      <main className="flex min-w-0 flex-[999_1_480px] flex-col gap-3 p-4">
        {run ? (
          <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-card border border-line bg-panel px-3.5 py-2.5 text-sm" data-testid="run-status">
            <b>
              {run.number ? `#${run.number} ` : ''}
              {t(RUN_STATUS[run.status] ?? run.status)}
            </b>
            <span className="text-muted">{when(run.startedAt)}</span>
            <span className="text-muted">
              {[
                counts('done') && t('{n}단계 끝').replace('{n}', String(counts('done'))),
                counts('running') && t('{n}단계 진행').replace('{n}', String(counts('running'))),
                counts('waiting') && t('{n}단계 대기').replace('{n}', String(counts('waiting'))),
                counts('failed') && t('{n}단계 실패').replace('{n}', String(counts('failed'))),
                counts('skipped') && t('{n}단계 건너뜀').replace('{n}', String(counts('skipped'))),
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
            {run.sessionId && (
              <Link to={`/s/${run.sessionId}`} className="ml-auto text-accent hover:underline">
                {t('대화 열기')}
              </Link>
            )}
          </div>
        ) : (
          runs.length === 0 && <EmptyState icon={<History size={18} />} title={t('실행 기록이 없습니다')} />
        )}
        {ctx && (
          <ChainContext.Provider value={ctx}>
            <ReactFlowProvider>
              <ChainCanvas
                label="실행 캔버스"
                readOnly
                nodes={nodes}
                edges={edges}
                onNodesChange={(changes) => setNodes((ns) => applyNodeChanges(changes, ns))}
                onSelect={setPicked}
              />
            </ReactFlowProvider>
          </ChainContext.Provider>
        )}
        <p className="text-sm text-muted">{t('단계를 누르면 오른쪽에 그 단계가 받은 것과 넘긴 것이 보입니다.')}</p>
      </main>
      <aside aria-label={t('단계 입출력')} className="flex flex-[1_1_300px] flex-col gap-4 border-line bg-panel p-4 lg:max-w-[360px] lg:border-l max-lg:border-t" data-testid="run-step">
        {step && parent ? (
          <>
            <div className="flex flex-col gap-0.5">
              <span className="text-sm text-muted">
                {t('{n}단계').replace('{n}', String(index))} · {t(kindLabel(step))}
              </span>
              <b className="text-lg">{t(shownName(step.id))}</b>
              {record && <span className={cn('text-sm', RUN_TEXT[record.status])}>{t(RUN_LABEL[record.status])}</span>}
            </div>
            {[
              { label: '받은 것', text: record?.inputSummary },
              { label: '한 일', text: [record?.note || didOf(record), branch ? `${t('고른 갈래')}: ${t(branch)}` : ''].filter(Boolean).join('\n') },
              { label: '넘긴 것', text: record?.outputSummary ? `${record.outputSummary}${next.length ? ` → ${next.map((n) => t(n)).join(', ')}` : ''}` : '' },
            ].map((row) => (
              <div key={row.label} className="flex flex-col gap-1">
                <span className="text-sm text-muted">{t(row.label)}</span>
                <div className="rounded-card bg-elevated/60 p-3 text-sm leading-relaxed whitespace-pre-line">{row.text ? t(row.text) : t(record && record.status !== 'waiting' && record.status !== 'running' ? '없음' : '아직 없습니다')}</div>
              </div>
            ))}
            <Button type="button" size="sm" className="self-start" onClick={() => onEditStep(step.id)}>
              {t('이 단계 설정 고치기')}
            </Button>
          </>
        ) : (
          <p className="text-sm text-muted">{t('단계를 고르면 받은 것·한 일·넘긴 것이 보입니다.')}</p>
        )}
      </aside>
    </div>
  )
}
