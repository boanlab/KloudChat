import { Children, isValidElement, useCallback, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { decodeSort, encodeSort, nextSort, type SortDir, type SortState } from '@/lib/tableSort'

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
