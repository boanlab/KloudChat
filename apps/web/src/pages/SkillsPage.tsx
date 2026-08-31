import { Check, ChevronRight, Download, FileCode2, Globe, Lock, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Markdown } from '@/components/chat/Markdown'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Field,
  Input,
  Modal,
  PageHeader,
  Switch,
  Tabs,
  Textarea,
} from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { useStableOrder } from '@/lib/useStableOrder'
import { cn, relativeTime } from '@/lib/utils'
import { ShowMore, usePaged } from '@/components/ui/ShowMore'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { useStore } from '@/store/useStore'
import type { Skill, StoreSkill } from '@/types'
import { errorMessage } from '@/lib/api'
import { NAME_LIMIT } from '@/lib/limits'
import { useT } from '@/lib/useT'

type Filter = 'all' | 'built-in' | 'workspace' | 'personal' | 'store'

const sourceLabel: Record<Skill['source'], string> = {
  'built-in': '기본',
  workspace: '워크스페이스',
  personal: '개인',
}

/**
 * One shared skill, and the button that makes it yours.
 *
 * A copy rather than a reference: the author keeps editing theirs, and an edit
 * over there never rewrites a procedure somebody here is relying on. Which is
 * also why an installed entry stays on this list, greyed — the store is a
 * catalogue of originals, not a list of things you are missing.
 */
function StoreCard({ skill }: { skill: StoreSkill }) {
  const t = useT()
  const { installSkill } = useStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  return (
    <Card className="flex items-start gap-3 p-4">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-base font-medium">{t(skill.name)}</span>
          <span className="font-mono text-xs text-faint">{skill.slug}</span>
          {/* 누가 올린 것인지가 이 화면에서 가장 먼저 필요한 정보입니다 —
              관리자가 올린 기본 목록과 동료가 쓴 것은 다르게 읽힙니다. */}
          <Badge tone={skill.official ? 'accent' : 'neutral'}>
            {skill.official ? t('공식') : skill.ownerName || t('워크스페이스')}
          </Badge>
          {skill.kinds.map((k) => (
            <Badge key={k} tone="accent">
              {t(kindMeta[k].label)}
            </Badge>
          ))}
        </div>
        <p className="mt-1 text-base text-muted">{t(skill.description)}</p>
        <p className="mt-1.5 text-xs text-faint">
          {t('사용 시점')}: {t(skill.whenToUse)} ·{' '}
          {t('약 {n} 토큰').replace('{n}', skill.estimatedTokens.toLocaleString())} ·{' '}
          {t('{n}회 설치').replace('{n}', String(skill.installs))}
        </p>
        {error && (
          <p role="status" className="mt-1.5 text-xs text-danger">
            {error}
          </p>
        )}
      </div>
      <Button
        size="sm"
        variant={skill.installed ? 'ghost' : 'primary'}
        disabled={skill.installed || busy}
        onClick={async () => {
          setBusy(true)
          setError(null)
          try {
            await installSkill(skill.id)
          } catch (err) {
            setError(errorMessage(err, t('가져오지 못했습니다.')))
          } finally {
            setBusy(false)
          }
        }}
      >
        {skill.installed ? <Check size={13} /> : <Download size={13} />}
        {skill.installed ? t('가져옴') : busy ? t('가져오는 중…') : t('가져오기')}
      </Button>
    </Card>
  )
}

