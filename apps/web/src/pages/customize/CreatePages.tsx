import { useCallback, useEffect, useMemo, useState } from 'react'
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
import type { Agent, AgentDraft, DistillState } from '@/types'

/** What the draft's 지시 is waiting for: the distillation arrives after the rest of the form. */
interface Decided {
  state: DistillState
  /** The 지시 section to add once it arrives; empty until then and when nothing was settled. */
  prompt: string
}

/** 「에이전트로 저장」: `?from=session:<id>` reads the conversation as a draft. Nothing is saved
 *  until the form is. `undefined` while it loads, `null` without one. The draft comes without
 *  its 지시 first (no model call), then the distillation fills it; a failure says so and can be
 *  asked again. */
function useSessionDraft(): {
  draft: AgentDraft | null | undefined
  error: string | null
  decided: Decided
  retry: () => void
} {
  const t = useT()
  const [params] = useSearchParams()
  const from = params.get('from') ?? ''
  const sessionId = from.startsWith('session:') ? from.slice('session:'.length) : ''
  const [state, setState] = useState<{ id: string; draft: AgentDraft | null; error: string | null } | null>(null)
  const [decided, setDecided] = useState<Decided & { id: string }>({ id: '', state: 'pending', prompt: '' })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!sessionId) return
    let current = true
    void sessionsApi
      .agentDraft(sessionId, false)
      .then((draft) => {
        if (!current) return
        setState({ id: sessionId, draft, error: null })
        // A server that already distilled (no `pending`) has the 지시 in the draft itself.
        if (draft.distilled !== 'pending')
          setDecided({ id: sessionId, state: draft.distilled ?? (draft.systemPrompt ? 'ok' : 'empty'), prompt: '' })
      })
      .catch((err) => current && setState({ id: sessionId, draft: null, error: errorMessage(err, t('대화를 읽어 오지 못했습니다.')) }))
    return () => {
      current = false
    }
  }, [sessionId, t])
  const waiting = state?.id === sessionId && state.draft?.distilled === 'pending'
  useEffect(() => {
    if (!sessionId || !waiting) return
    let current = true
    void sessionsApi
      .agentDraftDecided(sessionId)
      .then((got) => current && setDecided({ id: sessionId, state: got.distilled, prompt: got.prompt }))
      .catch(() => current && setDecided({ id: sessionId, state: 'failed', prompt: '' }))
    return () => {
      current = false
    }
  }, [sessionId, waiting, attempt])
  const retry = useCallback(() => {
    setDecided((d) => ({ ...d, state: 'pending', prompt: '' }))
    setAttempt((n) => n + 1)
  }, [])
  const shown: Decided = decided.id === sessionId ? decided : { state: 'pending', prompt: '' }
  if (!sessionId) return { draft: null, error: null, decided: shown, retry }
  if (state?.id !== sessionId) return { draft: undefined, error: null, decided: shown, retry }
  return { draft: state.draft, error: state.error, decided: shown, retry }
}

/** The banner above a draft: what it says is what the 지시 holds, never 「filled」 when it is not. */
function DraftNotice({ decided, onRetry }: { decided: Decided; onRetry: () => void }) {
  const t = useT()
  const box = 'mb-6 rounded-control border px-3 py-2 text-base text-fg'
  if (decided.state === 'failed')
    return (
      <div className={`${box} flex flex-wrap items-center gap-2 border-warn/40 bg-warn/10`} data-testid="draft-notice" data-state="failed" role="status">
        <span className="min-w-0 flex-1">
          {t('이 대화에서 정한 것을 정리하지 못했습니다. 나머지는 채워 두었습니다. 다시 시도하거나 지시를 직접 적어 주세요.')}
        </span>
        <button type="button" onClick={onRetry} className="rounded-control border border-line bg-panel px-2.5 py-1 text-sm font-medium hover:bg-elevated">
          {t('다시 시도')}
        </button>
      </div>
    )
  const text =
    decided.state === 'pending'
      ? t('나머지는 채워 두었습니다. 이 대화에서 정한 것을 정리하는 중이며, 끝나면 지시에 더합니다.')
      : decided.state === 'empty'
        ? t('이 대화에서 따로 정한 규칙은 찾지 못했습니다. 지시를 확인하고 저장해야 에이전트가 만들어집니다.')
        : t('이 대화에서 정한 것을 채워 두었습니다. 고친 뒤 저장해야 에이전트가 만들어집니다.')
  return (
    <p className={`${box} border-accent/30 bg-accent-soft/40`} data-testid="draft-notice" data-state={decided.state} role="status">
      {text}
    </p>
  )
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
  const { draft, error, decided, retry } = useSessionDraft()
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
          <DraftNotice decided={decided} onRetry={retry} />
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
            addedPrompt={decided.prompt}
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
