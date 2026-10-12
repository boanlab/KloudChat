import { Check, Globe, Loader2, Lock, MessageCircle, Plus } from 'lucide-react'
import { createElement, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PickBox } from '@/components/ui/BulkSelect'
import { kindMeta } from '@/lib/kinds'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { STATUS_LABEL, itemMeta, type HubItem } from './hubItems'
import { itemIcon } from './itemIcons'

/** Keyword icon tile, tinted with the agent's color if any. */
export function ItemIconTile({ item, size = 'md' }: { item: HubItem; size?: 'md' | 'lg' }) {
  const t = useT()
  const icon = itemIcon(item.type, `${item.name} ${t(item.name)} ${item.summary}`, item.surfaces)
  return (
    <span
      aria-hidden
      className={cn(
        'grid shrink-0 place-items-center rounded-card border border-line bg-elevated text-muted',
        size === 'lg' ? 'size-14' : 'size-11',
      )}
      style={item.color ? { color: item.color, background: `color-mix(in srgb, ${item.color} 10%, transparent)` } : undefined}
    >
      {createElement(icon, { size: size === 'lg' ? 24 : 19, strokeWidth: 1.75 })}
    </span>
  )
}

const pillTone = {
  on: 'border-success/25 bg-success/10 text-success',
  off: 'border-line bg-elevated text-muted',
  draft: 'border-warn/25 bg-warn/10 text-warn',
  review: 'border-accent/25 bg-accent-soft text-accent',
} as const

export function StatusPill({ item }: { item: HubItem }) {
  const t = useT()
  return (
    <span
      className={cn(
        'inline-flex h-7 shrink-0 items-center rounded-full border px-2.5 text-xs font-medium whitespace-nowrap',
        pillTone[item.status],
      )}
    >
      {t(STATUS_LABEL[item.status])}
    </span>
  )
}

