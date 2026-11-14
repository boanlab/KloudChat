import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import { Children, cloneElement, createContext, isValidElement, useContext, useMemo, useState, type ReactElement, type ReactNode } from 'react'
import { currentLocale } from '@/lib/i18n'
import { ariaSort, nextSort, sortRows, sortValue, type SortState } from '@/lib/tableSort'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

/** A markdown (hast) node as react-markdown hands it to a component. */
type Hast = { type: string; tagName?: string; value?: string; children?: Hast[] }

const textOf = (node: Hast | undefined): string =>
  !node ? '' : node.type === 'text' ? (node.value ?? '') : (node.children ?? []).map(textOf).join('')

const cellsOf = (row: Hast | undefined) => (row?.children ?? []).filter((c) => c.type === 'element')

type Sorting = { sort: SortState; onSort: (col: number) => void; columnOf: (node: unknown) => number | undefined }
const SortContext = createContext<Sorting | null>(null)

/**
 * A table in an answer: its headings sort the body rows (a figure by value, "2.2 GB" above
 * "499 MB"; text naturally; asc → desc → as written). The written order is the default, and
 * a table of one row stays plain.
 */
export function MarkdownTable({ node, children }: { node?: Hast; children?: ReactNode }) {
  const [sort, setSort] = useState<SortState>(null)
  const heads = useMemo(() => {
    const thead = node?.children?.find((c) => c.tagName === 'thead')
    return cellsOf(thead?.children?.find((c) => c.tagName === 'tr'))
  }, [node])
  const tbody = node?.children?.find((c) => c.tagName === 'tbody')
  const rowCount = (tbody?.children ?? []).filter((c) => c.tagName === 'tr').length
  const sorting = useMemo<Sorting | null>(
    () =>
      rowCount < 2
        ? null
        : {
            sort,
            onSort: (col) => {
              const key = String(col)
              const figures = (tbody?.children ?? [])
                .filter((c) => c.tagName === 'tr')
                .every((tr) => typeof sortValue(textOf(cellsOf(tr)[col])) !== 'string')
              setSort((s) => nextSort(s, key, figures ? 'desc' : 'asc'))
            },
            columnOf: (n) => {
              const at = heads.indexOf(n as Hast)
              return at < 0 ? undefined : at
            },
          },
    [rowCount, sort, heads, tbody],
  )

  const body = Children.map(children, (child) => {
    if (!sort || !isValidElement<{ node?: Hast; children?: ReactNode }>(child) || child.props.node?.tagName !== 'tbody') {
      return child
    }
    const rows = Children.toArray(child.props.children).filter(isValidElement) as ReactElement<{ node?: Hast }>[]
    const col = Number(sort.key)
    const ordered = sortRows(rows, (r) => textOf(cellsOf(r.props.node)[col]), sort.dir, currentLocale())
    return cloneElement(child, {}, ordered)
  })

  return (
    <div className="my-3 overflow-x-auto rounded-card border border-line">
      <table className="w-full min-w-[30rem] border-collapse text-base">
        <SortContext.Provider value={sorting}>{body}</SortContext.Provider>
      </table>
    </div>
  )
}

export function MarkdownTh({ node, children }: { node?: Hast; children?: ReactNode }) {
  const t = useT()
  const sorting = useContext(SortContext)
  const col = sorting?.columnOf(node)
  const base = 'border-b border-line px-3 py-2 text-left font-semibold break-keep'
  if (!sorting || col === undefined) return <th className={base}>{children}</th>
  const aria = ariaSort(sorting.sort, String(col))
  const Arrow = aria === 'none' ? ArrowUpDown : aria === 'ascending' ? ArrowUp : ArrowDown
  return (
    <th scope="col" aria-sort={aria} className={cn(base, 'py-1.5')}>
      <button
        type="button"
        onClick={() => sorting.onSort(col)}
        title={t('{column} 기준으로 정렬').replace('{column}', textOf(node))}
        className="group/sort -mx-1.5 inline-flex items-center gap-1 rounded-control px-1.5 py-0.5 text-left font-semibold hover:bg-line/40 focus-visible:outline-2 focus-visible:outline-accent"
      >
        {children}
        <Arrow size={12} aria-hidden className={aria === 'none' ? 'shrink-0 opacity-0 group-hover/sort:opacity-60 group-focus-visible/sort:opacity-60' : 'shrink-0'} />
      </button>
    </th>
  )
}
