import { Brain, Pin, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  LoadingState,
  ReloadNotice,
  PageHeader,
  Tabs,
} from '@/components/ui'
import { MemoryEditor, emptyMemory, memoryTypeTone } from '@/components/memory/MemoryEditor'
import { cn, relativeTime } from '@/lib/utils'
import { ShowMore, usePaged } from '@/components/ui/ShowMore'
import { useStore } from '@/store/useStore'
import type { MemoryEntry, MemoryType } from '@/types'
import { useT } from '@/lib/useT'

export function MemoryPage() {
  const t = useT()
  const { memories, projects, deleteMemory, togglePinMemory, loadWorkspace, workspaceLoading, workspaceFailed } =
    useStore()

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  const [filter, setFilter] = useState<MemoryType | 'all'>('all')
  const [draft, setDraft] = useState<MemoryEntry | null>(null)
  const [confirming, setConfirming] = useState<MemoryEntry | null>(null)

  const visible = filter === 'all' ? memories : memories.filter((m) => m.type === filter)
  const ordered = [...visible].sort((a, b) => Number(b.pinned) - Number(a.pinned))
  const { visible: sorted, hidden, more } = usePaged(ordered, [filter, memories.length])

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('메모리')}</span>} />
      <PageBody>
        <PageHeader
          title={t('메모리')}
          description={t('대화에서 알게 된 사실을 하나씩 저장해 두면, 다음 대화에서 관련 있는 것만 골라 참고합니다.')}
          action={
            <Button variant="primary" onClick={() => setDraft(emptyMemory())}>
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

        {workspaceFailed && <ReloadNotice onRetry={() => void loadWorkspace()} />}

        <div className="space-y-2 pt-4">
          {workspaceLoading && memories.length === 0 && <LoadingState />}
          {!workspaceLoading && sorted.length === 0 && (
            <EmptyState
              icon={<Brain size={18} />}
              title={t('저장된 메모리가 없습니다')}
              description={t('반복해서 설명하게 되는 것을 하나 적어 두면, 다음 대화부터는 말하지 않아도 됩니다.')}
              action={
                <Button variant="primary" onClick={() => setDraft(emptyMemory())}>
                  <Plus size={16} />
                  {t('첫 메모리 만들기')}
                </Button>
              }
            />
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
                        title={t('이 기억을 엽니다')}
                        /* 이름 자체가 여는 버튼이다. 글자 높이(18px)가 곧
                           누르는 높이여서, 손가락으로는 옆의 배지를 눌렀다. */
                        className="-my-2 py-2 font-mono text-sm text-accent hover:underline"
                      >
                        {m.name}
                      </button>
                      <Badge tone={memoryTypeTone[m.type]}>{m.type}</Badge>
                      {project && <Badge>{project.emoji} {project.name}</Badge>}
                      {m.pinned && (
                        <Badge tone="accent">
                          <Pin size={10} />
                          {t('고정')}
                        </Badge>
                      )}
                    </div>
                    <p className="mt-1.5 text-base">{m.description}</p>
                    <p className="mt-1.5 line-clamp-2 text-sm whitespace-pre-line text-muted">
                      {m.body}
                    </p>
                    {m.links.length > 0 && (
                      <p className="mt-1.5 flex flex-wrap gap-1.5 text-xs text-faint">
                        {m.links.map((l) => (
                          <span key={l} className="font-mono">
                            [[{l}]]
                          </span>
                        ))}
                      </p>
                    )}
                    <p className="mt-2 text-xs text-faint">{t('{when} 수정').replace('{when}', relativeTime(m.updatedAt))}</p>
                  </div>
                  <div className="flex shrink-0 gap-0.5">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 고정').replace('{name}', m.name)}
                      title={t('고정하면 모든 대화에 먼저 전달됩니다')}
                      className={cn(m.pinned && 'text-accent')}
                      onClick={() => togglePinMemory(m.id)}
                    >
                      <Pin size={14} />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 삭제').replace('{name}', m.name)}
                      title={t('이 기억을 삭제합니다')}
                      onClick={() => setConfirming(m)}
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

      <ConfirmDialog
        open={!!confirming}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming && deleteMemory(confirming.id)}
        title={t('{name} 삭제').replace('{name}', confirming?.name ?? '')}
        description={t('되돌릴 수 없습니다. 다음 대화부터는 이 내용을 참고하지 않습니다.')}
      />

      <MemoryEditor draft={draft} onDraft={setDraft} onClose={() => setDraft(null)} />
    </>
  )
}