/** Hub card: whole card opens the item; buttons act on it. */
export function ItemCard({
  item,
  view,
  onTry,
  onAdd,
  onOpen,
  picking,
  picked,
  onPick,
  highlight,
}: {
  item: HubItem
  view: 'mine' | 'explore'
  onTry: (item: HubItem) => Promise<void>
  onAdd?: (item: HubItem) => Promise<void>
  /** Overrides the card's link (a shipped flow has no detail screen). */
  onOpen?: (item: HubItem) => void
  picking?: boolean
  picked?: boolean
  onPick?: (item: HubItem) => void
  highlight?: boolean
}) {
  const t = useT()
  const navigate = useNavigate()
  const [busy, setBusy] = useState<'try' | 'add' | null>(null)
  const [lockOpen, setLockOpen] = useState(false)
  const name = t(item.name)
  const open = () => {
    if (onOpen) onOpen(item)
    else if (item.to) navigate(item.to)
  }
  // Surface glyphs, only when not for every surface.
  const writing = item.surfaces.filter((k) => k === 'report' || k === 'slides')
  const glyphs = writing.length === 2 && item.surfaces.includes('chat') ? [] : writing

  return (
    <div
      data-testid="item-card"
      data-item={item.key}
      className={cn(
        'group relative flex min-w-0 items-start gap-3 rounded-card border bg-panel p-4 transition-colors max-sm:p-3',
        highlight ? 'border-accent ring-2 ring-accent/40' : 'border-line hover:border-line-strong',
        (item.to || onOpen) && 'cursor-pointer',
      )}
      onClick={open}
    >
      {picking && item.deletable && onPick && (
        <PickBox
          checked={!!picked}
          onChange={() => onPick(item)}
          label={t('{name} 선택').replace('{name}', name)}
          className="-ml-1"
        />
      )}
      <ItemIconTile item={item} />
      <div className="min-w-0 flex-1">
        {/* Name is the card's link for keyboard and screen readers. */}
        <p className="flex min-w-0 items-center gap-1.5">
          <button
            type="button"
            className="min-w-0 truncate text-left text-md font-medium text-fg focus-visible:outline-2 focus-visible:outline-accent"
            onClick={(e) => {
              e.stopPropagation()
              open()
            }}
          >
            {name}
          </button>
          {glyphs.map((k) => {
            const Glyph = kindMeta[k].icon
            return (
              <span key={k} title={t(kindMeta[k].label)} className="shrink-0 text-faint">
                <Glyph size={12} aria-label={t(kindMeta[k].label)} />
              </span>
            )
          })}
        </p>
        {item.summary && <p className="mt-0.5 line-clamp-2 text-base text-muted">{t(item.summary)}</p>}
        <p className="mt-1 flex flex-wrap items-center gap-x-1.5 text-sm text-faint">
          <span>{itemMeta(item, view, t)}</span>
          {/* Phone: status here, name keeps the width. */}
          {view === 'mine' && (
            <span className={cn('rounded-full border px-1.5 text-xs sm:hidden', pillTone[item.status])}>
              {t(STATUS_LABEL[item.status])}
            </span>
          )}
          {item.hidden && (
            <span className="rounded-full border border-line px-1.5 text-xs text-muted" data-testid="item-hidden">
              {t('숨김')}
            </span>
          )}
          {item.shared && view === 'mine' && (
            <span className="inline-flex items-center gap-0.5 text-success">
              <Globe size={11} aria-hidden />
              {t('공유됨')}
            </span>
          )}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          aria-label={t('{name} 바로 쓰기').replace('{name}', name)}
          title={t('새 대화에서 바로 써 봅니다')}
          disabled={busy !== null}
          onClick={async () => {
            setBusy('try')
            try {
              await onTry(item)
            } finally {
              setBusy(null)
            }
          }}
          className="inline-flex h-8 items-center gap-1.5 rounded-control border border-line bg-panel px-2.5 text-sm font-medium text-fg transition-colors hover:bg-elevated disabled:opacity-50 max-sm:w-8 max-sm:justify-center max-sm:px-0"
        >
          {busy === 'try' ? <Loader2 size={14} className="animate-spin" /> : <MessageCircle size={14} />}
          <span className="max-sm:hidden">{t('바로 쓰기')}</span>
        </button>
        {view === 'explore' && onAdd ? (
          item.added ? (
            <span
              title={t('이미 내 항목에 있습니다')}
              aria-label={t('{name} 추가됨').replace('{name}', name)}
              className="grid size-8 place-items-center rounded-control border border-line text-success"
            >
              <Check size={15} />
            </span>
          ) : item.bot && !item.bot.copyable ? (
            // Locked bot: answers on its page only; no copy, no instructions. The reason opens by
            // keyboard and touch too, not only on hover.
            <span className="relative">
              <button
                type="button"
                title={t('만든 사람이 복사를 허용하지 않아 바로 쓰기만 할 수 있습니다')}
                aria-label={t('{name} 복사 잠김').replace('{name}', name)}
                aria-expanded={lockOpen}
                aria-controls={lockOpen ? `lock-${item.key}` : undefined}
                data-testid="bot-copy-locked"
                onClick={(e) => {
                  e.stopPropagation()
                  setLockOpen((v) => !v)
                }}
                onBlur={() => setLockOpen(false)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape' && lockOpen) {
                    e.stopPropagation()
                    setLockOpen(false)
                  }
                }}
                className="grid size-8 place-items-center rounded-control border border-line text-faint hover:bg-elevated"
              >
                <Lock size={14} />
              </button>
              {lockOpen && (
                <span
                  id={`lock-${item.key}`}
                  role="note"
                  data-testid="bot-copy-locked-reason"
                  className="absolute top-full right-0 z-20 mt-1 w-56 rounded-control border border-line bg-panel px-3 py-2 text-sm text-fg shadow-float"
                >
                  {t('만든 사람이 복사를 허용하지 않아 바로 쓰기만 할 수 있습니다')}
                </span>
              )}
            </span>
          ) : (
            <button
              type="button"
              aria-label={t('{name} 추가').replace('{name}', name)}
              title={t('내 항목에 추가합니다')}
              disabled={busy !== null}
              onClick={async () => {
                setBusy('add')
                try {
                  await onAdd(item)
                } finally {
                  setBusy(null)
                }
              }}
              className="grid size-8 place-items-center rounded-control border border-line bg-panel text-fg transition-colors hover:bg-elevated disabled:opacity-50"
            >
              {busy === 'add' ? <Loader2 size={14} className="animate-spin" /> : <Plus size={15} />}
            </button>
          )
        ) : (
          view === 'mine' && <span className="max-sm:hidden"><StatusPill item={item} /></span>
        )}
      </div>
    </div>
  )
}
