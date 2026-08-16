import { X } from 'lucide-react'
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
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react'
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
  sm: 'h-8 px-2.5 text-[13px] gap-1.5',
  md: 'h-9 px-3.5 text-sm gap-2',
  lg: 'h-11 px-5 text-[15px] gap-2',
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
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-lg font-medium whitespace-nowrap transition-colors',
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

/**
 * A link that looks like a button, for downloads: `<a download>` hands the file
 * over in one attribute, where a button has to fetch, blob and revoke.
 */
export function ButtonLink({
  variant = 'secondary',
  size = 'md',
  className,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: Variant; size?: Size }) {
  return (
    <a
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-lg font-medium whitespace-nowrap transition-colors',
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
  'w-full rounded-lg border border-line bg-panel px-3 py-2 text-sm text-fg placeholder:text-faint ' +
  'transition-colors focus:border-accent focus:outline-none'

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(fieldBase, 'h-9', className)} {...props} />
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(fieldBase, 'resize-y leading-relaxed', className)} {...props} />
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
      <span className="text-[13px] font-medium text-fg">{label}</span>
      {children}
      {hint && <span className="block text-xs text-faint">{hint}</span>}
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
        // The button is the *hit area*, not the track. At 36×20 the track was
        // the whole target, which is under a fingertip on the tablet these
        // lists are read on — so the control grew to 44×36 and the track moved
        // into `::before`, unchanged at 36×20.
        'relative h-9 w-11 shrink-0 border-0 bg-transparent p-0',
        'disabled:pointer-events-none disabled:opacity-45',
        "before:absolute before:top-1/2 before:left-1 before:h-5 before:w-9 before:-translate-y-1/2 before:rounded-full before:transition-colors before:content-['']",
        checked ? 'before:bg-accent' : 'before:bg-line-strong',
      )}
    >
      <span
        className={cn(
          // Track 36, knob 16, inset 2 → 16px of travel. Vertical position is
          // `top`, never a transform: mixing translate axes collapses to
          // `translate: -50%`, which CSS applies on x.
          'absolute top-2.5 left-1.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform',
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
        'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium',
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
      className={cn('rounded-xl border border-line bg-panel', className)}
      {...props}
    >
      {children}
    </div>
  )
}

/* ── Modal ──────────────────────────────────────────────────────────── */

/** What the keyboard can land on. Shared by the focus trap and the restore. */
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
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children?: ReactNode
  footer?: ReactNode
  width?: string
}) {
  const t = useT()
  const panelRef = useRef<HTMLDivElement>(null)
  /** Whatever had focus when this opened, so it can be given back. */
  const returnTo = useRef<HTMLElement | null>(null)
  const closeRef = useRef(onClose)
  useLayoutEffect(() => {
    closeRef.current = onClose
  }, [onClose])

  /**
   * Focus trap and restore. `aria-modal` claims the rest of the page is inert;
   * this is what makes it true for the keyboard.
   */
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
      // Wrapping at the edges is what makes it a loop rather than an exit.
      if (document.activeElement === edge || !panel.contains(document.activeElement)) {
        e.preventDefault()
        ;(e.shiftKey ? items[items.length - 1] : items[0]).focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      // Back to the control that opened it — not to the top of the document,
      // which is where the browser drops you when the focused node vanishes.
      returnTo.current?.focus?.()
    }
  }, [open])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 py-[8vh]">
      <div className="fixed inset-0 bg-black/45 backdrop-blur-[2px]" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        // Focusable as a fallback: a dialog whose body is only text still has
        // to be somewhere the keyboard can land.
        tabIndex={-1}
        className={cn(
          'animate-fade-up relative w-full rounded-2xl border border-line bg-panel shadow-2xl outline-none',
          width,
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div>
            <h2 className="text-[15px] font-semibold">{title}</h2>
            {description && <p className="mt-0.5 text-[13px] text-muted">{description}</p>}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label={t('닫기')}>
            <X size={16} />
          </Button>
        </header>
        {children && <div className="space-y-4 px-5 py-4">{children}</div>}
        {footer && (
          <footer className="flex justify-end gap-2 border-t border-line px-5 py-3.5">
            {footer}
          </footer>
        )}
      </div>
    </div>
  )
}

/**
 * Confirmation before a destructive action. Nothing on the server restores what
 * a card's delete removes, and that button sits beside a toggle.
 *
 * The name goes in the question: "Delete this?" is answered yes by everyone.
 */
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

