import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { AgentForm } from '@/components/customize/AgentForm'
import { emptyAgent } from '@/components/customize/formModel'
import { BackLink, HubTopBar } from '@/components/customize/DetailLayout'
import { SkillForm } from '@/components/customize/SkillForm'
import { LoadingState } from '@/components/ui'
import { errorMessage, sessionsApi } from '@/lib/api'
import type { ProcessDraft } from '@/lib/process'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { Agent, AgentDraft } from '@/types'

/** 「에이전트로 저장」: `?from=session:<id>` reads the conversation as a draft. Nothing is saved
 *  until the form is. `undefined` while it loads, `null` without one. */
function useSessionDraft(): { draft: AgentDraft | null | undefined; error: string | null } {
  const t = useT()
  const [params] = useSearchParams()
  const from = params.get('from') ?? ''
  const sessionId = from.startsWith('session:') ? from.slice('session:'.length) : ''
  const [state, setState] = useState<{ id: string; draft: AgentDraft | null; error: string | null } | null>(null)
  useEffect(() => {
    if (!sessionId) return
    let current = true
    void sessionsApi
      .agentDraft(sessionId)
      .then((draft) => current && setState({ id: sessionId, draft, error: null }))
      .catch((err) => current && setState({ id: sessionId, draft: null, error: errorMessage(err, t('대화를 읽어 오지 못했습니다.')) }))
    return () => {
      current = false
    }
  }, [sessionId, t])
  if (!sessionId) return { draft: null, error: null }
  if (state?.id !== sessionId) return { draft: undefined, error: null }
  return { draft: state.draft, error: state.error }
}

function fromDraft(base: Agent, d: AgentDraft): Agent {
  return {
    ...base,
    name: d.name,
    description: d.description,
    systemPrompt: d.systemPrompt,
    kinds: d.kinds,
    surface: d.surface,
    renderTemplateId: d.renderTemplateId,
    designSystemId: d.designSystemId,
    skillIds: d.skillIds.length > 0 ? d.skillIds : base.skillIds,
  }
}

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
  const { draft, error } = useSessionDraft()
  const initial = useMemo(() => {
    if (models.length === 0 || draft === undefined) return null
    const blank = emptyAgent(defaultModel)
    return draft ? fromDraft(blank, draft) : blank
  }, [models.length, defaultModel, draft])
  const initialProcess: ProcessDraft | undefined = draft
    ? { mode: draft.mode, base: draft.base, bundles: draft.bundles, steps: [] }
    : undefined

  return (
    <>
      <HubTopBar tab="agents" tabLabel="에이전트" title={t('새 에이전트')} />
      <PageBody>
        <div className="mx-auto max-w-3xl">
        <BackLink tab="agents" label="에이전트" />
        <h1 className="mb-1 text-2xl font-semibold tracking-tight">{t('새 에이전트')}</h1>
        {draft ? (
          <p className="mb-6 rounded-control border border-accent/30 bg-accent-soft/40 px-3 py-2 text-base text-fg" data-testid="draft-notice">
            {t('이 대화에서 정한 것을 채워 두었습니다. 고친 뒤 저장해야 에이전트가 만들어집니다.')}
          </p>
        ) : (
          <p className="mb-6 text-base text-muted">{t('이름과 지시만 있으면 됩니다. 나머지는 나중에 바꿔도 됩니다.')}</p>
        )}
        {error && <p role="alert" className="mb-4 text-sm text-danger">{error}</p>}
        {initial ? (
          <AgentForm
            key={draft ? 'draft' : 'blank'}
            initial={initial}
            initialProcess={initialProcess}
            draftFiles={draft?.files ?? []}
            creating
            onSaved={(saved) => navigate(`/customize/agents/${saved.id}`, { replace: true })}
            onCancel={() => navigate('/customize?tab=agents')}
          />
        ) : (
          <LoadingState label={draft === undefined ? t('대화를 읽는 중입니다') : t('모델 목록을 불러오는 중입니다')} />
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
