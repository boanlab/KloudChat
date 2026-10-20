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
import type { Agent, AgentDraft, DistillState, Skill } from '@/types'

/** Distillation arrives after the rest of the form. */
interface Decided {
  state: DistillState
  /** 지시 section to add on arrival; empty until then or when nothing settled. */
  prompt: string
}

/** 「에이전트로 저장」: `?from=session:<id>` drafts from the conversation; nothing saved until the form is; 지시 filled later by distillation (retryable). */
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
        // Already distilled (no `pending`): 지시 in the draft.
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

/** Says what the 지시 holds; never 「filled」 when it is not. */
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

/** 「이 스킬로 에이전트 만들기」: `?from=skill:<id>` (the person's skill or a store one). */
function useSkillSource(): { skill: Skill | null | undefined; store: boolean; asked: boolean } {
  const [params] = useSearchParams()
  const from = params.get('from') ?? ''
  const skillId = from.startsWith('skill:') ? from.slice('skill:'.length) : ''
  const skills = useStore((s) => s.skills)
  const skillStore = useStore((s) => s.skillStore)
  const workspaceLoading = useStore((s) => s.workspaceLoading)
  const skillStoreLoaded = useStore((s) => s.skillStoreLoaded)
  const loadSkillStore = useStore((s) => s.loadSkillStore)
  useEffect(() => {
    if (skillId && !skillStoreLoaded) void loadSkillStore()
  }, [skillId, skillStoreLoaded, loadSkillStore])
  if (!skillId) return { skill: null, store: false, asked: false }
  const own = skills.find((s) => s.id === skillId)
  if (own) return { skill: own, store: false, asked: true }
  // A store skill already installed: its copy.
  const copy = skills.find((s) => s.originId === skillId)
  if (copy) return { skill: copy, store: false, asked: true }
  if (workspaceLoading || !skillStoreLoaded) return { skill: undefined, store: false, asked: true }
  const row = skillStore.find((s) => s.id === skillId)
  return { skill: row ?? null, store: !!row, asked: true }
}

/** Agent drafted from one skill, kept on in every turn: what it is built from. */
function fromSkill(base: Agent, skill: Skill): Agent {
  const lines = [
    skill.description.trim(),
    `「${skill.name}」 스킬을 매 턴 적용해 일합니다. 스킬의 절차와 형식을 그대로 따릅니다.`,
    skill.whenToUse.trim() && skill.whenToUse.trim() !== skill.description.trim() ? `이럴 때 씁니다: ${skill.whenToUse.trim()}` : '',
  ]
  return {
    ...base,
    name: skill.name,
    description: skill.description,
    systemPrompt: lines.filter(Boolean).join('\n\n'),
    kinds: [...skill.kinds],
    surface: skill.kinds[0] ?? null,
    tools: skill.requiredTools.length > 0 ? [...skill.requiredTools] : base.tools,
    skillIds: [skill.id],
    activeSkillIds: [skill.id],
  }
}

/** Where a save goes: `?return=<path>` (same-app relative only) with `added=<id>`, else the agent. */
function afterSave(back: string | null, id: string): string {
  if (!back || !back.startsWith('/') || back.startsWith('//') || back.startsWith('/\\')) return `/customize/agents/${id}`
  return `${back}${back.includes('?') ? '&' : '?'}added=${encodeURIComponent(id)}`
}

/** Four essentials, rest folded; saving opens the agent. */
export function AgentCreatePage() {
  const t = useT()
  const navigate = useNavigate()
  const ensureWorkspace = useStore((s) => s.ensureWorkspace)
  const ensureDesignTemplates = useStore((s) => s.ensureDesignTemplates)
  useEffect(() => {
    void ensureWorkspace()
    void ensureDesignTemplates()
  }, [ensureWorkspace, ensureDesignTemplates])

  const [params] = useSearchParams()
  const { draft, error, decided, retry } = useSessionDraft()
  const source = useSkillSource()
  // No model pinned: the agent follows the surface's default (`AgentForm` 모델 field).
  const sourceId = source.skill?.id
  const initial = useMemo(() => {
    if (draft === undefined || source.skill === undefined) return null
    const blank = emptyAgent('')
    if (source.skill) return fromSkill(blank, source.skill)
    return draft ? fromDraft(blank, draft) : blank
    // Drafted once per skill, not on every store refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, sourceId, source.skill === undefined])
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
        {source.skill ? (
          <p className="mb-6 rounded-control border border-accent/30 bg-accent-soft/40 px-3 py-2 text-base text-fg" data-testid="skill-draft-notice" role="status">
            {t('「{name}」 스킬로 채워 두었습니다. 이름을 확인하고 저장하세요.').replace('{name}', t(source.skill.name))}
            {source.store && ` ${t('저장하면 이 스킬이 내 스킬에 설치됩니다.')}`}
          </p>
        ) : draft ? (
          <DraftNotice decided={decided} onRetry={retry} />
        ) : (
          <p className="mb-6 text-base text-muted">{t('이름과 지시만 있으면 됩니다. 나머지는 나중에 바꿔도 됩니다.')}</p>
        )}
        {error && <p role="alert" className="mb-4 text-sm text-danger">{error}</p>}
        {source.skill === null && source.asked && (
          <p role="alert" className="mb-4 text-sm text-danger">{t('이 스킬을 찾을 수 없습니다. 빈 에이전트로 시작합니다.')}</p>
        )}
        {initial ? (
          <AgentForm
            key={source.skill ? `skill:${source.skill.id}` : draft ? 'draft' : 'blank'}
            initial={initial}
            initialProcess={initialProcess}
            draftFiles={draft?.files ?? []}
            addedPrompt={decided.prompt}
            creating
            onSaved={(saved) => navigate(afterSave(params.get('return'), saved.id), { replace: true })}
            onCancel={() => navigate('/customize?tab=agents')}
          />
        ) : (
          <LoadingState label={source.asked ? t('스킬을 읽는 중입니다') : t('대화를 읽는 중입니다')} />
        )}
        </div>
      </PageBody>
    </>
  )
}

/** 이름, 설명, steps; saving opens the skill. */
export function SkillCreatePage() {
  const t = useT()
  const navigate = useNavigate()
  const ensureWorkspace = useStore((s) => s.ensureWorkspace)
  useEffect(() => {
    void ensureWorkspace()
  }, [ensureWorkspace])
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
