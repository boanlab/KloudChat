import { LayoutTemplate } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Button, Modal } from '@/components/ui'
import { templatesFor } from '@/lib/templates'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/**
 * Starting points for each surface. Opened as a modal so it does not clutter
 * an empty screen, but the button that opens it is always visible — the point
 * is that somebody who does not know how to ask can still begin.
 *
 * A card shows **what you need to bring**, not a prompt to paste.
 */
export function TemplateGallery({
  kind,
  onPick,
}: {
  kind: SessionKind
  onPick?: (prompt: string) => void
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [group, setGroup] = useState<string | 'all'>('all')
  const setDraft = useStore((s) => s.setDraft)

  const items = useMemo(() => templatesFor(kind), [kind])
  const groups = useMemo(() => [...new Set(items.map((t) => t.group))], [items])
  const visible = group === 'all' ? items : items.filter((t) => t.group === group)

  if (items.length === 0) return null

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <LayoutTemplate size={14} />
        {t('템플릿에서 시작')}
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={t('무엇을 만드나요')}
        description={t('고르면 입력창에 채워집니다. 나머지는 직접 적으면 됩니다.')}
        width="max-w-2xl"
      >
        <div className="flex flex-wrap gap-1.5">
          {(['all', ...groups] as const).map((g) => (
            <button
              key={g}
              onClick={() => setGroup(g)}
              className={cn(
                'rounded-lg border px-2.5 py-1 text-[13px] transition-colors',
                group === g
                  ? 'border-accent bg-accent-soft text-accent'
                  : 'border-line text-muted hover:bg-elevated',
              )}
            >
              {g === 'all' ? t('전체') : g}
            </button>
          ))}
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          {visible.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                setOpen(false)
                // Filled, never sent. Every prompt here ends mid-sentence.
                if (onPick) onPick(t.prompt)
                else setDraft(t.prompt)
              }}
              className="group rounded-xl border border-line bg-panel p-3.5 text-left transition-colors hover:border-accent hover:bg-elevated"
            >
              <p className="text-[13px] font-medium">{t.title}</p>
              <p className="mt-1 text-[12px] text-muted">{t.description}</p>
              {/* 준비물. 고르기 전에 "지금 이걸 시작할 수 있나" 를 알 수 있어야 한다 */}
              <div className="mt-2.5 flex flex-wrap gap-1">
                {t.fills.map((f) => (
                  <span
                    key={f}
                    className="rounded-md bg-elevated px-1.5 py-0.5 text-[11px] text-faint transition-colors group-hover:bg-panel"
                  >
                    {f}
                  </span>
                ))}
              </div>
            </button>
          ))}
        </div>
      </Modal>
    </>
  )
}
