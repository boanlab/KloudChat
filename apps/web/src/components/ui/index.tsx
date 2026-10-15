import { Check, ChevronDown, X } from 'lucide-react'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useId,
  useState,
  type AnchorHTMLAttributes,
  type ButtonHTMLAttributes,
  type ComponentProps,
  type ReactNode,
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
      // Ribbon CSS restyles every non-primary variant.
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
  // 1px border change too faint (WCAG 2.4.11): 2px halo too.
  'transition-colors focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30 ' +
  // Disabled must not look editable.
  'disabled:cursor-not-allowed disabled:bg-elevated disabled:opacity-60 ' +
  'aria-invalid:border-danger'

/** Field → control context (hint, error, required, invalid); spares hand-wired `aria-describedby`. */
interface FieldState {
  describedBy?: string
  invalid: boolean
  required: boolean
}
const FieldCtx = createContext<FieldState | null>(null)

/** The ARIA a control inside a `Field` carries; its own props win. */
function useFieldAria(props: {
  'aria-describedby'?: string
  'aria-invalid'?: boolean | 'true' | 'false' | 'grammar' | 'spelling'
  'aria-required'?: boolean | 'true' | 'false'
}) {
  const field = useContext(FieldCtx)
  if (!field) return {}
  return {
    'aria-describedby': props['aria-describedby'] ?? field.describedBy,
    'aria-invalid': props['aria-invalid'] ?? (field.invalid || undefined),
    'aria-required': props['aria-required'] ?? (field.required || undefined),
  }
}

// `ComponentProps` carries `ref`.
export function Input({ className, ...props }: ComponentProps<'input'>) {
  const aria = useFieldAria(props)
  return <input className={cn(fieldBase, 'h-9', className)} {...props} {...aria} />
}

export function Textarea({ className, ...props }: ComponentProps<'textarea'>) {
  const aria = useFieldAria(props)
  return <textarea className={cn(fieldBase, 'resize-y leading-relaxed', className)} {...props} {...aria} />
}

