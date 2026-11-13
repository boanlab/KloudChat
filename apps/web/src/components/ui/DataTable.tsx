import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import { Children, isValidElement, useCallback, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { currentLocale } from '@/lib/i18n'
import {
  ariaSort,
  decodeSort,
  encodeSort,
  nextSort,
  sortRows,
  type SortDir,
  type SortState,
} from '@/lib/tableSort'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

/** Plain text of a cell for sorting: strings and numbers inside elements, in order. */
export function nodeText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(nodeText).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) return Children.toArray(node.props.children).map(nodeText).join('')
  return ''
}

const STORE = 'kloudchat.sort.'

function readStored(id: string): string | null {
  try {
    return window.localStorage.getItem(STORE + id)
  } catch {
    return null
  }
}

function writeStored(id: string, value: string) {
  try {
    if (value) window.localStorage.setItem(STORE + id, value)
    else window.localStorage.removeItem(STORE + id)
  } catch {
    // Private windows and blocked storage: the sort still works for this visit.
  }
}

/**
 * One table's sort. `param` keeps it in the address (a reload, 뒤로 and a shared link keep
 * it); otherwise it is remembered per table in this browser. A heading cycles first
 * direction → the other → unsorted (the table's own order, `initial` if given).
 */
export function useTableSort(
  id: string,
  {
    keys,
    initial = null,
    param,
    clearable = true,
  }: {
    keys: readonly string[]
    initial?: SortState
    param?: string
    /** False: a heading flips between its two directions and never clears (server-ordered lists). */
    clearable?: boolean
  },
): [SortState, (key: string, first?: SortDir) => void] {
  const [params, setParams] = useSearchParams()
  const [local, setLocal] = useState<SortState>(() => {
    const stored = param ? null : readStored(id)
    return stored === null ? initial : stored === 'none' ? null : decodeSort(stored, keys)
  })
  const fromUrl = param ? params.get(param) : null
  const state: SortState = param
    ? fromUrl === null
      ? initial
      : fromUrl === 'none'
        ? null
        : decodeSort(fromUrl, keys) ?? initial
    : local
  const toggle = useCallback(
    (key: string, first: SortDir = 'asc') => {
      const next: SortState = clearable
        ? nextSort(state, key, first)
        : state?.key === key
          ? { key, dir: state.dir === 'asc' ? 'desc' : 'asc' }
          : { key, dir: first }
      // Back to the table's own order; 'none' marks "unsorted on purpose" when that differs.
      const same = encodeSort(next) === encodeSort(initial)
      const text = same ? '' : encodeSort(next) || 'none'
      if (param) {
        setParams(
          (prev) => {
            const out = new URLSearchParams(prev)
            if (text) out.set(param, text)
            else out.delete(param)
            return out
          },
          { replace: true },
        )
      } else {
        setLocal(next)
        writeStored(id, text)
      }
    },
    [state, initial, param, setParams, id, clearable],
  )
  return [state, toggle]
}

/** A sortable column heading: the button names the column, the cell carries `aria-sort`. */
export function SortHeader({
  label,
  columnKey,
  sort,
  onSort,
  first = 'asc',
  align = 'left',
  className,
  buttonClassName,
}: {
  label: ReactNode
  columnKey: string
  sort: SortState
  onSort: (key: string, first: SortDir) => void
  first?: SortDir
  align?: 'left' | 'right'
  className?: string
  buttonClassName?: string
}) {
  const t = useT()
  const aria = ariaSort(sort, columnKey)
  const active = aria !== 'none'
  const Arrow = !active ? ArrowUpDown : aria === 'ascending' ? ArrowUp : ArrowDown
  const name = nodeText(label)
  return (
    <th
      scope="col"
      aria-sort={aria}
      className={cn('font-medium whitespace-nowrap', align === 'right' ? 'text-right' : 'text-left', className)}
    >
      <button
        type="button"
        onClick={() => onSort(columnKey, first)}
        title={
          aria === 'none'
            ? t('{column} 기준으로 정렬').replace('{column}', name)
            : aria === 'ascending'
              ? t('{column} 오름차순으로 정렬됨').replace('{column}', name)
              : t('{column} 내림차순으로 정렬됨').replace('{column}', name)
        }
        className={cn(
          'group/sort -mx-1.5 inline-flex items-center gap-1 rounded-control px-1.5 py-1 whitespace-nowrap transition-colors hover:bg-line/40 hover:text-fg focus-visible:outline-2 focus-visible:outline-accent',
          align === 'right' && 'flex-row-reverse',
          active && 'text-fg',
          buttonClassName,
        )}
      >
        {label}
        <Arrow
          size={12}
          aria-hidden
          className={active ? undefined : 'opacity-40 group-hover/sort:opacity-80'}
        />
      </button>
    </th>
  )
}

