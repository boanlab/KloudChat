import { ChevronRight, Globe, Lock } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { AgentKnowledge } from '@/components/agents/AgentKnowledge'
import { GapQuestionsEditor, InheritedQuestions } from '@/components/agents/GapQuestionsEditor'
import { gapFieldId, gapProblems, type GapProblem } from '@/lib/gapQuestions'
import { ProcessSection } from '@/components/agents/ProcessSection'
import { fullSettings, ProcessSettingsEditor } from '@/components/agents/ProcessSettingsEditor'
import { DOC_FORMATS, RETIRED_FORMATS } from '@/components/report/docFormats'
import { Button, Field, Input, Select, Textarea } from '@/components/ui'
import { agentSaveError, type AgentErrorPlace } from '@/lib/agentErrors'
import { agentsApi, errorMessage, processApi, templateText } from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { processOf, processPayload, useProcessCatalogue, type ProcessDraft } from '@/lib/process'
import { agentKinds, kindMeta, kindOrder } from '@/lib/kinds'
import { NAME_LIMIT } from '@/lib/limits'
import { useTemplateCatalogue } from '@/lib/templates'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Agent, SessionKind } from '@/types'
import { slugify } from './formModel'
import { isListed } from '@/lib/listed'
import { SkillPicker, ToolPicker } from './Pickers'

/** The draft with `kinds`, minus the bindings those kinds no longer offer. */
function withinKinds(draft: Agent, kinds: SessionKind[]): Agent {
  const next = { ...draft, kinds }
  const offered = agentKinds(next)
  const templates = useStore.getState().designTemplates
  const template = templates.find((row) => row.id === draft.renderTemplateId)
  return {
    ...next,
    surface: next.surface && offered.includes(next.surface) ? next.surface : null,
    renderTemplateId: template && !offered.includes(template.surface) ? null : next.renderTemplateId,
    docFormat: offered.includes('report') ? next.docFormat : null,
  }
}

/** One folded group of the longer settings. Closed until opened. */
export function Group({
  title,
  hint,
  children,
  testId,
  open,
}: {
  title: string
  hint?: string
  children: ReactNode
  testId?: string
  open?: boolean
}) {
  return (
    <details className="group rounded-card border border-line bg-panel" data-testid={testId} open={open}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 select-none [&::-webkit-details-marker]:hidden">
        <ChevronRight size={15} aria-hidden className="shrink-0 text-faint transition-transform group-open:rotate-90" />
        <span className="min-w-0 flex-1">
          <span className="block text-base font-medium">{title}</span>
          {hint && <span className="block text-sm text-faint">{hint}</span>}
        </span>
      </summary>
      <div className="space-y-4 border-t border-line px-4 py-4">{children}</div>
    </details>
  )
}

/** A labelled block that is not a `<label>`: pickers and button rows live inside. */
export function Block({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <p className="text-base font-medium text-fg">{label}</p>
      {children}
      {hint && <p className="text-sm text-faint">{hint}</p>}
    </div>
  )
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(
        'rounded-full border px-3 py-1.5 text-base transition-colors',
        on ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:bg-elevated',
      )}
    >
      {children}
    </button>
  )
}

