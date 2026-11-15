import { ArrowDown, ArrowUp } from 'lucide-react'
import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button, Select } from '@/components/ui'
import { SortHeader } from '@/components/ui/DataTable'
import type { SortDir } from '@/lib/tableSort'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

/** One sortable column of a server-sorted list: its id, its heading, and which way it starts. */
export type SortField<F extends string> = { id: F; label: string; first: SortDir }

export type ListSortState<F extends string> = { field: F; dir: SortDir }

/**
 * A list's sort kept in the address (`?sort=…&dir=…`), so a reload or Back keeps it; the
 * default sort leaves the address bare. Clicking the sorted column turns it round, another
 * column starts in its own first direction.
 */
export function useUrlSort<F extends string>(fields: SortField<F>[], fallback: ListSortState<F>) {
  const [params, setParams] = useSearchParams()
  const asked = params.get('sort')
  const field = fields.some((f) => f.id === asked) ? (asked as F) : fallback.field
  const dirParam = params.get('dir')
  const dir: SortDir =
    dirParam === 'asc' || dirParam === 'desc'
      ? dirParam
      : field === fallback.field
        ? fallback.dir
        : (fields.find((f) => f.id === field)?.first ?? 'asc')
  const set = useCallback(
    (next: ListSortState<F>) =>
      setParams(
        (current) => {
          const out = new URLSearchParams(current)
          if (next.field === fallback.field && next.dir === fallback.dir) {
            out.delete('sort')
            out.delete('dir')
          } else {
            out.set('sort', next.field)
            out.set('dir', next.dir)
          }
          return out
        },
        { replace: true },
      ),
    [setParams, fallback.field, fallback.dir],
  )
  const click = useCallback(
    (id: F) => {
      if (id === field) set({ field, dir: dir === 'asc' ? 'desc' : 'asc' })
      else set({ field: id, dir: fields.find((f) => f.id === id)?.first ?? 'asc' })
    },
    [field, dir, fields, set],
  )
  return { field, dir, set, click }
}

/** A column heading of a grid list; the button sorts the server's whole list, not the page. */
export function ColumnHeader<F extends string>({
  field,
  sort,
  onSort,
  align,
  className,
}: {
  field: SortField<F>
  sort: ListSortState<F>
  onSort: (id: F) => void
  align?: 'left' | 'right'
  className?: string
}) {
  const t = useT()
  return (
    <SortHeader
      cell="div"
      label={t(field.label)}
      columnKey={field.id}
      sort={{ key: sort.field, dir: sort.dir }}
      onSort={() => onSort(field.id)}
      align={align}
      className={className}
    />
  )
}

/** A phone has no heading row: the column and the direction, side by side. */
export function PhoneSort<F extends string>({
  fields,
  sort,
  onSort,
  className,
}: {
  fields: SortField<F>[]
  sort: ListSortState<F>
  onSort: (next: ListSortState<F>) => void
  className?: string
}) {
  const t = useT()
  const up = sort.dir === 'asc'
  return (
    <div className={cn('flex items-center gap-1.5 sm:hidden', className)} data-testid="phone-sort">
      <Select
        value={sort.field}
        aria-label={t('정렬 기준')}
        onChange={(e) => {
          const id = e.target.value as F
          onSort({ field: id, dir: fields.find((f) => f.id === id)?.first ?? 'asc' })
        }}
      >
        {fields.map((f) => (
          <option key={f.id} value={f.id}>
            {t(f.label)}
          </option>
        ))}
      </Select>
      <Button
        variant="secondary"
        size="icon"
        aria-label={up ? t('오름차순 정렬됨, 내림차순으로 바꾸기') : t('내림차순 정렬됨, 오름차순으로 바꾸기')}
        title={up ? t('오름차순') : t('내림차순')}
        onClick={() => onSort({ field: sort.field, dir: up ? 'desc' : 'asc' })}
      >
        {up ? <ArrowUp size={14} /> : <ArrowDown size={14} />}
      </Button>
    </div>
  )
}
