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

/**
 * The server's slug rule (`_slug` in routers/workspace.py), mirrored so the
 * form can show the handle a name will get and catch a collision before the
 * round trip. The server still decides.
 */
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
  /**
   * The handle this draft will be stored under, and whether another of the
   * person's agents already holds it. The server has the last word (409
   * `slug_taken`); this says so before 저장 is pressed.
   */
  const draftSlug = draft ? slugify(draft.slug || draft.name) : ''
  const slugTaken = !!draft && agents.some((a) => a.id !== draft.id && a.slug === draftSlug)
  const [tab, setTab] = useState<'mine' | 'store'>('mine')
  //: 삭제는 되돌릴 수 없고, 시스템 프롬프트는 누군가 써 둔 것이다.
  const [confirming, setConfirming] = useState<Agent | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  /** Agent-name validation message. */
  const [nameError, setNameError] = useState<{ draftId: string; text: string } | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  // Keyed to the draft it was said on, so a new draft starts clean without an
  // effect to clear it.
  const nameNotice = draft && nameError?.draftId === draft.id ? nameError.text : null

  // The store is what makes one person's agent reusable by the workspace.
  // Your own shared agents are not in it: there is nothing to import from
  // yourself, and the button on such a card would refuse.
  const shared = agents.filter((a) => a.visibility === 'org' && a.ownerId !== user?.id)
  // And the other half of the same line: somebody else's shared agent is not
  // one of mine. It was listed under 내 에이전트 with its edit, delete and
  // enable controls already withheld — a card that could only be looked at.
  const mine = agents.filter((a) => a.ownerId === user?.id)
  // Cheapest model that can hold a conversation, the same rule the surface
  // defaults use.
  const defaultModel =
    [...models]
      .filter((m) => m.kinds.includes('chat'))
      .sort((a, b) => a.creditCost - b.creditCost)[0]?.id ?? ''
  // Same reasoning as skills: newest first so a fresh agent is on the first
  // page, then held in place so toggling one does not move it.
  const ordered = useStableOrder(agents)
  const all =
    tab === 'store'
      ? ordered.filter((a) => a.visibility === 'org' && a.ownerId !== user?.id)
      : ordered.filter((a) => a.ownerId === user?.id)
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
          description={t('고정된 시스템 프롬프트, 모델, 도구 권한을 묶어 둔 전문 작업자입니다. 입력창의 @ 버튼으로 새 대화를 맡기고, 잘 만든 것은 스토어에 공개합니다.')}
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
                {/* And for the same reason as the checkbox above: the write
                    behind this one is a PATCH of somebody else's row, which
                    comes back 404 and leaves the switch flicking back with
                    nothing said. Whether their agent is on is still worth
                    reading — it is the control that does not belong here. */}
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

              {/* `mb-3` is the floor under the foot's `mt-auto`, which is 0 on a
                  card that is already as tall as its row. */}
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
                    {/* 관리자가 올린 기본 목록과 동료가 만든 것은 같은 무게로
                        읽히면 안 됩니다. */}
                    {a.official ? t('공식') : a.ownerName || t('워크스페이스')}
                  </Badge>
                )}
                <Badge tone={a.visibility === 'org' ? 'success' : 'neutral'}>
                  {a.visibility === 'org' ? <Globe size={10} /> : <Lock size={10} />}
                  {a.visibility === 'org' ? t('공유됨') : t('개인')}
                </Badge>
                {(a.sealed || (a.shareMode === 'sealed' && a.visibility === 'org')) && (
                  <Badge title={t('지침은 작성자만 봅니다. 가져가면 원본의 지침으로 동작합니다.')}>
                    <Lock size={10} />
                    {t('지침 비공개')}
                  </Badge>
                )}
                {/* 모델을 고정하지 않은 에이전트가 정상이다 — 화면의 기본 모델을 따른다 */}
                <Badge>{models.find((m) => m.id === a.model)?.label ?? t('화면 기본 모델')}</Badge>
                <Badge>temp {a.temperature}</Badge>
                {(a.tools ?? []).map((t) => (
                  <Badge key={t}>{t}</Badge>
                ))}
                {a.tools === null && <Badge>{t('사용자 도구 상속')}</Badge>}
                {a.tools?.length === 0 && <Badge>{t('도구 없음')}</Badge>}
              </div>

              {/* `mt-auto`, not `mt-3`: the grid already stretches the cards in
                  a row to one height, but the foot floated wherever the badges
                  above it happened to end — so an agent carrying two rows of
                  tool badges put its 실행 button 28px below its neighbour's, and
                  a tidy two-column grid read as a broken one. Pinned to the
                  bottom, the buttons line up across the row whatever is above
                  them. */}
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
        {/* 새 계정은 이제 빈 화면으로 시작합니다. 나머지가 어디 있는지 말하지
            않으면 빈 화면은 "기능이 없다" 로 읽힙니다. */}
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
                  // The form keeps what was typed. Closing it and saying
                  // nothing is how a system prompt somebody wrote disappears.
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

            {/* 공개 범위. 모델도 목록 질의도 처음부터 org 를 다뤘고, 배지도
                "공유됨" 을 그릴 줄 알았는데, 정작 그렇게 **바꿀 방법이**
                없었습니다 — 워크스페이스 스토어 탭이 영원히 비어 있던 이유.
                한동안 폼 아래쪽에 같은 선택기가 한 벌 더 있었습니다. 같은
                값을 두 곳에서 고르게 두면 어느 쪽이 지금 값인지 읽어 낼 수
                없으니, 무엇이 공개되는지까지 말하는 이쪽만 남깁니다. */}
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
              {/* 「가져가서 편집 가능하게 오픈할지, 가져갈 수는 있되 세부 내용을
                  비공개로 할지」 — only once it is shared, and never on a copy
                  that has no prompt of its own to withhold. */}
              {draft.visibility === 'org' && !draft.sealed && (
                <div className="mt-3 space-y-2">
                  {(
                    [
                      {
                        id: 'open',
                        label: t('가져가서 편집할 수 있게'),
                        note: t('복사본에 지침이 함께 가고, 가져간 사람이 마음대로 고칩니다.'),
                      },
                      {
                        id: 'sealed',
                        label: t('가져갈 수 있지만 지침은 비공개'),
                        note: t('복사본은 내 원본의 지침으로 동작하되 지침을 보거나 고칠 수 없습니다. 내가 원본을 고치면 복사본도 따라옵니다.'),
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
                        <span className="block text-sm text-muted">{o.note}</span>
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
              {draft.sealed ? (
                <p className="flex items-start gap-2 rounded-control border border-line bg-elevated px-3 py-2.5 text-base text-muted">
                  <Lock size={14} className="mt-0.5 shrink-0" />
                  {t('작성자가 지침을 비공개로 공유한 에이전트입니다. 지침은 원본에서 읽어 오며, 여기서 보거나 고칠 수 없습니다. 이름·모델·스킬은 바꿀 수 있습니다.')}
                </p>
              ) : (
                <Textarea
                  rows={6}
                  value={draft.systemPrompt}
                  onChange={(e) => setDraft({ ...draft, systemPrompt: e.target.value })}
                />
              )}
            </Field>

            {/* What the empty screen says before the first message: how to
                use it, and a few first sentences to press. */}
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
              {/* 상속 모드에는 고를 목록이 없다. 검색창만 남겨 두면 무엇도
                  거르지 못하는 칸에 대고 타자를 치게 된다 — 목록과 함께 나온다. */}
              {draft.skillIds !== null && (
                <>
                  {/* 전체를 나열하면 다섯 개일 때는 괜찮아도 예순 개면 못 쓴다.
                      선택된 것은 앞에 고정해, 검색이 이미 붙은 것을 가리지 않게 한다. */}
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
