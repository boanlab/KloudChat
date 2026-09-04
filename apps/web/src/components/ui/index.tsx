import { Check, ChevronDown, X } from 'lucide-react'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type AnchorHTMLAttributes,
  type ButtonHTMLAttributes,
  type ComponentProps,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'

/* ── Button ─────────────────────────────────────────────────────────── */

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline'
type Size = 'sm' | 'md' | 'lg' | 'icon'

const variants: Record<Variant, string> = {
  primary: 'bg-accent text-accent-fg hover:bg-accent-hover',
  secondary: 'bg-elevated text-fg hover:bg-line border border-line',
  ghost: 'text-muted hover:text-fg hover:bg-elevated',
  danger: 'text-danger hover:bg-danger/10 border border-danger/30',
  outline: 'border border-line-strong text-fg hover:bg-elevated',
}

const sizes: Record<Size, string> = {
  sm: 'h-8 px-2.5 text-sm gap-1.5',
  md: 'h-9 px-3.5 text-base gap-2',
  lg: 'h-11 px-5 text-md gap-2',
  icon: 'h-8 w-8',
}

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  return (
    <button
      // Read by the ribbon's CSS, which restyles every variant but primary.
      data-variant={variant}
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-control font-medium whitespace-nowrap transition-colors',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        'disabled:pointer-events-none disabled:opacity-45',
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  )
}

/** Button-styled anchor, for `<a download>`. */
export function ButtonLink({
  variant = 'secondary',
  size = 'md',
  className,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: Variant; size?: Size }) {
  return (
    <a
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-control font-medium whitespace-nowrap transition-colors',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  )
}

/* ── Inputs ─────────────────────────────────────────────────────────── */

const fieldBase =
  'w-full rounded-control border border-line bg-panel px-3 py-2 text-base text-fg placeholder:text-faint ' +
  'transition-colors focus:border-accent focus:outline-none'

// `ComponentProps` carries `ref`.
export function Input({ className, ...props }: ComponentProps<'input'>) {
  return <input className={cn(fieldBase, 'h-9', className)} {...props} />
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(fieldBase, 'resize-y leading-relaxed', className)} {...props} />
}

/** Native `<select>` styled like `Input`; only the chrome is replaced. */
export function Select({ className, children, ...props }: ComponentProps<'select'>) {
  return (
    <div className="relative">
      <select
        className={cn(
          fieldBase,
          'h-9 cursor-pointer appearance-none pr-9',
          'disabled:cursor-not-allowed disabled:opacity-60',
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        size={15}
        aria-hidden="true"
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-faint"
      />
    </div>
  )
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-base font-medium text-fg">{label}</span>
      {children}
      {hint && <span className="block text-sm text-faint">{hint}</span>}
    </label>
  )
}

export function Switch({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label?: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        // The button is the 44x36 hit area; the 36x20 track is drawn in `::before`.
        'relative h-9 w-11 shrink-0 border-0 bg-transparent p-0',
        'disabled:pointer-events-none disabled:opacity-45',
        "before:absolute before:top-1/2 before:left-1 before:h-5 before:w-9 before:-translate-y-1/2 before:rounded-full before:transition-colors before:content-['']",
        checked ? 'before:bg-accent' : 'before:bg-line-strong',
      )}
    >
      <span
        className={cn(
          // Track 36, knob 16, inset 2: 16px of travel. Vertical position via `top`, not transform.
          'absolute top-2.5 left-1.5 h-4 w-4 rounded-full bg-white shadow-raised transition-transform',
          checked ? 'translate-x-4' : 'translate-x-0',
        )}
      />
    </button>
  )
}

/* ── Badge ──────────────────────────────────────────────────────────── */

