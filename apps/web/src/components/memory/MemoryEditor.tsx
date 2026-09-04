import { useState } from 'react'
import { Button, Field, Input, Modal, Select, Textarea } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { NAME_LIMIT } from '@/lib/limits'
import { cn, uid } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { MemoryEntry, MemoryType } from '@/types'
import { useT } from '@/lib/useT'

export const memoryTypeTone: Record<MemoryType, 'accent' | 'success' | 'warn' | 'neutral'> = {
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

/** A blank memory. `scope` is preset by whoever opened the form. */
export const emptyMemory = (scope = 'global'): MemoryEntry => ({
  id: uid('m'),
  name: '',
  description: '',
  type: 'project',
  body: '',
  scope,
  links: [],
  updatedAt: new Date().toISOString(),
  pinned: false,
})

/** Shared memory form; `lockScope` fixes project context. */
export function MemoryEditor({
  draft,
  onDraft,
  onClose,
  lockScope,
}: {
  draft: MemoryEntry | null
  onDraft: (m: MemoryEntry) => void
  onClose: () => void
  /** Hides the scope select and keeps the draft's own scope. */
  lockScope?: boolean
}) {
  const t = useT()
  const memories = useStore((s) => s.memories)
  const projects = useStore((s) => s.projects)
  const upsertMemory = useStore((s) => s.upsertMemory)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const close = () => {
    setSaveError(null)
    onClose()
  }

  return (
    <Modal
      open={!!draft}
      onClose={close}
      title={memories.some((m) => m.id === draft?.id) ? t('메모리 편집') : t('새 메모리')}
      description={t('한 번에 하나씩, 짧고 분명하게 적으세요.')}
      width="max-w-xl"
      footer={
        <>
          <Button onClick={close}>{t('취소')}</Button>
          <Button
            variant="primary"
            disabled={saving || !draft?.name.trim()}
            onClick={async () => {
              if (!draft) return
              setSaving(true)
              setSaveError(null)
              try {
                await upsertMemory({ ...draft, updatedAt: new Date().toISOString() })
                close()
              } catch (err) {
                // The form stays open with what was typed.
                setSaveError(errorMessage(err, t('저장하지 못했습니다.')))
              } finally {
                setSaving(false)
              }
            }}
          >
            {saving ? t('저장 중…') : t('저장')}
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
          <Field
            label={t('이름')}
            hint={t('영문 소문자와 하이픈으로 짓습니다. 다른 메모리에서 [[이름]]으로 불러옵니다.')}
          >
            <Input
              value={draft.name}
              maxLength={NAME_LIMIT}
              onChange={(e) => onDraft({ ...draft, name: e.target.value })}
              placeholder="user-prefers-terse-answers"
              className="font-mono"
            />
          </Field>
          <Field label={t('유형')} hint={t(typeHelp[draft.type])}>
            <div className="flex gap-1.5">
              {(['user', 'feedback', 'project', 'reference'] as MemoryType[]).map((kind) => (
                <button
                  key={kind}
                  onClick={() => onDraft({ ...draft, type: kind })}
                  className={cn(
                    'rounded-control border px-2.5 py-1.5 text-base transition-colors',
                    draft.type === kind
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line hover:bg-elevated',
                  )}
                >
                  {kind}
                </button>
              ))}
            </div>
          </Field>
          <Field label={t('설명')} hint={t('이 메모리를 언제 참고할지 판단하는 한 줄 요약입니다.')}>
            <Input
              value={draft.description}
              onChange={(e) => onDraft({ ...draft, description: e.target.value })}
            />
          </Field>
          {!lockScope && (
            <Field label={t('범위')}>
              <Select
                value={draft.scope}
                onChange={(e) => onDraft({ ...draft, scope: e.target.value })}
              >
                <option value="global">{t('전역')}</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.emoji} {p.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field label={t('본문')}>
            <Textarea
              rows={6}
              value={draft.body}
              onChange={(e) => onDraft({ ...draft, body: e.target.value })}
              placeholder={'**Why:** …\n\n**How to apply:** …'}
            />
          </Field>
        </>
      )}
    </Modal>
  )
}
