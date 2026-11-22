import { ChevronRight } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Button } from '@/components/ui'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { parseMindmap, type MindNode } from '@/lib/mindmap'

/** Levels open at first: the branches and their children; deeper ones open on a click. */
const OPEN_DEPTH = 2

function paths(node: MindNode, at = '0', depth = 0, upTo = Infinity): string[] {
  if (!node.children.length || depth >= upTo) return []
  return [at, ...node.children.flatMap((child, i) => paths(child, `${at}.${i}`, depth + 1, upTo))]
}

/** Mermaid `mindmap` as a collapsible tree (left to right on wide screens, indented on a phone) so labels stay at text size. */
export function MindMap({ source }: { source: string }) {
  const t = useT()
  const root = useMemo(() => parseMindmap(source), [source])
  const every = useMemo(() => (root ? paths(root) : []), [root])
  const [open, setOpen] = useState<Set<string>>(() => new Set(root ? paths(root, '0', 0, OPEN_DEPTH) : []))
  if (!root) return null

  const toggle = (path: string) =>
    setOpen((was) => {
      const next = new Set(was)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  const allOpen = every.every((path) => open.has(path))

  return (
    <figure className="not-prose my-5 rounded-card border border-line bg-panel" data-testid="mindmap">
      <div className="flex flex-wrap items-center justify-end gap-1 border-b border-line px-2 py-1.5">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setOpen(allOpen ? new Set(['0']) : new Set(every))}
          data-testid="mindmap-toggle-all"
        >
          {allOpen ? t('모두 접기') : t('모두 펼치기')}
        </Button>
      </div>
      <div className="overflow-x-auto px-4 py-4 max-sm:px-3" role="tree" aria-label={root.label}>
        <Branch node={root} path="0" depth={0} open={open} onToggle={toggle} />
      </div>
    </figure>
  )
}

function Branch({
  node,
  path,
  depth,
  open,
  onToggle,
}: {
  node: MindNode
  path: string
  depth: number
  open: Set<string>
  onToggle: (path: string) => void
}) {
  const t = useT()
  const parent = node.children.length > 0
  const expanded = parent && open.has(path)
  const label = (
    <span
      className={cn(
        'inline-flex max-w-[18rem] items-center gap-1 rounded-control border px-2.5 py-1 text-left text-sm leading-snug break-keep',
        depth === 0 && 'rounded-full border-accent bg-accent px-3.5 py-1.5 text-base font-semibold text-white',
        depth === 1 && 'border-accent/40 bg-accent-soft font-medium text-fg',
        depth > 1 && 'border-line bg-panel text-fg',
      )}
    >
      {parent && depth > 0 && (
        <ChevronRight size={13} className={cn('shrink-0 text-muted transition-transform', expanded && 'rotate-90')} />
      )}
      <span className="min-w-0">{node.label}</span>
      {parent && !expanded && (
        <span className="shrink-0 text-xs text-muted" aria-hidden="true">
          +{node.children.length}
        </span>
      )}
    </span>
  )
  return (
    <div
      role="treeitem"
      aria-expanded={parent ? expanded : undefined}
      aria-level={depth + 1}
      aria-label={node.label}
      className="flex items-start max-sm:block"
      data-mindmap-depth={depth}
    >
      {parent && depth > 0 ? (
        <button
          type="button"
          onClick={() => onToggle(path)}
          title={expanded ? t('접기') : t('펼치기')}
          className="shrink-0 rounded-control focus-visible:outline-2 focus-visible:outline-accent"
        >
          {label}
        </button>
      ) : (
        <span className="shrink-0">{label}</span>
      )}
      {expanded && (
        // Lines from the label's middle: a stub out of this label, a bar down the children's
        // left edge from the first one's middle to the last one's, a stub into each.
        <div
          role="group"
          className={cn(
            'relative ml-4 flex flex-col',
            depth === 0 ? 'before:top-[calc(1.06rem+1px)]' : 'before:top-[calc(0.85rem+1px)]',
            'before:absolute before:-left-4 before:w-4 before:border-t before:border-line-strong',
            'max-sm:mt-1.5 max-sm:ml-3 max-sm:before:hidden',
          )}
        >
          {node.children.map((child, i) => (
            <div
              key={`${path}.${i}`}
              className={cn(
                'relative pb-1.5 pl-4 last:pb-0 max-sm:pl-3',
                'before:absolute before:top-0 before:bottom-0 before:left-0 before:border-l before:border-line-strong',
                'first:before:top-[calc(0.85rem+1px)] max-sm:first:before:top-0',
                'last:before:bottom-auto last:before:h-[calc(0.85rem+1px)] only:before:hidden max-sm:only:before:block',
                'after:absolute after:top-[calc(0.85rem+1px)] after:left-0 after:w-3.5 after:border-t after:border-line-strong max-sm:after:w-2.5',
              )}
            >
              <Branch node={child} path={`${path}.${i}`} depth={depth + 1} open={open} onToggle={onToggle} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