/** 나만 / 모두에게, and for a shared original whether copies may read its instructions. */
export function AgentSharing({ draft, onChange }: { draft: Agent; onChange: (next: Agent) => void }) {
  const t = useT()
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {(
          [
            { id: 'private', label: t('나만 쓰기'), icon: Lock },
            { id: 'org', label: t('모두에게 공개'), icon: Globe },
          ] as const
        ).map((o) => {
          const Icon = o.icon
          return (
            <button
              key={o.id}
              type="button"
              aria-pressed={draft.visibility === o.id}
              onClick={() => onChange({ ...draft, visibility: o.id })}
              className={cn(
                'flex items-center gap-1.5 rounded-control border px-2.5 py-1.5 text-base transition-colors',
                draft.visibility === o.id ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:bg-elevated',
              )}
            >
              <Icon size={13} />
              {o.label}
            </button>
          )
        })}
      </div>
      <p className="text-sm text-faint">
        {t('공개하면 이 인스턴스에 로그인한 누구나 스토어에서 복사해 갈 수 있습니다. 원본은 계속 내 것입니다.')}
      </p>
      {draft.visibility === 'org' && !draft.sealed && (
        <div className="space-y-2">
          {(
            [
              { id: 'open', label: t('가져가서 편집할 수 있게'), note: [t('복사본에 지침이 함께 가고, 가져간 사람이 마음대로 고칩니다.')] },
              {
                id: 'sealed',
                label: t('가져갈 수 있지만 내용은 비공개'),
                note: [
                  t('복사본은 편집할 수 없고 내 원본의 지침으로 동작합니다.'),
                  t('가져간 사람은 실행과 삭제만 할 수 있고, 내가 원본을 고치면 복사본도 따라옵니다.'),
                ],
              },
            ] as const
          ).map((o) => (
            <label key={o.id} className="flex cursor-pointer items-start gap-3 rounded-control border border-line px-3 py-2">
              <input
                type="radio"
                name={`share-mode-${draft.id}`}
                checked={draft.shareMode === o.id}
                onChange={() => onChange({ ...draft, shareMode: o.id })}
                className="mt-1 size-4 accent-[var(--accent)]"
              />
              <span className="min-w-0">
                <span className="text-base">{o.label}</span>
                {o.note.map((line) => (
                  <span key={line} className="block text-sm text-muted">
                    {line}
                  </span>
                ))}
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

/** A swatch row for the agent's colour on cards and its avatar. */
const COLORS = ['#5b53e8', '#2563eb', '#0f766e', '#334155', '#ea580c', '#db2777', '#7c3aed', '#0369a1']

/**
 * The agent form: name, instructions, 진행 과정 and 추가로 할 일 in view, the rest in folded
 * groups (아티팩트, 대화 시작, 자료, 스킬과 도구, 고급, 공유). The same form creates an agent (from
 * scratch, or prefilled by 「에이전트로 저장」) and edits one in its detail screen's 「설정」.
 * Nothing is saved before 저장.
 */
/** The folded group (or block) of the form each kind of refusal points to. */
const PLACE_TESTID: Record<AgentErrorPlace, string> = {
  name: 'agent-form',
  gap: 'agent-gap-group',
  settings: 'agent-settings-group',
  output: 'agent-output-group',
  tools: 'agent-tools-group',
  advanced: 'agent-more-group',
  process: 'agent-process',
}

export function AgentForm({
  initial,
  creating,
  onSaved,
  onCancel,
  aboveBottomBar = false,
  initialProcess,
  draftFiles = [],
  addedPrompt = '',
}: {
  initial: Agent
  creating: boolean
  onSaved: (saved: Agent) => void
  onCancel?: () => void
  /** A detail screen's phone action bar sits below; the save bar stops above it. */
  aboveBottomBar?: boolean
  /** 「에이전트로 저장」: the 진행 과정 the conversation suggests. */
  initialProcess?: ProcessDraft
  /** 「에이전트로 저장」: the conversation's files, offered for the agent's shelf. */
  draftFiles?: { id: string; name: string; size: number }[]
  /** 「에이전트로 저장」: the 지시 section distilled after the form opened, added once. */
  addedPrompt?: string
}) {
  const t = useT()
  const agents = useStore((s) => s.agents)
  const models = useStore((s) => s.models)
  const skills = useStore((s) => s.skills)
  const availableTools = useStore((s) => s.availableTools)
  const designs = useStore((s) => s.designs)
  const designTemplates = useStore((s) => s.designTemplates)
  const upsertAgent = useStore((s) => s.upsertAgent)
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const english = currentLang() === 'en'

  const [draft, setDraft] = useState<Agent>(initial)
  // Set once the agent exists on the server: a first knowledge file saves it early.
  const [savedId, setSavedId] = useState<string | null>(creating ? null : initial.id)
  const [editingSlug, setEditingSlug] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [nameError, setNameError] = useState<string | null>(null)
  // 빠진 정보 질문 fields a save stopped on, by question id.
  const [gapErrors, setGapErrors] = useState<Record<string, GapProblem>>({})
  const formRef = useRef<HTMLFormElement>(null)
  const [baseline, setBaseline] = useState<ProcessDraft>(() => initialProcess ?? processOf(initial))
  const [process, setProcess] = useState<ProcessDraft>(baseline)
  const [processSaved, setProcessSaved] = useState(!creating)
  const processDirty = !processSaved || JSON.stringify(process) !== JSON.stringify(baseline)
  const [keepFiles, setKeepFiles] = useState<string[]>(() => draftFiles.map((f) => f.id))
  const catalogue = useProcessCatalogue()
  const templateCatalogue = useTemplateCatalogue()
  const detectorLabels = Object.fromEntries((catalogue?.gapDetectors ?? []).map((d) => [d.name, d.label]))
  const nameRef = useRef<HTMLInputElement>(null)

  const draftSlug = slugify(draft.slug || draft.name)
  const slugTaken = agents.some((a) => a.id !== draft.id && a.slug === draftSlug)
  const offered = agentKinds(draft)
  const pickedBase = process.mode === 'base' ? catalogue?.bases.find((b) => b.key === process.base) : undefined
  // A base fixes the surface a conversation opens on: its templates are offered too.
  const templateSurfaces = pickedBase ? [...new Set([pickedBase.surface, ...offered])] : offered
  // A hidden 서식 or theme (`listed: false`) is not offered; the one the agent already has stays.
  const boundTemplates = designTemplates.filter(
    (row) =>
      row.kind !== 'image' &&
      templateSurfaces.includes(row.surface) &&
      (isListed(row) || row.id === draft.renderTemplateId),
  )
  const pickableDesigns = designs.filter((d) => isListed(d) || d.id === draft.designSystemId)
  const surface: SessionKind = draft.surface ?? offered[0] ?? 'chat'
  // A format retired with its agent stays where it is set; nothing new picks it.
  const formats = DOC_FORMATS.filter((f) => !RETIRED_FORMATS.includes(f.id) || f.id === initial.docFormat)
  const edit = (next: Agent) => {
    setDone(false)
    setDraft(next)
  }

  // The distillation arrives after the form is usable: added below whatever is written by then.
  const added = useRef('')
  useEffect(() => {
    if (!addedPrompt || added.current === addedPrompt) return
    added.current = addedPrompt
    setDraft((d) =>
      d.systemPrompt.includes(addedPrompt)
        ? d
        : { ...d, systemPrompt: d.systemPrompt.trim() ? `${d.systemPrompt.trimEnd()}\n\n${addedPrompt}` : addedPrompt },
    )
  }, [addedPrompt])

  /** Opens the part of the form a refusal points to, and brings it into view. */
  const reveal = useCallback((place: AgentErrorPlace | undefined, focusId?: string) => {
    if (!place) return
    if (place === 'name') {
      nameRef.current?.focus()
      return
    }
    const box = formRef.current?.querySelector<HTMLElement>(`[data-testid="${PLACE_TESTID[place]}"]`)
    if (box instanceof HTMLDetailsElement) box.open = true
    // After the group opens, so the field can take focus.
    window.requestAnimationFrame(() => {
      const field = focusId ? document.getElementById(focusId) : null
      ;(field ?? box)?.scrollIntoView({ block: 'center' })
      field?.focus()
    })
  }, [])

  const persist = useCallback(async (): Promise<Agent | null> => {
    if (!draft.name.trim()) {
      setNameError(t('이름을 입력하세요.'))
      nameRef.current?.focus()
      return null
    }
    // The questions are checked here first, each field said beside it, never as a code.
    const gaps = gapProblems(draft.gapQuestions ?? [])
    setGapErrors(gaps)
    const firstGap = (draft.gapQuestions ?? []).find((q) => gaps[q.id])
    if (firstGap) {
      setError(t('빠진 정보 질문에 채우지 않은 칸이 있습니다. * 표시한 칸을 채워 주세요.'))
      reveal('gap', gapFieldId(firstGap.id, gaps[firstGap.id].field))
      return null
    }
    setSaving(true)
    setError(null)
    try {
      // The on/off switch lives in the detail header; the form never overrides it.
      const live = useStore.getState().agents.find((a) => a.id === draft.id)
      const saved = await upsertAgent({
        ...draft,
        enabled: live?.enabled ?? draft.enabled,
        slug: draftSlug,
        updatedAt: new Date().toISOString(),
      })
      setDraft((d) => ({ ...d, id: saved.id, slug: saved.slug }))
      setSavedId(saved.id)
      return saved
    } catch (err) {
      const said = agentSaveError(err, t('저장하지 못했습니다. 입력한 내용을 확인해 주세요.'))
      setError(t(said.message))
      reveal(said.place)
      return null
    } finally {
      setSaving(false)
    }
  }, [draft, draftSlug, upsertAgent, t, reveal])

  const save = async () => {
    const saved = await persist()
    if (!saved) return
    setSaving(true)
    try {
      if (processDirty) {
        try {
          const out = await processApi.put(saved.id, processPayload(process))
          setBaseline(process)
          setProcessSaved(true)
          if (out.warnings?.length) setError(out.warnings.map((w) => t(w.message)).join(' '))
        } catch (err) {
          const said = agentSaveError(err, t('진행 과정을 저장하지 못했습니다. 단계를 확인해 주세요.'))
          setError(t(said.message))
          reveal(said.place ?? 'process')
          return
        }
      }
      if (keepFiles.length > 0) {
        try {
          await agentsApi.knowledge.fromFiles(saved.id, keepFiles)
          setKeepFiles([])
        } catch (err) {
          setError(errorMessage(err, t('대화의 파일을 자료로 옮기지 못했습니다.')))
        }
      }
      await loadWorkspace()
    } finally {
      setSaving(false)
    }
    setDone(true)
    onSaved(useStore.getState().agents.find((a) => a.id === saved.id) ?? saved)
  }

  // Stable: the knowledge list reloads whenever this callback changes.
  const knowledgeChanged = useCallback(
    (hasKnowledge: boolean) => {
      setDraft((d) => (d.hasKnowledge === hasKnowledge ? d : { ...d, hasKnowledge }))
      // The composer reads the saved agent, not this draft.
      void loadWorkspace()
    },
    [loadWorkspace],
  )

  const needAgent = useCallback(async () => (await persist())?.id ?? null, [persist])

  return (
    <form
      ref={formRef}
      className="space-y-5"
      data-testid="agent-form"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      {error && (
        <p role="status" className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger">
          {error}
        </p>
      )}

      <div className="space-y-1.5">
        <Field label={t('이름')}>
          <Input
            ref={nameRef}
            value={draft.name}
            maxLength={NAME_LIMIT}
            aria-required
            aria-invalid={nameError ? true : undefined}
            onChange={(e) => {
              setNameError(null)
              edit({ ...draft, name: e.target.value })
            }}
            placeholder={t('예: 기술 검토 도우미')}
          />
        </Field>
        {nameError && (
          <p role="alert" className="text-sm text-danger">
            {nameError}
          </p>
        )}
        {/* The handle follows the name until someone changes it. */}
        {editingSlug ? (
          <Field label={t('핸들')} hint={t('입력창의 @ 버튼과 목록에서 이 이름으로 부릅니다.')}>
            <Input
              value={draft.slug}
              aria-invalid={slugTaken || undefined}
              onChange={(e) => edit({ ...draft, slug: e.target.value })}
              placeholder={draftSlug}
              className="font-mono"
            />
          </Field>
        ) : draft.name.trim() || draft.slug ? (
          <p className="flex items-center gap-2 text-sm text-faint">
            <span className="font-mono">@{draftSlug}</span>
            <button type="button" className="text-accent hover:underline" onClick={() => setEditingSlug(true)}>
              {t('바꾸기')}
            </button>
          </p>
        ) : null}
        {slugTaken && (
          <p role="alert" className="text-sm text-danger">
            {t('이미 쓰는 핸들입니다. 다른 핸들을 붙이세요.')}
          </p>
        )}
      </div>

      <Field label={t('한 줄 설명')}>
        <Input
          value={draft.description}
          onChange={(e) => edit({ ...draft, description: e.target.value })}
          placeholder={t('예: 논문 초안을 학회 심사 기준으로 검토합니다')}
        />
      </Field>

      <Field
        label={t('지시')}
        hint={t('이 에이전트가 늘 따를 역할과 규칙입니다. [채팅에서] [보고서에서] 줄 아래에 쓴 것은 그 화면에서만 씁니다.')}
      >
        <Textarea
          rows={7}
          value={draft.systemPrompt}
          onChange={(e) => edit({ ...draft, systemPrompt: e.target.value })}
          placeholder={t('예: 너는 학회 심사위원이다. 기여·방법·실험을 나눠 근거와 함께 지적한다.')}
        />
      </Field>

      <ProcessSection
        agent={{ ...draft, id: savedId ?? '' }}
        value={process}
        surface={surface}
        onSurface={(next) => {
          const before = draft
          edit({ ...withinKinds(draft, draft.kinds.includes(next) ? draft.kinds : [...draft.kinds, next]), surface: next })
          // What the switch may have changed, as it was.
          return () => {
            setDone(false)
            setDraft((d) => ({
              ...d,
              kinds: before.kinds,
              surface: before.surface,
              renderTemplateId: before.renderTemplateId,
              docFormat: before.docFormat,
            }))
          }
        }}
        onChange={(next) => {
          setDone(false)
          setProcess(next)
        }}
        questions={draft.gapQuestions ?? undefined}
        onAskQuestions={() => {
          const rows = draft.gapQuestions ?? draft.baseQuestions ?? []
          const taken = new Set(rows.map((r) => r.id))
          let n = rows.length + 1
          while (taken.has(`q${n}`)) n += 1
          const fresh = { id: `q${n}`, label: '', question: '', why: '', placeholder: '', detector: { type: 'keyword' as const, words: [] } }
          edit({ ...draft, gapQuestions: [...rows, fresh] })
          reveal('gap', gapFieldId(fresh.id, 'question'))
        }}
      />

      {templateCatalogue && (offered.includes('report') || pickedBase?.surface === 'report') && (
        <Group
          title={t('세부 설정')}
          hint={`${t('진행 과정의 계산·검사·목차')} · ${draft.processSettings ? t('이 에이전트에서 따로 정함') : t('템플릿 설정을 따름')}`}
          testId="agent-settings-group"
        >
          <label className="flex items-start gap-2 text-base">
            <input
              type="checkbox"
              className="mt-1 size-4 accent-[var(--accent)]"
              checked={!!draft.processSettings}
              onChange={(e) =>
                edit({
                  ...draft,
                  processSettings: e.target.checked
                    ? fullSettings(draft.inheritedSettings ?? templateCatalogue.process.defaults, null)
                    : null,
                })
              }
            />
            <span>
              {t('이 에이전트에서 따로 정하기')}
              <span className="block text-sm text-muted">
                {t('끄면 템플릿(또는 문서 형식)이 정한 계산·검사·구조를 그대로 씁니다. 켜면 그 값에서 시작해 바꿉니다.')}
              </span>
            </span>
          </label>
          {draft.processSettings && (
            <ProcessSettingsEditor
              value={fullSettings(draft.inheritedSettings ?? templateCatalogue.process.defaults, draft.processSettings)}
              catalogue={templateCatalogue.process}
              onChange={(next) => edit({ ...draft, processSettings: next })}
            />
          )}
        </Group>
      )}

      <Group
        title={t('빠진 정보 질문')}
        hint={t('쓰기 전에 자료에 없으면 물어볼 것. 「빠진 정보 먼저 묻기」가 켜져 있을 때 한 번에 네 개까지 묻습니다.')}
        testId="agent-gap-group"
        open={(draft.gapQuestions?.length ?? 0) > 0 || undefined}
      >
        <InheritedQuestions rows={draft.inheritedQuestions ?? []} labels={detectorLabels} />
        <GapQuestionsEditor
          value={draft.gapQuestions ?? draft.baseQuestions ?? []}
          detectors={catalogue?.gapDetectors ?? []}
          problems={gapErrors}
          onChange={(next) => {
            // A field said to be missing stops being said once it is filled.
            if (Object.keys(gapErrors).length > 0) {
              const still = gapProblems(next)
              setGapErrors(Object.fromEntries(Object.entries(still).filter(([id]) => id in gapErrors)))
            }
            edit({ ...draft, gapQuestions: next })
          }}
        />
      </Group>

      <div className="space-y-2" data-testid="agent-advanced">
        <p className="text-sm font-semibold text-faint">{t('더 설정하기')}</p>

        <Group
          title={t('아티팩트 모양')}
          hint={offered.includes('report') && process.mode !== 'base' ? t('템플릿, 테마, 문서 형식') : t('템플릿, 테마')}
          testId="agent-output-group"
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
            <Field label={t('템플릿')} hint={t('보고서·슬라이드 대화가 이 템플릿으로 시작합니다.')}>
              <Select value={draft.renderTemplateId ?? ''} onChange={(e) => edit({ ...draft, renderTemplateId: e.target.value || null })}>
                <option value="">{process.mode === 'base' ? t('기본 과정의 템플릿') : t('정하지 않음')}</option>
                {boundTemplates.map((row) => (
                  <option key={row.id} value={row.id}>
                    {`${t(kindMeta[row.surface].label)} · ${templateText(row, english).name}`}
                  </option>
                ))}
              </Select>
            </Field>
              <Link to="/customize/templates/new" className="inline-block text-sm text-accent hover:underline" data-testid="new-template-link">
                {t('새 템플릿')}
              </Link>
            </div>
            <div className="space-y-1.5">
              <Field label={t('테마')} hint={t('프로젝트의 테마보다 먼저 씁니다.')}>
                <Select value={draft.designSystemId ?? ''} onChange={(e) => edit({ ...draft, designSystemId: e.target.value || null })}>
                  <option value="">{t('정하지 않음')}</option>
                  {pickableDesigns.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                  {draft.designSystemId && !designs.some((d) => d.id === draft.designSystemId) && (
                    <option value={draft.designSystemId}>{t('다른 사람의 테마')}</option>
                  )}
                </Select>
              </Field>
              <Link to="/settings/themes" className="inline-block text-sm text-accent hover:underline">
                {t('테마 관리')}
              </Link>
            </div>
            {offered.includes('report') && process.mode !== 'base' && (
              <Field label={t('문서 형식')} hint={t('템플릿이 형식을 정하지 않을 때 보고서의 머리 항목과 절 규칙을 이것으로 고정합니다.')}>
                <Select value={draft.docFormat ?? ''} onChange={(e) => edit({ ...draft, docFormat: e.target.value || null })}>
                  <option value="">{t('요청에 따라')}</option>
                  {formats.map((f) => (
                    <option key={f.id} value={f.id}>
                      {t(f.label)}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
          </div>
        </Group>

        <Group title={t('대화 시작')} hint={t('사용법, 시작 문장, 담당 부서·연락처')}>
          <Field label={t('사용법')} hint={t('대화를 열면 첫 화면에 보입니다. 무엇을 가져와야 하는지, 한 턴에 무슨 일이 일어나는지.')}>
            <Textarea
              rows={3}
              value={draft.guide}
              onChange={(e) => edit({ ...draft, guide: e.target.value.slice(0, 2000) })}
              placeholder={t('예: 논문 PDF 를 첨부하고 어느 학회 기준인지 알려 주세요. 리뷰어 관점에서 검토합니다.')}
            />
          </Field>
          <Field label={t('시작 문장')} hint={t('한 줄에 하나, 여섯 개까지. 첫 화면에 버튼으로 보이고 누르면 그대로 보냅니다.')}>
            <Textarea
              rows={4}
              value={draft.starters.join('\n')}
              onChange={(e) => edit({ ...draft, starters: e.target.value.split('\n').slice(0, 6) })}
              placeholder={t('첨부한 논문을 리뷰어 관점에서 검토해 줘')}
            />
          </Field>
          <Field
            label={t('담당 부서·연락처')}
            hint={t('자료로 답할 수 없는 질문에 이 내용을 적힌 그대로 안내합니다. 비워 두면 「담당 부서에 문의해 주세요」라고 답합니다.')}
          >
            <Input
              value={draft.contact ?? ''}
              maxLength={300}
              onChange={(e) => edit({ ...draft, contact: e.target.value.slice(0, 300) })}
              placeholder={t('예: 학사지원팀 02-760-1234, haksa@example.ac.kr')}
            />
          </Field>
        </Group>

        <Group
          title={t('자료')}
          hint={t('이 에이전트가 찾아 답할 파일과 웹 페이지')}
          open={draftFiles.length > 0 || undefined}
          testId="agent-knowledge-group"
        >
          {draftFiles.length > 0 && (
            <fieldset className="space-y-1.5" data-testid="draft-files">
              <legend className="mb-1 text-base font-medium">{t('이 대화의 파일을 자료로 함께 저장')}</legend>
              {draftFiles.map((f) => (
                <label key={f.id} className="flex items-center gap-2 text-base">
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--accent)]"
                    checked={keepFiles.includes(f.id)}
                    onChange={() =>
                      setKeepFiles((ids) => (ids.includes(f.id) ? ids.filter((x) => x !== f.id) : [...ids, f.id]))
                    }
                  />
                  <span className="min-w-0 truncate">{f.name}</span>
                </label>
              ))}
            </fieldset>
          )}
          <AgentKnowledge agentId={savedId} onNeedAgent={needAgent} onKnowledgeChange={knowledgeChanged} />
        </Group>

        <Group title={t('스킬과 도구')} hint={t('쓸 수 있는 스킬과 도구를 좁힙니다')} testId="agent-tools-group">
          <Block label={t('스킬')}>
            <SkillPicker value={draft.skillIds} onChange={(skillIds) => edit({ ...draft, skillIds })} skills={skills} />
          </Block>
          <Block label={t('도구')}>
            <ToolPicker
              value={draft.tools}
              onChange={(tools) => edit({ ...draft, tools })}
              tools={availableTools}
              hasKnowledge={draft.hasKnowledge}
            />
          </Block>
        </Group>

        <Group title={t('고급')} hint={t('사용할 화면, 모델, 다양성, 색')} testId="agent-more-group">
          <Block label={t('사용할 화면')} hint={t('여기서 고른 화면의 @ 버튼에만 나타납니다.')}>
            <div className="flex flex-wrap gap-1.5">
              {kindOrder.map((k) => {
                const on = draft.kinds.includes(k)
                return (
                  <Chip
                    key={k}
                    on={on}
                    onClick={() => edit(withinKinds(draft, on ? draft.kinds.filter((x) => x !== k) : [...draft.kinds, k]))}
                  >
                    {t(kindMeta[k].label)}
                  </Chip>
                )
              })}
            </div>
          </Block>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t('처음 여는 화면')} hint={t('이 에이전트로 새 대화를 열 때의 화면입니다.')}>
              <Select value={draft.surface ?? ''} onChange={(e) => edit({ ...draft, surface: (e.target.value || null) as SessionKind | null })}>
                <option value="">{t('첫 번째 화면')}</option>
                {offered.map((k) => (
                  <option key={k} value={k}>
                    {t(kindMeta[k].label)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t('모델')}>
              <Select value={draft.model} onChange={(e) => edit({ ...draft, model: e.target.value })}>
                <option value="">{t('화면 기본 모델')}</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label={`${t('다양성')} — ${draft.temperature}`}
              hint={t('낮을수록 일관되게, 높을수록 다양하게 답합니다. 대화 화면에만 적용됩니다.')}
            >
              <input
                type="range"
                min={0}
                max={1}
                step={0.1}
                value={draft.temperature}
                onChange={(e) => edit({ ...draft, temperature: Number(e.target.value) })}
                className="h-9 w-full accent-[var(--accent)]"
              />
            </Field>
            <Block label={t('색')} hint={t('카드와 목록에서 이 에이전트를 알아보는 색입니다.')}>
              <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={t('색')}>
                {COLORS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    role="radio"
                    aria-checked={draft.color === c}
                    aria-label={c}
                    onClick={() => edit({ ...draft, color: c })}
                    className={cn('size-7 rounded-full border-2', draft.color === c ? 'border-fg' : 'border-transparent')}
                    style={{ backgroundColor: c }}
                  />
                ))}
              </div>
            </Block>
          </div>
        </Group>

        <Group title={t('공유')} hint={draft.visibility === 'org' ? t('모두에게 공개') : t('나만 쓰기')}>
          <AgentSharing draft={draft} onChange={edit} />
        </Group>
      </div>

      <div className={cn('sticky bottom-0 z-10 -mx-4 flex items-center justify-end gap-2 border-t border-line bg-bg/95 px-4 py-3 sm:-mx-6 sm:px-6', aboveBottomBar && 'max-sm:bottom-[4.25rem]')}>
        {done && !creating && (
          <span role="status" className="mr-auto text-sm text-success">
            {t('저장했습니다.')}
          </span>
        )}
        {onCancel && (
          <Button type="button" onClick={onCancel}>
            {t('취소')}
          </Button>
        )}
        <Button type="submit" variant="primary" disabled={saving || slugTaken}>
          {saving ? t('저장 중…') : creating && !savedId ? t('만들기') : t('저장')}
        </Button>
      </div>
    </form>
  )
}