/**
 * Closes the enclosing Dropdown from a custom menu body. `MenuItem` closes
 * itself; a menu rendering its own rows — the model picker — cannot.
 */
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
  /**
   * Which way the panel opens, and how tall it may be. The composer's menus sit
   * a few pixels above the bottom of the window, so downward-only with no
   * height cap puts them off-screen.
   */
  const [placement, setPlacement] = useState<{ up: boolean; maxHeight: number }>({
    up: false,
    maxHeight: 0,
  })
  const ref = useRef<HTMLDivElement>(null)

  const measure = useCallback(() => {
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return
    const margin = 12
    const below = window.innerHeight - rect.bottom - margin
    const above = rect.top - margin
    // Downward by preference; flipped only when the other side has more room.
    const up = below < 220 && above > below
    setPlacement({ up, maxHeight: Math.max(160, up ? above : below) })
  }, [])

  useLayoutEffect(() => {
    if (open) measure()
  }, [open, measure])

  /**
   * Arrow-key navigation, as `role="menu"` promises. Down/Up walk the items,
   * Home/End jump to the ends, Escape restores focus to the trigger.
   */
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const items = () =>
      [...(ref.current?.querySelectorAll<HTMLElement>('[role="menu"] button') ?? [])].filter(
        (el) => el.offsetWidth > 0 || el.offsetHeight > 0,
      )
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        // Back to the trigger, which is the only landmark the reader has.
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
    // A resize while open changes which side fits.
    window.addEventListener('resize', measure)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', measure)
    }
  }, [open, measure])

  return (
    /* `shrink-0`: this wrapper is a flex item in every toolbar it sits in, and
       without it the wrapper shrinks while the button inside keeps its own
       `shrink-0` — so the label wraps one character per line and spills out of
       the button, which is what a narrow artifact panel showed. */
    <div ref={ref} className="relative shrink-0">
      {/* The trigger is whatever the caller rendered — usually a button — so the
          menu semantics are declared on the wrapper and the panel instead. */}
      <div
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {trigger({ open })}
      </div>
      {open && (
        <MenuCtx.Provider value={{ close: () => setOpen(false) }}>
          <div
            role="menu"
            style={{ maxHeight: placement.maxHeight || undefined }}
            className={cn(
              'animate-fade-up absolute z-40 min-w-52 overflow-y-auto rounded-xl border border-line bg-panel p-1 shadow-xl',
              placement.up ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
              align === 'right' ? 'right-0' : 'left-0',
              className,
            )}
          >
            {children}
          </div>
        </MenuCtx.Provider>
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
}: {
  children: ReactNode
  onClick?: () => void
  danger?: boolean
  icon?: ReactNode
  hint?: ReactNode
  disabled?: boolean
  /** Turns an ordinary menu command into an accessible toggle item. */
  checked?: boolean
}) {
  const { close } = useContext(MenuCtx)
  return (
    <button
      role={checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => {
        onClick?.()
        close()
      }}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors',
        disabled
          ? 'cursor-not-allowed text-faint opacity-60'
          : danger
            ? 'text-danger hover:bg-danger/10'
            : 'text-fg hover:bg-elevated',
      )}
    >
      {icon && <span className="shrink-0 text-muted">{icon}</span>}
      <span className="flex-1 truncate">{children}</span>
      {hint && <span className="shrink-0 text-xs text-faint">{hint}</span>}
    </button>
  )
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return (
    <div className="px-2.5 pt-2 pb-1 text-[11px] font-semibold tracking-wide text-faint uppercase">
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
}: {
  icon: ReactNode
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <div className="grid size-11 place-items-center rounded-xl border border-line bg-elevated text-muted">
        {icon}
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        {description && <p className="max-w-sm text-[13px] text-muted">{description}</p>}
      </div>
      {action}
    </div>
  )
}

/**
 * "Not here yet", as distinct from "not here". `length === 0` is equally true
 * of a request that has not answered.
 */
export function LoadingState({ label }: { label?: string }) {
  const t = useT()
  return (
    <div
      role="status"
      className="flex items-center justify-center gap-2 px-6 py-16 text-[13px] text-faint"
    >
      <span className="size-3 animate-spin rounded-full border-2 border-line-strong border-t-transparent" />
      {label ?? t('불러오는 중…')}
    </div>
  )
}

/**
 * Stale-data notice with a retry. A failed refresh keeps the list — an empty
 * screen would be the worse lie — so the staleness has to be said.
 */
export function ReloadNotice({ onRetry }: { onRetry: () => void }) {
  const t = useT()
  return (
    <div
      role="status"
      className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-warn/30 bg-warn/5 px-3 py-2 text-[13px] text-warn"
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
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
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
            '-mb-px border-b-2 px-3 py-2 text-[13px] font-medium transition-colors',
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
