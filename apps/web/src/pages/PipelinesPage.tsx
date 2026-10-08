import { ArrowLeft, Copy, Lock, Pencil, Plus, Workflow } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { PipelineChain, PipelineFlowView } from '@/components/agents/PipelineChain'
import { PipelineEditor, PipelineStore, StepChips } from '@/components/agents/PipelineEditor'
import { Badge, Button, Card, EmptyState, LoadingState, PageHeader, Tabs } from '@/components/ui'
import { errorMessage, userPipelinesApi } from '@/lib/api'
import { useT } from '@/lib/useT'
import { REVIEW_LABEL, REVIEW_TONE, agentOutput } from '@/lib/userPipelines'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Agent, UserPipeline } from '@/types'

type Tab = 'mine' | 'shipped' | 'store'

/** 「흐름」: your own flows, the shipped ones to copy from, and the ones the organization shares. */
export function PipelinesPage() {
  const t = useT()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const setNotice = useStore((s) => s.setNotice)
  const [mine, setMine] = useState<UserPipeline[] | null>(null)
  const [busy, setBusy] = useState(false)
  const raw = params.get('tab')
  const tab: Tab = raw === 'shipped' || raw === 'store' ? raw : 'mine'
  const setTab = (next: Tab) => setParams(next === 'mine' ? {} : { tab: next }, { replace: true })

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  useEffect(() => {
    let current = true
    void userPipelinesApi
      .list()
      .then((rows) => current && setMine(rows))
      .catch(() => current && setMine([]))
    return () => {
      current = false
    }
  }, [])

  const create = async () => {
    setBusy(true)
    try {
      const made = await userPipelinesApi.create({ name: t('새 흐름') })
      navigate(`/pipelines/${made.id}`)
    } catch (err) {
      setNotice(errorMessage(err, t('흐름을 만들지 못했습니다.')))
      setBusy(false)
    }
  }

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('흐름')}</span>} />
      <PageBody>
        <PageHeader
          title={t('흐름')}
          description={t('에이전트·스킬·검사·검토·내보내기를 순서대로 엮은 작업 흐름입니다. 기본 흐름을 복제해 고치고, 에이전트에 연결하거나 바로 시작합니다.')}
          action={
            <Button variant="primary" disabled={busy} onClick={() => void create()}>
              <Plus size={16} />
              {t('새 흐름')}
            </Button>
          }
        />
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'mine', label: t('내 흐름'), count: mine?.length },
            { id: 'shipped', label: t('기본 흐름') },
            { id: 'store', label: t('스토어') },
          ]}
        />
        <div className="pt-4" role="tabpanel">
          {tab === 'mine' && <MyPipelines rows={mine} onShipped={() => setTab('shipped')} />}
          {tab === 'shipped' && <ShippedPipelines focus={params.get('agent')} />}
          {tab === 'store' && <PipelineStore />}
        </div>
      </PageBody>
    </>
  )
}

function MyPipelines({ rows, onShipped }: { rows: UserPipeline[] | null; onShipped: () => void }) {
  const t = useT()
  if (rows === null) return <LoadingState />
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={<Workflow size={18} />}
        title={t('아직 내 흐름이 없습니다')}
        description={t('기본 흐름을 복제하거나 스토어에서 가져와 시작하세요.')}
        action={
          <Button size="sm" onClick={onShipped}>
            {t('기본 흐름 보기')}
          </Button>
        }
      />
    )
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {rows.map((p) => (
        <Card key={p.id} className="flex flex-col gap-2 p-4" data-testid="my-pipeline">
          <div className="flex items-start justify-between gap-2">
            <Link
              to={`/pipelines/${p.id}`}
              className="min-w-0 truncate text-base font-semibold hover:underline focus-visible:outline-2 focus-visible:outline-accent"
            >
              {p.name}
            </Link>
            <span className="flex shrink-0 gap-1">
              {p.surface && <Badge>{p.surface === 'report' ? t('문서') : t('답')}</Badge>}
              <Badge tone={REVIEW_TONE[p.review]}>{t(REVIEW_LABEL[p.review])}</Badge>
            </span>
          </div>
          <StepChips steps={p.stages} />
          {p.boundAgents.length > 0 && (
            <p className="text-sm text-faint">
              {t('연결된 에이전트')}: {p.boundAgents.map((a) => t(a.name)).join(', ')}
            </p>
          )}
          {(p.problems?.length ?? 0) > 0 && <p className="text-sm text-danger">{t('고칠 단계가 있습니다.')}</p>}
          <div className="mt-auto flex gap-2 pt-1">
            <Link
              to={`/pipelines/${p.id}`}
              className="inline-flex h-8 items-center gap-1.5 rounded-control border border-line bg-elevated px-2.5 text-sm hover:bg-line focus-visible:outline-2 focus-visible:outline-accent"
            >
              <Pencil size={13} />
              {t('편집')}
            </Link>
          </div>
        </Card>
      ))}
    </div>
  )
}

