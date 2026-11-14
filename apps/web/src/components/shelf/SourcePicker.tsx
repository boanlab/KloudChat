import { ListSearch } from '@/components/ui/ListSearch'
import { Button } from '@/components/ui'
import type { ShelfSource } from '@/lib/shelf'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useMemo, useState } from 'react'
import { SOURCE_ICONS } from './sourceKinds'

/** Which sources an output is grounded in: a checkbox each, all/none, search, count. */
export function SourcePicker({
  sources,
  picked,
  onPick,
}: {
  sources: ShelfSource[]
  picked: Set<string>
  onPick: (next: Set<string>) => void
}) {
  const t = useT()
  const [query, setQuery] = useState('')
  const readable = sources.filter((s) => s.readable)
  const shown = useMemo(() => {
    const words = query.trim().toLowerCase()
    return readable.filter((s) => !words || s.name.toLowerCase().includes(words))
  }, [readable, query])
  return (
    <div className="space-y-2" data-testid="source-picker">
      <div className="flex flex-wrap items-center gap-1.5">
        <p className="text-base font-medium">{t('쓸 자료')}</p>
        <span className="text-sm text-muted">
          {t('{n}/{total}개 선택').replace('{n}', String(picked.size)).replace('{total}', String(readable.length))}
        </span>
        <Button size="sm" variant="ghost" onClick={() => onPick(new Set(readable.map((s) => s.id)))}>{t('모두 선택')}</Button>
        <Button size="sm" variant="ghost" onClick={() => onPick(new Set())}>{t('모두 해제')}</Button>
        {readable.length > 6 && <ListSearch value={query} onChange={setQuery} label={t('자료 검색')} className="ml-auto w-56" />}
      </div>
      <ul className="grid max-h-56 gap-1 overflow-y-auto sm:grid-cols-2">
        {shown.map((source) => {
          const Icon = SOURCE_ICONS[source.kind]
          const on = picked.has(source.id)
          return (
            <li key={source.id}>
              <label className={cn('flex cursor-pointer items-center gap-2 rounded-control border px-2 py-1.5 text-sm', on ? 'border-accent/40 bg-accent-soft' : 'border-line')}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => {
                    const next = new Set(picked)
                    if (on) next.delete(source.id)
                    else next.add(source.id)
                    onPick(next)
                  }}
                  className="size-4 shrink-0 accent-[var(--accent)]"
                />
                <Icon size={13} className="shrink-0 text-faint" />
                <span className="min-w-0 truncate">{source.name}</span>
              </label>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
