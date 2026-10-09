import { ChevronRight, Globe, Lock } from 'lucide-react'
import { useCallback, useRef, useState, type ReactNode } from 'react'
import { AgentKnowledge } from '@/components/agents/AgentKnowledge'
import { AgentFlowSummary } from '@/components/agents/PipelineEditor'
import { DOC_FORMATS } from '@/components/report/docFormats'
import { Button, Field, Input, Select, Textarea } from '@/components/ui'
import { errorCode, errorMessage, templateText, userPipelinesApi } from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { agentKinds, kindMeta, kindOrder } from '@/lib/kinds'
import { NAME_LIMIT } from '@/lib/limits'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Agent, SessionKind } from '@/types'
import { slugify } from './formModel'
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
export function Group({ title, hint, children, testId }: { title: string; hint?: string; children: ReactNode; testId?: string }) {
  return (
    <details className="group rounded-card border border-line bg-panel" data-testid={testId}>
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

/**
 * The agent form: four essentials in view, everything else in folded groups. The same form
 * creates an agent and edits one in its detail screen's 「설정」.
 */
export function AgentForm({
  initial,
  creating,
  onSaved,
  onCancel,
  aboveBottomBar = false,
}: {
  initial: Agent
  creating: boolean
  onSaved: (saved: Agent) => void
  onCancel?: () => void
  /** A detail screen's phone action bar sits below; the save bar stops above it. */
  aboveBottomBar?: boolean
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
  const [flowPick, setFlowPick] = useState<string | null | undefined>(undefined)
  const nameRef = useRef<HTMLInputElement>(null)

  const draftSlug = slugify(draft.slug || draft.name)
  const slugTaken = agents.some((a) => a.id !== draft.id && a.slug === draftSlug)
  const offered = agentKinds(draft)
  const boundTemplates = designTemplates.filter((row) => row.kind !== 'image' && offered.includes(row.surface))
  const edit = (next: Agent) => {
    setDone(false)
    setDraft(next)
  }

  const persist = useCallback(async (): Promise<Agent | null> => {
    if (!draft.name.trim()) {
      setNameError(t('이름을 입력하세요.'))
      nameRef.current?.focus()
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
      setError(
        errorCode(err) === 'slug_taken'
          ? t('이미 쓰는 핸들입니다. 다른 핸들을 붙이세요.')
          : errorMessage(err, t('저장하지 못했습니다.')),
      )
      return null
    } finally {
      setSaving(false)
    }
  }, [draft, draftSlug, upsertAgent, t])

  const save = async () => {
    const saved = await persist()
    if (!saved) return
    if (flowPick !== undefined) {
      try {
        await userPipelinesApi.bind(saved.id, flowPick)
        setFlowPick(undefined)
      } catch (err) {
        setError(errorMessage(err, t('이 워크플로를 묶지 못했습니다. 워크플로의 단계를 확인해 주세요.')))
        return
      }
    }
    setDone(true)
    onSaved(saved)
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

  const savedAgent = savedId ? agents.find((a) => a.id === savedId) ?? null : null

  return (
    <form
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
        ) : (
          <p className="flex items-center gap-2 text-sm text-faint">
            <span className="font-mono">@{draftSlug}</span>
            <button type="button" className="text-accent hover:underline" onClick={() => setEditingSlug(true)}>
              {t('바꾸기')}
            </button>
          </p>
        )}
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

      <Field label={t('지시')} hint={t('이 에이전트가 늘 따를 역할과 규칙입니다.')}>
        <Textarea
          rows={7}
          value={draft.systemPrompt}
          onChange={(e) => edit({ ...draft, systemPrompt: e.target.value })}
          placeholder={t('예: 너는 학회 심사위원이다. 기여·방법·실험을 나눠 근거와 함께 지적한다.')}
        />
      </Field>

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

      <div className="space-y-2" data-testid="agent-advanced">
        <p className="text-sm font-semibold text-faint">{t('더 설정하기')}</p>

        <Group title={t('결과물 모양')} hint={t('처음 여는 화면, 양식, 문서 형식, 디자인')}>
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
            <Field label={t('양식')} hint={t('보고서·슬라이드 대화가 이 양식으로 시작합니다.')}>
              <Select value={draft.renderTemplateId ?? ''} onChange={(e) => edit({ ...draft, renderTemplateId: e.target.value || null })}>
                <option value="">{t('정하지 않음')}</option>
                {boundTemplates.map((row) => (
                  <option key={row.id} value={row.id}>
                    {`${t(kindMeta[row.surface].label)} · ${templateText(row, english).name}`}
                  </option>
                ))}
              </Select>
            </Field>
            {offered.includes('report') && (
              <Field label={t('문서 형식')} hint={t('양식이 형식을 정하지 않을 때 보고서의 머리 항목과 절 규칙을 이것으로 고정합니다.')}>
                <Select value={draft.docFormat ?? ''} onChange={(e) => edit({ ...draft, docFormat: e.target.value || null })}>
                  <option value="">{t('요청에 따라')}</option>
                  {DOC_FORMATS.map((f) => (
                    <option key={f.id} value={f.id}>
                      {t(f.label)}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            <Field label={t('디자인')} hint={t('프로젝트의 디자인보다 먼저 씁니다.')}>
              <Select value={draft.designSystemId ?? ''} onChange={(e) => edit({ ...draft, designSystemId: e.target.value || null })}>
                <option value="">{t('정하지 않음')}</option>
                {designs.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
                {draft.designSystemId && !designs.some((d) => d.id === draft.designSystemId) && (
                  <option value={draft.designSystemId}>{t('다른 사람의 디자인')}</option>
                )}
              </Select>
            </Field>
          </div>
        </Group>

        <Group title={t('대화 시작')} hint={t('사용법, 시작 문장')}>
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
        </Group>

        <Group title={t('자료')} hint={t('이 에이전트가 찾아 답할 파일과 웹 페이지')}>
          <AgentKnowledge
            agentId={savedId}
            onNeedAgent={needAgent}
            onKnowledgeChange={knowledgeChanged}
          />
        </Group>

        <Group title={t('도구와 스킬')} hint={t('쓸 수 있는 도구와 스킬을 좁힙니다')} testId="agent-tools-group">
          <Block label={t('도구')}>
            <ToolPicker
              value={draft.tools}
              onChange={(tools) => edit({ ...draft, tools })}
              tools={availableTools}
              hasKnowledge={draft.hasKnowledge}
            />
          </Block>
          <Block label={t('스킬')}>
            <SkillPicker value={draft.skillIds} onChange={(skillIds) => edit({ ...draft, skillIds })} skills={skills} />
          </Block>
        </Group>

        <Group title={t('모델')} hint={t('모델과 답의 다양성')}>
          <div className="grid gap-4 sm:grid-cols-2">
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
          </div>
        </Group>

        <Group title={t('워크플로 연결')} hint={t('이 에이전트가 일하는 순서')}>
          {savedAgent ? (
            <AgentFlowSummary agent={savedAgent} choice={flowPick} onChoose={setFlowPick} />
          ) : (
            <p className="text-sm text-faint">{t('에이전트를 만든 뒤 워크플로를 연결할 수 있습니다.')}</p>
          )}
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
