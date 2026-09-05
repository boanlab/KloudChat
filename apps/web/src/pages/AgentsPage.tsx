import { Bot, Check, Download, Globe, Lock, Play, Plus, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AgentKnowledge } from '@/components/agents/AgentKnowledge'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  LoadingState,
  Field,
  Input,
  Modal,
  PageHeader,
  Switch,
  Tabs,
  Textarea,
} from '@/components/ui'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { useStableOrder } from '@/lib/useStableOrder'
import { cn, relativeTime, uid } from '@/lib/utils'
import { ShowMore, usePaged } from '@/components/ui/ShowMore'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { useStore } from '@/store/useStore'
import { startFailure } from '@/lib/failures'
import type { Agent } from '@/types'
import { errorCode, errorMessage } from '@/lib/api'
import { NAME_LIMIT } from '@/lib/limits'
import { useT } from '@/lib/useT'

const emptyAgent = (model: string): Agent => ({
  id: uid('ag'),
  name: '',
  slug: '',
  description: '',
  model,
  systemPrompt: '',
  guide: '',
  starters: [],
  shareMode: 'open',
  sealed: false,
  tools: [],
  skillIds: [],
  kinds: ['chat'],
  visibility: 'private',
  ownerId: '',
  ownerName: '',
  installs: 0,
  catalogKey: null,
  originId: null,
  official: false,
  installed: false,
  temperature: 0.5,
  color: '#5b53e8',
  enabled: true,
  runs: 0,
  hasKnowledge: false,
  updatedAt: new Date().toISOString(),
})

/** Mirrors the server's slug rule for preview and early collision checks; the server still decides. */
function slugify(text: string): string {
  const base = text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_]+/gu, '-')
    .replace(/^-+|-+$/g, '')
  return base.slice(0, 60) || 'item'
}

