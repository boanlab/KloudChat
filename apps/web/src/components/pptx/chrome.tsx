import {
  ArrowDown, ArrowUp, ChevronDown, ChevronUp, Copy, MoreVertical, Plus, Trash2, type LucideIcon,
} from 'lucide-react'
import {
  useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode,
} from 'react'
import { useT } from '@/lib/useT'
import './slides.css'

/**
 * The slides family's chrome, shared by the PowerPoint editor (`SlidesEditor`) and the web
 * slides editor (`DeckPanel`): toolbar buttons and drop-down menus, the thumbnail rail with its
 * slide menu and drag to reorder, and the speaker-notes pane. Light on purpose: no editor
 * library comes with it, so the web slides do not load the PowerPoint editor's chunk.
 */

/** Keeps toolbar clicks from taking the text editor's selection. */
export const keep = (event: ReactMouseEvent) => event.preventDefault()

export function Btn({
  label, icon: Icon, onClick, disabled, pressed, children, testid,
}: {
  label: string
  icon?: LucideIcon
  onClick?: () => void
  disabled?: boolean
  pressed?: boolean
  children?: ReactNode
  testid?: string
}) {
  return (
    <button
      type="button"
      className={'kc-btn' + (pressed ? ' on' : '')}
      title={label}
      aria-label={label}
      aria-pressed={pressed === undefined ? undefined : !!pressed}
      disabled={disabled}
      data-testid={testid}
      onMouseDown={keep}
      onClick={onClick}
    >
      {Icon ? <Icon size={16} strokeWidth={2} /> : null}
      {children}
    </button>
  )
}

export function Menu({
  label, button, disabled, children, width, testid, align,
}: {
  label: string
  button: ReactNode
  disabled?: boolean
  children: (close: () => void) => ReactNode
  width?: number
  testid?: string
  /** Opens to the left of the button (one at the toolbar's right end). */
  align?: 'right'
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return undefined
    const away = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', away, true)
    window.addEventListener('keydown', escape, true)
    return () => {
      window.removeEventListener('mousedown', away, true)
      window.removeEventListener('keydown', escape, true)
    }
  }, [open])
  const style = { ...(width ? { width } : {}), ...(align === 'right' ? { left: 'auto', right: 0 } : {}) }
  return (
    <div className="kc-menu" ref={ref} data-testid={testid}>
      <button
        type="button"
        className={'kc-btn kc-drop' + (open ? ' on' : '')}
        title={label}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onMouseDown={keep}
        onClick={() => setOpen((was) => !was)}
      >
        {button}
        <ChevronDown size={12} />
      </button>
      {open && !disabled ? (
        <div className="kc-pop" role="menu" aria-label={label} style={style} onMouseDown={keep}>
          {children(() => setOpen(false))}
        </div>
      ) : null}
    </div>
  )
}

export const Sep = () => <span className="kc-sep" aria-hidden="true" />

// ── thumbnail rail ──

export interface RailProps {
  count: number
  active: number
  /** A stable key per slide. */
  keyOf: (index: number) => string
  /** The slide's picture at `width` px. */
  thumb: (index: number, width: number) => ReactNode
  /** A hidden slide shows faded. */
  hidden?: (index: number) => boolean
  /** Marks over a thumbnail (a warning, open review notes). */
  badge?: (index: number) => ReactNode
  /** Something in front of a thumbnail (a check box to pick several). */
  lead?: (index: number) => ReactNode
  canEdit: boolean
  compact: boolean
  /** A phone: the thumbnails in a row under the slide. */
  row: boolean
  onSelect: (index: number) => void
  onMove: (from: number, to: number) => void
  onDuplicate: (index: number) => void
  onDelete: (index: number) => void
  onAddAfter: (index: number) => void
}

/**
 * Numbered thumbnails; a slide's menu (⋮ or right-click): 새 슬라이드, 슬라이드 복제, 위로 이동,
 * 아래로 이동, 슬라이드 삭제; drag to reorder; Delete removes the slide, the arrows step; and
 * 「+ 새 슬라이드」 under the list.
 */