export function Badge({
  children,
  tone = 'neutral',
  className,
  title,
}: {
  children: ReactNode
  tone?: 'neutral' | 'accent' | 'success' | 'warn' | 'danger'
  className?: string
  title?: string
}) {
  const tones = {
    neutral: 'bg-elevated text-muted border-line',
    accent: 'bg-accent-soft text-accent border-accent/25',
    success: 'bg-success/10 text-success border-success/25',
    warn: 'bg-warn/10 text-warn border-warn/25',
    danger: 'bg-danger/10 text-danger border-danger/25',
  }
  return (
    <span
      title={title}
      className={cn(
        'inline-flex shrink-0 items-center gap-1 rounded-control border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

/* ── Card ───────────────────────────────────────────────────────────── */

export function Card({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { children: ReactNode }) {
  return (
    <div
      className={cn('rounded-card border border-line bg-panel', className)}
      {...props}
    >
      {children}
    </div>
  )
}

/* ── Modal ──────────────────────────────────────────────────────────── */

/** Focusable elements, for the focus trap. */
const FOCUSABLE =
  'button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), ' +
  'select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = 'max-w-lg',
  bare = false,
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children?: ReactNode
  footer?: ReactNode
  width?: string
  /** The child draws its own title bar and close control. */
  bare?: boolean
}) {
  const t = useT()
  const panelRef = useRef<HTMLDivElement>(null)
  /** Element to return focus to on close. */
  const returnTo = useRef<HTMLElement | null>(null)
  const closeRef = useRef(onClose)
  useLayoutEffect(() => {
    closeRef.current = onClose
  }, [onClose])

  // Focus trap and restore.
  useEffect(() => {
    if (!open) return
    returnTo.current = document.activeElement as HTMLElement | null
    const panel = panelRef.current
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE)
    ;(first ?? panel)?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        closeRef.current()
        return
      }
      if (e.key !== 'Tab' || !panel) return
      const items = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (el) => el.offsetWidth > 0 || el.offsetHeight > 0,
      )
      if (items.length === 0) return
      const edge = e.shiftKey ? items[0] : items[items.length - 1]
      if (document.activeElement === edge || !panel.contains(document.activeElement)) {
        e.preventDefault()
        ;(e.shiftKey ? items[items.length - 1] : items[0]).focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      returnTo.current?.focus?.()
    }
  }, [open])

  if (!open) return null
  return (
    /* `my-auto` on the panel, not `items-center`: a centred flex item taller
       than the scroll container gets its top clipped. */
    <div className="fixed inset-0 z-50 flex justify-center overflow-y-auto p-4 max-sm:p-2">
      <div className="fixed inset-0 bg-black/45 backdrop-blur-[2px]" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        // Focus fallback for a dialog with no focusable child.
        tabIndex={-1}
        className={cn(
          'animate-fade-up relative my-auto w-full rounded-panel border border-line bg-panel shadow-float outline-none',
          width,
        )}
      >
        {!bare && <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4 max-sm:px-3 max-sm:py-3">
          <div className="min-w-0 flex-1">
            <h2 className="truncate whitespace-nowrap text-md font-semibold" title={title}>{title}</h2>
            {description && <p className="mt-0.5 truncate whitespace-nowrap text-base text-muted" title={description}>{description}</p>}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label={t('닫기')}>
            <X size={16} />
          </Button>
        </header>}
        {children && <div className={bare ? 'min-w-0' : 'min-w-0 space-y-4 px-5 py-4 max-sm:px-3'}>{children}</div>}
        {footer && (
          <footer className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3.5 max-sm:px-3 max-sm:py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>
  )
}

/** Confirmation before a destructive action. */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel,
}: {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  title: string
  description?: string
  confirmLabel?: string
}) {
  const t = useT()
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      width="max-w-md"
      footer={
        <>
          <Button onClick={onClose}>{t('취소')}</Button>
          <Button
            variant="danger"
            onClick={() => {
              onConfirm()
              onClose()
            }}
          >
            {confirmLabel ?? t('삭제')}
          </Button>
        </>
      }
    />
  )
}

/* ── Dropdown ───────────────────────────────────────────────────────── */

