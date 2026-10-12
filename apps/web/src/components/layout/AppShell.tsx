import { X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { IssueReportDialog } from '@/components/issues/IssueReportDialog'
import { useIssueBadge } from '@/lib/issues'
import { useBotAttentionBadge } from '@/lib/publishing'
import { useNarrowLayout } from '@/lib/useMediaQuery'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { KeyboardShortcuts } from './KeyboardShortcuts'
import { SearchDialog } from './SearchDialog'
import { TopBar } from './TopBar'
import { Sidebar } from './Sidebar'
import { useT } from '@/lib/useT'

/** Undo toast for a pending delete; in the shell to survive navigation. */
function UndoBar() {
  const t = useT()
  const pending = useStore((s) => s.pendingDelete)
  if (!pending) return null
  return (
    <div
      role="status"
      className="animate-fade-up absolute bottom-4 left-1/2 z-50 flex max-w-[92vw] -translate-x-1/2 items-center gap-3 rounded-card border border-line bg-panel px-4 py-2.5 text-base shadow-float"
    >
      <span className="min-w-0 truncate">
        {t('{name} 삭제됨').replace('{name}', pending.label)}
      </span>
      <button
        onClick={pending.undo}
        className="shrink-0 font-medium text-accent hover:underline"
      >
        {t('실행 취소')}
      </button>
    </div>
  )
}

/** Passing neutral message (「바로 쓰려고 내 스킬에 추가했어요 · 빼기」); outlives navigation. */
function ToastBar() {
  const t = useT()
  const toast = useStore((s) => s.toast)
  const setToast = useStore((s) => s.setToast)
  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 8_000)
    return () => clearTimeout(timer)
  }, [toast, setToast])
  if (!toast) return null
  return (
    <div
      role="status"
      data-testid="toast"
      className="animate-fade-up absolute bottom-16 left-1/2 z-50 flex max-w-[92vw] -translate-x-1/2 items-center gap-3 rounded-card border border-line bg-panel px-4 py-2.5 text-base shadow-float"
    >
      <span className="min-w-0">{toast.text}</span>
      {toast.action && (
        <button
          onClick={() => {
            toast.action?.run()
            setToast(null)
          }}
          className="shrink-0 font-medium text-accent hover:underline"
        >
          {toast.action.label}
        </button>
      )}
      <button
        onClick={() => setToast(null)}
        aria-label={t('닫기')}
        className="shrink-0 rounded-control p-0.5 text-faint hover:bg-elevated hover:text-fg"
      >
        <X size={14} />
      </button>
    </div>
  )
}

/** Error toast for failures with no screen of their own. */
function NoticeBar() {
  const t = useT()
  const notice = useStore((s) => s.notice)
  const setNotice = useStore((s) => s.setNotice)
  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(null), 10_000)
    return () => clearTimeout(timer)
  }, [notice, setNotice])
  if (!notice) return null
  return (
    <div
      role="alert"
      className="animate-fade-up absolute top-4 left-1/2 z-50 flex max-w-[92vw] -translate-x-1/2 items-start gap-3 rounded-card border border-danger/30 bg-panel px-4 py-2.5 text-base text-danger shadow-float"
    >
      <span className="min-w-0">{notice}</span>
      <button
        onClick={() => setNotice(null)}
        aria-label={t('닫기')}
        className="shrink-0 rounded-control p-0.5 text-faint hover:bg-elevated hover:text-fg"
      >
        <X size={14} />
      </button>
    </div>
  )
}

export function AppShell() {
  const t = useT()
  const narrow = useNarrowLayout()
  const { sidebar, cycleSidebar } = useStore()
  const open = sidebar !== 'hidden'
  const location = useLocation()
  useIssueBadge()
  useBotAttentionBadge()

  // Narrow layout: sidebar overlays, so navigation closes it.
  useEffect(() => {
    if (narrow && open) cycleSidebar()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, narrow])

  // Phone drawer is modal: focus in, Escape closes, focus restored; page behind `inert`.
  const drawer = useRef<HTMLDivElement>(null)
  const mainRef = useRef<HTMLElement>(null)
  const drawerOpen = narrow && open
  useEffect(() => {
    if (!drawerOpen) return
    const opener = document.activeElement as HTMLElement | null
    drawer.current?.querySelector<HTMLElement>('a[href], button:not([disabled]), input:not([disabled])')?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      // A menu or dialog from the drawer owns this Escape.
      const above = [...document.querySelectorAll('[role="menu"], [aria-modal="true"]')]
      if (above.some((el) => el !== drawer.current)) return
      e.preventDefault()
      cycleSidebar()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      if (opener?.isConnected) opener.focus()
    }
  }, [drawerOpen, cycleSidebar])

  return (
    <div className="relative flex h-full overflow-hidden bg-bg text-fg">
      {/* Skip link past the sidebar. */}
      <a
        href="#main"
        onClick={(e) => {
          e.preventDefault()
          mainRef.current?.focus()
        }}
        className="sr-only z-[200] rounded-control bg-accent px-3 py-2 text-base font-medium text-accent-fg focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        {t('본문으로 건너뛰기')}
      </a>
      {/* Drawer stays mounted and slides; reduced-motion guard in index.css. */}
      {narrow && (
        <button
          aria-label={t('사이드바 닫기')}
          // Out of Tab order (Escape closes); screen-reader swipe still reaches it while open.
          tabIndex={-1}
          aria-hidden={!open}
          className={cn(
            'absolute inset-0 z-30 bg-black/40 transition-opacity duration-200',
            open ? 'opacity-100' : 'pointer-events-none opacity-0',
          )}
          onClick={cycleSidebar}
        />
      )}
      <div
        ref={drawer}
        role={drawerOpen ? 'dialog' : undefined}
        aria-modal={drawerOpen || undefined}
        aria-label={drawerOpen ? t('탐색') : undefined}
        className={
          narrow
            ? cn(
                'absolute inset-y-0 left-0 z-40 shadow-float transition-transform duration-300',
                open ? 'translate-x-0' : '-translate-x-full',
              )
            : cn(
                'shrink-0 transition-[width] duration-300',
                // `overflow-hidden` only when hidden; would clip the rail's menus.
                sidebar === 'hidden'
                  ? 'w-0 overflow-hidden'
                  : sidebar === 'rail'
                    ? 'w-16'
                    : 'w-[268px]',
              )
        }
        // Collapsed panel out of tab order.
        inert={!open ? true : undefined}
      >
        <Sidebar />
      </div>
      <main
        id="main"
        ref={mainRef}
        tabIndex={-1}
        className="flex min-w-0 flex-1 flex-col outline-none"
        inert={drawerOpen ? true : undefined}
      >
        <Outlet />
      </main>
      <UndoBar />
      <NoticeBar />
      <ToastBar />
      <KeyboardShortcuts />
      <SearchDialog />
      <IssueReportDialog />
    </div>
  )
}

/** Standard scroll container for the non-chat management pages. */
export function PageBody({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className={cn('mx-auto w-full max-w-5xl px-4 py-6 sm:px-6 sm:py-8', className)}>
        {children}
      </div>
    </div>
  )
}

/** Management page top bar and scroll body; neither inside the 사용자 지정 hub. */
export function PageFrame({
  title,
  embedded = false,
  children,
}: {
  title: string
  embedded?: boolean
  children: React.ReactNode
}) {
  if (embedded) return <>{children}</>
  return (
    <>
      <TopBar left={<span className="text-base font-medium">{title}</span>} />
      <PageBody>{children}</PageBody>
    </>
  )
}
