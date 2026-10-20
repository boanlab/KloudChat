import { Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Input } from '@/components/ui'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { DRAG_TYPE } from './flow'
import type { PaletteItem } from './paletteItems'

/** Left side: what can be put on the canvas; click (or Enter) adds after the selected step, drag places it. */
export function Palette({
  groups,
  onPick,
  full,
}: {
  groups: { label: string; items: PaletteItem[]; store?: boolean }[]
  onPick: (item: PaletteItem) => void
  /** At the step limit: steps can no longer be added. */
  full: boolean
}) {
  const t = useT()
  const [query, setQuery] = useState('')
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? groups.map((g) => ({ ...g, items: g.items.filter((i) => i.name.toLowerCase().includes(q)) })) : groups
  }, [groups, query])
  return (
    <div className="flex flex-col gap-4">
      <Field label={t('단계 찾기')}>
        <Input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t('에이전트·스킬 이름')} className="h-8 text-sm" />
      </Field>
      {shown.map((g) => (
        <section key={g.label} aria-label={t(g.label)} className="flex flex-col gap-1.5">
          <h3 className="text-sm font-semibold text-muted">{t(g.label)}</h3>
          {g.items.length === 0 && !g.store && <p className="text-sm text-faint">{t('넣을 수 있는 것이 없습니다.')}</p>}
          {g.items.map((item) => {
            const disabled = full && 'step' in item.make
            return (
              <button
                key={item.key}
                type="button"
                draggable={!disabled}
                disabled={disabled}
                onDragStart={(e) => {
                  e.dataTransfer.setData(DRAG_TYPE, item.key)
                  e.dataTransfer.effectAllowed = 'copy'
                }}
                onClick={() => onPick(item)}
                className={cn(
                  'flex h-9 items-center gap-2 rounded-control border border-line bg-panel px-2.5 text-left text-sm text-fg',
                  'hover:border-accent hover:bg-elevated focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-45',
                )}
                data-testid="palette-item"
              >
                <item.icon size={15} className={cn('shrink-0', item.tone)} aria-hidden />
                <span className="min-w-0 flex-1 truncate">{t(item.name)}</span>
                <Plus size={13} className="shrink-0 text-faint" aria-hidden />
              </button>
            )
          })}
          {g.store && (
            <>
              <Link
                to="/customize?tab=skills&view=explore"
                className="flex h-9 items-center gap-2 rounded-control border border-dashed border-line-strong px-2.5 text-sm text-muted hover:text-fg"
              >
                <Plus size={15} aria-hidden />
                {t('스토어에서 찾기')}
              </Link>
              <p className="text-xs leading-relaxed text-muted">{t('스킬을 캔버스에 끌어 놓으면 그 스킬로 에이전트 초안이 열립니다.')}</p>
            </>
          )}
        </section>
      ))}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm text-muted">
      {label}
      {children}
    </label>
  )
}
