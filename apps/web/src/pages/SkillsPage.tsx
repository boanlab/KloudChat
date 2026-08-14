import { FileCode2, Plus, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  Modal,
  PageHeader,
  Switch,
  Tabs,
  Textarea,
} from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { relativeTime } from '@/lib/utils'
import { ShowMore, usePaged } from '@/components/ui/ShowMore'
import { useStore } from '@/store/useStore'
import type { Skill } from '@/types'
import { useT } from '@/lib/useT'

type Filter = 'all' | 'built-in' | 'workspace' | 'personal'

const sourceLabel: Record<Skill['source'], string> = {
  'built-in': '기본',
  workspace: '워크스페이스',
  personal: '개인',
}

export function SkillsPage() {
  const t = useT()
  const { skills, toggleSkill, upsertSkill, deleteSkill, loadWorkspace } = useStore()

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  const [filter, setFilter] = useState<Filter>('all')
  const [detail, setDetail] = useState<Skill | null>(null)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
    /** The skill being written. `editing` holding an id means edit; null
     *  means create. */
  const [draft, setDraft] = useState({ name: '', description: '', whenToUse: '', body: '' })
  const [editing, setEditing] = useState<string | null>(null)

  const reset = () => {
    setDraft({ name: '', description: '', whenToUse: '', body: '' })
    setEditing(null)
  }
  const startEdit = (s: Skill) => {
    setDraft({
      name: s.name,
      description: s.description,
      whenToUse: s.whenToUse,
      body: (s as Skill & { body?: string }).body ?? '',
    })
    setEditing(s.id)
    setDetail(null)
    setCreating(true)
  }

  // Newest first. With a paged list the ordering decides what exists as far as
  // the user is concerned — a skill created a moment ago that sorts to position
  // sixty is indistinguishable from one that failed to save.
  const all = [...(filter === 'all' ? skills : skills.filter((s) => s.source === filter))].sort(
    (a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt),
  )
  const { visible, hidden, more } = usePaged(all, [filter, skills.length])

  return (
    <>
      <TopBar left={<span className="text-[13px] font-medium">{t('스킬')}</span>} />
      <PageBody>
        <PageHeader
          title={t('스킬')}
          description={t('특정 작업을 어떻게 처리할지 적어 둔 절차입니다. 관련된 요청이 오면 모델이 스스로 불러옵니다.')}
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

        <div className="space-y-2 pt-4">
          {visible.map((s) => (
            <Card key={s.id} className="flex items-start gap-3 p-4">
              <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent">
                <Sparkles size={15} />
              </span>
              <button
                onClick={() => setDetail(s)}
                className="min-w-0 flex-1 cursor-pointer text-left"
              >
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{t(s.name)}</span>
                  <span className="font-mono text-[11px] text-faint">{s.slug}</span>
                  <Badge>{t(sourceLabel[s.source])}</Badge>
                  <Badge>v{s.version}</Badge>
                  {s.kinds.map((k) => (
                    <Badge key={k} tone="accent">
                      {t(kindMeta[k].label)}
                    </Badge>
                  ))}
                </span>
                <span className="mt-1 block text-[13px] text-muted">{t(s.description)}</span>
                <span className="mt-1.5 block text-[11px] text-faint">
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
                  onClick={() => void deleteSkill(s.id)}
                >
                  <Trash2 size={14} />
                </Button>
              )}
              <Switch
                checked={s.enabled}
                onChange={() => toggleSkill(s.id)}
                label={t('{name} 활성화').replace('{name}', t(s.name))}
              />
            </Card>
          ))}
        </div>
        <ShowMore hidden={hidden} onMore={more} />
      </PageBody>

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
                {detail.enabled ? t('활성') : t('비활성')}
              </Badge>
            </div>
            <div>
              <p className="mb-1.5 text-[13px] font-medium">{t('사용 시점')}</p>
              <p className="rounded-lg border border-line bg-elevated px-3 py-2 text-[13px] text-muted">
                {t(detail.whenToUse)}
              </p>
            </div>
            <div>
              <p className="mb-1.5 text-[13px] font-medium">{t('번들 파일')}</p>
              <div className="divide-y divide-[var(--border)] overflow-hidden rounded-lg border border-line">
                {detail.files.map((f) => (
                  <div key={f} className="flex items-center gap-2 px-3 py-2 text-[13px]">
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
        onClose={() => setCreating(false)}
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
                try {
                  const current = editing ? skills.find((s) => s.id === editing) : undefined
                  // A blank id means "create" — the server assigns id and slug.
                  // Editing keeps everything the form does not cover, so a
                  // revision cannot silently reset a skill's surfaces or state.
                  await upsertSkill({
                    id: editing ?? '',
                    slug: current?.slug ?? '',
                    source: current?.source ?? 'personal',
                    kinds: current?.kinds ?? ['chat', 'report', 'slides'],
                    enabled: current?.enabled ?? true,
                    version: current?.version ?? '1.0.0',
                    files: current?.files ?? ['SKILL.md'],
                    updatedAt: new Date().toISOString(),
                    ...draft,
                  })
                  setCreating(false)
                  reset()
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
        <Field label={t('이름')}>
          <Input
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder={t('예: 실험 로그 요약')}
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
            placeholder={t('사용자가 학습 로그 파일을 붙여넣고 요약을 요청할 때')}
          />
        </Field>
        <Field label={t('절차')} hint={t('모델이 그대로 따를 단계입니다.')}>
          <Textarea
            rows={5}
            value={draft.body}
            onChange={(e) => setDraft({ ...draft, body: e.target.value })}
            placeholder={t('1. 로그에서 epoch/loss/metric 열을 찾는다\n2. 최고 성능 지점을 표로 정리한다')}
          />
        </Field>
      </Modal>
    </>
  )
}