export function SkillsPage() {
  const t = useT()
  const {
    skills,
    skillStore,
    skillStoreLoading,
    skillStoreError,
    availableTools,
    toggleSkill,
    upsertSkill,
    deleteSkill,
    deleteMany,
    loadWorkspace,
    loadSkillStore,
  } = useStore()

  useEffect(() => {
    void loadWorkspace()
    void loadSkillStore()
  }, [loadWorkspace, loadSkillStore])
  const [filter, setFilter] = useState<Filter>('all')
  const [detail, setDetail] = useState<Skill | null>(null)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  //: 저장이 거절돼도 아무 말이 없었다. 대화상자는 열린 채였지만 이유가 없어,
  //: 남는 선택은 같은 버튼을 다시 누르는 것뿐이었다.
  const [saveError, setSaveError] = useState<string | null>(null)
    /** The skill being written. `editing` holding an id means edit; null
     *  means create. */
  const [draft, setDraft] = useState({
    name: '',
    description: '',
    whenToUse: '',
    body: '',
    requiredTools: [] as string[],
    visibility: 'private' as Skill['visibility'],
  })
  const [editing, setEditing] = useState<string | null>(null)
  //: 지우기 전에 무엇을 지우는지 묻는다. 되돌릴 곳이 서버에 없다.
  const [confirming, setConfirming] = useState<Skill | null>(null)
  const toolOptions = [
    ...availableTools,
    ...draft.requiredTools
      .filter((name) => !availableTools.some((tool) => tool.name === name))
      .map((name) => ({ name, label: name, available: false })),
  ]

  const reset = () => {
    setDraft({
      name: '',
      description: '',
      whenToUse: '',
      body: '',
      requiredTools: [],
      visibility: 'private',
    })
    setEditing(null)
  }
  const startEdit = (s: Skill) => {
    setDraft({
      name: s.name,
      description: s.description,
      whenToUse: s.whenToUse,
      body: s.body,
      requiredTools: s.requiredTools,
      visibility: s.visibility,
    })
    setEditing(s.id)
    setDetail(null)
    setCreating(true)
  }

  // Newest first, and then held there. With a paged list the ordering decides
  // what exists as far as the user is concerned — a skill created a moment ago
  // that sorts to position sixty is indistinguishable from one that failed to
  // save — but re-ranking on every write moved the card out from under the
  // switch that had just been flipped.
  const ordered = useStableOrder(skills)
  const all =
    filter === 'all' || filter === 'store' ? ordered : ordered.filter((s) => s.source === filter)
  const { visible, hidden, more } = usePaged(all, [filter, skills.length])
  const storePaged = usePaged(skillStore, [filter, skillStore.length])
  const anySelectable = visible.some((s) => s.source !== 'built-in')
  // Only the rows a delete can actually reach, so 전체 선택 does not pick a
  // built-in skill and then have the request refuse it.
  const pick = useBulkSelect(visible.filter((s) => s.source !== 'built-in'))

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('스킬')}</span>} />
      <PageBody>
        <PageHeader
          title={t('스킬')}
          description={t('설치해 둔 절차입니다. 입력창에서 이번 요청에 적용할 스킬을 최대 3개 고릅니다.')}
          action={
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus size={16} />
          {t('새 스킬')}
            </Button>
          }
        />

        <Tabs<Filter>
          value={filter}
          onChange={setFilter}
          tabs={[
            { id: 'all', label: t('전체'), count: skills.length },
            { id: 'built-in', label: t('기본') },
            { id: 'workspace', label: t('워크스페이스') },
            { id: 'personal', label: t('개인') },
            // The one tab that is not a filter over your own rows: these are
            // somebody else's, and nothing here happens until you take a copy.
            { id: 'store', label: t('워크스페이스 스토어'), count: skillStore.length },
          ]}
        />

        {filter === 'store' ? (
          <div className="space-y-2 pt-4">
            {storePaged.visible.map((skill) => (
              <StoreCard key={skill.id} skill={skill} />
            ))}
            {skillStore.length === 0 && (
              <p className="py-10 text-center text-base text-muted">
                {skillStoreLoading
                  ? t('불러오는 중…')
                  : skillStoreError
                    ? t('공유된 스킬 목록을 불러오지 못했습니다. 잠시 뒤 다시 열어 보세요.')
                    : t('아직 공유된 스킬이 없습니다. 내 스킬을 편집해 워크스페이스에 공유할 수 있습니다.')}
              </p>
            )}
            <ShowMore hidden={storePaged.hidden} onMore={storePaged.more} />
          </div>
        ) : (
          <>
        <div className="pt-4">
          <BulkBar
            count={pick.count}
            allPicked={pick.allPicked}
            onToggleAll={pick.toggleAll}
            onClear={pick.clear}
            title={t('스킬')}
            onDelete={async () => {
              await deleteMany('skills', pick.ids)
              pick.clear()
            }}
          />
        </div>
        <div className="space-y-2">
          {/* 기본 스킬은 지울 수 없어 고르는 칸이 없습니다. 목록이 전부 기본이면
              그 칸은 아무것도 담지 못하면서 본문을 28px 밀어 좌우를 어긋나게
              합니다. 고를 수 있는 것이 하나라도 있을 때만 칸을 둡니다. */}
          {visible.map((s) => (
            <Card
              key={s.id}
              className="group flex items-start gap-3 p-4 transition-colors hover:border-line-strong hover:bg-elevated"
            >
              {/* Only where a delete is possible. A checkbox on a built-in
                  skill would put it in 전체 선택 and then refuse it. */}
              {anySelectable &&
                (s.source !== 'built-in' ? (
                  <PickBox
                    checked={pick.picked.has(s.id)}
                    onChange={() => pick.toggle(s.id)}
                    label={t('{name} 선택').replace('{name}', t(s.name))}
                    className="mt-2.5"
                  />
                ) : (
                  <span className="size-4 shrink-0" />
                ))}
              {/* 아이콘은 모든 행이 같은 그림이라 어느 스킬인지 말해 주지 않으면서
                  본문 앞에 40px 을 먹고 있었습니다. */}
              <button
                onClick={() => setDetail(s)}
                title={t('자세히 보기')}
                className="min-w-0 flex-1 cursor-pointer text-left"
              >
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-base font-medium">{t(s.name)}</span>
                  <span className="font-mono text-xs text-faint">{s.slug}</span>
                  <Badge>{t(sourceLabel[s.source])}</Badge>
                  <Badge>v{s.version}</Badge>
                  {/* 공유한 것만 배지를 답니다. 개인이 기본값이라 모든 행에
                      "개인" 을 붙이면 알려 주는 것 없이 줄만 길어집니다. */}
                  {s.visibility === 'org' && (
                    <Badge tone="success">
                      <Globe size={10} />
                      {t('공유됨')}
                    </Badge>
                  )}
                  {s.kinds.map((k) => (
                    <Badge key={k} tone="accent">
                      {t(kindMeta[k].label)}
                    </Badge>
                  ))}
                </span>
                <span className="mt-1 block text-base text-muted">{t(s.description)}</span>
                <span className="mt-1.5 block text-xs text-faint">
                  {t('사용 시점')}: {t(s.whenToUse)} · {t('{when} 수정').replace('{when}', relativeTime(s.updatedAt))}
                </span>
              </button>
              {/* 카드 전체가 상세를 여는데 그렇게 보이는 데가 없었습니다. 호버로만
                  드러내면 손가락으로 읽는 화면에서는 끝내 안 보입니다. */}
              {/* Delete lives on the card, like memory and connectors. It used
                  to be reachable only by opening the detail modal, so the same
                  action sat in two different places depending on the screen. */}
              {s.source !== 'built-in' && (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('{name} 삭제').replace('{name}', t(s.name))}
                  title={t('이 스킬을 삭제합니다')}
                  onClick={() => setConfirming(s)}
                >
                  <Trash2 size={14} />
                </Button>
              )}
              {/* 켜고 끄는 것과 열어 보는 것을 세로로 겹쳐 둡니다. 나란히 놓으면
                  카드 오른쪽이 그만큼 넓어지고, 세 줄짜리 카드의 세로는 비어
                  있었습니다. */}
              <div className="flex shrink-0 flex-col items-end justify-between self-stretch">
                <ChevronRight
                  size={16}
                  aria-hidden
                  className="mr-1.5 text-faint transition-transform group-hover:translate-x-0.5"
                />
                {/* 스위치의 히트 영역은 36px 인데 보이는 트랙은 그 안에서 20px
                    입니다. 상자를 기준으로 맞추면 눈에는 아래가 8px 넓어 보여,
                    그 8px 을 되돌려 트랙과 화살표의 여백을 같게 둡니다. */}
                <span className="-mb-2 flex">
                  <Switch
                    checked={s.enabled}
                    onChange={() => toggleSkill(s.id)}
                    label={t('{name} 설치 상태').replace('{name}', t(s.name))}
                  />
                </span>
              </div>
            </Card>
          ))}
          {/* 승인만 받으면 스킬 여덟 개가 들어와 있던 시절이 끝났습니다. 빈
              화면이 "기능이 없다" 로 읽히지 않도록, 나머지가 어디 있는지 여기서
              말합니다. */}
          {all.length === 0 && (
            <EmptyState
              icon={<FileCode2 size={18} />}
              title={t('아직 스킬이 없습니다')}
              description={t('워크스페이스 스토어에서 필요한 절차를 가져오거나, 직접 하나 만들어 시작하세요.')}
              action={
                <Button variant="primary" onClick={() => setFilter('store')}>
                  <Download size={16} />
                  {t('스토어 둘러보기')}
                </Button>
              }
            />
          )}
        </div>
        <ShowMore hidden={hidden} onMore={more} />
          </>
        )}
      </PageBody>

      <ConfirmDialog
        open={!!confirming}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming && void deleteSkill(confirming.id)}
        title={t('{name} 삭제').replace('{name}', t(confirming?.name ?? ''))}
        description={t('되돌릴 수 없습니다. 이 스킬을 쓰던 대화는 그대로 남습니다.')}
      />

      <Modal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.name ?? ''}
        description={detail?.description}
        width="max-w-2xl"
        /* 기본 스킬에는 편집도 삭제도 없습니다. 빈 조각을 넘기면 Modal 이
           아무것도 안 든 바를 하나 그립니다. */
        footer={
          detail && detail.source !== 'built-in' ? (
            <>
              <Button onClick={() => startEdit(detail)}>{t('편집')}</Button>
              <Button
                variant="danger"
                onClick={() => {
                  void deleteSkill(detail.id)
                  setDetail(null)
                }}
              >
                {t('삭제')}
              </Button>
            </>
          ) : undefined
        }
      >
        {detail && (
          <>
            <div className="flex flex-wrap gap-2">
              <Badge tone="accent">{detail.slug}</Badge>
              <Badge>{t(sourceLabel[detail.source])}</Badge>
              <Badge>v{detail.version}</Badge>
              <Badge tone={detail.visibility === 'org' ? 'success' : 'neutral'}>
                {detail.visibility === 'org' ? <Globe size={10} /> : <Lock size={10} />}
                {detail.visibility === 'org' ? t('공유됨') : t('개인')}
              </Badge>
              <Badge tone={detail.enabled ? 'success' : 'neutral'}>
                {detail.enabled ? t('설치됨') : t('사용 중지')}
              </Badge>
              <Badge>{t('약 {n} 토큰').replace('{n}', detail.estimatedTokens.toLocaleString())}</Badge>
            </div>
            {detail.requiredTools.length > 0 && (
              <div>
                <p className="mb-1.5 text-base font-medium">{t('필수 도구')}</p>
                <div className="flex flex-wrap gap-1.5">
                  {detail.requiredTools.map((tool) => (
                    <Badge key={tool}>{tool}</Badge>
                  ))}
                </div>
              </div>
            )}
            <div>
              <p className="mb-1.5 text-base font-medium">{t('사용 시점')}</p>
              <p className="rounded-control border border-line bg-elevated px-3 py-2 text-md text-muted">
                {t(detail.whenToUse)}
              </p>
            </div>
            {/* 무엇을 지시하는 스킬인지는 이 본문이 전부인데, 여기에는 파일
                이름만 있고 본문은 수정 폼 안에만 있었습니다 — 읽으려면 편집
                모드로 들어가야 했습니다. */}
            {detail.body.trim() && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-base font-medium">
                  <FileCode2 size={14} className="text-faint" />
                  {/* 스킬은 본문 한 벌이 전부다. 파일 목록이라 부를 것이
                      서버에 없어, 곁들여 오는 파일을 그리던 자리는 늘 비어
                      있었다 — 없는 기능을 화면이 지어내지 않게 걷어낸다. */}
                  <span className="font-mono">SKILL.md</span>
                </p>
                <div className="max-h-80 overflow-y-auto rounded-control border border-line bg-elevated px-3 py-2">
                  <Markdown>{detail.body}</Markdown>
                </div>
              </div>
            )}
          </>
        )}
      </Modal>

      <Modal
        open={creating}
        onClose={() => {
          setCreating(false)
          setSaveError(null)
        }}
        title={editing ? t('스킬 편집') : t('새 스킬')}
        description={t('스킬의 기본 정보를 채웁니다. 저장하면 워크스페이스에 등록됩니다.')}
        footer={
          <>
            <Button
              onClick={() => {
                setCreating(false)
                reset()
              }}
            >
              {t('취소')}
            </Button>
            <Button
              variant="primary"
              disabled={saving || !draft.name.trim()}
              onClick={async () => {
                setSaving(true)
                setSaveError(null)
                try {
                  const current = editing ? skills.find((s) => s.id === editing) : undefined
                  // A blank id means "create" — the server assigns id and slug.
                  // Editing keeps everything the form does not cover, so a
                  // revision cannot silently reset a skill's surfaces or state.
                  await upsertSkill({
                    id: editing ?? '',
                    slug: current?.slug ?? '',
                    source: current?.source ?? 'personal',
                    catalogKey: current?.catalogKey ?? null,
                    kinds: current?.kinds ?? ['chat', 'report', 'slides'],
                    enabled: current?.enabled ?? true,
                    version: current?.version ?? '1.0.0',
                    estimatedTokens: current?.estimatedTokens ?? 0,
                    installs: current?.installs ?? 0,
                    originId: current?.originId ?? null,
                    updatedAt: new Date().toISOString(),
                    ...draft,
                  })
                  setCreating(false)
                  reset()
                } catch (err) {
                  setSaveError(errorMessage(err, t('저장하지 못했습니다.')))
                } finally {
                  setSaving(false)
                }
              }}
            >
              {saving ? t('저장 중…') : editing ? t('저장') : t('만들기')}
            </Button>
          </>
        }
      >
        {saveError && (
          <p
            role="status"
            className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger"
          >
            {saveError}
          </p>
        )}
        <Field label={t('이름')}>
          <Input
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder={t('예: 배포 전 리스크 검토')}
            // The server refuses anything longer. Offering the extra
            // characters and then rejecting them is a round trip spent to say
            // no to something the form could have declined to take.
            maxLength={NAME_LIMIT}
          />
        </Field>
        <Field label={t('설명')}>
          <Input
            value={draft.description}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            placeholder={t('이 스킬이 무엇을 하는지 한 줄로')}
          />
        </Field>
        <Field
          label={t('사용 시점')}
          hint={t('이 설명을 보고 모델이 스킬을 쓸지 판단합니다. 구체적일수록 좋습니다.')}
        >
          <Textarea
            rows={2}
            value={draft.whenToUse}
            onChange={(e) => setDraft({ ...draft, whenToUse: e.target.value })}
            placeholder={t('사용자가 의사결정 자료를 붙여넣고 리스크 검토를 요청할 때')}
          />
        </Field>
        <Field label={t('절차')} hint={t('모델이 그대로 따를 단계입니다.')}>
          <Textarea
            rows={5}
            value={draft.body}
            onChange={(e) => setDraft({ ...draft, body: e.target.value })}
            placeholder={t('1. 입력 자료와 판단 기준을 확인한다\n2. 결과와 근거, 미확인 항목을 구분한다')}
          />
        </Field>
        {/* 공개 범위. 에이전트 화면과 같은 두 상태이고, 같은 뜻입니다 — 공유는
            "여기서 쓰라" 가 아니라 "가져다 쓰라" 입니다. 스킬은 언제나 소유자의
            계정에서 실행되므로, 공유된 스킬은 복사할 수 있을 뿐입니다. */}
        <Field
          label={t('공개 범위')}
          hint={t('공유하면 워크스페이스 스토어에 올라가고, 다른 사용자가 각자 사본을 가져갑니다. 내가 고쳐도 이미 가져간 사본은 그대로입니다.')}
        >
          <div className="flex gap-2">
            {([
              { id: 'private', label: t('나만 사용'), icon: Lock },
              { id: 'org', label: t('모두에게 공개'), icon: Globe },
            ] as const).map((o) => {
              const Icon = o.icon
              return (
                <button
                  type="button"
                  key={o.id}
                  onClick={() => setDraft({ ...draft, visibility: o.id })}
                  className={cn(
                    'flex flex-1 items-center justify-center gap-1.5 rounded-control border px-3 py-2 text-base transition-colors',
                    draft.visibility === o.id
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-muted hover:bg-elevated',
                  )}
                >
                  <Icon size={14} />
                  {o.label}
                </button>
              )
            })}
          </div>
        </Field>
        <Field
          label={t('필수 도구')}
          hint={t('선택한 도구가 현재 모델과 에이전트에 허용된 경우에만 이 스킬을 실행합니다.')}
        >
          <div className="flex flex-wrap gap-1.5">
            {toolOptions.map((tool) => {
              const on = draft.requiredTools.includes(tool.name)
              return (
                <button
                  type="button"
                  key={tool.name}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      requiredTools: on
                        ? draft.requiredTools.filter((name) => name !== tool.name)
                        : [...draft.requiredTools, tool.name],
                    })
                  }
                  className={cn(
                    'rounded-control border px-2.5 py-1.5 text-base transition-colors',
                    on
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-muted hover:bg-elevated',
                  )}
                >
                  {tool.label}
                  <span className="ml-1 font-mono text-2xs text-faint">{tool.name}</span>
                </button>
              )
            })}
          </div>
        </Field>
      </Modal>
    </>
  )
}