export function SlideRail({
  count, active, keyOf, thumb, hidden, badge, lead, canEdit, compact, row, onSelect, onMove,
  onDuplicate, onDelete, onAddAfter,
}: RailProps) {
  const t = useT()
  const [drag, setDrag] = useState<{ from: number; over: number } | null>(null)
  const [menu, setMenu] = useState<{ i: number; x: number; y: number } | null>(null)
  const width = row ? 112 : compact ? 92 : 148
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    listRef.current?.querySelector(`.kc-thumb[data-i="${active}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [active])
  useEffect(() => {
    if (!menu) return undefined
    const away = (event: MouseEvent) => {
      if (!(event.target as HTMLElement).closest?.('.kc-ctx')) setMenu(null)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenu(null)
    }
    window.addEventListener('mousedown', away, true)
    window.addEventListener('keydown', escape, true)
    return () => {
      window.removeEventListener('mousedown', away, true)
      window.removeEventListener('keydown', escape, true)
    }
  }, [menu])
  const close = (run: () => void) => () => {
    run()
    setMenu(null)
  }
  return (
    <nav className="kc-rail" aria-label={t('슬라이드')}
      style={row ? undefined : { width: width + (compact ? 30 : 36) }}
      onKeyDown={(event) => {
        if ((event.target as HTMLElement).closest?.('input,textarea,select,.kc-ctx')) return
        if (!canEdit && !['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return
        if (event.key === 'Delete' || event.key === 'Backspace') {
          event.preventDefault()
          if (count > 1) onDelete(active)
        } else if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
          event.preventDefault()
          onSelect(Math.min(count - 1, active + 1))
        } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
          event.preventDefault()
          onSelect(Math.max(0, active - 1))
        }
      }}>
      <div className="kc-rail-list" tabIndex={0} ref={listRef}>
        {Array.from({ length: count }, (_, i) => (
          <div key={keyOf(i)} data-i={i}
            className={'kc-thumb' + (i === active ? ' on' : '') + (drag && drag.over === i ? ' over' : '') +
              (hidden?.(i) ? ' hid' : '')}
            draggable={canEdit}
            role="button"
            aria-label={t('{n}번 장').replace('{n}', String(i + 1))}
            aria-current={i === active ? 'true' : undefined}
            onDragStart={() => setDrag({ from: i, over: i })}
            onDragOver={(event) => { event.preventDefault(); if (drag) setDrag({ ...drag, over: i }) }}
            onDrop={() => { if (drag && drag.from !== i) onMove(drag.from, i); setDrag(null) }}
            onDragEnd={() => setDrag(null)}
            onClick={() => onSelect(i)}
            onContextMenu={(event) => {
              event.preventDefault()
              onSelect(i)
              if (canEdit) setMenu({ i, x: event.clientX, y: event.clientY })
            }}>
            <span className="kc-num">{i + 1}</span>
            {lead?.(i)}
            <div className="kc-tframe" style={{ width }}>
              {thumb(i, width)}
              {badge?.(i)}
            </div>
            {canEdit && (
              <button type="button" className="kc-tmore" aria-label={t('슬라이드 메뉴')}
                onClick={(event) => {
                  event.stopPropagation()
                  onSelect(i)
                  const box = event.currentTarget.getBoundingClientRect()
                  setMenu({ i, x: box.left, y: box.bottom })
                }}>
                <MoreVertical size={14} />
              </button>
            )}
          </div>
        ))}
      </div>
      {canEdit && (
        <button type="button" className="kc-add" title={t('새 슬라이드')} aria-label={t('새 슬라이드')}
          onClick={() => onAddAfter(active)}>
          <Plus size={16} />
          {compact || row ? null : <span>{t('새 슬라이드')}</span>}
        </button>
      )}
      {menu && (
        <div className="kc-ctx" role="menu" aria-label={t('슬라이드 메뉴')} style={{ left: menu.x, top: menu.y }}>
          <button type="button" role="menuitem" className="kc-item" onClick={close(() => onAddAfter(menu.i))}>
            <Plus size={14} />{t('새 슬라이드')}
          </button>
          <button type="button" role="menuitem" className="kc-item"
            onClick={close(() => onDuplicate(menu.i))}>
            <Copy size={14} />{t('슬라이드 복제')}
          </button>
          <button type="button" role="menuitem" className="kc-item" disabled={menu.i === 0}
            onClick={close(() => onMove(menu.i, menu.i - 1))}>
            <ArrowUp size={14} />{t('위로 이동')}
          </button>
          <button type="button" role="menuitem" className="kc-item" disabled={menu.i === count - 1}
            onClick={close(() => onMove(menu.i, menu.i + 1))}>
            <ArrowDown size={14} />{t('아래로 이동')}
          </button>
          <button type="button" role="menuitem" className="kc-item danger" disabled={count < 2}
            onClick={close(() => onDelete(menu.i))}>
            <Trash2 size={14} />{t('슬라이드 삭제')}
          </button>
        </div>
      )}
    </nav>
  )
}

// ── notes ──

/**
 * The speaker notes under the slide, folded open by default. Typing commits after a pause and
 * on leaving the box; a new slide (`slideKey`) shows its own notes.
 */
export function NotesPane({
  slideKey, value, canEdit, onCommit, children,
}: {
  slideKey: string | undefined
  value: string
  canEdit: boolean
  onCommit?: (text: string) => void
  /** Under the notes: what goes with them (a fact check's result). */
  children?: ReactNode
}) {
  const t = useT()
  const [open, setOpen] = useState(true)
  const [draft, setDraft] = useState(value)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const latest = useRef({ value, onCommit })
  useEffect(() => {
    latest.current = { value, onCommit }
  })
  useEffect(() => {
    setDraft(value)
    // a new slide's notes, or notes changed from outside (an undo, a new version)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slideKey, value])
  // a pending commit is not lost when the slide changes
  useEffect(() => () => clearTimeout(timer.current), [slideKey])
  const commit = (text: string) => {
    clearTimeout(timer.current)
    const now = latest.current
    if (!now.onCommit || text === now.value) return
    now.onCommit(text)
  }
  return (
    <section className="kc-notes" aria-label={t('발표자 노트')}>
      <button type="button" className="kc-notes-bar" aria-expanded={open} onClick={() => setOpen((was) => !was)}>
        {open ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        <span>{t('발표자 노트')}</span>
      </button>
      {open && (
        <textarea className="kc-notes-text" value={draft} aria-label={t('발표자 노트')}
          placeholder={canEdit ? t('클릭하여 발표자 노트 추가') : ''}
          readOnly={!canEdit || !onCommit}
          onChange={(event) => {
            const text = event.target.value
            setDraft(text)
            clearTimeout(timer.current)
            timer.current = setTimeout(() => commit(text), 600)
          }}
          onBlur={(event) => commit(event.target.value)} />
      )}
      {open && children}
    </section>
  )
}

export type { LucideIcon }
