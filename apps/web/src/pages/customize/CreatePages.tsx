import { useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { AgentForm } from '@/components/customize/AgentForm'
import { emptyAgent } from '@/components/customize/formModel'
import { BackLink, HubTopBar } from '@/components/customize/DetailLayout'
import { SkillForm } from '@/components/customize/SkillForm'
import { LoadingState } from '@/components/ui'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** /customize/agents/new — four essentials, the rest folded; saving opens the agent. */
export function AgentCreatePage() {
  const t = useT()
  const navigate = useNavigate()
  const models = useStore((s) => s.models)
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const ensureDesignTemplates = useStore((s) => s.ensureDesignTemplates)
  useEffect(() => {
    void loadWorkspace()
    void ensureDesignTemplates()
  }, [loadWorkspace, ensureDesignTemplates])

  // Cheapest chat model, the same rule the surface defaults use.
  const defaultModel =
    [...models].filter((m) => m.kinds.includes('chat')).sort((a, b) => a.creditCost - b.creditCost)[0]?.id ?? ''
  const initial = useMemo(() => (models.length > 0 ? emptyAgent(defaultModel) : null), [models.length, defaultModel])

  return (
    <>
      <HubTopBar tab="agents" tabLabel="에이전트" title={t('새 에이전트')} />
      <PageBody>
        <div className="mx-auto max-w-3xl">
        <BackLink tab="agents" label="에이전트" />
        <h1 className="mb-1 text-2xl font-semibold tracking-tight">{t('새 에이전트')}</h1>
        <p className="mb-6 text-base text-muted">{t('이름과 지시만 있으면 됩니다. 나머지는 나중에 바꿔도 됩니다.')}</p>
        {initial ? (
          <AgentForm
            initial={initial}
            creating
            onSaved={(saved) => navigate(`/customize/agents/${saved.id}`, { replace: true })}
            onCancel={() => navigate('/customize?tab=agents')}
          />
        ) : (
          <LoadingState label={t('모델 목록을 불러오는 중입니다')} />
        )}
        </div>
      </PageBody>
    </>
  )
}

/** /customize/skills/new — 이름, 설명 and the steps; saving opens the skill. */
export function SkillCreatePage() {
  const t = useT()
  const navigate = useNavigate()
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  return (
    <>
      <HubTopBar tab="skills" tabLabel="스킬" title={t('새 스킬')} />
      <PageBody>
        <div className="mx-auto max-w-3xl">
        <BackLink tab="skills" label="스킬" />
        <h1 className="mb-1 text-2xl font-semibold tracking-tight">{t('새 스킬')}</h1>
        <p className="mb-6 text-base text-muted">{t('모델이 그대로 따를 절차를 적습니다. 입력창에서 이번 요청에 켜서 씁니다.')}</p>
        <SkillForm
          onSaved={(saved) => navigate(`/customize/skills/${saved.id}`, { replace: true })}
          onCancel={() => navigate('/customize?tab=skills')}
        />
        </div>
      </PageBody>
    </>
  )
}