/** The flows agents ship with: locked, copied to edit. One card per shipped agent. */
function ShippedPipelines({ focus }: { focus: string | null }) {
  const t = useT()
  const navigate = useNavigate()
  const agents = useStore((s) => s.agents)
  const user = useStore((s) => s.user)
  const setNotice = useStore((s) => s.setNotice)
  const [open, setOpen] = useState<string | null>(focus)
  const [busy, setBusy] = useState(false)

  const shipped = useMemo(() => {
    const byKey = new Map<string, Agent>()
    for (const a of agents) {
      if (!a.catalogKey || !agentOutput(a)) continue
      if (a.ownerId !== user?.id && a.visibility !== 'org') continue
      const held = byKey.get(a.catalogKey)
      if (!held || (held.ownerId !== user?.id && a.ownerId === user?.id)) byKey.set(a.catalogKey, a)
    }
    return [...byKey.values()]
  }, [agents, user?.id])

  useEffect(() => {
    if (!focus) return
    document.querySelector(`[data-agent="${CSS.escape(focus)}"]`)?.scrollIntoView({ block: 'center' })
  }, [focus, shipped.length])

  const clone = async (agent: Agent) => {
    setBusy(true)
    try {
      const made = await userPipelinesApi.create({ fromAgentId: agent.id })
      navigate(`/pipelines/${made.id}`)
    } catch (err) {
      setNotice(errorMessage(err, t('흐름을 복제하지 못했습니다.')))
      setBusy(false)
    }
  }

  if (shipped.length === 0) {
    return <EmptyState icon={<Workflow size={18} />} title={t('기본 흐름이 있는 에이전트가 없습니다')} />
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">{t('기본 흐름은 고칠 수 없습니다. 복제하면 내 흐름으로 고칠 수 있습니다.')}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {shipped.map((a) => (
          <Card
            key={a.id}
            data-agent={a.id}
            data-testid="shipped-pipeline"
            className={cn('flex flex-col gap-2 p-4', focus === a.id && 'ring-2 ring-accent')}
          >
            <div className="flex items-start justify-between gap-2">
              <p className="min-w-0 truncate text-base font-semibold">{t(a.name)}</p>
              <Badge>
                <Lock size={11} aria-hidden />
                {t('잠김')}
              </Badge>
            </div>
            <PipelineChain agentId={a.id} />
            {open === a.id && (
              <div className="rounded-control border border-line p-3">
                <PipelineFlowView agentId={a.id} />
              </div>
            )}
            <div className="mt-auto flex flex-wrap gap-2 pt-1">
              <Button size="sm" variant="primary" disabled={busy} onClick={() => void clone(a)}>
                <Copy size={13} />
                {t('복제해서 편집')}
              </Button>
              <Button size="sm" variant="ghost" aria-expanded={open === a.id} onClick={() => setOpen(open === a.id ? null : a.id)}>
                {open === a.id ? t('단계 접기') : t('단계 보기')}
              </Button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}

/** /pipelines/:id — one of your flows, full width, its actions pinned to the bottom. */
export function PipelineEditPage() {
  const t = useT()
  const { pipelineId = '' } = useParams()
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const [loaded, setLoaded] = useState<{ id: string; row: UserPipeline | null } | null>(null)
  const row = loaded?.id === pipelineId ? loaded.row : undefined

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  useEffect(() => {
    let current = true
    void userPipelinesApi
      .get(pipelineId)
      .then((p) => current && setLoaded({ id: pipelineId, row: p }))
      .catch(() => current && setLoaded({ id: pipelineId, row: null }))
    return () => {
      current = false
    }
  }, [pipelineId])

  return (
    <>
      <TopBar
        left={
          <span className="flex min-w-0 items-center gap-1.5 text-base">
            <Link to="/pipelines" className="shrink-0 text-muted hover:text-fg">
              {t('흐름')}
            </Link>
            <span className="text-faint" aria-hidden>
              /
            </span>
            <span className="min-w-0 truncate font-medium">{row?.name ?? ''}</span>
          </span>
        }
      />
      <PageBody className="pb-0 sm:pb-0">
        <Link
          to="/pipelines"
          className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg focus-visible:outline-2 focus-visible:outline-accent"
        >
          <ArrowLeft size={14} aria-hidden />
          {t('흐름 목록')}
        </Link>
        {row === undefined && <LoadingState />}
        {row === null && (
          <EmptyState
            icon={<Workflow size={18} />}
            title={t('이 흐름을 찾을 수 없습니다')}
            description={t('지워졌거나 내 흐름이 아닙니다.')}
          />
        )}
        {row && <PipelineEditor key={row.id} pipeline={row} />}
      </PageBody>
    </>
  )
}