const MenuCtx = createContext<{ close: () => void }>({ close: () => {} })

/** Closes the enclosing Dropdown from a custom menu body. */
export function useMenuClose() {
  return useContext(MenuCtx).close
}

export function Dropdown({
  trigger,
  children,
  align = 'left',
  className,
}: {
  trigger: (props: { open: boolean }) => ReactNode
  children: ReactNode
  align?: 'left' | 'right'
  className?: string
}) {
  const [open, setOpen] = useState(false)
  // Opening direction and height cap, measured against the viewport.
  const [placement, setPlacement] = useState<{ up: boolean; maxHeight: number; left: number; top: number; bottom: number }>({
    up: false,
    maxHeight: 0,
    left: 0,
    top: 0,
    bottom: 0,
  })
  const ref = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const measure = useCallback(() => {
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return
    const margin = 12
    const below = window.innerHeight - rect.bottom - margin
    const above = rect.top - margin
    const up = below < 220 && above > below
    const menuWidth = menuRef.current?.getBoundingClientRect().width ?? 208
    const left = align === 'right'
      ? Math.max(8, Math.min(window.innerWidth - menuWidth - 8, rect.right - menuWidth))
      : Math.max(8, Math.min(window.innerWidth - menuWidth - 8, rect.left))
    setPlacement({
      up,
      maxHeight: Math.max(160, up ? above : below),
      left,
      top: rect.bottom + 6,
      bottom: window.innerHeight - rect.top + 6,
    })
  }, [align])

  useLayoutEffect(() => {
    if (open) measure()
  }, [open, measure])

  useLayoutEffect(() => {
    const button = ref.current?.querySelector<HTMLButtonElement>('button')
    if (!button) return
    button.setAttribute('aria-haspopup', 'menu')
    button.setAttribute('aria-expanded', String(open))
    if (!open) return
    const frame = requestAnimationFrame(() => {
      menuRef.current
        ?.querySelector<HTMLButtonElement>('button:not(:disabled)')
        ?.focus()
    })
    return () => cancelAnimationFrame(frame)
  }, [open])

  // Outside-click close and arrow-key navigation.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node) && !menuRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const items = () =>
      [...(menuRef.current?.querySelectorAll<HTMLElement>('button') ?? [])].filter(
        (el) => !el.hasAttribute('disabled') && (el.offsetWidth > 0 || el.offsetHeight > 0),
      )
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopImmediatePropagation()
        setOpen(false)
        ref.current?.querySelector<HTMLElement>('button')?.focus()
        return
      }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
      const list = items()
      if (list.length === 0) return
      e.preventDefault()
      const at = list.indexOf(document.activeElement as HTMLElement)
      const next =
        e.key === 'Home'
          ? 0
          : e.key === 'End'
            ? list.length - 1
            : e.key === 'ArrowDown'
              ? at < 0
                ? 0
                : (at + 1) % list.length
              : at <= 0
                ? list.length - 1
                : at - 1
      list[next].focus()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', measure)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', measure)
    }
  }, [open, measure])

  return (
    <div ref={ref} className={cn('relative shrink-0', open && 'z-50')}>
      <div
        onClick={() => setOpen((o) => !o)}
      >
        {trigger({ open })}
      </div>
      {open && typeof document !== 'undefined' && createPortal(
        <MenuCtx.Provider value={{ close: () => setOpen(false) }}>
          <div
            ref={menuRef}
            role="menu"
            style={{
              position: 'fixed',
              maxHeight: placement.maxHeight || undefined,
              left: placement.left,
              ...(placement.up ? { bottom: placement.bottom } : { top: placement.top }),
            }}
            className={cn(
              'animate-fade-up z-[100] min-w-52 max-w-[calc(100vw-1rem)] overflow-y-auto rounded-card border border-line bg-panel p-1 shadow-overlay',
              className,
            )}
          >
            {children}
          </div>
        </MenuCtx.Provider>,
        document.body,
      )}
    </div>
  )
}

