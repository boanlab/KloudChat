import { Copy, Lock, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PipelineChain, PipelineFlowView } from '@/components/agents/PipelineChain'
import { Badge, Button, Card, EmptyState } from '@/components/ui'
import { errorMessage, userPipelinesApi } from '@/lib/api'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Agent } from '@/types'

/** The flows agents ship with: locked, copied to edit. One card per shipped agent. */
export function ShippedWorkflows({ agents, focus }: { agents: Agent[]; focus: string | null }) {
  const t = useT()
  const navigate = useNavigate()
  const setNotice = useStore((s) => s.setNotice)
  const [open, setOpen] = useState<string | null>(focus)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!focus) return
    document.querySelector(`[data-agent="${CSS.escape(focus)}"]`)?.scrollIntoView({ block: 'center' })
  }, [focus, agents.length])

  const clone = async (agent: Agent) => {
    setBusy(true)
    try {
      const made = await userPipelinesApi.create({ fromAgentId: agent.id })
      navigate(`/customize/workflows/${made.id}`)
    } catch (err) {
      setNotice(errorMessage(err, t('워크플로를 복제하지 못했습니다.')))
      setBusy(false)
    }
  }

  if (agents.length === 0) {
    return <EmptyState icon={<Workflow size={18} />} title={t('기본 워크플로가 있는 에이전트가 없습니다')} />
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">{t('기본 워크플로는 고칠 수 없습니다. 복제하면 내 워크플로로 고칠 수 있습니다.')}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {agents.map((a) => (
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