export function AgentsPage() {
  const t = useT()
  const navigate = useNavigate()
  const {
    agents,
    models,
    skills,
    availableTools,
    upsertAgent,
    deleteAgent,
    deleteMany,
    installAgent,
    newSession,
    setNotice,
    loadWorkspace,
    workspaceLoading,
    user,
  } = useStore()
  const [skillQuery, setSkillQuery] = useState('')

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  const [draft, setDraft] = useState<Agent | null>(null)
  const draftSlug = draft ? slugify(draft.slug || draft.name) : ''
  const slugTaken = !!draft && agents.some((a) => a.id !== draft.id && a.slug === draftSlug)
  const [tab, setTab] = useState<'mine' | 'store'>('mine')
  const [confirming, setConfirming] = useState<Agent | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [nameError, setNameError] = useState<{ draftId: string; text: string } | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  // Keyed to the draft, so a new draft starts clean.
  const nameNotice = draft && nameError?.draftId === draft.id ? nameError.text : null

  const shared = agents.filter((a) => a.visibility === 'org' && a.ownerId !== user?.id)
  const mine = agents.filter((a) => a.ownerId === user?.id)
  // Cheapest chat model, the same rule the surface defaults use.
  const defaultModel =
    [...models]
      .filter((m) => m.kinds.includes('chat'))
      .sort((a, b) => a.creditCost - b.creditCost)[0]?.id ?? ''
  const ordered = useStableOrder(agents)
  const all =
    tab === 'store'
      ? ordered.filter((a) => a.visibility === 'org' && a.ownerId !== user?.id)
      : ordered.filter((a) => a.ownerId === user?.id)
  const { visible, hidden, more } = usePaged(all, [tab, agents.length])
  // Only own agents are deletable.
  const pick = useBulkSelect(visible.filter((a) => a.ownerId === user?.id))

  // Chosen skills first; a disabled skill stays visible while chosen so it can be removed.
  const chosen = new Set(draft?.skillIds ?? [])
  const matchedSkills = skills.filter(
    (s) =>
      (s.enabled || chosen.has(s.id)) &&
      s.name.toLowerCase().includes(skillQuery.trim().toLowerCase()),
  )
  const rankedSkills = [
    ...matchedSkills.filter((s) => chosen.has(s.id)),
    ...matchedSkills.filter((s) => !chosen.has(s.id)),
  ]
  const visibleSkills = rankedSkills.slice(0, 24)
  const hiddenSkills = rankedSkills.length - visibleSkills.length
  const toolOptions = [
    ...availableTools,
    ...(draft?.tools ?? [])
      .filter((name) => !availableTools.some((tool) => tool.name === name))
      .map((name) => ({ name, label: name, available: false })),
  ]
  const updateKnowledgeAvailability = useCallback(
    (hasKnowledge: boolean) => {
      setDraft((current) =>
        current && current.hasKnowledge !== hasKnowledge
          ? { ...current, hasKnowledge }
          : current,
      )
      // The composer reads the saved agent, not this draft.
      void loadWorkspace()
    },
    [loadWorkspace],
  )

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('에이전트')}</span>} />
      <PageBody>
        <PageHeader
          title={t('에이전트')}
          description={t('고정된 시스템 프롬프트, 모델, 도구 권한을 묶어 둔 전문 작업자입니다. 입력창의 @ 버튼으로 새 대화를 맡기고, 잘 만든 것은 스토어에 공개합니다.')}
          action={
            <Button
              variant="primary"
              disabled={models.length === 0}
              title={models.length === 0 ? t('모델 목록을 불러오는 중입니다') : undefined}
              onClick={() => setDraft(emptyAgent(defaultModel))}
            >
              <Plus size={16} />
          {t('새 에이전트')}
            </Button>
          }
        />

        <Tabs<'mine' | 'store'>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'mine', label: t('내 에이전트'), count: mine.length },
            { id: 'store', label: t('워크스페이스 스토어'), count: shared.length },
          ]}
        />

        <div className="pt-4">
          <BulkBar
            count={pick.count}
            allPicked={pick.allPicked}
            onToggleAll={pick.toggleAll}
            onClear={pick.clear}
            title={t('에이전트')}
            note={t('붙여 둔 자료와 검색 색인도 함께 지워집니다.')}
            onDelete={async () => {
              await deleteMany('agents', pick.ids)
              pick.clear()
            }}
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {visible.map((a) => (
            <Card key={a.id} className="flex flex-col p-4">
              <div className="flex items-start gap-3">
                {/* Another owner's agent is read-only. */}
                {a.ownerId === user?.id ? (
                  <PickBox
                    checked={pick.picked.has(a.id)}
                    onChange={() => pick.toggle(a.id)}
                    label={t('{name} 선택').replace('{name}', t(a.name))}
                    className="mt-2.5"
                  />
                ) : (
                  <span className="size-4 shrink-0" />
                )}
                <span
                  className="grid size-9 shrink-0 place-items-center rounded-card text-white"
                  style={{ background: a.color }}
                >
                  <Bot size={16} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-base font-medium">{t(a.name)}</p>
                    <span className="font-mono text-xs text-faint">@{a.slug}</span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-base text-muted">{t(a.description)}</p>
                </div>
                {a.ownerId === user?.id ? (
                  <Switch
                    checked={a.enabled}
                    onChange={(v) => upsertAgent({ ...a, enabled: v })}
                    label={t('{name} 활성화').replace('{name}', t(a.name))}
                  />
                ) : (
                  <Badge tone={a.enabled ? 'success' : 'neutral'}>
                    {a.enabled ? t('사용 가능') : t('꺼짐')}
                  </Badge>
                )}
              </div>

              <div className="mb-3 mt-3 flex flex-wrap gap-1.5">
                {a.kinds.map((k) => {
                  const meta = kindMeta[k]
                  const KindIcon = meta.icon
                  return (
                    <Badge key={k} tone="accent">
                      <KindIcon size={10} />
                      {t(meta.label)}
                    </Badge>
                  )
                })}
                {a.ownerId !== user?.id && (
                  <Badge tone={a.official ? 'accent' : 'neutral'}>
                    {a.official ? t('공식') : a.ownerName || t('워크스페이스')}
                  </Badge>
                )}
                <Badge tone={a.visibility === 'org' ? 'success' : 'neutral'}>
                  {a.visibility === 'org' ? <Globe size={10} /> : <Lock size={10} />}
                  {a.visibility === 'org' ? t('공유됨') : t('개인')}
                </Badge>
                {(a.sealed || (a.shareMode === 'sealed' && a.visibility === 'org')) && (
                  <Badge title={t('내용은 작성자만 봅니다. 가져간 복사본은 편집할 수 없고 원본의 지침으로 동작합니다.')}>
                    <Lock size={10} />
                    {t('내용 비공개')}
                  </Badge>
                )}
                <Badge>{models.find((m) => m.id === a.model)?.label ?? t('화면 기본 모델')}</Badge>
                <Badge>temp {a.temperature}</Badge>
                {(a.tools ?? []).map((t) => (
                  <Badge key={t}>{t}</Badge>
                ))}
                {a.tools === null && <Badge>{t('사용자 도구 상속')}</Badge>}
                {a.tools?.length === 0 && <Badge>{t('도구 없음')}</Badge>}
              </div>

              {/* `mt-auto` keeps the foot aligned across a row of cards. */}
              <div className="mt-auto flex items-center justify-between border-t border-line pt-3">
                <span className="flex items-center gap-2 text-xs text-faint">
                  {t('{n}회 실행').replace('{n}', String(a.runs))}
                  {a.visibility === 'org' && (
                    <span className="flex items-center gap-1">
                      <Download size={10} />
                      {a.installs}
                    </span>
                  )}
                  <span>{relativeTime(a.updatedAt)}</span>
                </span>
                <div className="flex gap-1.5">
                  {a.ownerId === user?.id ? (
                    <>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t('{name} 삭제').replace('{name}', t(a.name))}
                        title={t('이 에이전트를 삭제합니다')}
                        onClick={() => setConfirming(a)}
                      >
                        <Trash2 size={14} />
                      </Button>
                      {/* A sealed copy has nothing to show: its prompt lives in the original. */}
                      {!a.sealed && (
                        <Button size="sm" onClick={() => setDraft(a)}>
                          {t('편집')}
                        </Button>
                      )}
                    </>
                  ) : (
                    <Button
                      size="sm"
                      variant={a.installed ? 'ghost' : 'secondary'}
                      disabled={a.installed}
                      onClick={() => void installAgent(a)}
                    >
                      {a.installed ? <Check size={13} /> : <Download size={13} />}
                      {a.installed ? t('가져옴') : t('가져오기')}
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() =>
                      void newSession(a.kinds[0] ?? 'chat', { agentId: a.id })
                        .then((id) => navigate(`/s/${id}`))
                        .catch((err: unknown) => setNotice(startFailure(err, t)))
                    }
                  >
                    <Play size={13} />
                    {t('실행')}
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
        {workspaceLoading && all.length === 0 ? (
          <LoadingState label={t('에이전트를 불러오는 중…')} />
        ) : all.length === 0 && (
          <EmptyState
            icon={<Bot size={18} />}
            title={tab === 'store' ? t('공유된 에이전트가 없습니다') : t('아직 에이전트가 없습니다')}
            description={
              tab === 'store'
                ? t('내 에이전트를 편집해 모두에게 공개하면 여기에 올라갑니다.')
                : t('워크스페이스 스토어에서 가져오거나, 직접 하나 만들어 시작하세요.')
            }
            action={
              tab === 'store' ? undefined : (
                <Button variant="primary" onClick={() => setTab('store')}>
                  <Download size={16} />
                  {t('스토어 둘러보기')}
                </Button>
              )
            }
          />
        )}
        <ShowMore hidden={hidden} onMore={more} />
      </PageBody>

      <ConfirmDialog
        open={!!confirming}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming && void deleteAgent(confirming.id)}
        title={t('{name} 삭제').replace('{name}', t(confirming?.name ?? ''))}
        description={t('되돌릴 수 없습니다. 이 에이전트로 하던 대화는 그대로 남습니다.')}
      />

      <Modal
        open={!!draft}
        onClose={() => {
          setDraft(null)
          setSaveError(null)
        }}
        title={agents.some((a) => a.id === draft?.id) ? t('에이전트 편집') : t('새 에이전트')}
        width="max-w-2xl"
        footer={
          <>
            {draft && agents.some((a) => a.id === draft.id) && (
              <Button
                variant="danger"
                className="mr-auto"
                onClick={() => {
                  deleteAgent(draft.id)
                  setDraft(null)
                }}
              >
                <Trash2 size={14} />
                {t('삭제')}
              </Button>
            )}
            <Button onClick={() => setDraft(null)}>{t('취소')}</Button>
            <Button
              variant="primary"
              disabled={saving || slugTaken}
              onClick={async () => {
                if (!draft) return
                if (!draft.name.trim()) {
                  setNameError({ draftId: draft.id, text: t('이름을 입력하세요.') })
                  nameRef.current?.focus()
                  return
                }
                setSaving(true)
                setSaveError(null)
                try {
                  await upsertAgent({
                    ...draft,
                    slug: draftSlug,
                    updatedAt: new Date().toISOString(),
                  })
                  setDraft(null)
                } catch (err) {
                  // Keep the form open so the typed prompt is not lost.
                  setSaveError(
                    errorCode(err) === 'slug_taken'
                      ? t('이미 쓰는 슬러그입니다. 다른 슬러그를 붙이세요.')
                      : errorMessage(err, t('저장하지 못했습니다.')),
                  )
                } finally {
                  setSaving(false)
                }
              }}
            >
              {t('저장')}
            </Button>
          </>
        }
      >
        {draft && (
          <>
            {saveError && (
              <p
                role="status"
                className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger"
              >
                {saveError}
              </p>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Field label={t('이름')} hint={t('필수 항목입니다.')}>
                  <Input
                    ref={nameRef}
                    value={draft.name}
                    maxLength={NAME_LIMIT}
                    aria-required
                    aria-invalid={nameNotice ? true : undefined}
                    onChange={(e) => {
                      setNameError(null)
                      setDraft({ ...draft, name: e.target.value })
                    }}
                    placeholder={t('예: 기술 검토 도우미')}
                  />
                </Field>
                {nameNotice && (
                  <p role="alert" className="mt-1 text-sm text-danger">
                    {nameNotice}
                  </p>
                )}
              </div>
              <div>
                <Field label={t('슬러그')} hint={t('이 목록의 카드에 이 핸들로 표시됩니다. 비워 두면 자동으로 만듭니다.')}>
                  <Input
                    value={draft.slug}
                    aria-invalid={slugTaken || undefined}
                    onChange={(e) => setDraft({ ...draft, slug: e.target.value })}
                    placeholder="paper-reviewer"
                    className="font-mono"
                  />
                </Field>
                {slugTaken ? (
                  <p role="alert" className="mt-1 text-sm text-danger">
                    {t('이미 쓰는 슬러그입니다. 다른 슬러그를 붙이세요.')}
                  </p>
                ) : (
                  draftSlug &&
                  draftSlug !== draft.slug && (
                    <p className="mt-1 font-mono text-sm text-faint">@{draftSlug}</p>
                  )
                )}
              </div>
            </div>

            <Field label={t('설명')}>
              <Input
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              />
            </Field>

            <Field
              label={t('공개 범위')}
              hint={t('공개하면 이 인스턴스에 로그인한 누구나 스토어에서 복사해 갈 수 있습니다. 원본은 계속 내 것입니다.')}
            >
              <div className="flex gap-1.5">
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
                      onClick={() => setDraft({ ...draft, visibility: o.id })}
                      className={cn(
                        'flex items-center gap-1.5 rounded-control border px-2.5 py-1.5 text-base transition-colors',
                        draft.visibility === o.id
                          ? 'border-accent bg-accent-soft text-accent'
                          : 'border-line text-muted hover:bg-elevated',
                      )}
                    >
                      <Icon size={13} />
                      {o.label}
                    </button>
                  )
                })}
              </div>
              {/* Share mode applies only to shared originals; a sealed copy has no prompt of its own. */}
              {draft.visibility === 'org' && !draft.sealed && (
                <div className="mt-3 space-y-2">
                  {(
                    [
                      {
                        id: 'open',
                        label: t('가져가서 편집할 수 있게'),
                        note: [t('복사본에 지침이 함께 가고, 가져간 사람이 마음대로 고칩니다.')],
                      },
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
                    <label
                      key={o.id}
                      className="flex cursor-pointer items-start gap-3 rounded-control border border-line px-3 py-2"
                    >
                      <input
                        type="radio"
                        name="share-mode"
                        checked={draft.shareMode === o.id}
                        onChange={() => setDraft({ ...draft, shareMode: o.id })}
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
            </Field>

            <Field label={t('사용할 화면')} hint={t('여기서 선택한 화면의 @ 버튼과 카드에만 나타납니다.')}>
              <div className="flex flex-wrap gap-1.5">
                {kindOrder.map((k) => {
                  const on = draft.kinds.includes(k)
                  const meta = kindMeta[k]
                  return (
                    <button
                      key={k}
                      onClick={() =>
                        setDraft({
                          ...draft,
                          kinds: on ? draft.kinds.filter((x) => x !== k) : [...draft.kinds, k],
                        })
                      }
                      className={cn(
                        'rounded-control border px-2.5 py-1.5 text-base transition-colors',
                        on
                          ? 'border-accent bg-accent-soft text-accent'
                          : 'border-line text-muted hover:bg-elevated',
                      )}
                    >
                      {t(meta.label)}
                    </button>
                  )
                })}
              </div>
            </Field>

            <Field label={t('시스템 프롬프트')}>
              <Textarea
                rows={6}
                value={draft.systemPrompt}
                onChange={(e) => setDraft({ ...draft, systemPrompt: e.target.value })}
              />
            </Field>

            <Field
              label={t('사용법')}
              hint={t('대화를 열면 첫 화면에 보입니다. 무엇을 가져와야 하는지, 한 턴에 무슨 일이 일어나는지.')}
            >
              <Textarea
                rows={3}
                value={draft.guide}
                onChange={(e) => setDraft({ ...draft, guide: e.target.value.slice(0, 2000) })}
                placeholder={t('예: 논문 PDF 를 첨부하고 어느 학회 기준인지 알려 주세요. 리뷰어 관점에서 검토합니다.')}
              />
            </Field>
            <Field
              label={t('시작 문장')}
              hint={t('한 줄에 하나, 여섯 개까지. 첫 화면에 버튼으로 보이고 누르면 그대로 보냅니다.')}
            >
              <Textarea
                rows={4}
                value={draft.starters.join('\n')}
                onChange={(e) =>
                  setDraft({ ...draft, starters: e.target.value.split('\n').slice(0, 6) })
                }
                placeholder={t('첨부한 논문을 리뷰어 관점에서 검토해 줘')}
              />
            </Field>

            {/* Knowledge is keyed by agent id, so only for a saved agent. */}
            <AgentKnowledge
              agentId={agents.some((a) => a.id === draft.id) ? draft.id : null}
              onKnowledgeChange={updateKnowledgeAvailability}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t('모델')}>
                <select
                  value={draft.model}
                  onChange={(e) => setDraft({ ...draft, model: e.target.value })}
                  className="h-9 w-full rounded-control border border-line bg-panel px-3 text-base focus:border-accent focus:outline-none"
                >
                  {/* Empty value means "follow the surface default". */}
                  <option value="">{t('화면 기본 모델')}</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label={`Temperature — ${draft.temperature}`}
                hint={t('낮을수록 일관되게, 높을수록 다양하게 답합니다. 대화 화면에만 적용됩니다.')}
              >
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.1}
                  value={draft.temperature}
                  onChange={(e) => setDraft({ ...draft, temperature: Number(e.target.value) })}
                  className="h-9 w-full accent-[var(--accent)]"
                />
              </Field>
            </div>

            <Field label={t('도구 권한')}>
              <div className="mb-2 flex gap-1.5">
                <button
                  type="button"
                  onClick={() => setDraft({ ...draft, tools: null })}
                  className={cn(
                    'rounded-control border px-2.5 py-1.5 text-base',
                    draft.tools === null
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-muted',
                  )}
                >
                  {t('사용자 도구 상속')}
                </button>
                <button
                  type="button"
                  onClick={() => setDraft({ ...draft, tools: draft.tools ?? [] })}
                  className={cn(
                    'rounded-control border px-2.5 py-1.5 text-base',
                    draft.tools !== null
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-muted',
                  )}
                >
                  {t('직접 선택')}
                </button>
              </div>
              {draft.tools !== null && (
                <div className="flex flex-wrap gap-1.5">
                  {toolOptions.map((tool) => {
                    const on = draft.tools?.includes(tool.name) ?? false
                    const runtimeAvailable =
                      tool.name === 'search_knowledge' ? draft.hasKnowledge : tool.available
                    return (
                      <button
                        key={tool.name}
                        aria-pressed={on}
                        disabled={!runtimeAvailable && !on}
                        title={
                          runtimeAvailable || on
                            ? undefined
                            : tool.name === 'search_knowledge'
                              ? t('에이전트에 읽을 수 있는 지식 문서를 먼저 추가하세요.')
                              : t('현재 사용할 수 없는 도구입니다.')
                        }
                        onClick={() =>
                          setDraft({
                            ...draft,
                            tools: on
                              ? (draft.tools ?? []).filter((name) => name !== tool.name)
                              : [...(draft.tools ?? []), tool.name],
                          })
                        }
                        className={cn(
                          'rounded-control border px-2.5 py-1.5 text-base transition-colors',
                          on
                            ? 'border-accent bg-accent-soft text-accent'
                            : runtimeAvailable
                              ? 'border-line text-muted hover:bg-elevated'
                              : 'cursor-not-allowed border-line text-faint opacity-60',
                        )}
                      >
                        {t(tool.label)}
                        <span className="ml-1 font-mono text-2xs text-faint">
                          {tool.name}
                        </span>
                      </button>
                    )
                  })}
                </div>
              )}
            </Field>

            <Field label={t('연결된 스킬')}>
              <div className="mb-2 flex gap-1.5">
                <button
                  type="button"
                  onClick={() => setDraft({ ...draft, skillIds: null })}
                  className={cn(
                    'rounded-control border px-2.5 py-1.5 text-base',
                    draft.skillIds === null
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-muted',
                  )}
                >
                  {t('턴 선택 상속')}
                </button>
                <button
                  type="button"
                  onClick={() => setDraft({ ...draft, skillIds: draft.skillIds ?? [] })}
                  className={cn(
                    'rounded-control border px-2.5 py-1.5 text-base',
                    draft.skillIds !== null
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-muted',
                  )}
                >
                  {t('허용 목록 지정')}
                </button>
              </div>
              {draft.skillIds !== null && (
                <>
                  <Input
                    value={skillQuery}
                    onChange={(e) => setSkillQuery(e.target.value)}
                    placeholder={t('스킬 검색 ({n}개)').replace('{n}', String(skills.length))}
                    className="mb-2 h-8 text-base"
                  />
                  <div className="flex max-h-44 flex-wrap gap-1.5 overflow-y-auto">
                    {visibleSkills.map((s) => {
                      const on = draft.skillIds?.includes(s.id) ?? false
                      return (
                        <button
                          key={s.id}
                          aria-pressed={on}
                          onClick={() =>
                            setDraft({
                              ...draft,
                              skillIds: on
                                ? (draft.skillIds ?? []).filter((x) => x !== s.id)
                                : [...(draft.skillIds ?? []), s.id],
                            })
                          }
                          className={cn(
                            'rounded-control border px-2.5 py-1.5 text-base transition-colors',
                            on
                              ? 'border-accent bg-accent-soft text-accent'
                              : 'border-line text-muted hover:bg-elevated',
                          )}
                        >
                          {t(s.name)}
                        </button>
                      )
                    })}
                    {visibleSkills.length === 0 && (
                      <p className="py-2 text-base text-faint">{t('검색 결과가 없습니다')}</p>
                    )}
                    {hiddenSkills > 0 && (
                      <span className="self-center text-sm text-faint">
                        {t('외 {n}개 — 검색해서 찾으세요').replace('{n}', String(hiddenSkills))}
                      </span>
                    )}
                  </div>
                </>
              )}
            </Field>
          </>
        )}
      </Modal>
    </>
  )
}
