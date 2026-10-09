import { ArrowLeft, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { PipelineEditor } from '@/components/agents/PipelineEditor'
import { EmptyState, LoadingState } from '@/components/ui'
import { userPipelinesApi } from '@/lib/api'
import { isUntouchedPipeline } from '@/lib/userPipelines'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { UserPipeline } from '@/types'

/** Where the editor's list links lead: the hub's 워크플로 tab, 내 항목. */
export const WORKFLOWS_HOME = '/customize?tab=workflows&view=mine'

/** /customize/workflows/:id — one of your flows, full width, its actions pinned to the bottom. */
export function WorkflowEditPage() {
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
  // Leaving a flow that was made and never touched deletes it, so 「워크플로 만들기」
  // followed by 뒤로 leaves no empty 「새 워크플로」 behind.
  useEffect(
    () => () => {
      // Still on this flow's page: a remount (StrictMode), not a leave.
      if (window.location.pathname.endsWith(`/workflows/${pipelineId}`)) return
      void userPipelinesApi
        .get(pipelineId)
        .then((p) => (isUntouchedPipeline(p) ? userPipelinesApi.remove(pipelineId) : undefined))
        .catch(() => undefined)
    },
    [pipelineId],
  )

  return (
    <>
      <TopBar
        left={
          <span className="flex min-w-0 items-center gap-1.5 text-base">
            <Link to={WORKFLOWS_HOME} className="shrink-0 text-muted hover:text-fg">
              {t('워크플로')}
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
          to={WORKFLOWS_HOME}
          className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg focus-visible:outline-2 focus-visible:outline-accent"
        >
          <ArrowLeft size={14} aria-hidden />
          {t('워크플로 목록')}
        </Link>
        {row === undefined && <LoadingState />}
        {row === null && (
          <EmptyState
            icon={<Workflow size={18} />}
            title={t('이 워크플로를 찾을 수 없습니다')}
            description={t('지워졌거나 내 워크플로가 아닙니다.')}
          />
        )}
        {row && <PipelineEditor key={row.id} pipeline={row} onSaved={(saved) => setLoaded({ id: saved.id, row: saved })} />}
      </PageBody>
    </>
  )
}
