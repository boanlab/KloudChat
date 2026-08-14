import { Brain, Pin, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Modal,
  PageHeader,
  Tabs,
  Textarea,
} from '@/components/ui'
import { cn, relativeTime, uid } from '@/lib/utils'
import { ShowMore, usePaged } from '@/components/ui/ShowMore'
import { useStore } from '@/store/useStore'
import type { MemoryEntry, MemoryType } from '@/types'
import { useT } from '@/lib/useT'

const typeTone: Record<MemoryType, 'accent' | 'success' | 'warn' | 'neutral'> = {
  user: 'accent',
  feedback: 'warn',
  project: 'success',
  reference: 'neutral',
}

const typeHelp: Record<MemoryType, string> = {
  user: '사용자가 누구인지 — 역할, 전문성, 선호',
  feedback: '작업 방식에 대한 지시 — 왜 그런지 함께',
  project: '진행 중인 일과 제약 — 코드에서 유추할 수 없는 것',
  reference: '외부 자료 포인터 — URL, 대시보드, 티켓',
}

const emptyDraft = (): MemoryEntry => ({
  id: uid('m'),
  name: '',
  description: '',
  type: 'project',
  body: '',
  scope: 'global',
  links: [],
  updatedAt: new Date().toISOString(),
  pinned: false,
})

export function MemoryPage() {
  const t = useT()
  const { memories, projects, upsertMemory, deleteMemory, togglePinMemory, loadWorkspace } = useStore()

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  const [filter, setFilter] = useState<MemoryType | 'all'>('all')
  const [draft, setDraft] = useState<MemoryEntry | null>(null)

  const visible = filter === 'all' ? memories : memories.filter((m) => m.type === filter)
  const ordered = [...visible].sort((a, b) => Number(b.pinned) - Number(a.pinned))
  const { visible: sorted, hidden, more } = usePaged(ordered, [filter, memories.length])

  return (
    <>
      <TopBar left={<span className="text-[13px] font-medium">{t('메모리')}</span>} />
      <PageBody>
        <PageHeader
          title={t('메모리')}
          description={t('대화에서 알게 된 사실을 하나씩 저장해 두면, 다음 대화에서 관련 있는 것만 골라 참고합니다.')}
          action={
            <Button variant="primary" onClick={() => setDraft(emptyDraft())}>
              <Plus size={16} />
          {t('새 메모리')}
            </Button>
          }
        />

        <Tabs<MemoryType | 'all'>
          value={filter}
          onChange={setFilter}
          tabs={[
            { id: 'all', label: t('전체'), count: memories.length },
            { id: 'user', label: t('사용자') },
            { id: 'feedback', label: t('피드백') },
            { id: 'project', label: t('프로젝트') },
            { id: 'reference', label: t('참조') },
          ]}
        />

        <div className="space-y-2 pt-4">
          {sorted.length === 0 && (
            <EmptyState icon={<Brain size={18} />} title={t('저장된 메모리가 없습니다')} />
          )}
          {sorted.map((m) => {
            const project = projects.find((p) => p.id === m.scope)
            return (
              <Card key={m.id} className="p-4">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        onClick={() => setDraft(m)}
                        className="font-mono text-[12px] text-accent hover:underline"
                      >
                        {m.name}
                      </button>
                      <Badge tone={typeTone[m.type]}>{m.type}</Badge>
                      {project && <Badge>{project.emoji} {project.name}</Badge>}
                      {m.pinned && (
                        <Badge tone="accent">
                          <Pin size={10} />
                          {t('고정')}
                        </Badge>
                      )}
                    </div>
                    <p className="mt-1.5 text-[13px]">{m.description}</p>
                    <p className="mt-1.5 line-clamp-2 text-[12px] whitespace-pre-line text-muted">
                      {m.body}
                    </p>
                    {m.links.length > 0 && (
                      <p className="mt-1.5 flex flex-wrap gap-1.5 text-[11px] text-faint">
                        {m.links.map((l) => (
                          <span key={l} className="font-mono">
                            [[{l}]]
                          </span>
                        ))}
                      </p>
                    )}
                    <p className="mt-2 text-[11px] text-faint">{t('{when} 수정').replace('{when}', relativeTime(m.updatedAt))}</p>
                  </div>
                  <div className="flex shrink-0 gap-0.5">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 고정').replace('{name}', m.name)}
                      className={cn(m.pinned && 'text-accent')}
                      onClick={() => togglePinMemory(m.id)}
                    >
                      <Pin size={14} />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 삭제').replace('{name}', m.name)}
                      onClick={() => deleteMemory(m.id)}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                </div>
              </Card>
            )
          })}
        </div>
        <ShowMore hidden={hidden} onMore={more} />
      </PageBody>

      <Modal
        open={!!draft}
        onClose={() => setDraft(null)}
        title={memories.some((m) => m.id === draft?.id) ? t('메모리 편집') : t('새 메모리')}
        description={t('한 번에 하나씩, 짧고 분명하게 적으세요.')}
        width="max-w-xl"
        footer={
          <>
            <Button onClick={() => setDraft(null)}>{t('취소')}</Button>
            <Button
              variant="primary"
              disabled={!draft?.name.trim()}
              onClick={() => {
                if (draft) upsertMemory({ ...draft, updatedAt: new Date().toISOString() })
                setDraft(null)
              }}
            >
              {t('저장')}
            </Button>
          </>
        }
      >
        {draft && (
          <>
            <Field label={t('이름')} hint={t('영문 소문자와 하이픈으로 짓습니다. 다른 메모리에서 [[이름]]으로 불러옵니다.')}>
              <Input
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="user-prefers-terse-answers"
                className="font-mono"
              />
            </Field>
            <Field label={t('유형')} hint={t(typeHelp[draft.type])}>
              <div className="flex gap-1.5">
                {(['user', 'feedback', 'project', 'reference'] as MemoryType[]).map((t) => (
                  <button
                    key={t}
                    onClick={() => setDraft({ ...draft, type: t })}
                    className={cn(
                      'rounded-lg border px-2.5 py-1.5 text-[13px] transition-colors',
                      draft.type === t
                        ? 'border-accent bg-accent-soft text-accent'
                        : 'border-line hover:bg-elevated',
                    )}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </Field>
            <Field label={t('설명')} hint={t('이 메모리를 언제 참고할지 판단하는 한 줄 요약입니다.')}>
              <Input
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              />
            </Field>
            <Field label={t('범위')}>
              <select
                value={draft.scope}
                onChange={(e) => setDraft({ ...draft, scope: e.target.value })}
                className="h-9 w-full rounded-lg border border-line bg-panel px-3 text-sm focus:border-accent focus:outline-none"
              >
                <option value="global">{t('전역')}</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.emoji} {p.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t('본문')}>
              <Textarea
                rows={6}
                value={draft.body}
                onChange={(e) => setDraft({ ...draft, body: e.target.value })}
                placeholder={'**Why:** …\n\n**How to apply:** …'}
              />
            </Field>
          </>
        )}
      </Modal>
    </>
  )
}
