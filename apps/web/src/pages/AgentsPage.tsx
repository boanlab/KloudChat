import { Bot, Download, Globe, Lock, Play, Plus, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AgentKnowledge } from '@/components/agents/AgentKnowledge'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
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
import type { Agent } from '@/types'
import { errorMessage } from '@/lib/api'
import { NAME_LIMIT } from '@/lib/limits'
import { useT } from '@/lib/useT'

const emptyAgent = (model: string): Agent => ({
  id: uid('ag'),
  name: '',
  slug: '',
  description: '',
  model,
  systemPrompt: '',
  // New agents start with no tool or skill authority. The user can explicitly
  // inherit their tools, or choose names from the server's real registry.
  tools: [],
  skillIds: [],
  kinds: ['chat'],
  visibility: 'private',
  // Filled in by the server on save; a draft has no owner yet.
  ownerId: '',
  ownerName: '',
  installs: 0,
  temperature: 0.5,
  color: '#5b53e8',
  enabled: true,
  runs: 0,
  hasKnowledge: false,
  updatedAt: new Date().toISOString(),
})

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
    forkAgent,
    newSession,
    loadWorkspace,
    user,
  } = useStore()
  const [skillQuery, setSkillQuery] = useState('')

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  const [draft, setDraft] = useState<Agent | null>(null)
  const [tab, setTab] = useState<'mine' | 'store'>('mine')
  //: 삭제는 되돌릴 수 없고, 시스템 프롬프트는 누군가 써 둔 것이다.
  const [confirming, setConfirming] = useState<Agent | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // The store is what makes one person's agent reusable by the workspace.
  const shared = agents.filter((a) => a.visibility === 'org')
  // Cheapest model that can hold a conversation, the same rule the surface
  // defaults use.
  const defaultModel =
    [...models]
      .filter((m) => m.kinds.includes('chat'))
      .sort((a, b) => a.creditCost - b.creditCost)[0]?.id ?? ''
  // Same reasoning as skills: newest first so a fresh agent is on the first
  // page, then held in place so toggling one does not move it.
  const ordered = useStableOrder(agents)
  const all = tab === 'store' ? ordered.filter((a) => a.visibility === 'org') : ordered
  const { visible, hidden, more } = usePaged(all, [tab, agents.length])
  // Mine only: somebody else's shared agent is read-only.
  const pick = useBulkSelect(visible.filter((a) => a.ownerId === user?.id))

  // Attached first, then matches; capped, with `skills.length` in the
  // placeholder saying what is hidden. A disabled existing selection remains
  // visible so it can be removed, but cannot be newly granted.
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
      // Composer reads the saved agent from the workspace store, not this
      // modal draft. Refresh after a shelf change so closing the modal does
      // not leave `search_knowledge` falsely disabled.
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
          description={t('고정된 시스템 프롬프트, 모델, 도구 권한을 묶어 둔 전문 작업자입니다. @이름으로 불러오고, 잘 만든 것은 스토어에 공개합니다.')}
          action={
            <Button
              variant="primary"
              // `models` is empty until the catalogue lands, and stays empty
              // when the proxy is unreachable.
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
            { id: 'mine', label: t('내 에이전트'), count: agents.length },
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
                {/* Somebody else's shared agent is read-only, so it gets no
                    checkbox — the delete behind it would 403. */}
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
                <Switch
                  checked={a.enabled}
                  onChange={(v) => upsertAgent({ ...a, enabled: v })}
                  label={t('{name} 활성화').replace('{name}', t(a.name))}
                />
              </div>

              <div className="mt-3 flex flex-wrap gap-1.5">
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
                {a.ownerId !== user?.id && a.ownerName && (
                  <Badge>{a.ownerName}</Badge>
                )}
                <Badge tone={a.visibility === 'org' ? 'success' : 'neutral'}>
                  {a.visibility === 'org' ? <Globe size={10} /> : <Lock size={10} />}
                  {a.visibility === 'org' ? t('공유됨') : t('개인')}
                </Badge>
                {/* 모델을 고정하지 않은 에이전트가 정상이다 — 화면의 기본 모델을 따른다 */}
                <Badge>{models.find((m) => m.id === a.model)?.label ?? t('화면 기본 모델')}</Badge>
                <Badge>temp {a.temperature}</Badge>
                {(a.tools ?? []).map((t) => (
                  <Badge key={t}>{t}</Badge>
                ))}
                {a.tools === null && <Badge>{t('사용자 도구 상속')}</Badge>}
                {a.tools?.length === 0 && <Badge>{t('도구 없음')}</Badge>}
              </div>

              <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
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
                  {/* Someone else's shared agent is read-only: editing or
                      deleting it would 403, and offering buttons that cannot
                      work is worse than not offering them. Copying is the
                      action that makes sense — it is why the store exists. */}
                  {a.ownerId === user?.id ? (
                    <>
                      {/* Same reasoning as skills: the card is where the other
                          screens put it, and the edit modal is a strange place
                          to go looking for a delete. */}
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t('{name} 삭제').replace('{name}', t(a.name))}
                        title={t('이 에이전트를 삭제합니다')}
                        onClick={() => setConfirming(a)}
                      >
                        <Trash2 size={14} />
                      </Button>
                      <Button size="sm" onClick={() => setDraft(a)}>
                        {t('편집')}
                      </Button>
                    </>
                  ) : (
                    <Button size="sm" onClick={() => void forkAgent(a)}>
                      <Download size={13} />
                      {t('가져오기')}
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() =>
                      void newSession(a.kinds[0] ?? 'chat', { agentId: a.id }).then((id) =>
                        navigate(`/s/${id}`),
                      )
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
              disabled={saving || !draft?.name.trim()}
              onClick={async () => {
                if (!draft) return
                setSaving(true)
                setSaveError(null)
                try {
                  await upsertAgent({
                    ...draft,
                    slug: draft.slug || draft.name.toLowerCase().replace(/\s+/g, '-'),
                    updatedAt: new Date().toISOString(),
                  })
                  setDraft(null)
                } catch (err) {
                  // The form keeps what was typed. Closing it and saying
                  // nothing is how a system prompt somebody wrote disappears.
                  setSaveError(errorMessage(err, t('저장하지 못했습니다.')))
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
              <Field label={t('이름')}>
                <Input
                  value={draft.name}
                  maxLength={NAME_LIMIT}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  placeholder={t('예: 기술 검토 도우미')}
                />
              </Field>
              <Field label={t('슬러그')} hint={t('@슬러그로 호출합니다.')}>
                <Input
                  value={draft.slug}
                  onChange={(e) => setDraft({ ...draft, slug: e.target.value })}
                  placeholder="paper-reviewer"
                  className="font-mono"
                />
              </Field>
            </div>

            <Field label={t('설명')}>
              <Input
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              />
            </Field>

            <Field
              label={t('공개 범위')}
              hint={t('공유하면 워크스페이스 스토어에 올라가고 다른 구성원이 그대로 씁니다.')}
            >
              <div className="flex gap-1.5">
                {(
                  [
                    { id: 'private', label: t('개인'), icon: Lock },
                    { id: 'org', label: t('모두에게 공개'), icon: Globe },
                  ] as const
                ).map((o) => (
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
                    <o.icon size={13} />
                    {o.label}
                  </button>
                ))}
              </div>
            </Field>

            <Field label={t('사용할 화면')} hint={t('여기서 선택한 화면의 입력창에서만 @로 호출됩니다.')}>
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
                {visibleSkills.length === 0 && (
                  <p className="py-2 text-base text-faint">{t('검색 결과가 없습니다')}</p>
                )}
                {hiddenSkills > 0 && (
                  <span className="self-center text-sm text-faint">
                    {t('외 {n}개 — 검색해서 찾으세요').replace('{n}', String(hiddenSkills))}
                  </span>
                )}
              </div>
            </Field>

            <Field label={t('시스템 프롬프트')}>
              <Textarea
                rows={6}
                value={draft.systemPrompt}
                onChange={(e) => setDraft({ ...draft, systemPrompt: e.target.value })}
              />
            </Field>

            {/* Only for an agent that exists: the shelf hangs off its id. */}
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
                  {/* 고정하지 않는 것도 하나의 상태다 — 카드가 '화면 기본
                      모델' 이라고 그리는 그 상태이고, 씨앗 에이전트는 전부
                      거기에 있다. 그런데 목록에 그 항목이 없어서, 값이 빈
                      에이전트를 열면 브라우저가 첫 모델을 대신 골라 보여
                      주었다. 고르지도 않은 모델이 편집기에 적혀 있는 것이고,
                      한 번 건드리면 그대로 박힌다. */}
                  <option value="">{t('화면 기본 모델')}</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </Field>
              {/* 무엇에 닿는지까지 적는다 — 이 값은 대화 턴의 표본 추출로만
                  내려가고, 보고서·슬라이드는 각자의 생성 절차를 따른다. */}
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

            {/* 공개 범위. 모델도 목록 질의도 처음부터 org 를 다뤘고, 배지도
                "공유됨" 을 그릴 줄 알았는데, 정작 그렇게 **바꿀 방법이**
                없었습니다 — 워크스페이스 스토어 탭이 영원히 비어 있던 이유. */}
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
            </Field>

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
                        {tool.label}
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
              {/* 전체를 나열하면 다섯 개일 때는 괜찮아도 예순 개면 못 쓴다.
                  선택된 것은 앞에 고정해, 검색이 이미 붙은 것을 가리지 않게 한다. */}
              <Input
                value={skillQuery}
                onChange={(e) => setSkillQuery(e.target.value)}
                placeholder={t('스킬 검색 ({n}개)').replace('{n}', String(skills.length))}
                className="mb-2 h-8 text-base"
              />
              {draft.skillIds !== null && (
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
                </div>
              )}
            </Field>
          </>
        )}
      </Modal>
    </>
  )
}
