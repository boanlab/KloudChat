import { Copy, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PipelineSteps } from '@/components/agents/PipelineChain'
import { Button, Card, EmptyState } from '@/components/ui'
import { errorMessage, userPipelinesApi } from '@/lib/api'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Agent } from '@/types'

/** The flows agents ship with: a few plain steps each, copied to edit. One card per shipped agent. */
export function ShippedWorkflows({ agents, focus }: { agents: Agent[]; focus: string | null }) {
  const t = useT()
  const navigate = useNavigate()
  const setNotice = useStore((s) => s.setNotice)
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
    <div className="grid gap-3 sm:grid-cols-2">
      {agents.map((a) => (
        <Card
          key={a.id}
          data-agent={a.id}
          data-testid="shipped-pipeline"
          className={cn('flex flex-col gap-3 p-4', focus === a.id && 'ring-2 ring-accent')}
        >
          <div className="min-w-0">
            <p className="truncate text-base font-semibold">{t(a.name)}</p>
            <p className="text-sm text-faint">{t('기본 제공 · 복제해서 고칠 수 있어요')}</p>
          </div>
          <PipelineSteps agentId={a.id} details />
          <div className="mt-auto pt-1">
            <Button size="sm" variant="primary" disabled={busy} onClick={() => void clone(a)}>
              <Copy size={13} />
              {t('복제해서 고치기')}
            </Button>
          </div>
        </Card>
      ))}
    </div>
  )
}