/** Native `<select>` in `Input` chrome. */
export function Select({ className, children, ...props }: ComponentProps<'select'>) {
  const aria = useFieldAria(props)
  return (
    <div className="relative">
      <select
        className={cn(fieldBase, 'h-9 cursor-pointer appearance-none pr-9', className)}
        {...props}
        {...aria}
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

/** Labelled control; hint and error via `aria-describedby`, heard after the name. */
export function Field({
  label,
  hint,
  error,
  children,
  required = false,
  className,
}: {
  label: string
  hint?: ReactNode
  /** Refusal reason: shown, announced, marks invalid. */
  error?: string | null
  children: ReactNode
  /** Required: red *, kept out of the accessible name. */
  required?: boolean
  className?: string
}) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [errorId, hintId].filter(Boolean).join(' ') || undefined
  return (
    <FieldCtx.Provider value={{ describedBy, invalid: Boolean(error), required }}>
      <div className={cn('space-y-1.5', className)}>
        <label className="block space-y-1.5">
          <span className="block text-base font-medium text-fg">
            {label}
            {required && (
              <span aria-hidden className="ml-0.5 text-danger">
                *
              </span>
            )}
          </span>
          {children}
        </label>
        {error && (
          <p id={errorId} role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        {hint && (
          <div id={hintId} className="text-sm text-faint">
            {hint}
          </div>
        )}
      </div>
    </FieldCtx.Provider>
  )
}

/** The note a form with required fields carries once, above its fields. */
export function RequiredNote() {
  const t = useT()
  return (
    <p className="text-sm text-muted">
      <span aria-hidden className="text-danger">*</span> {t('표시는 꼭 채워야 하는 칸입니다.')}
    </p>
  )
}

export function Switch({
  checked,
  onChange,
  label,
  disabled = false,
  describedBy,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label?: string
  disabled?: boolean
  /** Id of the text that says what on and off mean. */
  describedBy?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        // 44x36 hit area; 36x20 track in `::before`.
        'relative h-9 w-11 shrink-0 border-0 bg-transparent p-0',
        'disabled:pointer-events-none disabled:opacity-45',
        "before:absolute before:top-1/2 before:left-1 before:h-5 before:w-9 before:-translate-y-1/2 before:rounded-full before:transition-colors before:content-['']",
        checked ? 'before:bg-accent' : 'before:bg-line-strong',
      )}
    >
      <span
        className={cn(
          // Track 36, knob 16, inset 2 → 16px travel; `top`, not transform.
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

  useEffect(() => {
    if (!open) return
    returnTo.current = document.activeElement as HTMLElement | null
    const panel = panelRef.current
    // Initial focus: `autofocus` control, else first body/footer control, else close button.
    const marked = panel?.querySelector<HTMLElement>('[data-autofocus], [autofocus]')
    const inBody = panel?.querySelector<HTMLElement>(
      ':scope > [data-modal-body] :is(input, textarea, select):not([disabled]):not([type="hidden"])',
    )
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE)
    // A child's own `autoFocus` already ran during commit; keep it.
    if (!panel?.contains(document.activeElement)) (marked ?? inBody ?? first ?? panel)?.focus()

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
    /* `my-auto`, not `items-center`: a centred item taller than the scroller loses its top. */
    // Above dropdowns (z-[100]): a stray open menu must not take dialog clicks.
    <div className="fixed inset-0 z-[150] flex justify-center overflow-y-auto p-4 max-sm:p-2">
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
            {description && <p className="mt-0.5 line-clamp-2 text-base text-muted" title={description}>{description}</p>}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label={t('닫기')}>
            <X size={16} />
          </Button>
        </header>}
        {children && (
          <div data-modal-body className={bare ? 'min-w-0' : 'min-w-0 space-y-4 px-5 py-4 max-sm:px-3'}>
            {children}
          </div>
        )}
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
  open: controlledOpen,
  onOpenChange,
  sheet = false,
  label,
  matchTrigger = false,
}: {
  trigger: (props: { open: boolean }) => ReactNode
  children: ReactNode
  align?: 'left' | 'right'
  className?: string
  /** Controlled open state; self-closing still reported via `onOpenChange`. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
  /** Opens as a sheet along the bottom edge instead of beside the trigger (phones). */
  sheet?: boolean
  /** Accessible name of the menu. */
  label?: string
  /** Menu as wide as the trigger (form fields). */
  matchTrigger?: boolean
}) {
  const [ownOpen, setOwnOpen] = useState(false)
  const open = controlledOpen ?? ownOpen
  const openChange = useRef(onOpenChange)
  const openNow = useRef(open)
  useLayoutEffect(() => {
    openChange.current = onOpenChange
    openNow.current = open
  })
  const setOpen = useCallback((next: boolean | ((was: boolean) => boolean)) => {
    const value = typeof next === 'function' ? next(openNow.current) : next
    openNow.current = value
    setOwnOpen(value)
    openChange.current?.(value)
  }, [])
  // Opening direction and height cap, measured against the viewport.
  const [placement, setPlacement] = useState<{ up: boolean; maxHeight: number; left: number; top: number; bottom: number; width: number }>({
    up: false,
    maxHeight: 0,
    width: 0,
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
    const menuWidth = matchTrigger ? rect.width : (menuRef.current?.getBoundingClientRect().width ?? 208)
    const left = align === 'right'
      ? Math.max(8, Math.min(window.innerWidth - menuWidth - 8, rect.right - menuWidth))
      : Math.max(8, Math.min(window.innerWidth - menuWidth - 8, rect.left))
    setPlacement({
      up,
      // height cap: 60% of the viewport
      maxHeight: Math.max(160, Math.min(up ? above : below, Math.round(window.innerHeight * 0.6))),
      width: matchTrigger ? rect.width : 0,
      left,
      top: rect.bottom + 6,
      bottom: window.innerHeight - rect.top + 6,
    })
  }, [align, matchTrigger])

  useLayoutEffect(() => {
    if (open) measure()
  }, [open, measure])

  // Focus back to the trigger after a choice, unless the item moved it.
  const wasOpen = useRef(false)
  useEffect(() => {
    const was = wasOpen.current
    wasOpen.current = open
    if (!was || open) return
    let observer: MutationObserver | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    const frame = requestAnimationFrame(() => {
      const active = document.activeElement
      if (active && active !== document.body) return
      const trigger = ref.current?.querySelector<HTMLButtonElement>('button')
      if (!trigger) return
      if (!trigger.disabled) {
        trigger.focus()
        return
      }
      // Trigger disabled while saving: refocus once enabled, bounded wait.
      observer = new MutationObserver(() => {
        if (trigger.disabled) return
        observer?.disconnect()
        if (!document.activeElement || document.activeElement === document.body) trigger.focus()
      })
      observer.observe(trigger, { attributes: true, attributeFilter: ['disabled'] })
      timer = setTimeout(() => observer?.disconnect(), 4000)
    })
    return () => {
      cancelAnimationFrame(frame)
      observer?.disconnect()
      if (timer) clearTimeout(timer)
    }
  }, [open])

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
  }, [open, measure, setOpen])

  return (
    <div ref={ref} className={cn('relative shrink-0', open && 'z-50')}>
      <div
        onClick={() => setOpen((o) => !o)}
      >
        {trigger({ open })}
      </div>
      {open && typeof document !== 'undefined' && createPortal(
        <MenuCtx.Provider value={{ close: () => setOpen(false) }}>
          {sheet && <div className="fixed inset-0 z-[99] bg-black/30" aria-hidden />}
          <div
            ref={menuRef}
            role="menu"
            aria-label={label}
            style={
              sheet
                ? undefined
                : {
                    position: 'fixed',
                    maxHeight: placement.maxHeight || undefined,
                    width: placement.width || undefined,
                    left: placement.left,
                    ...(placement.up ? { bottom: placement.bottom } : { top: placement.top }),
                  }
            }
            className={cn(
              sheet
                ? 'animate-fade-up fixed inset-x-0 bottom-0 z-[100] max-h-[80dvh] overflow-y-auto rounded-t-panel border-t border-line bg-panel p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] shadow-overlay'
                : cn(
                    'animate-fade-up z-[100] min-w-52 max-w-[calc(100vw-1rem)] overflow-y-auto rounded-card border border-line bg-panel p-1 shadow-overlay',
                    className,
                  ),
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
  label,
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
  /** Accessible name when the visible row says more than the name (a badge, a hint). */
  label?: string
}) {
  const { close } = useContext(MenuCtx)
  return (
    <button
      role={checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
      aria-checked={checked}
      aria-label={label}
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

/** `useResource` loading/failed states: slow-load spinner or 다시 시도; nothing once loaded. */
export function ResourceStatus({
  resource,
  label,
  failed,
  compact = false,
}: {
  resource: { status: 'loading' | 'error' | 'ok'; slow: boolean; reload: () => void }
  /** While loading, e.g. 「모델 목록을 불러오는 중…」. */
  label?: string
  /** On failure; 「불러오지 못했습니다.」 when not given. */
  failed?: string
  /** One line, for a single field rather than a whole section. */
  compact?: boolean
}) {
  const t = useT()
  if (resource.status === 'ok') return null
  const box = compact
    ? 'flex flex-wrap items-center gap-2 py-1.5 text-sm'
    : 'flex flex-wrap items-center justify-center gap-2 px-6 py-12 text-base'
  if (resource.status === 'error') {
    return (
      <div role="alert" className={cn(box, 'text-danger')}>
        <span>{failed ?? t('불러오지 못했습니다.')}</span>
        <Button type="button" size="sm" onClick={resource.reload}>
          {t('다시 시도')}
        </Button>
      </div>
    )
  }
  return (
    <div role="status" className={cn(box, 'text-faint')}>
      <span className="size-3 animate-spin rounded-full border-2 border-line-strong border-t-transparent" aria-hidden />
      <span>{label ?? t('불러오는 중…')}</span>
      {resource.slow && <span>{t('평소보다 오래 걸립니다.')}</span>}
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
      <span className="min-w-0 flex-1 max-sm:basis-32">
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
  embedded = false,
}: {
  title: string
  description?: string
  action?: ReactNode
  /** Inside another screen that owns the heading: description and action only. */
  embedded?: boolean
}) {
  return (
    <div className={cn('flex flex-wrap justify-between gap-4', embedded ? 'items-center pb-4' : 'items-end pb-6')}>
      <div className="min-w-0 flex-1 max-sm:basis-32">
        {!embedded && <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>}
        {description && <p className={cn('text-base text-muted', !embedded && 'mt-1')}>{description}</p>}
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
    // A phone scrolls the row sideways instead of the page.
    <div
      role="tablist"
      className="flex gap-1 border-b border-line max-sm:overflow-x-auto max-sm:[scrollbar-width:none]"
    >
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            // One line per tab, even at phone width.
            '-mb-px border-b-2 px-3 py-2 text-base font-medium whitespace-nowrap transition-colors max-sm:px-2.5',
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
