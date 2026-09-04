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
  LoadingState,
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

/** A shared skill with an install button; installing takes a copy. */
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
    workspaceLoading,
  } = useStore()

  useEffect(() => {
    void loadWorkspace()
    void loadSkillStore()
  }, [loadWorkspace, loadSkillStore])
  const [filter, setFilter] = useState<Filter>('all')
  const [detail, setDetail] = useState<Skill | null>(null)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [draft, setDraft] = useState({
    name: '',
    description: '',
    whenToUse: '',
    body: '',
    requiredTools: [] as string[],
    visibility: 'private' as Skill['visibility'],
  })
  // Id being edited; null means create.
  const [editing, setEditing] = useState<string | null>(null)
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

  const ordered = useStableOrder(skills)
  const all =
    filter === 'all' || filter === 'store' ? ordered : ordered.filter((s) => s.source === filter)
  const { visible, hidden, more } = usePaged(all, [filter, skills.length])
  const storePaged = usePaged(skillStore, [filter, skillStore.length])
  const anySelectable = visible.some((s) => s.source !== 'built-in')
  // Built-in skills cannot be deleted.
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
          {visible.map((s) => (
            <Card
              key={s.id}
              className="group flex items-start gap-3 p-4 transition-colors hover:border-line-strong hover:bg-elevated"
            >
              {/* Checkbox column only when something on the page is deletable. */}
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
              <div className="flex shrink-0 flex-col items-end justify-between self-stretch">
                <ChevronRight
                  size={16}
                  aria-hidden
                  className="mr-1.5 text-faint transition-transform group-hover:translate-x-0.5"
                />
                {/* `-mb-2` offsets the switch's hit area so the visible track aligns. */}
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
          {workspaceLoading && all.length === 0 ? (
            <LoadingState label={t('스킬을 불러오는 중…')} />
          ) : all.length === 0 && (
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
            {detail.body.trim() && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-base font-medium">
                  <FileCode2 size={14} className="text-faint" />
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
                  // Blank id means create; edits keep fields the form does not cover.
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