export type DataColumn<R> = {
  key: string
  label: ReactNode
  cell: (row: R) => ReactNode
  /** What the column sorts by; the cell's text when left out (units understood: "2.2 GB" > "499 MB"). */
  value?: (row: R) => unknown
  /** False: a plain heading (actions, previews). */
  sortable?: boolean
  align?: 'left' | 'right'
  /** Direction of the first click; figures open largest first, text A→Z. */
  first?: SortDir
  className?: string
  /** Dropped at phone width. */
  phoneHidden?: boolean
}

/**
 * The app's data table: every column heading sorts (asc → desc → unsorted), the heading
 * row stays in view while the body scrolls, and a narrow screen scrolls the table sideways
 * inside its own box rather than the page.
 */
export function DataTable<R>({
  id,
  rows,
  columns,
  rowKey,
  label,
  empty,
  initialSort = null,
  param,
  maxHeight,
  className,
  tableClassName,
  headClassName = 'bg-elevated text-xs text-muted',
  cellClassName = 'px-3 py-2',
  rowClassName,
  minWidth,
}: {
  id: string
  rows: readonly R[]
  columns: DataColumn<R>[]
  rowKey: (row: R, index: number) => string
  label: string
  empty?: ReactNode
  initialSort?: SortState
  param?: string
  /** A height cap (CSS length) that makes the body scroll under a sticky heading row. */
  maxHeight?: string
  className?: string
  tableClassName?: string
  headClassName?: string
  cellClassName?: string
  rowClassName?: (row: R) => string | undefined
  minWidth?: string
}) {
  const keys = useMemo(() => columns.filter((c) => c.sortable !== false).map((c) => c.key), [columns])
  const [sort, onSort] = useTableSort(id, { keys, initial: initialSort, param })
  const sorted = useMemo(() => {
    if (!sort) return rows
    const col = columns.find((c) => c.key === sort.key)
    if (!col) return rows
    const value = col.value ?? ((r: R) => nodeText(col.cell(r)))
    return sortRows(rows, value, sort.dir, currentLocale())
  }, [rows, columns, sort])

  if (rows.length === 0 && empty !== undefined) return <>{empty}</>
  return (
    <div
      className={cn('overflow-x-auto rounded-card border border-line', maxHeight && 'overflow-y-auto', className)}
      style={maxHeight ? { maxHeight } : undefined}
      data-testid={`table-${id}`}
    >
      <table className={cn('w-full text-sm', tableClassName)} aria-label={label} style={minWidth ? { minWidth } : undefined}>
        <thead className={cn('sticky top-0 z-[1]', headClassName)}>
          <tr>
            {columns.map((c) =>
              c.sortable === false ? (
                <th
                  key={c.key}
                  scope="col"
                  className={cn(
                    'font-medium whitespace-nowrap',
                    cellClassName,
                    c.align === 'right' ? 'text-right' : 'text-left',
                    c.phoneHidden && 'max-sm:hidden',
                  )}
                >
                  {c.label}
                </th>
              ) : (
                <SortHeader
                  key={c.key}
                  label={c.label}
                  columnKey={c.key}
                  sort={sort}
                  onSort={onSort}
                  first={c.first ?? (c.align === 'right' ? 'desc' : 'asc')}
                  align={c.align}
                  className={cn(cellClassName, 'py-1.5', c.phoneHidden && 'max-sm:hidden')}
                />
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={rowKey(r, i)} className={cn('border-t border-line', rowClassName?.(r))}>
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={cn(
                    cellClassName,
                    c.align === 'right' && 'text-right tabular-nums whitespace-nowrap',
                    c.phoneHidden && 'max-sm:hidden',
                    c.className,
                  )}
                >
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
