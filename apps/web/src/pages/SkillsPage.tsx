import { FileCode2, Plus, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
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
import { kindMeta } from '@/lib/kinds'
import { useStableOrder } from '@/lib/useStableOrder'
import { cn, relativeTime } from '@/lib/utils'
import { ShowMore, usePaged } from '@/components/ui/ShowMore'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { useStore } from '@/store/useStore'
import type { Skill } from '@/types'
import { errorMessage } from '@/lib/api'
import { NAME_LIMIT } from '@/lib/limits'
import { useT } from '@/lib/useT'

type Filter = 'all' | 'built-in' | 'workspace' | 'personal'

const sourceLabel: Record<Skill['source'], string> = {
  'built-in': '기본',
  workspace: '워크스페이스',
  personal: '개인',
}

export function SkillsPage() {
  const t = useT()
  const { skills, availableTools, toggleSkill, upsertSkill, deleteSkill, deleteMany, loadWorkspace } =
    useStore()

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
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
    setDraft({ name: '', description: '', whenToUse: '', body: '', requiredTools: [] })
    setEditing(null)
  }
  const startEdit = (s: Skill) => {
    setDraft({
      name: s.name,
      description: s.description,
      whenToUse: s.whenToUse,
      body: s.body,
      requiredTools: s.requiredTools,
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
  const all = filter === 'all' ? ordered : ordered.filter((s) => s.source === filter)
  const { visible, hidden, more } = usePaged(all, [filter, skills.length])
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
          ]}
        />

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
            <Card key={s.id} className="flex items-start gap-3 p-4">
              {/* Only where a delete is possible. A checkbox on a built-in
                  skill would put it in 전체 선택 and then refuse it. */}
              {s.source !== 'built-in' ? (
                <PickBox
                  checked={pick.picked.has(s.id)}
                  onChange={() => pick.toggle(s.id)}
                  label={t('{name} 선택').replace('{name}', t(s.name))}
                  className="mt-2.5"
                />
              ) : (
                <span className="size-4 shrink-0" />
              )}
              <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-control bg-accent-soft text-accent">
                <Sparkles size={15} />
              </span>
              <button
                onClick={() => setDetail(s)}
                className="min-w-0 flex-1 cursor-pointer text-left"
              >
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-base font-medium">{t(s.name)}</span>
                  <span className="font-mono text-xs text-faint">{s.slug}</span>
                  <Badge>{t(sourceLabel[s.source])}</Badge>
                  <Badge>v{s.version}</Badge>
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
              <Switch
                checked={s.enabled}
                onChange={() => toggleSkill(s.id)}
                label={t('{name} 설치 상태').replace('{name}', t(s.name))}
              />
            </Card>
          ))}
        </div>
        <ShowMore hidden={hidden} onMore={more} />
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
          <>
            <Button onClick={() => setDetail(null)}>{t('닫기')}</Button>
            {detail?.source !== 'built-in' && (
              <Button onClick={() => detail && startEdit(detail)}>{t('편집')}</Button>
            )}
            {detail?.source !== 'built-in' && (
              <Button
                variant="danger"
                onClick={() => {
                  if (detail) void deleteSkill(detail.id)
                  setDetail(null)
                }}
              >
                {t('삭제')}
              </Button>
            )}
          </>
        }
      >
        {detail && (
          <>
            <div className="flex flex-wrap gap-2">
              <Badge tone="accent">{detail.slug}</Badge>
              <Badge>{t(sourceLabel[detail.source])}</Badge>
              <Badge>v{detail.version}</Badge>
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
              <p className="rounded-control border border-line bg-elevated px-3 py-2 text-base text-muted">
                {t(detail.whenToUse)}
              </p>
            </div>
            <div>
              <p className="mb-1.5 text-base font-medium">{t('번들 파일')}</p>
              <div className="divide-y divide-[var(--border)] overflow-hidden rounded-control border border-line">
                {detail.files.map((f) => (
                  <div key={f} className="flex items-center gap-2 px-3 py-2 text-base">
                    <FileCode2 size={14} className="text-faint" />
                    <span className="font-mono">{f}</span>
                  </div>
                ))}
              </div>
            </div>
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
                    files: current?.files ?? ['SKILL.md'],
                    estimatedTokens: current?.estimatedTokens ?? 0,
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