export function MenuItem({
  children,
  onClick,
  danger,
  icon,
  hint,
  disabled,
  checked,
  keepOpen,
  title,
}: {
  children: ReactNode
  onClick?: () => void
  danger?: boolean
  icon?: ReactNode
  hint?: ReactNode
  disabled?: boolean
  /** Renders as a checkbox item with a selection mark. */
  checked?: boolean
  /** Leaves the menu open after the click (multi-select). */
  keepOpen?: boolean
  title?: string
}) {
  const { close } = useContext(MenuCtx)
  return (
    <button
      role={checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
      aria-checked={checked}
      disabled={disabled}
      title={title}
      onClick={() => {
        onClick?.()
        if (!keepOpen) close()
      }}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-control px-2.5 py-1.5 text-left text-base transition-colors',
        disabled
          ? 'cursor-not-allowed text-faint opacity-60'
          : danger
            ? 'text-danger hover:bg-danger/10'
            : 'text-fg hover:bg-elevated',
      )}
    >
      {checked !== undefined && (
        <span className="grid size-4 shrink-0 place-items-center text-accent" aria-hidden>
          {checked && <Check size={14} />}
        </span>
      )}
      {icon && <span className="shrink-0 text-muted">{icon}</span>}
      <span className="flex-1 truncate">{children}</span>
      {hint && <span className="shrink-0 text-sm text-faint">{hint}</span>}
    </button>
  )
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return (
    <div className="px-2.5 pt-2 pb-1 text-xs font-semibold tracking-wide text-faint uppercase">
      {children}
    </div>
  )
}

export function MenuSeparator() {
  return <div className="my-1 h-px bg-line" />
}

/* ── Misc ───────────────────────────────────────────────────────────── */

export function EmptyState({
  icon,
  title,
  description,
  action,
  headingLevel,
}: {
  icon: ReactNode
  title: string
  description?: string
  action?: ReactNode
  /** h1 only when the empty state is the whole screen. */
  headingLevel?: 'h1' | 'h2'
}) {
  const Heading = headingLevel ?? 'p'
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <div className="grid size-11 place-items-center rounded-card border border-line bg-elevated text-muted">
        {icon}
      </div>
      <div className="space-y-1">
        <Heading className="text-base font-medium">{title}</Heading>
        {description && <p className="max-w-sm text-base text-muted">{description}</p>}
      </div>
      {action}
    </div>
  )
}

export function LoadingState({ label }: { label?: string }) {
  const t = useT()
  return (
    <div
      role="status"
      className="flex items-center justify-center gap-2 px-6 py-16 text-base text-faint"
    >
      <span className="size-3 animate-spin rounded-full border-2 border-line-strong border-t-transparent" />
      {label ?? t('불러오는 중…')}
    </div>
  )
}

/** Stale-list notice with a retry. */
export function ReloadNotice({ onRetry }: { onRetry: () => void }) {
  const t = useT()
  return (
    <div
      role="status"
      className="mb-3 flex flex-wrap items-center gap-2 rounded-control border border-warn/30 bg-warn/5 px-3 py-2 text-base text-warn"
    >
      <span className="min-w-0 flex-1">
        {t('목록을 새로 불러오지 못했습니다. 화면에 보이는 것은 마지막으로 받은 내용입니다.')}
      </span>
      <Button size="sm" onClick={onRetry}>
        {t('다시 시도')}
      </Button>
    </div>
  )
}

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 pb-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-base text-muted">{description}</p>}
      </div>
      {action}
    </div>
  )
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: string; count?: number }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div role="tablist" className="flex gap-1 border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            '-mb-px border-b-2 px-3 py-2 text-base font-medium transition-colors',
            value === t.id
              ? 'border-accent text-fg'
              : 'border-transparent text-muted hover:text-fg',
          )}
        >
          {t.label}
          {t.count !== undefined && <span className="ml-1.5 text-faint">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}
